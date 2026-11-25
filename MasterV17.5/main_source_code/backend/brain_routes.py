"""V17.2 Signal Brain API: per-symbol analysis with confidence, overview of best picks, learning report."""
from __future__ import annotations

from fastapi import APIRouter, Query

from brain import PHASE2_TYPES, SignalBrain


def brain_router(brain: SignalBrain, deriv=None):
    router = APIRouter(prefix='/api/v1/brain', tags=['brain'])

    @router.get('/signal')
    async def signal(symbol: str = Query(..., min_length=1, max_length=40), timeframe: str = Query(default='1m')):
        analysis = await brain.analyse(symbol, timeframe if timeframe in {'1m', '5m', '15m'} else '1m')
        analysis['marketOpen'] = deriv.market_is_open(symbol) if deriv is not None else None
        return analysis

    @router.get('/overview')
    async def overview(limit: int = Query(default=12, ge=1, le=40)):
        symbols = []
        if deriv is not None:
            symbols = [s for s in getattr(deriv, 'symbols', []) if deriv.market_is_open(s) is not False]
        return {'items': await brain.overview(symbols, limit), 'tracked': len(symbols)}

    @router.get('/learning')
    async def learning():
        await brain.load()
        return {'items': brain.learning_report(), 'phase2Types': list(PHASE2_TYPES)}

    return router
