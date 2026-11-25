import asyncio
import time

import server_clock

_BACKGROUND = set()
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Query
from market_models import Source, AnalysisInput, Document, Items
from market_config import TIMEFRAMES, FRESHNESS


def _broker_available(instrument):
    available = instrument.get('available')
    if isinstance(available, str):
        available = available.strip().lower() not in {'0', 'false', 'no', 'closed', 'off'}
    return available is not False


def instrument_is_open(instrument, now=None):
    # The broker's own open flag is authoritative: Deriv `exchange_is_open` (refreshed every minute by
    # DerivService) or the Quotex observer flag. No hardcoded weekend rule – crypto/synthetics trade 24/7.
    if instrument.get('source') == 'market-qx-observer-v2':
        return _broker_available(instrument)
    exchange_open = instrument.get('exchangeIsOpen')
    if isinstance(exchange_open, bool):
        return exchange_open
    return _broker_available(instrument)


def active_instruments(instruments, now=None):
    now = now or datetime.now(timezone.utc)
    return [
        {**instrument, 'isOpen': True}
        for instrument in instruments
        if instrument_is_open(instrument, now)
    ]


def listed_instruments(instruments, now=None):
    now = now or datetime.now(timezone.utc)
    listed = []
    for instrument in instruments:
        is_open = instrument_is_open(instrument, now)
        if not is_open and not (
            instrument.get('source') == 'deriv'
            and instrument.get('marketDataEligible') is not False
        ):
            continue
        listed.append({**instrument, 'isOpen': is_open})
    return listed


def market_router(store, deriv, analysis, agent_pool=None):
    router = APIRouter(prefix='/api/v1')

    def agent_status():
        return agent_pool.status() if agent_pool is not None else {'workerSlots': 0, 'assignedStreams': 0}

    async def observer_status():
        status = await store.db.observer_status.find_one({'id': 'current'}, {'_id': 0})
        if not status:
            return {'source': 'market-qx-observer-v2', 'state': 'WAITING_FOR_EXTENSION'}
        age = time.time() - status['lastReceived']
        return {**status, 'source': 'market-qx-observer-v2', 'state': 'DATA_RECEIVING' if age < FRESHNESS else 'STALE', 'ageSeconds': age, 'freshnessSeconds': FRESHNESS, 'verification': status.get('verification', 'BROWSER_OBSERVED_UNVERIFIED')}

    def choose_source_selection(deriv_status, observer_status):
        deriv_state = (deriv_status or {}).get('state', 'DISCONNECTED')
        observer_state = (observer_status or {}).get('state', 'WAITING_FOR_EXTENSION')
        deriv_live = deriv_state in {'CONNECTED', 'DATA_RECEIVING'}
        observer_live = observer_state in {'DATA_RECEIVING'}
        if deriv_live and observer_live:
            return {'active': 'all', 'available': ['deriv', 'market-qx-observer-v2', 'all'], 'mode': 'combined', 'label': 'Auto · both live'}
        if deriv_live:
            return {'active': 'deriv', 'available': ['deriv', 'market-qx-observer-v2', 'all'], 'mode': 'deriv', 'label': 'Auto · Deriv'}
        if observer_live:
            return {'active': 'market-qx-observer-v2', 'available': ['deriv', 'market-qx-observer-v2', 'all'], 'mode': 'observer', 'label': 'Auto · QX observer'}
        if observer_state == 'STALE' and deriv_state != 'DISCONNECTED':
            return {'active': 'deriv', 'available': ['deriv', 'market-qx-observer-v2', 'all'], 'mode': 'deriv', 'label': 'Auto · fallback to Deriv'}
        if deriv_state == 'STALE' and observer_state != 'WAITING_FOR_EXTENSION':
            return {'active': 'market-qx-observer-v2', 'available': ['deriv', 'market-qx-observer-v2', 'all'], 'mode': 'observer', 'label': 'Auto · fallback to QX observer'}
        return {'active': 'all', 'available': ['deriv', 'market-qx-observer-v2', 'all'], 'mode': 'combined', 'label': 'Auto · combined (degraded)'}

    @router.get('/runtime', response_model=Document)
    async def runtime():
        await store.db.command('ping')
        workers = agent_status()
        deriv_status = deriv.status()
        observer_status_value = await observer_status()
        return {
            'providers': [deriv_status, observer_status_value],
            'database': 'CONNECTED',
            'ticksReceived': store.tick_count,
            'analysisCycles': analysis.cycles,
            'analysisError': analysis.error,
            'sourceSelection': choose_source_selection(deriv_status, observer_status_value),
            'agents': {'configuredSlots': workers['workerSlots'], 'workerSlots': workers['workerSlots'], 'assignedStreams': workers['assignedStreams'], 'independentAiModels': 0, 'type': 'PAIR_SHARDED_ASYNC_WORKERS'},
            'masterAgent': {'state': 'RANKING_QUALIFIED_SIGNALS', 'topPairLimit': 15, 'finalSelectionLimit': 3},
            'training': {'state': 'VERIFIED_DERIV_OUTCOMES_ONLY', 'accuracy': None},
            'executionEnabled': False,
        }

    @router.get('/instruments', response_model=Items)
    async def instruments(source: Source | None = None):
        query = {'source': source} if source else {}
        if source == 'deriv':
            query['marketDataEligible'] = {'$ne': False}
        elif source is None:
            query['$or'] = [
                {'source': {'$ne': 'deriv'}},
                {'marketDataEligible': {'$ne': False}},
            ]
        rows = await store.db.market_instruments.find(query, {'_id': 0}).limit(1000).to_list(1000)
        return {'items': listed_instruments(rows)}

    @router.get('/observation', response_model=Document)
    async def observation():
        return await observer_status()

    @router.get('/market/state', response_model=Document)
    async def state(source: Source, symbol: str = Query(min_length=1, max_length=64), timeframe: str = '1m'):
        if timeframe not in TIMEFRAMES:
            raise HTTPException(422, 'Unsupported timeframe')
        instrument = await store.resolve(source, symbol)
        if not instrument:
            return dict(source=source, symbol=symbol, timeframe=timeframe, state='UNAVAILABLE', candles=[], price=None, reason='PAIR_NOT_RECEIVED_FROM_THIS_SOURCE')
        history_error = None
        rows = await store.candles(source, instrument['symbol'], timeframe)
        if source == 'deriv':
            if len(rows) >= 120:
                # Already streaming: refresh history in the background so pair switches are instant.
                task = asyncio.create_task(deriv.history(instrument['symbol'], timeframe))
                _BACKGROUND.add(task)
                task.add_done_callback(_BACKGROUND.discard)
            else:
                try:
                    await asyncio.wait_for(deriv.history(instrument['symbol'], timeframe), 25)
                except Exception:
                    history_error = 'HISTORY_UNAVAILABLE'
                rows = await store.candles(source, instrument['symbol'], timeframe)
            instrument = await store.resolve(source, symbol) or instrument
        last = instrument.get('latestEpoch')
        fresh = last is not None and server_clock.now() - last <= FRESHNESS
        return dict(source=source, symbol=instrument['symbol'], timeframe=timeframe, state='LIVE' if fresh else 'STALE' if rows else 'WAITING', candles=rows, price=instrument.get('latestPrice'), timestamp=last, reason=history_error, provenance=instrument.get('provenance'))

    @router.post('/analysis', response_model=Document)
    async def analyze(body: AnalysisInput):
        if body.timeframe not in TIMEFRAMES:
            raise HTTPException(422, 'Unsupported timeframe')
        instrument = await store.resolve(body.source, body.symbol)
        if not instrument:
            return {'state': 'NO_DATA', 'signal': {'direction': 'NO_SIGNAL', 'reason': 'PAIR_NOT_RECEIVED_FROM_THIS_SOURCE', 'executionOrder': False}}
        if body.source == 'market-qx-observer-v2' and instrument.get('verificationStatus') != 'CROSS_VALIDATED':
            return {'state': 'OBSERVATION_ONLY', 'source': body.source, 'symbol': instrument['symbol'], 'timeframe': body.timeframe, 'signal': {'direction': 'NO_SIGNAL', 'reason': 'QX_REAL_MARKET_REQUIRES_DERIV_CROSS_VALIDATION', 'executionOrder': False}}
        try:
            return await analysis.analyze(body.source, instrument['symbol'], body.timeframe)
        except Exception as exc:
            raise HTTPException(503, 'Analysis temporarily unavailable') from exc

    @router.get('/analysis/latest', response_model=Document)
    async def latest(source: Source, symbol: str, timeframe: str = '1m'):
        instrument = await store.resolve(source, symbol)
        result = await store.db.market_analyses.find_one({'source': source, 'symbol': instrument['symbol'] if instrument else symbol, 'timeframe': timeframe}, {'_id': 0})
        return result or {'state': 'NO_DATA', 'signal': {'direction': 'NO_SIGNAL', 'reason': 'WAITING_FOR_ANALYSIS'}}

    @router.get('/top-pairs', response_model=Items)
    async def top_pairs():
        signal_symbols = getattr(deriv, 'candle_symbols', None)
        query = {
            'timeframe': '1m',
            'latestEpoch': {'$gte': time.time() - FRESHNESS - 60},
            'quality.status': 'VALID',
            'candleCount': {'$gte': 60},
        }
        if signal_symbols is not None:
            query['$or'] = [
                {'source': {'$ne': 'deriv'}},
                {'source': 'deriv', 'symbol': {'$in': list(signal_symbols)}},
            ]
        rows = await store.db.market_analyses.find(query, {'_id': 0}).sort([('quality.score', -1), ('candleCount', -1)]).limit(15).to_list(15)
        return {'items': [{'source': r['source'], 'symbol': r['symbol'], 'quality': r['quality']['score'], 'candleCount': r['candleCount'], 'signal': 'NO_SIGNAL', 'qualification': 'DATA_QUALITY_ONLY_NOT_WIN_PROBABILITY'} for r in rows]}

    @router.get('/history', response_model=Items)
    async def history(limit: int = Query(50, ge=1, le=200)):
        return {'items': await store.db.signal_history.find({}, {'_id': 0}).sort('createdAt', -1).limit(limit).to_list(limit)}

    @router.get('/events', response_model=Items)
    async def events():
        return {'items': await store.db.runtime_events.find({}, {'_id': 0}).sort('time', -1).limit(50).to_list(50)}

    @router.get('/agents', response_model=Document)
    async def agents():
        return {**agent_status(), 'status': 'PAIR_SHARDED_WORKER_POOL', 'independentAiModels': 0, 'items': []}

    @router.get('/modules', response_model=Items)
    async def modules():
        ctx = getattr(store, 'flow_context', {}) or {}
        registry, runner, deep, signals, autotrade = (ctx.get(k) for k in ('registry', 'runner', 'deep', 'signals', 'autotrade'))
        agents = len(registry.agents) if registry else 0
        runner_status = runner.status() if runner else {}
        trained = sum(1 for model in runner_status.get('models', {}).values() if model.get('trained'))
        total = len(runner.models) if runner else 0
        deep_ready = len(getattr(deep, 'models', {}) or {}) if deep else 0
        engine = signals.status() if signals else {}
        return {'items': [
            {'name': f'Agent Tracking Pool ({agents}/500)', 'state': 'LIVE_PAIR_AGENTS' if agents else 'WAITING_FOR_FIRST_CYCLE'},
            {'name': 'Deriv Feed', 'state': getattr(deriv, 'state', 'DISABLED') if deriv else 'DISABLED', 'engines': [f'{len(getattr(deriv, "accepted", []) or [])} pairs streaming', '1m / 5m / 15m candle streams']},
            {'name': 'Pipeline', 'state': 'CONNECTED', 'engines': ['Pattern Detection Engine', 'Chart Analysis Engine', '50+ Indicator Engine', 'Quality Definition Engine', 'Candle Type Tracking Engine', 'Quality Pipeline Engine']},
            {'name': 'Market Algorithm Detection Layer', 'state': 'ACTIVE_FAIL_CLOSED', 'engines': ['Hurst regime', 'Autocorrelation', 'Colour n-gram', 'Streak exhaustion', 'Wick rejection', 'Range z-score', 'FFT cycle phase']},
            {'name': 'Behaviour Finder', 'state': 'RULE_BASED_CANDIDATES'},
            {'name': 'OTC / Binary Algorithm Pattern Analysis', 'state': 'ALGORITHM_LAYER_ON_OBSERVED_CANDLES'},
            {'name': 'Pair Detection', 'state': 'TOP_15_DATA_QUALITY_RANKING'},
            {'name': 'Breakout & Gap Up/Down Detection', 'state': 'OBSERVED_CANDIDATES'},
            {'name': f'ML / DL / DS Ensemble ({trained}/{total} adapters · {deep_ready} deep)', 'state': 'TRAINED' if trained else 'AUTO_TRAINING', 'engines': list(runner.models) if runner else []},
            {'name': 'Master Agent', 'state': 'TOP_15_TO_TOP_3_QUALIFIED_SELECTION'},
            {'name': 'Self-Training System', 'state': 'AUTO_STARTUP_AND_DAILY_RETRAIN'},
            {'name': 'Signal Generation 1m / 5m', 'state': 'RUNNING' if engine.get('enabled') else 'OFF', 'engines': [f"{engine.get('cycles', 0)} cycles"]},
            {'name': 'Auto Trade (system signals + safety margin)', 'state': ('RUNNING_' + autotrade.mode.upper()) if autotrade and autotrade.running else 'IDLE'},
        ]}
    return router