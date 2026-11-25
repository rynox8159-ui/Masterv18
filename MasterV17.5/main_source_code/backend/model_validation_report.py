"""V17.2 model report: trains the Step 8 stack on stored Deriv candles and prints the walk-forward OOS report.
Usage: python main_source_code/backend/model_validation_report.py [SYMBOL] [TIMEFRAME] [CANDLES]"""
import asyncio
import json
import os
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND.parents[1]))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND / '.env')
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

from ml.ml_model_adapters import _train_adapters_in_worker  # noqa: E402
from ml import model_validation  # noqa: E402


async def main():
    symbol = sys.argv[1] if len(sys.argv) > 1 else 'R_100'
    timeframe = sys.argv[2] if len(sys.argv) > 2 else '1m'
    count = int(sys.argv[3]) if len(sys.argv) > 3 else 1500
    db = AsyncIOMotorClient(os.environ['MONGO_URL'])[os.environ['DB_NAME']]
    rows = await db.market_candles.find({'source': 'deriv', 'symbol': symbol, 'timeframe': timeframe}, {'_id': 0}).sort('epoch', -1).limit(count).to_list(count)
    rows.sort(key=lambda r: r['epoch'])
    if len(rows) < 400:
        print(json.dumps({'status': 'INSUFFICIENT_CANDLES', 'candles': len(rows)}))
        return
    started = time.time()
    payload = _train_adapters_in_worker('deriv', symbol, timeframe, rows)
    validation = payload.get('validation') or {}
    report = {
        'symbol': symbol, 'timeframe': timeframe, 'candles': len(rows), 'range': [rows[0]['epoch'], rows[-1]['epoch']],
        'trainingStatus': payload.get('status'), 'samples': payload.get('sampleCount'), 'seconds': round(time.time() - started, 1),
        'validation': {k: v for k, v in validation.items() if k != 'raw'}, 'headline': model_validation.headline(validation),
    }
    print(json.dumps(report, indent=1, default=str))


if __name__ == '__main__':
    asyncio.run(main())
