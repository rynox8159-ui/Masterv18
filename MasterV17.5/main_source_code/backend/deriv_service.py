"""New Deriv Options public API. Never sends account tokens or authorize."""
import asyncio
import inspect
import json
import logging
import math
import os
import server_clock
import re
import time
from urllib.parse import parse_qs, urlsplit

import websockets
from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidStatus

from pairs_config import last_forex_session_close
from market_config import (
    DERIV_CANDLE_SYMBOLS,
    DERIV_ENABLED,
    DERIV_MAX_CANDLE_SYMBOLS,
    DERIV_SYMBOLS,
    DERIV_URL_CANDIDATES,
    FRESHNESS,
    TIMEFRAMES,
)

logger = logging.getLogger(__name__)

DERIV_USER_AGENT = 'MasterCandelBackend/1.0 (public Deriv market-data client)'
RECONNECT_BASE_SECONDS = 1
RECONNECT_MAX_SECONDS = int(os.environ.get('DERIV_RECONNECT_MAX_SECONDS', '20'))
RECONNECT_STABLE_SECONDS = 60
TICK_STREAM_STALE_SECONDS = max(20, int(os.environ.get('DERIV_TICK_STALE_SECONDS', '30')))
WEBSOCKET_RECV_POLL_SECONDS = 5
CANDLE_STREAM_TIMEFRAMES = ('1m', '5m', '15m')
# Extra websocket connections stream 1m/5m candles for every pair beyond the main connection's 50 slots.
EXTRA_CANDLE_TIMEFRAMES = ('1m', '5m', '15m')
EXTRA_SYMBOLS_PER_CONNECTION = 16
MAX_DERIV_STREAM_SUBSCRIPTIONS = 50
HISTORY_WARMUP_TIMEFRAMES = ('1m', '5m', '15m', '1h')
HISTORY_WARMUP_CANDLES = 200
HISTORY_REQUEST_INTERVAL_SECONDS = 2.0
HISTORY_RATE_LIMIT_RETRIES = 4
HISTORY_MAX_EMPTY_DAYS = 6  # skip weekends/holidays while paging day-bounded history backwards
TICK_SUBSCRIPTION_INTERVAL_SECONDS = 0.2
CANDLE_SUBSCRIPTION_INTERVAL_SECONDS = 0.5


def reconnect_delay_seconds(attempt):
    """Exponential reconnect delay: 2, 4, 8, 16, 32, then capped at 60 seconds."""
    attempt = max(0, int(attempt))
    return min(RECONNECT_MAX_SECONDS, RECONNECT_BASE_SECONDS * (2 ** min(attempt, 10)))


def reconnect_attempt_after_disconnect(attempt, connection_duration):
    """Reset retry backoff only after the stream stayed up long enough to be healthy."""
    if connection_duration is not None and connection_duration >= RECONNECT_STABLE_SECONDS:
        return 0
    return max(0, int(attempt))


def candle_symbol_capacity(tick_symbol_count, max_candle_symbols):
    streams_per_symbol = sum(
        timeframe in TIMEFRAMES for timeframe in CANDLE_STREAM_TIMEFRAMES
    )
    if streams_per_symbol == 0:
        return 0
    available_slots = max(
        0, MAX_DERIV_STREAM_SUBSCRIPTIONS - max(0, int(tick_symbol_count)),
    )
    return min(
        max(0, int(max_candle_symbols)),
        available_slots // streams_per_symbol,
    )


def always_open_tick_stream_is_stale(symbols, last_ticks, now, stale_after):
    return bool(symbols) and not any(
        now - last_ticks.get(symbol, 0) <= stale_after
        for symbol in symbols
    )


def tick_stream_reconnect_reason(symbols, last_ticks, now, connection_age, stale_after):
    if (
        connection_age > stale_after
        and always_open_tick_stream_is_stale(symbols, last_ticks, now, stale_after)
    ):
        return 'No recent Deriv ticks for always-open instruments'
    return None


def _disconnect_cause(error):
    close_frame = getattr(error, 'rcvd', None) or getattr(error, 'sent', None)
    close_code = getattr(close_frame, 'code', None)
    close_reason = getattr(close_frame, 'reason', None)
    if close_code is not None or close_reason is not None:
        return f' · close code {close_code or "unknown"} · reason {close_reason or "none"}'
    if isinstance(error, (ConnectionClosed, TimeoutError)):
        detail = str(error).strip()
        return f' · {detail[:160]}' if detail else ''
    return ''


def websocket_user_agent_options(connect=websockets.connect):
    """Return the header option supported by the installed websockets API."""
    parameters = inspect.signature(connect).parameters
    if 'user_agent_header' in parameters:
        return {'user_agent_header': DERIV_USER_AGENT}
    if 'additional_headers' in parameters:
        return {'additional_headers': {'User-Agent': DERIV_USER_AGENT}}
    if 'extra_headers' in parameters:
        return {'extra_headers': {'User-Agent': DERIV_USER_AGENT}}
    raise RuntimeError('Installed websockets client does not support custom request headers')


class DerivRequestError(ValueError):
    def __init__(self, error):
        self.code = str(error.get('code') or 'DERIV_REQUEST_REJECTED')
        self.provider_message = str(error.get('message') or 'Provider rejected the request')
        super().__init__(f'{self.code}: {self.provider_message}')


WEBSOCKET_USER_AGENT_OPTIONS = websocket_user_agent_options()


def _endpoint_label(url):
    parsed = urlsplit(url)
    app_id = (parse_qs(parsed.query).get('app_id') or [''])[0]
    app_label = 'default-app' if app_id == '1089' else 'custom-app' if app_id else 'no-app-id'
    return f'{parsed.hostname or "unknown-host"}{parsed.path} ({app_label})'

SYMBOL_MAPPING = {
    'R_10': 'Volatility 10 Index',
    'R_25': 'Volatility 25 Index',
    'R_50': 'Volatility 50 Index',
    'R_75': 'Volatility 75 Index',
    'R_100': 'Volatility 100 Index',
    'BOOM1000': 'Boom 1000 Index',
    'CRASH1000': 'Crash 1000 Index',
}

_OTC_MARKER = re.compile(r'(^|[^A-Z0-9])OTC([^A-Z0-9]|$)', re.IGNORECASE)
_SYNTHETIC_VOLATILITY_SYMBOL = re.compile(
    r'^(?:R_(?:10|25|50|75|100)|1HZ(?:10|15|25|30|50|75|90|100)V)$'
)


def is_real_market_instrument(row):
    """Keep supported non-OTC FX/metals/indices, crypto, and volatility indices."""
    symbol = str(row.get('underlying_symbol') or row.get('symbol') or '').strip()
    market = str(row.get('market') or '').strip().lower()
    submarket = str(row.get('submarket') or '').strip().lower()
    label = str(row.get('underlying_symbol_name') or row.get('display_name') or row.get('label') or '')
    if not symbol:
        return False
    if any(_OTC_MARKER.search(value) for value in (symbol, submarket, label)):
        return False
    if row.get('is_trading_suspended'):
        return False
    # V16.8: every non-OTC Deriv market (forex, synthetics incl. boom/crash/jump/step, crypto, commodities, indices, baskets).
    return bool(market) and submarket != 'otc'


def format_market_data(raw_symbol: str, price_data: float):
    display_name = SYMBOL_MAPPING.get(raw_symbol, raw_symbol)
    return {
        'symbol': raw_symbol,
        'displayName': display_name,
        'price': price_data,
    }


async def subscribe_all_pairs(websocket, symbols=None, send_interval=TICK_SUBSCRIPTION_INTERVAL_SECONDS):
    selected_symbols = list(symbols or [])
    if not selected_symbols:
        raise ValueError('NO_ELIGIBLE_SYMBOLS_TO_SUBSCRIBE')
    for request_id, symbol in enumerate(selected_symbols, 1):
        payload = {'ticks': symbol, 'subscribe': 1, 'req_id': request_id}
        await websocket.send(json.dumps(payload))
        logger.info('Subscribed to live feed for: %s (%s)', SYMBOL_MAPPING.get(symbol, symbol), symbol)
        if send_interval > 0:
            await asyncio.sleep(send_interval)


async def subscribe_candle_streams(
    websocket, symbols=None, request_id_start=1, timeframes=CANDLE_STREAM_TIMEFRAMES,
    send_interval=CANDLE_SUBSCRIPTION_INTERVAL_SECONDS, initial_count=2,
):
    selected_symbols = list(symbols or [])
    if not selected_symbols:
        raise ValueError('NO_ELIGIBLE_SYMBOLS_TO_SUBSCRIBE')
    selected_timeframes = [
        timeframe for timeframe in timeframes
        if timeframe in CANDLE_STREAM_TIMEFRAMES and timeframe in TIMEFRAMES
    ]
    if not selected_timeframes:
        raise ValueError('NO_SUPPORTED_CANDLE_TIMEFRAMES_TO_SUBSCRIBE')
    request_id = request_id_start
    for symbol in selected_symbols:
        for timeframe in selected_timeframes:
            await websocket.send(json.dumps({
                'ticks_history': symbol,
                'end': 'latest',
                'count': initial_count,
                'style': 'candles',
                'granularity': TIMEFRAMES[timeframe],
                'subscribe': 1,
                'req_id': request_id,
            }))
            request_id += 1
            if send_interval > 0:
                await asyncio.sleep(send_interval)


APP_PING_SECONDS = 12  # Deriv drops sockets after 120s without a client request; WS pings don't count.
HEARTBEAT_SILENCE_SECONDS = max(15, int(os.environ.get('DERIV_HEARTBEAT_SILENCE_SECONDS', '18')))
REDISCOVER_INTERVAL_SECONDS = max(30, int(os.environ.get('DERIV_MARKET_STATUS_INTERVAL_SECONDS', '60')))  # active_symbols poll: open/closed flags + new pairs
REQUEST_MIN_INTERVAL_SECONDS = 0.25  # Deriv general rate limit: keep one-off requests well under their per-minute budget


class DerivRequestChannel:
    """One shared request/response websocket (history, active_symbols, time) with req_id routing and auto-reconnect."""

    def __init__(self, open_connection):
        self._open_connection = open_connection
        self.ws = None
        self.reader = None
        self.pinger = None
        self.futures = {}
        self.request_id = 0
        self.lock = asyncio.Lock()
        self.last_sent_at = 0.0

    @property
    def closed(self):
        return self.ws is None or self.reader is None or self.reader.done()

    async def _ensure(self):
        async with self.lock:
            if not self.closed:
                return
            await self._close_tasks()
            self.ws = await self._open_connection()
            self.reader = asyncio.create_task(self._read())
            self.pinger = asyncio.create_task(app_keepalive(self.ws, 30))

    async def _read(self):
        try:
            async for raw in self.ws:
                message = json.loads(raw)
                future = self.futures.pop(message.get('req_id'), None)
                if future is not None and not future.done():
                    future.set_result(message)
        except Exception as exc:  # noqa: BLE001
            logger.debug('Deriv request channel closed: %s', type(exc).__name__)
        finally:
            for future in list(self.futures.values()):
                if not future.done():
                    future.set_exception(ConnectionError('DERIV_REQUEST_CHANNEL_CLOSED'))
            self.futures.clear()

    async def request(self, body, timeout=20):
        for attempt in range(2):
            await self._ensure()
            self.request_id += 1
            rid = self.request_id
            future = asyncio.get_running_loop().create_future()
            self.futures[rid] = future
            try:
                wait = REQUEST_MIN_INTERVAL_SECONDS - (time.monotonic() - self.last_sent_at)
                if wait > 0:
                    await asyncio.sleep(wait)
                self.last_sent_at = time.monotonic()
                await self.ws.send(json.dumps({**body, 'req_id': rid}))
                message = await asyncio.wait_for(future, timeout)
            except (ConnectionError, ConnectionClosed, asyncio.TimeoutError) as exc:
                self.futures.pop(rid, None)
                await self._close_tasks()
                if attempt == 1:
                    raise exc
                continue
            error = message.get('error') or next(iter(message.get('errors') or []), None)
            if error:
                raise DerivRequestError(error)
            return message

    async def _close_tasks(self):
        for task in (self.reader, self.pinger):
            if task is not None:
                task.cancel()
        self.reader = self.pinger = None
        if self.ws is not None:
            try:
                await self.ws.close()
            except Exception:  # noqa: BLE001
                pass
        self.ws = None

    async def close(self):
        await self._close_tasks()


async def app_keepalive(ws, interval=APP_PING_SECONDS):
    while True:
        await asyncio.sleep(interval)
        await ws.send(json.dumps({'ping': 1}))


class DerivService:
    def __init__(self, store, market_cache=None, agent_pool=None):
        self.store = store
        self.market_cache = market_cache
        self.agent_pool = agent_pool
        self.state = 'CONNECTING' if DERIV_ENABLED else 'DISABLED'
        self.last_error = None
        self.last_tick = None
        self.last_tick_by_symbol = {}
        self.symbols = []
        self.candle_symbols = []
        self._symbol_markets = {}
        self.warmup_priority_symbols = set()
        self._symbol_labels = {}
        self.task = None
        self.history_locks = {}
        self.history_loaded = {}
        self.history_request_lock = asyncio.Lock()
        self.last_history_request_at = 0.0
        self.semaphore = asyncio.Semaphore(3)
        self.accepted = set()
        self.rejected = {}
        self.request_symbols = {}
        self.request_streams = {}
        self.connection_latency_ms = None
        self.heartbeat_latency_ms = None
        self.last_message_at = None
        self.last_extra_message_at = None
        self.processing_lag = None
        self._pending_ticks = {}
        self._tick_event = asyncio.Event()
        self._extra_streams = {}
        self._discovered_at = 0.0
        self.candle_streams = set()
        self.candle_rejected = {}
        self.open_candles = {}
        self.connect_urls = list(DERIV_URL_CANDIDATES)
        self.last_connect_url = self.connect_urls[0] if self.connect_urls else None
        self.connect_timeout_seconds = max(1.0, float(os.environ.get('DERIV_CONNECT_TIMEOUT_SECONDS', '6')))
        self.channel = DerivRequestChannel(self._open_connection)
        self.exchange_open = {}
        self.market_status_at = None
        self._tick_subscribers = {}  # symbol -> set[asyncio.Queue]  (0-lag fan-out to SSE tick streams)
        self.tick_listeners = []  # V17.2: sync callbacks (signal brain) fed on every tick

    def subscribe_ticks(self, symbol):
        queue = asyncio.Queue(maxsize=64)
        self._tick_subscribers.setdefault(symbol, set()).add(queue)
        return queue

    def unsubscribe_ticks(self, symbol, queue):
        queues = self._tick_subscribers.get(symbol)
        if queues:
            queues.discard(queue)
            if not queues:
                self._tick_subscribers.pop(symbol, None)

    def publish_tick(self, symbol, price, epoch):
        for listener in self.tick_listeners:
            try:
                listener(symbol, price, epoch)
            except Exception as exc:  # noqa: BLE001
                logger.debug('tick listener failed: %s', type(exc).__name__)
        for queue in self._tick_subscribers.get(symbol, ()):
            if queue.full():  # slow client: drop the oldest, never block the feed
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait((price, epoch))

    async def request(self, body):
        return await self.channel.request(body)

    def market_is_open(self, symbol):
        return self.exchange_open.get(symbol)

    async def _refresh_market_status(self):
        """Poll active_symbols: Deriv's own exchange_is_open flag per pair (no weekend rule), new/closed pairs."""
        payload = await self.request({'active_symbols': 'brief'})
        rows = [row for row in payload.get('active_symbols', []) if is_real_market_instrument(row)]
        instruments = getattr(getattr(self.store, 'db', None), 'market_instruments', None)
        fresh_open = {}
        for row in rows:
            symbol = row.get('underlying_symbol') or row.get('symbol')
            if not symbol:
                continue
            is_open = str(row.get('exchange_is_open', True)).strip().lower() not in {'0', 'false', 'no'}
            fresh_open[symbol] = is_open
            if instruments is not None and self.exchange_open.get(symbol) != is_open:
                await instruments.update_one({'source': 'deriv', 'symbol': symbol}, {'$set': {'available': is_open, 'exchangeIsOpen': is_open, 'marketStatusAt': time.time()}})
        self.exchange_open = fresh_open
        self.market_status_at = time.time()
        newly_open = sorted(symbol for symbol, is_open in fresh_open.items() if is_open and symbol not in self.symbols)
        return newly_open

    async def _open_connection(self):
        last_error = None
        failures = []
        for index, url in enumerate(self.connect_urls):
            try:
                connection = await asyncio.wait_for(
                    websockets.connect(
                        url,
                        **WEBSOCKET_USER_AGENT_OPTIONS,
                        open_timeout=self.connect_timeout_seconds,
                        close_timeout=5,
                        ping_interval=None,
                        ping_timeout=20,
                        max_size=2 ** 22,
                    ),
                    timeout=self.connect_timeout_seconds + 2,
                )
                self.last_connect_url = url
                return connection
            except (InvalidStatus, InvalidHandshake, ConnectionError, OSError, asyncio.TimeoutError, ValueError) as exc:
                last_error = exc
                response = getattr(exc, 'response', None)
                status_code = getattr(response, 'status_code', None)
                failures.append(f'{_endpoint_label(url)}: {type(exc).__name__} (HTTP {status_code or "n/a"})')
                if index + 1 < len(self.connect_urls):
                    await asyncio.sleep(0.25)
        if failures:
            logger.warning('Deriv endpoint failover exhausted: %s', '; '.join(failures))
        if last_error is not None:
            raise last_error
        raise RuntimeError('No Deriv websocket endpoints configured')

    async def _history_request(self, body):
        async with self.history_request_lock:
            for attempt in range(HISTORY_RATE_LIMIT_RETRIES + 1):
                elapsed = time.monotonic() - self.last_history_request_at
                if elapsed < HISTORY_REQUEST_INTERVAL_SECONDS:
                    await asyncio.sleep(HISTORY_REQUEST_INTERVAL_SECONDS - elapsed)
                self.last_history_request_at = time.monotonic()
                try:
                    return await self.request(body)
                except DerivRequestError as exc:
                    if exc.code != 'RateLimit' or attempt == HISTORY_RATE_LIMIT_RETRIES:
                        raise
                    delay = min(30, 2 ** (attempt + 1))
                    logger.warning(
                        'Deriv history rate limited for %s; retrying in %ss (%s/%s)',
                        body.get('ticks_history'), delay, attempt + 1,
                        HISTORY_RATE_LIMIT_RETRIES,
                    )
                    await asyncio.sleep(delay)

    async def discover(self):
        payload = await self.request({'active_symbols': 'brief'})
        self.symbols = []
        self.candle_symbols = []
        self.warmup_priority_symbols = set()
        self._symbol_labels = {}
        self._symbol_markets = {}
        seen = set()
        configured_symbols = set() if DERIV_SYMBOLS == ['ALL'] else set(DERIV_SYMBOLS)
        for row in payload.get('active_symbols', []):
            symbol = row.get('underlying_symbol') or row.get('symbol')
            if not symbol:
                continue
            if symbol in seen:
                continue
            seen.add(symbol)
            label = row.get('underlying_symbol_name') or row.get('display_name') or symbol
            exchange_open = row.get('exchange_is_open', True)
            available = str(exchange_open).strip().lower() not in {'0', 'false', 'no'}
            eligible = (
                is_real_market_instrument(row)
                and (not configured_symbols or symbol in configured_symbols)
            )
            if eligible:
                self.exchange_open[symbol] = available
            if not eligible:
                await self.store.instrument(
                    'deriv', symbol, label, market=row.get('market'),
                    submarket=row.get('submarket'), providerSymbol=symbol,
                    available=available, exchangeIsOpen=available, marketDataEligible=False,
                    signalEligible=False, supportedTimeframes=list(TIMEFRAMES),
                    provenance='DERIV_PUBLIC_API',
                )
                continue
            self._symbol_labels[symbol] = label
            self._symbol_markets[symbol] = str(row.get('market') or '').lower()
            await self.store.instrument(
                'deriv', symbol, label, market=row.get('market'),
                submarket=row.get('submarket'), providerSymbol=symbol,
                available=available, exchangeIsOpen=available, marketDataEligible=True, signalEligible=False,
                supportedTimeframes=list(TIMEFRAMES), provenance='DERIV_PUBLIC_API',
            )
            if str(row.get('market') or '').lower() in {'synthetic_index', 'cryptocurrency'}:
                self.warmup_priority_symbols.add(symbol)
            if available:
                self.symbols.append(symbol)
        instruments_collection = getattr(getattr(self.store, 'db', None), 'market_instruments', None)
        if instruments_collection is not None:
            await instruments_collection.update_many(
                {'source': 'deriv', 'symbol': {'$nin': list(seen)}},
                {'$set': {
                    'available': False, 'marketDataEligible': False,
                    'signalEligible': False,
                }},
            )
        if not self.symbols:
            raise ValueError('NO_CONFIGURED_SYMBOLS_AVAILABLE')
        if DERIV_CANDLE_SYMBOLS == ['ALL']:
            market_priority = {
                'forex': 0,
                'cryptocurrency': 1,
                'synthetic_index': 2,
                'indices': 2,
                'commodities': 3,
            }
            preferred_symbols = sorted(
                self.symbols,
                key=lambda symbol: market_priority.get(self._symbol_markets.get(symbol), 4),
            )
        else:
            preferred_symbols = [
                symbol for symbol in DERIV_CANDLE_SYMBOLS if symbol in self.symbols
            ]
            if not preferred_symbols and configured_symbols:
                preferred_symbols = list(self.symbols)
        candle_capacity = candle_symbol_capacity(
            len(self.symbols), DERIV_MAX_CANDLE_SYMBOLS,
        )
        self.candle_symbols = preferred_symbols[:candle_capacity]
        for symbol in self.candle_symbols:
            await self.store.instrument(
                'deriv', symbol, self._symbol_labels.get(symbol, symbol),
                marketDataEligible=True, signalEligible=True,
            )

    async def history(self, symbol, timeframe, count=300, force=False):
        if TIMEFRAMES[timeframe] < 60:
            return
        count = max(2, min(50_000, int(count)))
        key = (symbol, timeframe)
        async with self.history_locks.setdefault(key, asyncio.Lock()):
            if not force and time.time() - self.history_loaded.get(key, 0) < 60:
                return
            now = time.time()
            seconds = TIMEFRAMES[timeframe]
            provider_candles = {}
            end = 'latest'
            previous_earliest = None
            empty_days = 0
            while len(provider_candles) < count:
                page_count = min(1000, count - len(provider_candles))
                response = await self._history_request({
                    'ticks_history': symbol,
                    'end': end,
                    'count': page_count,
                    'style': 'candles',
                    'granularity': seconds,
                })
                page = response.get('candles', [])
                if not page and end == 'latest' and not provider_candles:
                    closed_end = last_forex_session_close(now)
                    if closed_end is not None:
                        # Deriv returns no 'latest' 1m forex candles while the session is closed; anchor at the last close.
                        end = closed_end
                        continue
                if not page:
                    # The public API pages per UTC day: an empty day (weekend / holiday) must be skipped, not treated as the end.
                    if end != 'latest' and empty_days < HISTORY_MAX_EMPTY_DAYS:
                        empty_days += 1
                        end = int(end) - 86_400
                        await asyncio.sleep(0.2)
                        continue
                    break
                empty_days = 0
                epochs = []
                for candle in page:
                    epoch = int(candle['epoch'])
                    epochs.append(epoch)
                    if epoch + seconds <= now:
                        provider_candles[epoch] = candle
                earliest = min(epochs)
                if previous_earliest is not None and earliest >= previous_earliest:
                    break
                previous_earliest = earliest
                end = earliest - 1
                if len(provider_candles) < count:
                    await asyncio.sleep(0.2)

            rows = [
                self.store.candle(
                    'deriv', symbol, timeframe, epoch,
                    float(candle['open']), float(candle['high']), float(candle['low']), float(candle['close']),
                    method='DERIV_HISTORY', completeness='PROVIDER_OHLC',
                )
                for epoch, candle in sorted(provider_candles.items())
            ][-count:]
            await self.store.save_candles(rows)
            if self.market_cache is not None and count <= 300:
                for candle in rows:
                    await self.market_cache.put(
                        'deriv', symbol, timeframe, candle['epoch'] + TIMEFRAMES[timeframe],
                        {key: candle[key] for key in ('open', 'high', 'low', 'close')} | {'price': candle['close'], 'verified': True},
                        event_id=f"{symbol}:{timeframe}:{candle['epoch']}",
                    )
            self.history_loaded[key] = time.time()

    async def warm(self):
        async def warm_timeframe(symbol, timeframe):
            try:
                await self.history(symbol, timeframe, count=HISTORY_WARMUP_CANDLES)
            except Exception as exc:
                logger.warning('Deriv %s history unavailable for %s: %s', timeframe, symbol, type(exc).__name__)

        priority_symbols = set(self.warmup_priority_symbols)
        priority_symbols.update(
            symbol for symbol in self.candle_symbols
            if _SYNTHETIC_VOLATILITY_SYMBOL.fullmatch(symbol)
        )
        primary = getattr(self, 'primary_candle_symbols', None) or self.candle_symbols
        ordered_symbols = sorted(priority_symbols.intersection(primary))
        ordered_symbols.extend(symbol for symbol in primary if symbol not in priority_symbols)
        for symbol in ordered_symbols:
            for timeframe in HISTORY_WARMUP_TIMEFRAMES:
                if timeframe in TIMEFRAMES:
                    await warm_timeframe(symbol, timeframe)

    async def _process_tick(self, symbol, tick_price, tick_epoch):
        try:
            await self.store.tick('deriv', symbol, tick_price, tick_epoch, 'DERIV_PUBLIC_TICK', skip_timeframes=CANDLE_STREAM_TIMEFRAMES)
            if self.market_cache is not None:
                await self.market_cache.put('deriv', symbol, 'tick', tick_epoch, {'price': tick_price, 'verified': True}, event_id=f'{symbol}:{tick_epoch}')
            if self.agent_pool is not None:
                await self.agent_pool.process_incoming_tick({'kind': 'tick', 'source': 'deriv', 'symbol': symbol, 'timeframe': 'tick', 'is_otc': False, 'verified': True})
            if symbol not in self.accepted:
                await self.store.db.market_instruments.update_one({'source': 'deriv', 'symbol': symbol}, {'$set': {'subscriptionState': 'ACCEPTED', 'subscriptionReason': None}})
            self.accepted.add(symbol)
            self.rejected.pop(symbol, None)
        except Exception as exc:  # noqa: BLE001
            logger.warning('Deriv tick processing failed for %s: %s', symbol, type(exc).__name__)

    async def _tick_consumer(self):
        # V17.2: all symbols of a batch are persisted concurrently (per-symbol lock in MarketStore keeps order).
        while True:
            await self._tick_event.wait()
            self._tick_event.clear()
            pending, self._pending_ticks = self._pending_ticks, {}
            if pending:
                await asyncio.gather(*(self._process_tick(symbol, price, epoch) for symbol, (price, epoch) in pending.items()))

    def _ensure_extra_streams(self, extra_symbols, extra_tick_symbols=()):
        # Extra connections survive primary reconnects so data keeps flowing (no stale gap).
        wanted = {('candle', tuple(extra_symbols[i:i + EXTRA_SYMBOLS_PER_CONNECTION])) for i in range(0, len(extra_symbols), EXTRA_SYMBOLS_PER_CONNECTION)}
        wanted |= {('tick', tuple(extra_tick_symbols[i:i + MAX_DERIV_STREAM_SUBSCRIPTIONS])) for i in range(0, len(extra_tick_symbols), MAX_DERIV_STREAM_SUBSCRIPTIONS)}
        for key in list(self._extra_streams):
            if key not in wanted or self._extra_streams[key].done():
                self._extra_streams.pop(key).cancel()
        for key in wanted:
            if key not in self._extra_streams:
                self._extra_streams[key] = asyncio.create_task(self._extra_candle_stream(list(key[1]), kind=key[0]))

    async def _extra_candle_stream(self, symbols, kind='candle'):
        timeframe_by_granularity = {TIMEFRAMES[name]: name for name in EXTRA_CANDLE_TIMEFRAMES}
        retry_attempt = 0
        while True:
            connection_started_at = None
            try:
                async with await self._open_connection() as ws:
                    connection_started_at = time.monotonic()
                    pinger = asyncio.create_task(app_keepalive(ws))
                    try:
                        if kind == 'tick':
                            await subscribe_all_pairs(ws, symbols)
                        else:
                            await subscribe_candle_streams(ws, symbols, timeframes=EXTRA_CANDLE_TIMEFRAMES, initial_count=HISTORY_WARMUP_CANDLES)
                        await self._extra_candle_loop(ws, symbols, timeframe_by_granularity)
                    finally:
                        pinger.cancel()
                    retry_attempt = reconnect_attempt_after_disconnect(
                        retry_attempt, time.monotonic() - connection_started_at,
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning('Deriv extra %s stream (%s symbols) reconnecting: %s', kind, len(symbols), type(exc).__name__)
                duration = time.monotonic() - connection_started_at if connection_started_at is not None else None
                retry_attempt = reconnect_attempt_after_disconnect(retry_attempt, duration)
                await asyncio.sleep(reconnect_delay_seconds(retry_attempt))

    def _queue_tick(self, tick):
        try:
            tick_price, tick_epoch = float(tick['quote']), float(tick['epoch'])
        except (TypeError, ValueError, KeyError):
            return
        self._pending_ticks[tick['symbol']] = (tick_price, tick_epoch)
        self._tick_event.set()
        self.publish_tick(tick['symbol'], tick_price, tick_epoch)
        self.processing_lag = round(time.time() - tick_epoch, 2)
        self.last_tick = time.time()  # local receive time: immune to PC clock drift
        self.last_tick_by_symbol[tick['symbol']] = time.time()
        self.state = 'DATA_RECEIVING'

    async def _extra_candle_loop(self, ws, symbols, timeframe_by_granularity):
        while True:
            data = json.loads(await asyncio.wait_for(ws.recv(), 45))
            self.last_extra_message_at = time.time()
            tick = data.get('tick')
            if tick and tick.get('symbol') in symbols:
                self._queue_tick(tick)
                continue
            echo = data.get('echo_req', {})
            timeframe = timeframe_by_granularity.get(echo.get('granularity'))
            if data.get('error') or timeframe is None:
                continue
            for candle in data.get('candles', []):
                await self._record_provider_candle(echo.get('ticks_history'), timeframe, candle)
            ohlc = data.get('ohlc')
            if ohlc and ohlc.get('symbol') in symbols:
                received = time.time()
                self.last_extra_message_at = received
                self.last_tick_by_symbol.setdefault(ohlc['symbol'], received)
                self.last_tick = max(self.last_tick or 0.0, received)
                await self._record_provider_candle(ohlc['symbol'], timeframe, ohlc)
                await self._finalize_closed_provider_candles()

    async def _cancel_background_tasks(self, *tasks):
        active_tasks = [task for task in tasks if task is not None]
        for task in active_tasks:
            task.cancel()
        if active_tasks:
            await asyncio.gather(*active_tasks, return_exceptions=True)

    async def _record_reconnect_warning(self, error, delay):
        message = (
            f'Connection unavailable · {type(error).__name__}'
            f'{_disconnect_cause(error)} · retry in {delay}s'
        )
        try:
            await self.store.event('WARN', 'Deriv', message)
        except Exception:
            logger.exception('Could not persist Deriv reconnect warning')

    async def _record_provider_candle(self, symbol, timeframe, candle, now=None):
        now = time.time() if now is None else now
        seconds = TIMEFRAMES.get(timeframe)
        if seconds is None or timeframe not in CANDLE_STREAM_TIMEFRAMES:
            return False
        try:
            epoch = int(candle.get('open_time', candle.get('epoch')))
            values = {
                name: float(candle[name])
                for name in ('open', 'high', 'low', 'close')
            }
        except (KeyError, TypeError, ValueError):
            return False
        if not all(math.isfinite(value) and value > 0 for value in values.values()):
            return False
        if values['low'] > min(values['open'], values['close']) or values['high'] < max(values['open'], values['close']):
            return False

        key = (symbol, timeframe)
        previous = self.open_candles.get(key)
        if previous and epoch > previous['epoch']:
            await self._finalize_closed_provider_candles(now, key=key)

        is_closed = epoch + seconds <= now
        row = self.store.candle(
            'deriv', symbol, timeframe, epoch,
            values['open'], values['high'], values['low'], values['close'],
            method='DERIV_PUBLIC_OHLC',
            completeness='PROVIDER_OHLC' if is_closed else 'PARTIAL_PROVIDER_OHLC',
        )
        await self.store.save_candles([row])
        if is_closed:
            if self.open_candles.get(key, {}).get('epoch') == epoch:
                self.open_candles.pop(key, None)
        else:
            self.open_candles[key] = {'epoch': epoch, 'row': row}
        self.candle_streams.add(key)
        return True

    async def _finalize_closed_provider_candles(self, now=None, key=None):
        now = time.time() if now is None else now
        candle_keys = [key] if key is not None else list(self.open_candles)
        for candle_key in candle_keys:
            current = self.open_candles.get(candle_key)
            timeframe = candle_key[1]
            if current is None or current['epoch'] + TIMEFRAMES[timeframe] > now:
                continue
            row = {**current['row'], 'completeness': 'PROVIDER_OHLC'}
            await self.store.save_candles([row])
            self.open_candles.pop(candle_key, None)

    async def run(self):
        retry_attempt = 0
        delay = reconnect_delay_seconds(retry_attempt)
        while True:
            warm = ping = candle_subscription = None
            extra_tasks = []
            connection_started_at = None
            try:
                self.state = 'CONNECTING'
                self.accepted.clear()
                self.rejected.clear()
                self.request_symbols.clear()
                self.request_streams.clear()
                self.candle_streams.clear()
                self.candle_rejected.clear()
                if not self.symbols or time.time() - self._discovered_at > REDISCOVER_INTERVAL_SECONDS:
                    await self.discover()
                    self._discovered_at = time.time()
                connect_started = time.monotonic()
                async with await self._open_connection() as ws:
                    connection_started_at = time.monotonic()
                    self.connection_latency_ms = round((time.monotonic() - connect_started) * 1000, 2)
                    primary_tick_symbols = list(self.symbols[:MAX_DERIV_STREAM_SUBSCRIPTIONS])
                    extra_tick_symbols = list(self.symbols[MAX_DERIV_STREAM_SUBSCRIPTIONS:])
                    for request_id, symbol in enumerate(primary_tick_symbols, 1):
                        self.request_symbols[request_id] = symbol
                        self.request_streams[request_id] = (symbol, 'tick')
                    await subscribe_all_pairs(ws, primary_tick_symbols)
                    candle_req_id_start = len(primary_tick_symbols) + 1
                    candle_request_id = candle_req_id_start
                    for symbol in self.candle_symbols:
                        for timeframe in CANDLE_STREAM_TIMEFRAMES:
                            if timeframe in TIMEFRAMES:
                                self.request_streams[candle_request_id] = (
                                    symbol, 'candle', timeframe,
                                )
                                candle_request_id += 1
                    if self.candle_symbols:
                        candle_subscription = asyncio.create_task(
                            subscribe_candle_streams(
                                ws, self.candle_symbols,
                                request_id_start=candle_req_id_start,
                            )
                        )
                    self.state = 'CONNECTED'
                    self.last_error = None
                    planned_subscriptions = len(primary_tick_symbols) + (
                        len(self.candle_symbols)
                        * sum(timeframe in TIMEFRAMES for timeframe in CANDLE_STREAM_TIMEFRAMES)
                    )
                    await self.store.event(
                        'INFO', 'Deriv',
                        f'Public stream connected · {len(self.symbols)} pairs · primary socket {planned_subscriptions}/{MAX_DERIV_STREAM_SUBSCRIPTIONS} slots'
                        f' · {len(extra_tick_symbols)} tick pairs + candles on extra connections',
                    )
                    self.primary_candle_symbols = list(self.candle_symbols)
                    extra_symbols = [symbol for symbol in self.symbols if symbol not in self.primary_candle_symbols]
                    self._ensure_extra_streams(extra_symbols, extra_tick_symbols)
                    extra_tasks = []
                    self.candle_symbols = self.primary_candle_symbols + extra_symbols
                    for symbol in extra_symbols:
                        await self.store.instrument(
                            'deriv', symbol, self._symbol_labels.get(symbol, symbol),
                            marketDataEligible=True, signalEligible=True,
                        )
                    if extra_symbols or extra_tick_symbols:
                        await self.store.event('INFO', 'Deriv', f'Fan-out · {len(extra_symbols)} candle pairs + {len(extra_tick_symbols)} extra tick pairs on {len(self._extra_streams)} persistent connection(s) · all {len(self.candle_symbols)} pairs analysed on 1m/5m')
                    warm = asyncio.create_task(self.warm())

                    async def keepalive():
                        # Application-level ping only: Deriv answers {"ping":1} with msg_type "ping";
                        # protocol pongs are unreliable when the event loop is busy (model training).
                        while True:
                            await asyncio.sleep(APP_PING_SECONDS)
                            self._app_ping_sent_at = time.monotonic()
                            await ws.send(json.dumps({'ping': 1}))
                            await self._finalize_closed_provider_candles()
                            silence = time.time() - (self.last_message_at or time.time())
                            if silence > HEARTBEAT_SILENCE_SECONDS:
                                raise TimeoutError(f'No Deriv message for {int(silence)}s (heartbeat silence)')

                    async def rediscover_loop():
                        # Deriv's own open/closed flag per pair every minute; newly opened/listed pairs join without a restart.
                        while True:
                            await asyncio.sleep(REDISCOVER_INTERVAL_SECONDS)
                            try:
                                new_symbols = await self._refresh_market_status()
                            except Exception as exc:  # noqa: BLE001
                                logger.warning('Deriv active_symbols refresh failed: %s', type(exc).__name__)
                                continue
                            if new_symbols:
                                self._discovered_at = 0.0
                                await self.store.event('INFO', 'Deriv', f'Markets opened / new pairs · {", ".join(new_symbols[:8])}{"…" if len(new_symbols) > 8 else ""} · resubscribing')
                                raise RuntimeError('DERIV_NEW_PAIRS_AVAILABLE')

                    async def clock_sync_loop():
                        while True:
                            try:
                                sent = time.time()
                                payload = await self.request({'time': 1})
                                server_clock.measure(float(payload['time']), sent, time.time())
                            except Exception as exc:  # noqa: BLE001
                                logger.debug('Deriv clock sync failed: %s', type(exc).__name__)
                            await asyncio.sleep(300)

                    extra_tasks.append(asyncio.create_task(clock_sync_loop()))
                    extra_tasks.append(asyncio.create_task(self._tick_consumer()))
                    ping = asyncio.create_task(keepalive())
                    rediscover = asyncio.create_task(rediscover_loop())
                    extra_tasks.append(rediscover)
                    always_open_symbols = sorted(
                        self.warmup_priority_symbols.intersection(self.symbols)
                    )
                    while True:
                        if ping.done():
                            ping.result()
                            raise RuntimeError('DERIV_KEEPALIVE_STOPPED')
                        if candle_subscription is not None and candle_subscription.done():
                            candle_subscription.result()
                            candle_subscription = None
                        try:
                            data = json.loads(await asyncio.wait_for(
                                ws.recv(), WEBSOCKET_RECV_POLL_SECONDS,
                            ))
                        except asyncio.TimeoutError:
                            reconnect_reason = tick_stream_reconnect_reason(
                                always_open_symbols,
                                self.last_tick_by_symbol,
                                time.time(),
                                time.monotonic() - connection_started_at,
                                TICK_STREAM_STALE_SECONDS,
                            )
                            if reconnect_reason:
                                raise TimeoutError(reconnect_reason)
                            continue
                        except (ConnectionClosed, InvalidStatus, InvalidHandshake):
                            raise
                        self.last_message_at = time.time()
                        if data.get('error') or data.get('errors'):
                            request = self.request_streams.get(data.get('req_id'))
                            symbol = (
                                (request[0] if request else None)
                                or self.request_symbols.get(data.get('req_id'))
                                or data.get('echo_req', {}).get('ticks')
                                or data.get('echo_req', {}).get('ticks_history')
                            )
                            stream_type = request[1] if request else 'tick'
                            timeframe = request[2] if request and len(request) > 2 else None
                            error = data.get('error') or (data.get('errors') or [{}])[0]
                            code = error.get('code', 'PROVIDER_REJECTED')
                            if symbol in self.symbols:
                                if stream_type == 'candle':
                                    self.candle_rejected[(symbol, timeframe)] = code
                                else:
                                    self.rejected[symbol] = code
                                    self.accepted.discard(symbol)
                                    await self.store.db.market_instruments.update_one({'source': 'deriv', 'symbol': symbol}, {'$set': {'subscriptionState': 'REJECTED', 'subscriptionReason': code}})
                                await self.store.event('WARN', 'Deriv', f'{symbol} {stream_type} subscription unavailable · {code}')
                            else:
                                self.last_error = code
                            continue
                        for candle in data.get('candles', []):
                            symbol = data.get('echo_req', {}).get('ticks_history')
                            if symbol not in self.symbols:
                                continue
                            granularity = data.get('echo_req', {}).get('granularity')
                            timeframe = next((
                                name for name in CANDLE_STREAM_TIMEFRAMES
                                if TIMEFRAMES.get(name) == granularity
                            ), None)
                            if timeframe is None:
                                continue
                            await self._record_provider_candle(
                                symbol,
                                timeframe,
                                candle,
                            )
                        ohlc = data.get('ohlc')
                        if ohlc and ohlc.get('symbol') in self.symbols:
                            request = self.request_streams.get(data.get('req_id'))
                            timeframe = (
                                request[2] if request and len(request) > 2
                                else next((
                                    name for name in CANDLE_STREAM_TIMEFRAMES
                                    if TIMEFRAMES.get(name) == data.get('echo_req', {}).get('granularity')
                                ), None)
                            )
                            if timeframe is not None:
                                await self._record_provider_candle(
                                    ohlc['symbol'], timeframe, ohlc,
                                )
                        tick = data.get('tick')
                        if tick and tick.get('symbol') in self.symbols:
                            # V16.3: receive fast, process latest-per-symbol in a consumer (no backlog / stale feed).
                            self._queue_tick(tick)
                        reconnect_reason = tick_stream_reconnect_reason(
                            always_open_symbols,
                            self.last_tick_by_symbol,
                            time.time(),
                            time.monotonic() - connection_started_at,
                            TICK_STREAM_STALE_SECONDS,
                        )
                        if reconnect_reason:
                            raise TimeoutError(reconnect_reason)
            except asyncio.CancelledError:
                for task in self._extra_streams.values():
                    task.cancel()
                self._extra_streams.clear()
                await self.channel.close()
                raise
            except Exception as exc:
                self.state = 'DISCONNECTED'
                cause = _disconnect_cause(exc)
                self.last_error = f'{type(exc).__name__}{cause}'
                connection_duration = (
                    time.monotonic() - connection_started_at
                    if connection_started_at is not None else None
                )
                retry_attempt = reconnect_attempt_after_disconnect(
                    retry_attempt, connection_duration,
                )
                delay = reconnect_delay_seconds(retry_attempt)
                retry_attempt += 1
                response = getattr(exc, 'response', None)
                status_code = getattr(response, 'status_code', None)
                logger.warning(
                    'Deriv disconnected: %s%s (HTTP %s); retrying in %ss',
                    type(exc).__name__, cause, status_code or 'n/a', delay,
                )
                await self._record_reconnect_warning(exc, delay)
            finally:
                self.accepted.clear()
                await self._cancel_background_tasks(warm, ping, candle_subscription, *extra_tasks)
            await asyncio.sleep(delay)

    def status(self):
        age = None if self.last_tick is None else time.time() - self.last_tick
        message_age = None if self.last_message_at is None else time.time() - self.last_message_at
        state = 'STALE' if self.state == 'DATA_RECEIVING' and age is not None and age > FRESHNESS else self.state
        extra_age = None if self.last_extra_message_at is None else time.time() - self.last_extra_message_at
        if state in {'CONNECTING', 'CONNECTED', 'DISCONNECTED', 'RECONNECTING'} and extra_age is not None and extra_age < FRESHNESS:
            state = 'DATA_RECEIVING'  # primary tick socket is reconnecting; persistent candle sockets keep data live
        candle_stream_status = {
            timeframe: {
                'acceptedCount': sum(1 for _symbol, stream_tf in self.candle_streams if stream_tf == timeframe),
                'acceptedSymbols': sorted(
                    symbol for symbol, stream_tf in self.candle_streams
                    if stream_tf == timeframe
                ),
                'rejectedCount': sum(
                    1 for _symbol, stream_tf in self.candle_rejected if stream_tf == timeframe
                ),
                'rejectedSymbols': [
                    {'symbol': symbol, 'code': code}
                    for (symbol, stream_tf), code in sorted(self.candle_rejected.items())
                    if stream_tf == timeframe
                ],
                'openCandles': sum(
                    1 for _symbol, stream_tf in self.open_candles if stream_tf == timeframe
                ),
            }
            for timeframe in CANDLE_STREAM_TIMEFRAMES
        }
        candle_rejected_symbols = [
            {'symbol': symbol, 'timeframe': timeframe, 'code': code}
            for (symbol, timeframe), code in sorted(self.candle_rejected.items())
        ]
        return dict(
            source='deriv', state=state, lastTick=self.last_tick, ageSeconds=age, clockOffsetSeconds=round(server_clock.offset(), 2), processingLagSeconds=self.processing_lag,
            symbolCount=len(self.accepted), requestedCount=len(self.symbols),
            signalSymbols=sorted(self.candle_symbols),
            signalSymbolCount=len(self.candle_symbols),
            acceptedCount=len(self.accepted), rejectedCount=len(self.rejected),
            acceptedSymbols=sorted(self.accepted),
            rejectedSymbols=[{'symbol': k, 'code': v} for k, v in sorted(self.rejected.items())],
            pendingSymbols=sorted(set(self.symbols) - self.accepted - self.rejected.keys()),
            candleStreams={
                'timeframes': candle_stream_status,
                'acceptedCount': len(self.candle_streams),
                'acceptedSymbols': sorted({symbol for symbol, _timeframe in self.candle_streams}),
                'rejectedCount': len(self.candle_rejected),
                'rejectedSymbols': candle_rejected_symbols,
                'openCandles': len(self.open_candles),
            },
            connectionLatencyMs=self.connection_latency_ms,
            heartbeatLatencyMs=self.heartbeat_latency_ms,
            lastMessageAgeSeconds=message_age,
            extraConnections=len(self._extra_streams),
            marketStatusAt=self.market_status_at,
            openMarkets=sum(1 for value in self.exchange_open.values() if value),
            closedMarkets=sum(1 for value in self.exchange_open.values() if not value),
            error=self.last_error,
        )