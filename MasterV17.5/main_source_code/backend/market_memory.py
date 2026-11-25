"""Market memory layer: remembers traps, gaps, behaviour notes and algorithm outcomes in MongoDB.

Events are detected only from closed provider candles. The layer feeds a veto/penalty gate
into the live signal pipeline so signals are not issued straight into a fresh trap or gap.
"""
import time
from fastapi import APIRouter, Query
from pymongo import ASCENDING, DESCENDING, UpdateOne

TRAP_LOOKBACK = 20
GAP_ATR_MULTIPLIER = 0.6
EVENT_TTL_DAYS = 30


def _atr(candles, period=14):
    if len(candles) < 2:
        return 0.0
    ranges = []
    for previous, current in zip(candles[:-1], candles[1:]):
        ranges.append(max(
            current['high'] - current['low'],
            abs(current['high'] - previous['close']),
            abs(current['low'] - previous['close']),
        ))
    window = ranges[-period:]
    return sum(window) / len(window) if window else 0.0


def detect_events(candles):
    """Return GAP_UP / GAP_DOWN / BULL_TRAP / BEAR_TRAP / STOP_HUNT events for a closed-candle window."""
    events = []
    if len(candles) < TRAP_LOOKBACK + 3:
        return events
    atr = _atr(candles)
    if atr <= 0:
        return events
    start = max(1, len(candles) - 60)
    for index in range(start, len(candles)):
        previous, current = candles[index - 1], candles[index]
        gap = current['open'] - previous['close']
        if abs(gap) >= GAP_ATR_MULTIPLIER * atr:
            events.append({
                'type': 'GAP_UP' if gap > 0 else 'GAP_DOWN', 'epoch': current['epoch'],
                'strength': round(abs(gap) / atr, 3), 'price': current['open'],
                'note': f"Opened {abs(gap):.6g} {'above' if gap > 0 else 'below'} previous close",
            })
        if index < TRAP_LOOKBACK + 1:
            continue
        window = candles[index - TRAP_LOOKBACK - 1:index - 1]
        swing_high = max(c['high'] for c in window)
        swing_low = min(c['low'] for c in window)
        breakout = candles[index - 1]
        if breakout['close'] > swing_high and current['close'] < swing_high:
            events.append({
                'type': 'BULL_TRAP', 'epoch': current['epoch'], 'price': swing_high,
                'strength': round((breakout['close'] - current['close']) / atr, 3),
                'note': 'Breakout above swing high failed and closed back inside the range',
            })
        elif breakout['close'] < swing_low and current['close'] > swing_low:
            events.append({
                'type': 'BEAR_TRAP', 'epoch': current['epoch'], 'price': swing_low,
                'strength': round((current['close'] - breakout['close']) / atr, 3),
                'note': 'Breakdown below swing low failed and closed back inside the range',
            })
        prior_high = max(c['high'] for c in candles[index - TRAP_LOOKBACK:index])
        prior_low = min(c['low'] for c in candles[index - TRAP_LOOKBACK:index])
        if current['high'] > prior_high and current['close'] < prior_high and (current['high'] - max(current['open'], current['close'])) > atr * 0.8:
            events.append({'type': 'STOP_HUNT', 'side': 'HIGH', 'epoch': current['epoch'], 'price': current['high'], 'strength': 1.0, 'note': 'Wick swept liquidity above the prior high'})
        elif current['low'] < prior_low and current['close'] > prior_low and (min(current['open'], current['close']) - current['low']) > atr * 0.8:
            events.append({'type': 'STOP_HUNT', 'side': 'LOW', 'epoch': current['epoch'], 'price': current['low'], 'strength': 1.0, 'note': 'Wick swept liquidity below the prior low'})
    return events


def gate_for(direction, events, last_epoch, seconds):
    """Hard vetoes for fresh traps/gaps against the direction; soft penalty for older ones."""
    vetoes, penalty = [], 0.0
    for event in events:
        age = (last_epoch - event['epoch']) // max(1, seconds)
        if age > 3:
            continue
        kind = event['type']
        if kind in {'GAP_UP', 'GAP_DOWN'} and age <= 1:
            vetoes.append(f'MEMORY_POST_{kind}_UNSTABLE')
        elif kind == 'BULL_TRAP' and direction == 'CALL':
            vetoes.append('MEMORY_RECENT_BULL_TRAP')
        elif kind == 'BEAR_TRAP' and direction == 'PUT':
            vetoes.append('MEMORY_RECENT_BEAR_TRAP')
        elif kind == 'STOP_HUNT':
            if (event.get('side') == 'HIGH' and direction == 'CALL') or (event.get('side') == 'LOW' and direction == 'PUT'):
                penalty += 6.0
    return sorted(set(vetoes)), penalty


class MarketMemoryLayer:
    def __init__(self, db):
        self.db = db
        self.last_run = {}

    async def initialize(self):
        await self.db.market_memory_events.create_index(
            [('source', ASCENDING), ('symbol', ASCENDING), ('timeframe', ASCENDING), ('epoch', ASCENDING), ('type', ASCENDING)], unique=True,
        )
        await self.db.market_memory_events.create_index('recordedAt', expireAfterSeconds=EVENT_TTL_DAYS * 86400)
        await self.db.market_behaviour_notes.create_index([('source', 1), ('symbol', 1), ('timeframe', 1)], unique=True)
        await self.db.algorithm_memory.create_index([('source', 1), ('symbol', 1), ('timeframe', 1), ('algorithm', 1)], unique=True)

    async def remember(self, source, symbol, timeframe, candles, behaviour=None):
        events = detect_events(candles)
        now = time.time()
        key = {'source': source, 'symbol': symbol, 'timeframe': timeframe}
        if events:
            from datetime import datetime, timezone
            recorded = datetime.now(timezone.utc)
            await self.db.market_memory_events.bulk_write([
                UpdateOne({**key, 'epoch': e['epoch'], 'type': e['type']}, {'$setOnInsert': {**key, **e, 'recordedAt': recorded}}, upsert=True)
                for e in events
            ], ordered=False)
        counts = {}
        for event in events:
            counts[event['type']] = counts.get(event['type'], 0) + 1
        await self.db.market_behaviour_notes.update_one(key, {'$set': {
            **key, 'updatedAt': now, 'recentEventCounts': counts,
            'lastEvent': events[-1] if events else None,
            'behaviour': (behaviour or {}).get('behaviour') if isinstance(behaviour, dict) else behaviour,
            'candlesAnalysed': len(candles),
        }}, upsert=True)
        return events

    async def apply(self, assessment, source, symbol, timeframe, candles, seconds):
        events = await self.remember(source, symbol, timeframe, candles, assessment.get('behaviour'))
        direction = assessment.get('direction')
        context = [e['type'] for e in events if (candles[-1]['epoch'] - e['epoch']) // max(1, seconds) <= 3]
        assessment['memoryContext'] = sorted(set(context))
        if direction not in {'CALL', 'PUT'}:
            return assessment
        vetoes, penalty = gate_for(direction, events, candles[-1]['epoch'], seconds)
        algo_penalty = await self._algorithm_penalty(source, symbol, timeframe, assessment.get('agreeing') or [])
        penalty += algo_penalty
        if vetoes:
            assessment['vetoes'] = sorted(set((assessment.get('vetoes') or []) + vetoes))
            assessment['blockedDirection'] = direction
            assessment['direction'] = 'NO_SIGNAL'
            assessment['reason'] = vetoes[0]
        elif penalty:
            assessment['confidence'] = max(0.0, float(assessment.get('confidence', 0.0)) - penalty)
            assessment['memoryPenalty'] = round(penalty, 2)
        return assessment

    async def _algorithm_penalty(self, source, symbol, timeframe, algorithms):
        if not algorithms:
            return 0.0
        rows = await self.db.algorithm_memory.find(
            {'source': source, 'symbol': symbol, 'timeframe': timeframe, 'algorithm': {'$in': list(algorithms)}},
            {'_id': 0, 'wins': 1, 'losses': 1},
        ).to_list(100)
        wins = sum(r.get('wins', 0) for r in rows)
        losses = sum(r.get('losses', 0) for r in rows)
        total = wins + losses
        if total < 20:
            return 0.0
        win_rate = wins / total
        return round(max(0.0, (0.55 - win_rate) * 40), 2)

    async def record_outcome(self, signal, result, now):
        if result not in {'WIN', 'LOSS'}:
            return
        key = {'source': signal.get('source'), 'symbol': signal.get('symbol'), 'timeframe': signal.get('timeframe')}
        field = 'wins' if result == 'WIN' else 'losses'
        algorithms = list(dict.fromkeys((signal.get('agreeing') or []) + [signal.get('reason') or 'UNSPECIFIED']))
        await self.db.algorithm_memory.bulk_write([
            UpdateOne({**key, 'algorithm': name}, {'$inc': {field: 1}, '$set': {'updatedAt': now}, '$setOnInsert': {**key, 'algorithm': name}}, upsert=True)
            for name in algorithms if isinstance(name, str)
        ], ordered=False)
        for tag in signal.get('memoryContext') or []:
            await self.db.algorithm_memory.update_one(
                {**key, 'algorithm': f'CONTEXT:{tag}'},
                {'$inc': {field: 1}, '$set': {'updatedAt': now}, '$setOnInsert': {**key, 'algorithm': f'CONTEXT:{tag}'}}, upsert=True,
            )


def memory_router(layer):
    router = APIRouter(prefix='/api/v1/memory', tags=['Market memory'])

    @router.get('/events')
    async def events(symbol: str | None = None, timeframe: str | None = None, type: str | None = None, limit: int = Query(100, ge=1, le=500)):
        query = {k: v for k, v in {'symbol': symbol, 'timeframe': timeframe, 'type': type.upper() if type else None}.items() if v}
        rows = await layer.db.market_memory_events.find(query, {'_id': 0}).sort('epoch', DESCENDING).limit(limit).to_list(limit)
        for row in rows:
            if row.get('recordedAt') is not None:
                row['recordedAt'] = row['recordedAt'].isoformat()
        return {'items': rows}

    @router.get('/notes')
    async def notes(symbol: str | None = None):
        query = {'symbol': symbol} if symbol else {}
        rows = await layer.db.market_behaviour_notes.find(query, {'_id': 0}).sort('updatedAt', DESCENDING).limit(200).to_list(200)
        return {'items': rows}

    @router.get('/algorithms')
    async def algorithms(symbol: str | None = None):
        query = {'symbol': symbol} if symbol else {}
        rows = await layer.db.algorithm_memory.find(query, {'_id': 0}).limit(500).to_list(500)
        for row in rows:
            total = row.get('wins', 0) + row.get('losses', 0)
            row['winRate'] = round(row.get('wins', 0) / total * 100, 1) if total else None
        rows.sort(key=lambda r: (r['winRate'] is None, -(r['winRate'] or 0)))
        return {'items': rows}

    @router.get('/summary')
    async def summary():
        pipeline = [{'$group': {'_id': '$type', 'count': {'$sum': 1}}}]
        counts = {row['_id']: row['count'] for row in await layer.db.market_memory_events.aggregate(pipeline).to_list(20)}
        return {
            'events': counts,
            'notes': await layer.db.market_behaviour_notes.count_documents({}),
            'algorithms': await layer.db.algorithm_memory.count_documents({}),
        }

    return router
