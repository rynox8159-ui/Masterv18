import asyncio
import json
import math
import statistics
import time
from pathlib import Path
from contextlib import suppress
import pandas as pd
from ta import add_momentum_ta, add_trend_ta, add_volatility_ta
from market_config import TIMEFRAMES, FRESHNESS, ANALYSIS_INTERVAL
from ds.market_behaviour_finder import MarketBehaviourFinder


def indicators(candles):
    if len(candles) < 60:
        return {'state': 'WARMING_UP', 'count': 0, 'values': {}, 'requiredCandles': 60}
    frame = pd.DataFrame(candles)[['open', 'high', 'low', 'close']].astype(float)
    for function in [add_trend_ta, add_volatility_ta]:
        frame = function(frame, high='high', low='low', close='close', fillna=False)
    # Unknown volume remains NaN; never manufacture zero-volume market evidence.
    frame['volume'] = float('nan')
    frame = add_momentum_ta(frame, high='high', low='low', close='close', volume='volume', fillna=False)
    values = {k: float(v) for k, v in frame.iloc[-1].items() if k not in ['open', 'high', 'low', 'close'] and math.isfinite(float(v))}
    return {'state': 'COMPUTED', 'count': len(values), 'values': values, 'library': 'ta-0.11.0', 'volumeIndicators': 'UNAVAILABLE_WITHOUT_REAL_VOLUME'}


class AnalysisService:
    MEMORY_WINDOW = 50
    MEMORY_HISTORY_LIMIT = 10

    def __init__(self, store):
        self.store = store
        self.worker = None
        self.lock = asyncio.Lock()
        self.cycles = 0
        self.error = None

    async def stop_worker(self):
        if self.worker and self.worker.returncode is None:
            self.worker.terminate()
            await self.worker.wait()
        self.worker = None

    async def engine(self, request):
        async with self.lock:
            if not self.worker or self.worker.returncode is not None:
                self.worker = await asyncio.create_subprocess_exec('node', str(Path(__file__).parent / 'analysis_worker.mjs'), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, limit=2**21)
            try:
                self.worker.stdin.write((json.dumps(request) + '\n').encode())
                await self.worker.stdin.drain()
                response = await asyncio.wait_for(self.worker.stdout.readline(), 10)
                result = json.loads(response)
                if result.get('error'):
                    raise ValueError(result['error'])
                return result
            except BaseException:
                await self.stop_worker()
                raise

    async def analyze(self, source, symbol, timeframe):
        candles = [c for c in await self.store.candles(source, symbol, timeframe) if c['epoch'] + TIMEFRAMES[timeframe] <= time.time()]
        if not candles:
            return {'source': source, 'symbol': symbol, 'timeframe': timeframe, 'state': 'NO_DATA', 'signal': {'direction': 'NO_SIGNAL', 'reason': 'NO_CLOSED_CANDLES', 'executionOrder': False}}
        result = await self.engine(dict(candles=candles, source=source, symbol=symbol, timeframe=timeframe, seconds=TIMEFRAMES[timeframe], freshness=max(FRESHNESS, TIMEFRAMES[timeframe] * 2)))
        result['indicators'] = await asyncio.to_thread(indicators, candles)
        latest = candles[-1]
        previous = candles[-2] if len(candles) > 1 else None
        gap = latest['open'] - previous['close'] if previous else None
        recent_ranges = [
            max(candle['high'] - candle['low'], abs(candle['high'] - prior['close']), abs(candle['low'] - prior['close']))
            for prior, candle in zip(candles[-21:-1], candles[-20:])
        ]
        typical_range = statistics.median(recent_ranges) if recent_ranges else 0.0
        gap_threshold = max(abs(previous['close']) * 1e-9, typical_range * 0.5) if previous else None
        gap_direction = (
            'GAP_UP' if gap is not None and gap_threshold is not None and gap > gap_threshold
            else 'GAP_DOWN' if gap is not None and gap_threshold is not None and gap < -gap_threshold
            else 'NONE'
        )
        result['gaps'] = {
            'value': gap,
            'direction': gap_direction,
            'threshold': gap_threshold,
            'typicalRange': typical_range if previous else None,
            'temporalGap': bool(previous and latest['epoch'] - previous['epoch'] > TIMEFRAMES[timeframe] * 1.5),
            'status': 'OBSERVED_PRICE_DISCONTINUITY_NOT_PREDICTION' if previous else 'INSUFFICIENT_CLOSED_CANDLES',
        }
        behaviour = MarketBehaviourFinder().scan(candles)
        selector = dict(source=source, symbol=symbol, timeframe=timeframe)
        history_cursor = self.store.db.market_intelligence_history.find(
            {**selector, 'candleEpoch': {'$lt': latest['epoch']}},
            {'_id': 0, 'candleEpoch': 1, 'behaviour.behaviour': 1, 'gaps.direction': 1, 'tags': 1, 'signal.direction': 1},
        )
        previous_snapshots = await history_cursor.sort('candleEpoch', -1).limit(self.MEMORY_HISTORY_LIMIT).to_list(self.MEMORY_HISTORY_LIMIT)
        memory_candles = candles[-self.MEMORY_WINDOW:]
        memory = {
            'status': 'READY' if previous_snapshots else 'INITIALIZED',
            'windowCandles': len(memory_candles),
            'windowStartEpoch': memory_candles[0]['epoch'],
            'windowEndEpoch': latest['epoch'],
            'previousAnalysisCount': len(previous_snapshots),
            'previousAnalyses': list(reversed(previous_snapshots)),
            'usedForTraining': False,
        }
        result.update(
            source=source, symbol=symbol, timeframe=timeframe, analyzedAt=time.time(),
            candleCount=len(candles), latestEpoch=latest['epoch'], state='ANALYZED',
            agentStatus='NOT_ATTACHED_TO_ANALYSIS_PIPELINE',
            trainingStatus='NOT_ATTACHED_TO_ANALYSIS_PIPELINE',
            algorithmStatus='COMPUTED_ANALYTICS_ONLY',
            behaviour=behaviour, memory=memory,
            algorithms={
                'technicalIndicators': {'status': result['indicators']['state'], 'count': result['indicators']['count']},
                'marketBehaviour': {'status': behaviour['status'], 'method': 'DETERMINISTIC_REGIME_CLASSIFIER'},
                'forecast': {'status': 'ANALYTICAL_ONLY', 'executionOrder': False},
            },
            tags=[
                f'SOURCE:{source.upper()}',
                f'DATA_QUALITY:{result["quality"]["status"]}',
                f'MARKET_BEHAVIOUR:{behaviour.get("behaviour", "UNKNOWN")}',
                f'GAP:{gap_direction}',
            ],
        )
        original_signal = result.get('signal') or {}
        if original_signal.get('direction') in {'CALL', 'PUT'}:
            result['analyticalCandidate'] = {
                'direction': original_signal['direction'],
                'confidence': original_signal.get('confidence'),
                'reason': original_signal.get('reason'),
                'status': 'UNVALIDATED_ANALYTICAL_CANDIDATE',
            }
        result['signal'] = {
            **original_signal,
            'direction': 'NO_SIGNAL',
            'confidence': 0,
            'reason': 'STALE_DATA' if 'STALE_DATA' in result.get('quality', {}).get('reasons', []) else 'ANALYTICAL_ONLY_NOT_PROMOTED_FOR_LIVE_SIGNALS',
            'executionOrder': False,
            'calibratedProbability': None,
        }
        selector = dict(source=source, symbol=symbol, timeframe=timeframe)
        await self.store.db.market_analyses.update_one(selector, {'$set': result}, upsert=True)
        # Distinct dataset version per closed candle. Repeated clicks do not inflate history.
        history_key = {**selector, 'candleEpoch': latest['epoch']}
        await self.store.db.market_intelligence_history.update_one(
            history_key,
            {'$setOnInsert': {**history_key, 'analyzedAt': result['analyzedAt'], 'memory': memory, 'behaviour': behaviour, 'algorithms': result['algorithms'], 'tags': result['tags'], 'gaps': result['gaps'], 'signal': result['signal'], 'provenance': 'CLOSED_CANDLES_ANALYTICAL_ONLY'}},
            upsert=True,
        )
        await self.store.db.signal_history.update_one(history_key, {'$setOnInsert': {**result['signal'], **history_key, 'outcome': 'NO_TRADE', 'provenance': 'ANALYTICAL_ONLY'}}, upsert=True)
        self.cycles += 1
        self.error = None
        return result

    async def run(self):
        while True:
            try:
                pairs = await self.store.db.market_instruments.find({
                    'latestEpoch': {'$gte': time.time() - FRESHNESS},
                    'marketDataEligible': {'$ne': False},
                    '$or': [
                        {'source': 'deriv', 'signalEligible': True},
                        {'source': 'market-qx-observer-v2', 'verificationStatus': 'CROSS_VALIDATED'},
                    ],
                }, {'_id': 0}).limit(100).to_list(100)
                for pair in pairs:
                    await self.analyze(pair['source'], pair['symbol'], '1m')
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.error = type(exc).__name__
                await self.store.event('WARN', 'Analysis', f'Analysis unavailable · {self.error}')
            await asyncio.sleep(ANALYSIS_INTERVAL)