"""Fetch-history → train-all pipeline for every tracked Deriv pair, with live progress and a daily schedule."""
import asyncio
import logging
import os
import time

from fastapi import APIRouter, Body, HTTPException

from market_config import DERIV_CANDLE_SYMBOLS, DERIV_SYMBOLS
from pairs_config import REAL_MARKET_PAIRS, is_real_deriv_forex

logger = logging.getLogger(__name__)

DAILY_RETRAIN_SECONDS = max(3600, int(os.environ.get('TRAIN_ALL_INTERVAL_SECONDS', str(24 * 3600))))
BACKFILL_CANDLES = max(500, int(os.environ.get('TRAIN_BACKFILL_CANDLES', '12000')))
PARALLEL_MARKETS = max(1, int(os.environ.get('TRAIN_PARALLEL_MARKETS', '2')))
MAX_FOREX_TRAINING_PAIRS = max(1, min(100, int(os.environ.get('TRAIN_MAX_FOREX_PAIRS', '24'))))
# V17.3: incremental retrain of the markets that produced settled signals (keeps the models fresh between full runs)
INCREMENTAL_SECONDS = max(300, int(os.environ.get('TRAIN_INCREMENTAL_INTERVAL_SECONDS', '1800')))
INCREMENTAL_MIN_OUTCOMES = max(1, int(os.environ.get('TRAIN_INCREMENTAL_MIN_OUTCOMES', '3')))


class TrainingOrchestrator:
    def __init__(self, db, deriv, signals, deep_models, model_runner, timeframes=('1m', '5m')):
        self.db = db
        self.deriv = deriv
        self.signals = signals
        self.deep_models = deep_models
        self.model_runner = model_runner
        self.feature_store = None
        self.timeframes = tuple(timeframes)
        self.task = None
        self.schedule_task = None
        self.progress = {'running': False, 'phase': 'IDLE', 'total': 0, 'done': 0, 'current': None, 'startedAt': None, 'finishedAt': None, 'results': [], 'errors': 0, 'lastRun': None}
        self.history = []
        self.incremental = {'lastRunAt': None, 'markets': [], 'runs': 0, 'nextCheckAt': None}
        self.incremental_task = None

    async def incremental_schedule(self):
        """V17.3: every INCREMENTAL_SECONDS retrain only the markets whose signals settled since the last pass (WIN/LOSS feedback -> fresh models)."""
        while True:
            self.incremental['nextCheckAt'] = time.time() + INCREMENTAL_SECONDS
            await asyncio.sleep(INCREMENTAL_SECONDS)
            if self.task is not None and not self.task.done():
                continue
            since = self.incremental['lastRunAt'] or (time.time() - INCREMENTAL_SECONDS)
            try:
                rows = await self.db.live_signals.aggregate([
                        {'$match': {
                            'source': 'deriv', 'symbol': {'$regex': '^frx[A-Z]{6}$', '$options': 'i'},
                            'timeframe': {'$in': list(self.timeframes)},
                            'status': {'$in': ['WIN', 'LOSS']}, 'generatedAt': {'$gte': since},
                        }},
                    {'$group': {'_id': {'symbol': '$symbol', 'timeframe': '$timeframe'}, 'n': {'$sum': 1}}},
                    {'$match': {'n': {'$gte': INCREMENTAL_MIN_OUTCOMES}}},
                    {'$sort': {'n': -1}}, {'$limit': 12},
                ]).to_list(12)
            except Exception:  # noqa: BLE001
                logger.exception('Incremental retrain: outcome lookup failed')
                continue
            markets = [(r['_id']['symbol'], r['_id']['timeframe']) for r in rows if r['_id'].get('timeframe') in self.timeframes]
            self.incremental['lastRunAt'] = time.time()
            if not markets:
                continue
            self.incremental.update(markets=[f'{s}/{tf}' for s, tf in markets], runs=self.incremental['runs'] + 1)
            try:
                self.start('INCREMENTAL_RETRAIN', backfill=False, train=True, markets=markets)
                logger.info('Incremental retrain started for %d market(s)', len(markets))
            except RuntimeError as exc:
                logger.info('Incremental retrain skipped: %s', exc)

    def _markets(self, timeframes=None):
        market_by_symbol = getattr(self.deriv, '_symbol_markets', {})
        candle_symbols = list(getattr(self.deriv, 'candle_symbols', None) or [])
        symbols = [
            symbol for symbol in candle_symbols
            if is_real_deriv_forex({
                'source': 'deriv', 'symbol': symbol,
                'market': market_by_symbol.get(symbol),
                'marketDataEligible': True,
            })
        ]
        if not symbols:
            eligible = [
                symbol for symbol, market in market_by_symbol.items()
                if is_real_deriv_forex({
                    'source': 'deriv', 'symbol': symbol, 'market': market,
                    'marketDataEligible': True,
                })
            ]
            if DERIV_CANDLE_SYMBOLS != ['ALL']:
                configured = set(DERIV_CANDLE_SYMBOLS)
                eligible = [symbol for symbol in eligible if symbol in configured]
            if not eligible:
                configured_pairs = set() if DERIV_SYMBOLS == ['ALL'] else set(DERIV_SYMBOLS)
                configured_candles = set() if DERIV_CANDLE_SYMBOLS == ['ALL'] else set(DERIV_CANDLE_SYMBOLS)
                eligible = [
                    pair['deriv'] for pair in REAL_MARKET_PAIRS.values()
                    if pair['category'] in {'forex_major', 'forex_minor'}
                    and (not configured_pairs or pair['deriv'] in configured_pairs)
                    and (not configured_candles or pair['deriv'] in configured_candles)
                ]
            else:
                eligible.sort()
            symbols = eligible[:MAX_FOREX_TRAINING_PAIRS]
        allowed_timeframes = [tf for tf in (timeframes or self.timeframes) if tf in self.timeframes]
        return [(symbol, tf) for symbol in symbols for tf in allowed_timeframes]

    def status(self):
        eligible_markets = len({symbol for symbol, _ in self._markets()})
        return {**self.progress, 'eligibleMarkets': eligible_markets, 'results': self.progress['results'][-300:], 'timeframes': list(self.timeframes), 'dailyRetrainSeconds': DAILY_RETRAIN_SECONDS,
                'parallelMarkets': PARALLEL_MARKETS, 'backfillCandles': BACKFILL_CANDLES, 'incrementalSeconds': INCREMENTAL_SECONDS, 'incremental': self.incremental, 'history': self.history[-10:]}

    def start(self, phase, timeframes=None, backfill=True, train=True, markets=None):
        if self.task is not None and not self.task.done():
            raise RuntimeError('TRAINING_PIPELINE_ALREADY_RUNNING')
        if markets is None:
            markets = self._markets(timeframes)
        else:
            eligible = {symbol for symbol, _ in self._markets(timeframes)}
            markets = [
                (symbol, timeframe) for symbol, timeframe in markets
                if symbol in eligible and timeframe in self.timeframes
            ]
        if not markets:
            raise RuntimeError('NO_DERIV_PAIRS_DISCOVERED_YET')
        self.progress.update({'running': True, 'phase': phase, 'total': len(markets), 'done': 0, 'current': None, 'startedAt': time.time(), 'finishedAt': None, 'results': [], 'errors': 0})
        self.task = asyncio.create_task(self._run(markets, backfill, train))
        return self.status()

    async def _train_one(self, symbol, timeframe, backfill, train):
        self.progress['current'] = {'symbol': symbol, 'timeframe': timeframe, 'startedAt': time.time()}
        row = {'symbol': symbol, 'timeframe': timeframe, 'at': time.time()}
        try:
            if backfill:
                await self.deriv.history(symbol, timeframe, count=BACKFILL_CANDLES, force=True)
                row['candles'] = await self.db.market_candles.count_documents({'source': 'deriv', 'symbol': symbol, 'timeframe': timeframe, 'completeness': 'PROVIDER_OHLC'})
                row['status'] = 'HISTORY_OK'
                if self.feature_store is not None:
                    row['features'] = await self.feature_store.refresh_market('deriv', symbol, timeframe)
            if train:
                started = time.time()
                report = await self.signals._train_market_models('deriv', symbol, timeframe)
                test = report.get('test') or {}
                row.update({
                    'status': report.get('status'), 'reason': report.get('reason'), 'tier': report.get('promotionTier'),
                    'candles': report.get('closedProviderCandles'), 'accuracy': test.get('accuracy'), 'winRate': test.get('winRate'),
                    'brier': test.get('brier'), 'samples': report.get('sampleCounts'), 'legacy': (report.get('legacyAdapters') or {}).get('status'),
                    'seconds': round(time.time() - started, 1),
                })
        except Exception as exc:  # noqa: BLE001 - one bad pair must not stop the pipeline
            logger.exception('Training pipeline failed for %s/%s', symbol, timeframe)
            row.update({'status': 'FAILED', 'reason': getattr(exc, 'code', type(exc).__name__)})
            self.progress['errors'] += 1
        self.progress['results'].append(row)
        self.progress['done'] += 1

    async def _run(self, markets, backfill, train):
        semaphore = asyncio.Semaphore(PARALLEL_MARKETS)

        async def guarded(symbol, timeframe):
            async with semaphore:
                await self._train_one(symbol, timeframe, backfill, train)
                await asyncio.sleep(0.1)

        try:
            await asyncio.gather(*(guarded(symbol, timeframe) for symbol, timeframe in markets))
        except asyncio.CancelledError:
            self.progress['phase'] = 'CANCELLED'
            raise
        finally:
            self.progress.update({'running': False, 'current': None, 'finishedAt': time.time()})
            if self.progress['phase'] not in {'CANCELLED'}:
                self.progress['phase'] = 'DONE'
            summary = {'phase': self.progress['phase'], 'total': self.progress['total'], 'done': self.progress['done'], 'errors': self.progress['errors'], 'startedAt': self.progress['startedAt'], 'finishedAt': self.progress['finishedAt'], 'trained': sum(1 for r in self.progress['results'] if r.get('legacy') == 'TRAINED' or r.get('tier'))}
            self.progress['lastRun'] = summary
            self.history.append(summary)
            await self.db.training_pipeline_runs.insert_one(dict(summary))
            gate = getattr(self.signals, 'promotion_gate', None)
            if gate is not None and not gate.running and self.progress['phase'] == 'DONE':
                try:
                    gate.start()  # walk-forward validation + promotion right after Train All
                except RuntimeError:
                    pass

    async def stop(self):
        if self.task is not None and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        return self.status()

    async def daily_schedule(self):
        # V16.3: training is automatic - first Train-All starts shortly after boot (no manual click needed).
        if os.environ.get('TRAIN_ALL_ON_STARTUP', 'true').strip().lower() == 'true':
            await asyncio.sleep(max(30, int(os.environ.get('TRAIN_ALL_STARTUP_DELAY_SECONDS', '30'))))
            # V17.2: only a completed run counts - a run cancelled by a restart must not block auto-training for 24 h.
            last = await self.db.training_pipeline_runs.find_one({'phase': 'DONE'}, sort=[('finishedAt', -1)])
            fresh = last and (time.time() - float(last.get('finishedAt') or 0)) < DAILY_RETRAIN_SECONDS
            if not fresh:
                for _ in range(20):
                    try:
                        self.start('STARTUP_TRAIN_ALL', backfill=True, train=True)
                        break
                    except RuntimeError as exc:
                        logger.info('Startup train-all waiting: %s', exc)
                        await asyncio.sleep(30)
        while True:
            await asyncio.sleep(DAILY_RETRAIN_SECONDS)
            if self.task is not None and not self.task.done():
                continue
            try:
                self.start('SCHEDULED_TRAIN_ALL', backfill=True, train=True)
            except RuntimeError as exc:
                logger.warning('Scheduled train-all skipped: %s', exc)

    async def model_history(self, limit=60):
        rows = await self.db.deep_model_training_runs.aggregate([
            {'$sort': {'generatedAt': -1}},
            {'$group': {'_id': {'symbol': '$symbol', 'timeframe': '$timeframe'}, 'doc': {'$first': '$$ROOT'}}},
            {'$replaceRoot': {'newRoot': '$doc'}},
            {'$project': {'_id': 0, 'symbol': 1, 'timeframe': 1, 'status': 1, 'promotionTier': 1, 'generatedAt': 1, 'closedProviderCandles': 1, 'modelVersion': 1, 'test.accuracy': 1, 'test.winRate': 1, 'test.brier': 1, 'sampleCounts': 1, 'legacyAdapters.status': 1}},
            {'$sort': {'generatedAt': -1}},
            {'$limit': max(1, min(500, int(limit)))},
        ]).to_list(500)
        return rows


def training_pipeline_router(orchestrator: TrainingOrchestrator):
    router = APIRouter(prefix='/api/v1/ml', tags=['training-pipeline'])

    @router.get('/pipeline')
    async def pipeline_status():
        return orchestrator.status()

    @router.post('/backfill-all')
    async def backfill_all(payload: dict = Body(default_factory=dict)):
        try:
            return orchestrator.start('FETCH_HISTORY', timeframes=payload.get('timeframes'), backfill=True, train=False)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post('/train-all')
    async def train_all(payload: dict = Body(default_factory=dict)):
        try:
            return orchestrator.start('TRAIN_ALL', timeframes=payload.get('timeframes'), backfill=payload.get('backfill', True), train=True)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post('/retrain-active')
    async def retrain_active(payload: dict = Body(default_factory=dict)):
        # markets that produced signals in the last N hours - prioritised heavy retrain
        hours = max(1, min(168, int(payload.get('hours', 24))))
        rows = await orchestrator.db.live_signals.aggregate([
            {'$match': {'source': 'deriv', 'generatedAt': {'$gte': time.time() - hours * 3600}}},
            {'$group': {'_id': {'symbol': '$symbol', 'timeframe': '$timeframe'}, 'n': {'$sum': 1}}}, {'$sort': {'n': -1}}, {'$limit': 40},
        ]).to_list(40)
        markets = [(r['_id']['symbol'], r['_id']['timeframe']) for r in rows if r['_id'].get('timeframe') in orchestrator.timeframes]
        if not markets:
            raise HTTPException(409, 'NO_ACTIVE_MARKETS_WITH_SIGNALS')
        try:
            return orchestrator.start('RETRAIN_ACTIVE', backfill=payload.get('backfill', True), train=True, markets=markets)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post('/pipeline/stop')
    async def stop_pipeline():
        return await orchestrator.stop()

    @router.get('/model-history')
    async def model_history(limit: int = 60):
        return {'items': await orchestrator.model_history(limit)}

    return router
