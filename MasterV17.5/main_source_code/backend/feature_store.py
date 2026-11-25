"""Feature store: pre-computed indicators per closed provider candle (market_features) for fast training and live inference."""
import asyncio
import logging
import time

import numpy as np
import pandas as pd
from fastapi import APIRouter

logger = logging.getLogger(__name__)

FEATURE_TIMEFRAMES = ('1m', '5m', '15m')
CONTEXT_CANDLES = 320
REFRESH_SECONDS = 60
FEATURE_COLUMNS = ('ret_1', 'ret_5', 'ema_20', 'ema_50', 'ema_gap', 'rsi_14', 'atr_14', 'atr_pct', 'bb_upper', 'bb_lower', 'bb_width', 'bb_pos', 'macd', 'macd_signal', 'macd_hist', 'range_pct', 'body_pct', 'label_up')


def compute_features(rows):
    """rows: closed candles sorted by epoch with open/high/low/close. Returns a DataFrame of indicator features."""
    frame = pd.DataFrame(rows)
    if frame.empty or len(frame) < 60:
        return pd.DataFrame()
    close = frame['close'].astype(float)
    high = frame['high'].astype(float)
    low = frame['low'].astype(float)
    open_ = frame['open'].astype(float)
    out = pd.DataFrame({'epoch': frame['epoch'].astype(int)})
    out['ret_1'] = close.pct_change(1)
    out['ret_5'] = close.pct_change(5)
    out['ema_20'] = close.ewm(span=20, adjust=False).mean()
    out['ema_50'] = close.ewm(span=50, adjust=False).mean()
    out['ema_gap'] = (out['ema_20'] - out['ema_50']) / close
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    out['rsi_14'] = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
    true_range = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    out['atr_14'] = true_range.ewm(alpha=1 / 14, adjust=False).mean()
    out['atr_pct'] = out['atr_14'] / close
    middle = close.rolling(20).mean()
    deviation = close.rolling(20).std()
    out['bb_upper'] = middle + 2 * deviation
    out['bb_lower'] = middle - 2 * deviation
    out['bb_width'] = (out['bb_upper'] - out['bb_lower']) / close
    out['bb_pos'] = (close - out['bb_lower']) / (out['bb_upper'] - out['bb_lower']).replace(0, np.nan)
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    out['macd'] = (ema12 - ema26) / close
    out['macd_signal'] = out['macd'].ewm(span=9, adjust=False).mean()
    out['macd_hist'] = out['macd'] - out['macd_signal']
    out['range_pct'] = (high - low) / close
    out['body_pct'] = (close - open_) / close
    out['label_up'] = (close.shift(-1) > close).astype(float).where(close.shift(-1).notna())
    out['close'] = close
    return out.replace([np.inf, -np.inf], np.nan).dropna(subset=['rsi_14', 'atr_14', 'bb_width', 'ema_50'])


class FeatureStore:
    def __init__(self, db, deriv):
        self.db = db
        self.deriv = deriv
        self.task = None
        self.last_refresh_at = None
        self.last_refresh = {'markets': 0, 'rowsWritten': 0, 'durationMs': 0}

    async def initialize(self):
        await self.db.market_features.create_index([('source', 1), ('symbol', 1), ('timeframe', 1), ('epoch', -1)], unique=True)
        await self.db.market_features.create_index([('source', 1), ('timeframe', 1), ('epoch', -1)])
        await self.db.market_candles.create_index([('source', 1), ('symbol', 1), ('timeframe', 1), ('completeness', 1), ('epoch', -1)])

    async def refresh_market(self, source, symbol, timeframe):
        rows = await self.db.market_candles.find(
            {'source': source, 'symbol': symbol, 'timeframe': timeframe, 'completeness': 'PROVIDER_OHLC'},
            {'_id': 0, 'epoch': 1, 'open': 1, 'high': 1, 'low': 1, 'close': 1},
        ).sort('epoch', -1).limit(CONTEXT_CANDLES).to_list(CONTEXT_CANDLES)
        rows.reverse()
        features = await asyncio.to_thread(compute_features, rows)
        if features.empty:
            return 0
        latest = await self.db.market_features.find_one({'source': source, 'symbol': symbol, 'timeframe': timeframe}, {'_id': 0, 'epoch': 1}, sort=[('epoch', -1)])
        since = (latest or {}).get('epoch', -1) - 2
        pending = features[features['epoch'] > since].tail(120)
        written = 0
        for record in pending.to_dict('records'):
            doc = {k: (None if (isinstance(v, float) and np.isnan(v)) else (float(v) if isinstance(v, (np.floating, float)) else v)) for k, v in record.items()}
            doc.update({'source': source, 'symbol': symbol, 'timeframe': timeframe, 'epoch': int(doc['epoch']), 'computedAt': time.time()})
            await self.db.market_features.update_one({'source': source, 'symbol': symbol, 'timeframe': timeframe, 'epoch': doc['epoch']}, {'$set': doc}, upsert=True)
            written += 1
        return written

    async def refresh_all(self):
        started = time.monotonic()
        symbols = list(getattr(self.deriv, 'candle_symbols', None) or [])
        written = 0
        for symbol in symbols:
            for timeframe in FEATURE_TIMEFRAMES:
                try:
                    written += await self.refresh_market('deriv', symbol, timeframe)
                except Exception as exc:  # noqa: BLE001
                    logger.warning('Feature refresh failed for %s/%s: %s', symbol, timeframe, type(exc).__name__)
        self.last_refresh_at = time.time()
        self.last_refresh = {'markets': len(symbols) * len(FEATURE_TIMEFRAMES), 'rowsWritten': written, 'durationMs': round((time.monotonic() - started) * 1000)}

    async def run(self):
        await asyncio.sleep(20)
        while True:
            try:
                await self.refresh_all()
            except Exception:  # noqa: BLE001
                logger.exception('Feature store refresh loop failed')
            await asyncio.sleep(REFRESH_SECONDS)

    async def status(self):
        total = await self.db.market_features.estimated_document_count()
        per_timeframe = await self.db.market_features.aggregate([{'$group': {'_id': '$timeframe', 'rows': {'$sum': 1}, 'symbols': {'$addToSet': '$symbol'}}}]).to_list(20)
        return {
            'collection': 'market_features', 'rows': total, 'featureColumns': list(FEATURE_COLUMNS), 'refreshSeconds': REFRESH_SECONDS,
            'lastRefreshAt': self.last_refresh_at, 'lastRefresh': self.last_refresh,
            'timeframes': {row['_id']: {'rows': row['rows'], 'symbols': len(row['symbols'])} for row in per_timeframe},
        }


def feature_router(store: FeatureStore):
    router = APIRouter(prefix='/api/v1/features', tags=['feature-store'])

    @router.get('/status')
    async def status():
        return await store.status()

    @router.get('/latest')
    async def latest(symbol: str, timeframe: str = '1m', limit: int = 50, source: str = 'deriv'):
        rows = await store.db.market_features.find({'source': source, 'symbol': symbol, 'timeframe': timeframe}, {'_id': 0}).sort('epoch', -1).limit(max(1, min(500, limit))).to_list(500)
        return {'items': rows}

    return router
