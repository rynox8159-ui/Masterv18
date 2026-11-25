"""Offline deep-model check: backfill Deriv history and run the out-of-sample training gate.

Usage (from backend/):  python forex_model_check.py frxEURUSD frxGBPUSD --timeframe 1m
Works even when the forex session is closed (uses Deriv historical candles).
"""
import argparse
import asyncio
import json
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / '.env')

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402
from market_store import MarketStore  # noqa: E402
from market_gatekeeper import RedisMarketCache, BaseAgent, BaseAgentPool  # noqa: E402
from deriv_service import DerivService  # noqa: E402
from dl.deep_model_service import DeepModelService  # noqa: E402

KEYS = ('status', 'promotionTier', 'reason', 'sampleCounts', 'closedProviderCandles', 'backfill', 'registryAction')


async def main(symbols, timeframe):
    client = AsyncIOMotorClient(os.environ['MONGO_URL'])
    db = client[os.environ['DB_NAME']]
    store = MarketStore(db)
    await store.initialize()
    deriv = DerivService(store, RedisMarketCache(), BaseAgentPool(BaseAgent(store), concurrency=1))
    deep = DeepModelService(db, deriv)
    await deep.initialize()
    results = {}
    for symbol in symbols:
        print(f'[train] {symbol} {timeframe} …', flush=True)
        report = await deep.train_market('deriv', symbol, timeframe)
        summary = {k: report.get(k) for k in KEYS}
        test = (report.get('metrics') or {}).get('test') or report.get('test') or {}
        summary['test'] = {k: test.get(k) for k in ('winRate', 'expectedValue', 'trades', 'coverage', 'accuracy') if k in test}
        results[symbol] = summary
        print(json.dumps(summary, indent=2, default=str), flush=True)
    client.close()
    return results


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('symbols', nargs='+')
    parser.add_argument('--timeframe', default='1m')
    args = parser.parse_args()
    asyncio.run(main(args.symbols, args.timeframe))
