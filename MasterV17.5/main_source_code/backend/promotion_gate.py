"""V16.8 accuracy proof: per-pair walk-forward backtest on unseen candles, payout-aware promotion gate.

A pair/timeframe is PROMOTED only when its out-of-sample win rate is >= PROMOTION_MIN_WIN_RATE over at
least PROMOTION_MIN_SAMPLES trades AND the 95% Wilson lower bound beats the payout break-even rate.
Everything else stays NOT_PROMOTED: its live signal is hidden (shadow-settled) and auto trade skips it.
"""
import asyncio
import logging
import os
import re
import time
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool

from fastapi import APIRouter, HTTPException

import algorithm_detector
from market_config import TIMEFRAMES
from pairs_config import demo_payout_percent

logger = logging.getLogger(__name__)

PROMOTION_MIN_WIN_RATE = float(os.environ.get('PROMOTION_MIN_WIN_RATE', '58'))
PROMOTION_MIN_SAMPLES = int(os.environ.get('PROMOTION_MIN_SAMPLES', '300'))
PROMOTION_TIMEFRAMES = [tf.strip() for tf in os.environ.get('SIGNAL_TIMEFRAMES', '1m,5m').split(',') if tf.strip() in TIMEFRAMES]
PROMOTION_INTERVAL_SECONDS = max(1800, int(os.environ.get('PROMOTION_INTERVAL_SECONDS', str(6 * 3600))))
WALK_FORWARD_CANDLES = 12000
WALK_FORWARD_POINTS = 12000

PROMOTED = set()  # (symbol, timeframe) with a proven, payout-positive out-of-sample edge


def is_promoted(symbol, timeframe):
    return (symbol, timeframe) in PROMOTED


def promoted_symbols():
    return sorted({symbol for symbol, _tf in PROMOTED})


ASYMMETRIC_SYNTHETIC = re.compile(r'^(BOOM|CRASH)\d+', re.IGNORECASE)
ASYMMETRIC_PAYOUT_PERCENT = float(os.environ.get('PROMOTION_ASYMMETRIC_PAYOUT_PERCENT', '10'))


def payout_estimate(symbol, market):
    """Conservative payout used for the break-even rate. Boom/Crash pay very little on their frequent
    direction, so the default market payout would overstate the edge; auto trade re-checks the real
    Deriv proposal payout before every order."""
    if ASYMMETRIC_SYNTHETIC.match(str(symbol or '')):
        return ASYMMETRIC_PAYOUT_PERCENT, 'ASYMMETRIC_ESTIMATE'
    return float(demo_payout_percent(market)), 'MARKET_DEFAULT'


def break_even_rate(payout_percent):
    payout = max(1.0, float(payout_percent)) / 100.0
    return round(100.0 / (1.0 + payout), 2)


def walk_forward_backtest(candles, payout_percent):
    """Purged/embargoed indicator-feature walk-forward in a worker process."""
    from lab.binary_lab import binary_walk_forward

    profile = binary_walk_forward(candles)
    samples = int(profile.get('oosSamples') or 0)
    hit_rate = profile.get('oosHitRate')
    win_rate = None if hit_rate is None else round(hit_rate * 100.0, 2)
    lower = round(float(profile.get('oosLowerBound') or 0.0) * 100.0, 2)
    breakeven = break_even_rate(payout_percent)
    enough = samples >= PROMOTION_MIN_SAMPLES
    promoted = bool(enough and win_rate is not None and win_rate >= PROMOTION_MIN_WIN_RATE and lower > breakeven)
    if not enough:
        reason = f'INSUFFICIENT_SAMPLES ({samples}/{PROMOTION_MIN_SAMPLES})'
    elif win_rate is None or win_rate < PROMOTION_MIN_WIN_RATE:
        reason = f'WIN_RATE_BELOW_{PROMOTION_MIN_WIN_RATE:g}'
    elif lower <= breakeven:
        reason = 'LOWER_BOUND_BELOW_BREAKEVEN'
    else:
        reason = 'PROMOTED'
    expectancy = None if win_rate is None else round((win_rate / 100.0) * (payout_percent / 100.0) - (1 - win_rate / 100.0), 4)
    return {
        'winRate': win_rate, 'samples': samples, 'wilsonLowerBound': lower, 'breakEvenRate': breakeven,
        'payoutPercent': float(payout_percent), 'expectancyPerUnit': expectancy, 'promoted': promoted, 'reason': reason,
        'trainPoints': profile.get('trainPoints'), 'validatedDetectors': profile.get('validated'), 'candles': profile.get('candles'),
    }


class PromotionGate:
    def __init__(self, db, store, deriv=None):
        self.db = db
        self.store = store
        self.deriv = deriv
        self.task = None
        self.progress = {'running': False, 'total': 0, 'done': 0, 'current': None, 'startedAt': None, 'finishedAt': None}
        self.executor = None

    async def initialize(self):
        await self.db.pair_promotions.create_index([('symbol', 1), ('timeframe', 1)], unique=True)
        await self.db.live_signals.create_index([('source', 1), ('symbol', 1), ('timeframe', 1), ('status', 1), ('expiryEpoch', -1)])
        await self.db.live_signals.create_index([('status', 1), ('generatedAt', -1)])
        await self.db.autotrade_trades.create_index([('mode', 1), ('openedAt', -1)])
        await self.db.autotrade_trades.create_index('requestId', unique=True, sparse=True)
        rows = await self.db.pair_promotions.find({'promoted': True}, {'_id': 0, 'symbol': 1, 'timeframe': 1}).to_list(5000)
        PROMOTED.clear()
        PROMOTED.update((row['symbol'], row['timeframe']) for row in rows)

    @property
    def running(self):
        return self.task is not None and not self.task.done()

    def start(self, symbols=None):
        if self.running:
            raise RuntimeError('PROMOTION_RUN_ALREADY_RUNNING')
        self.task = asyncio.create_task(self.run(symbols))
        return self.status()

    def status(self):
        return {**self.progress, 'minWinRate': PROMOTION_MIN_WIN_RATE, 'minSamples': PROMOTION_MIN_SAMPLES, 'timeframes': PROMOTION_TIMEFRAMES, 'promotedCount': len(PROMOTED)}

    async def _markets(self, symbols=None):
        if symbols:
            return [(s, tf) for s in symbols for tf in PROMOTION_TIMEFRAMES]
        rows = await self.db.market_instruments.find({'source': 'deriv', 'signalEligible': True}, {'_id': 0, 'symbol': 1}).to_list(2000)
        return [(row['symbol'], tf) for row in rows for tf in PROMOTION_TIMEFRAMES]

    async def evaluate(self, symbol, timeframe):
        instrument = await self.db.market_instruments.find_one({'source': 'deriv', 'symbol': symbol}, {'_id': 0, 'market': 1}) or {}
        payout, payout_source = payout_estimate(symbol, instrument.get('market'))
        seconds = TIMEFRAMES[timeframe]
        rows = await self.store.candles('deriv', symbol, timeframe, WALK_FORWARD_CANDLES)
        now = time.time()
        candles = [{k: c[k] for k in ('epoch', 'open', 'high', 'low', 'close')} for c in rows if c['epoch'] + seconds <= now]
        if len(candles) < 400:
            result = {'winRate': None, 'samples': 0, 'wilsonLowerBound': 0.0, 'breakEvenRate': break_even_rate(payout), 'payoutPercent': float(payout),
                      'expectancyPerUnit': None, 'promoted': False, 'reason': f'INSUFFICIENT_HISTORY ({len(candles)} candles)', 'candles': len(candles)}
        else:
            if self.executor is None:
                self.executor = ProcessPoolExecutor(max_workers=1)
            try:
                result = await asyncio.get_running_loop().run_in_executor(self.executor, walk_forward_backtest, candles, payout)
            except BrokenProcessPool:
                self.executor = ProcessPoolExecutor(max_workers=1)
                result = await asyncio.get_running_loop().run_in_executor(self.executor, walk_forward_backtest, candles, payout)
        previous = await self.db.pair_promotions.find_one({'symbol': symbol, 'timeframe': timeframe}, {'_id': 0, 'promoted': 1})
        document = {**result, 'symbol': symbol, 'timeframe': timeframe, 'lastTrainedAt': now, 'payoutSource': payout_source, 'minWinRate': PROMOTION_MIN_WIN_RATE, 'minSamples': PROMOTION_MIN_SAMPLES}
        await self.db.pair_promotions.update_one({'symbol': symbol, 'timeframe': timeframe}, {'$set': document}, upsert=True)
        key = (symbol, timeframe)
        if result['promoted']:
            PROMOTED.add(key)
        else:
            PROMOTED.discard(key)
        if previous is not None and bool(previous.get('promoted')) != result['promoted']:
            await self.store.event('INFO' if result['promoted'] else 'WARN', 'Promotion gate', f"{symbol} {timeframe} {'PROMOTED' if result['promoted'] else 'DEMOTED → signal off'} · {result['winRate']}% over {result['samples']} (break-even {result['breakEvenRate']}%)")
        return document

    async def run(self, symbols=None):
        markets = await self._markets(symbols)
        self.progress.update({'running': True, 'total': len(markets), 'done': 0, 'startedAt': time.time(), 'finishedAt': None})
        try:
            for symbol, timeframe in markets:
                self.progress['current'] = {'symbol': symbol, 'timeframe': timeframe}
                try:
                    await self.evaluate(symbol, timeframe)
                except Exception:  # noqa: BLE001 - one bad pair must not stop the report
                    logger.exception('Walk-forward failed for %s %s', symbol, timeframe)
                self.progress['done'] += 1
                await asyncio.sleep(0.05)
        finally:
            self.progress.update({'running': False, 'current': None, 'finishedAt': time.time()})
            await self.store.event('INFO', 'Promotion gate', f'Walk-forward report · {len(PROMOTED)} promoted of {len(markets)} pair/timeframes')

    async def schedule(self):
        await asyncio.sleep(max(60, int(os.environ.get('PROMOTION_STARTUP_DELAY_SECONDS', '240'))))
        while True:
            if not self.running:
                try:
                    await self.run()
                except Exception:  # noqa: BLE001
                    logger.exception('Scheduled promotion run failed')
            await asyncio.sleep(PROMOTION_INTERVAL_SECONDS)

    async def report(self):
        rows = await self.db.pair_promotions.find({}, {'_id': 0}).sort([('promoted', -1), ('winRate', -1)]).to_list(5000)
        labels = {row['symbol']: row.get('label') for row in await self.db.market_instruments.find({'source': 'deriv'}, {'_id': 0, 'symbol': 1, 'label': 1}).to_list(2000)}
        for row in rows:
            row['label'] = labels.get(row['symbol']) or row['symbol']
        status = self.status()
        if not status.get('finishedAt') and rows:
            status['finishedAt'] = max(float(r.get('lastTrainedAt') or 0) for r in rows) or None
        return {**status, 'items': rows, 'promotedCount': sum(1 for r in rows if r.get('promoted')), 'total': len(rows)}

    async def stop(self):
        if self.executor is not None:
            self.executor.shutdown(wait=False, cancel_futures=True)
        if self.running:
            self.task.cancel()
            try:
                await asyncio.wait_for(asyncio.gather(self.task, return_exceptions=True), 5)
            except asyncio.TimeoutError:
                pass


def promotion_router(gate: PromotionGate):
    router = APIRouter(prefix='/api/v1/promotion', tags=['Promotion gate'])

    @router.get('/report')
    async def report():
        return await gate.report()

    @router.post('/run')
    async def run(payload: dict | None = None):
        try:
            return gate.start((payload or {}).get('symbols'))
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get('/status')
    async def status():
        return gate.status()

    return router
