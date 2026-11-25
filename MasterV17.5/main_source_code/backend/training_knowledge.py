"""Mongo-backed, idempotent outcome labels and per-market training summaries."""
import math
import time
from datetime import datetime, timezone

from fastapi import APIRouter
from pymongo import ASCENDING, DESCENDING

MIN_GATE_SAMPLES = 12
MIN_GATE_WIN_RATE = 55.0


def _bucket(confidence):
    value = float(confidence or 0)
    return str(int(min(99, max(0, value)) // 5 * 5))


def _clean(value):
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(key): _clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(item) for item in value]
    return value


def _rate(wins, losses):
    decided = wins + losses
    return round(100.0 * wins / decided, 2) if decided else None


class TrainingKnowledge:
    def __init__(self, db):
        self.db = db

    async def initialize(self):
        await self.db.training_knowledge.create_index(
            [('source', ASCENDING), ('symbol', ASCENDING), ('timeframe', ASCENDING)],
            unique=True,
        )
        await self.db.training_samples.create_index(
            [('symbol', ASCENDING), ('timeframe', ASCENDING), ('entryEpoch', DESCENDING)]
        )
        await self.db.training_samples.create_index('signalId', unique=True)

    async def record_outcome(self, signal, result):
        signal_id = signal.get('id')
        if result not in {'WIN', 'LOSS'} or signal.get('direction') not in {'CALL', 'PUT'} or not signal_id:
            return
        key = {
            'source': signal.get('source'),
            'symbol': signal.get('symbol'),
            'timeframe': signal.get('timeframe'),
        }
        if not all(key.values()):
            return
        win = int(result == 'WIN')
        hour = datetime.fromtimestamp(float(signal.get('entryEpoch') or time.time()), timezone.utc).hour
        direction = signal['direction']
        bucket = _bucket(signal.get('confidence'))
        increments = {
            'wins': win,
            'losses': 1 - win,
            f'byDirection.{direction}.wins': win,
            f'byDirection.{direction}.losses': 1 - win,
            f'byHour.{hour}.wins': win,
            f'byHour.{hour}.losses': 1 - win,
            f'byConfidence.{bucket}.wins': win,
            f'byConfidence.{bucket}.losses': 1 - win,
        }
        for family, model_direction in (signal.get('modelDirections') or {}).items():
            if model_direction in {'CALL', 'PUT'}:
                increments[f'byModel.{family}.correct'] = int((model_direction == direction) == bool(win))
                increments[f'byModel.{family}.total'] = 1
        sample = {
            'signalId': signal_id,
            **key,
            'direction': direction,
            'result': result,
            'label': win,
            'confidence': signal.get('confidence'),
            'entryEpoch': signal.get('entryEpoch'),
            'hourUtc': hour,
            'indicators': _clean(signal.get('indicators') or {}),
            'modelProbabilities': _clean(signal.get('modelProbabilities') or {}),
            'modelDirections': signal.get('modelDirections') or {},
            'multiTimeframeFeatures': _clean(signal.get('multiTimeframeFeatures') or {}),
            'agreeing': signal.get('agreeing') or [],
            'opposing': signal.get('opposing') or [],
            'validationTier': signal.get('validationTier'),
            'createdAt': datetime.now(timezone.utc).isoformat(),
        }
        inserted = await self.db.training_samples.update_one(
            {'signalId': signal_id},
            {'$setOnInsert': sample},
            upsert=True,
        )
        if not inserted.upserted_id:
            return
        await self.db.training_knowledge.update_one(
            key,
            {
                '$inc': increments,
                '$set': {
                    'label': signal.get('label'),
                    'updatedAt': datetime.now(timezone.utc).isoformat(),
                    'lastResult': result,
                },
                '$push': {'recent': {'$each': [win], '$slice': -50}},
            },
            upsert=True,
        )

    async def gate(self, source, symbol, timeframe, direction, model_directions):
        opposing = [
            name for name, value in (model_directions or {}).items()
            if value in {'CALL', 'PUT'} and value != direction
        ]
        if opposing:
            return f'MODEL_FAMILY_DISAGREES:{",".join(sorted(opposing))}'
        doc = await self.db.training_knowledge.find_one(
            {'source': source, 'symbol': symbol, 'timeframe': timeframe},
            {'_id': 0, 'byDirection': 1, 'recent': 1},
        )
        if not doc:
            return None
        side = (doc.get('byDirection') or {}).get(direction) or {}
        wins, losses = int(side.get('wins', 0)), int(side.get('losses', 0))
        if wins + losses >= MIN_GATE_SAMPLES and _rate(wins, losses) < MIN_GATE_WIN_RATE:
            return f'KNOWLEDGE_LOW_WIN_RATE:{direction}:{_rate(wins, losses)}%'
        recent = doc.get('recent') or []
        if len(recent) >= MIN_GATE_SAMPLES and 100.0 * sum(recent[-MIN_GATE_SAMPLES:]) / MIN_GATE_SAMPLES < MIN_GATE_WIN_RATE - 5:
            return 'KNOWLEDGE_RECENT_DRAWDOWN'
        return None

    async def summary(self, limit=200):
        rows = await self.db.training_knowledge.find(
            {}, {'_id': 0, 'recent': 0}
        ).sort('updatedAt', -1).limit(limit).to_list(limit)
        items = []
        for row in rows:
            models = {
                name: round(100.0 * values.get('correct', 0) / values['total'], 1)
                for name, values in (row.get('byModel') or {}).items()
                if values.get('total')
            }
            items.append({
                'source': row.get('source'),
                'symbol': row.get('symbol'),
                'label': row.get('label'),
                'timeframe': row.get('timeframe'),
                'wins': row.get('wins', 0),
                'losses': row.get('losses', 0),
                'winRate': _rate(row.get('wins', 0), row.get('losses', 0)),
                'callWinRate': _rate(**{field: (row.get('byDirection') or {}).get('CALL', {}).get(field, 0) for field in ('wins', 'losses')}),
                'putWinRate': _rate(**{field: (row.get('byDirection') or {}).get('PUT', {}).get(field, 0) for field in ('wins', 'losses')}),
                'modelAccuracy': models,
                'updatedAt': row.get('updatedAt'),
            })
        samples = await self.db.training_samples.count_documents({})
        return {
            'items': items,
            'pairs': len(items),
            'trainingSamples': samples,
            'gate': {'minSamples': MIN_GATE_SAMPLES, 'minWinRate': MIN_GATE_WIN_RATE},
        }


def training_router(knowledge: TrainingKnowledge):
    router = APIRouter(prefix='/api/v1/training', tags=['training-knowledge'])

    @router.get('/knowledge')
    async def knowledge_summary(limit: int = 200):
        return await knowledge.summary(max(1, min(limit, 500)))

    @router.get('/samples')
    async def samples(symbol: str | None = None, limit: int = 100):
        query = {'symbol': symbol} if symbol else {}
        rows = await knowledge.db.training_samples.find(query, {'_id': 0}).sort(
            'entryEpoch', -1
        ).limit(max(1, min(limit, 1000))).to_list(1000)
        return {'items': rows}

    return router
