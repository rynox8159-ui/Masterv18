import asyncio
import json
import os
import time
from pathlib import Path

import websockets
from dotenv import dotenv_values
from pymongo import MongoClient, UpdateOne


TIMEFRAMES = {"1m": 60, "5m": 300, "15m": 900}


def env_config():
    env_path = Path(__file__).resolve().parents[1] / ".env"
    env = dotenv_values(env_path)
    app_id = os.environ.get("DERIV_APP_ID") or env.get("DERIV_APP_ID")
    mongo_url = os.environ.get("MONGO_URL") or env.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
    db_name = os.environ.get("DB_NAME") or env.get("DB_NAME") or "master_candel"
    if not app_id:
        raise RuntimeError("DERIV_APP_ID_REQUIRED")
    return app_id, mongo_url, db_name


def gap_report(rows, seconds):
    epochs = sorted({int(row["epoch"]) for row in rows})
    gaps = []
    for previous, current in zip(epochs, epochs[1:]):
        if current - previous > seconds:
            gaps.append({"from": previous, "to": current, "seconds": current - previous})
    return gaps


async def backfill(symbols, count=12000):
    app_id, mongo_url, db_name = env_config()
    client = MongoClient(mongo_url, serverSelectionTimeoutMS=2000)
    db = client[db_name]
    url = f"wss://api.derivws.com/trading/v1/options/ws/public?app_id={app_id}"
    report = []
    async with websockets.connect(url, open_timeout=15, close_timeout=5, ping_interval=20) as ws:
        request_id = 0
        for symbol in symbols:
            for timeframe, seconds in TIMEFRAMES.items():
                provider_rows = {}
                end = "latest"
                previous_earliest = None
                while len(provider_rows) < count:
                    request_id += 1
                    rid = request_id
                    await ws.send(json.dumps({
                        "ticks_history": symbol,
                        "end": end,
                        "count": min(1000, count - len(provider_rows)),
                        "style": "candles",
                        "granularity": seconds,
                        "req_id": rid,
                    }))
                    while True:
                        response = json.loads(await ws.recv())
                        if response.get("req_id") == rid:
                            break
                    if response.get("error"):
                        report.append({
                            "symbol": symbol, "timeframe": timeframe,
                            "status": "ERROR",
                            "error": response["error"].get("code"),
                            "candles": len(provider_rows),
                        })
                        break
                    page = response.get("candles") or []
                    if not page:
                        break
                    epochs = [int(row["epoch"]) for row in page]
                    earliest = min(epochs)
                    if previous_earliest is not None and earliest >= previous_earliest:
                        break
                    previous_earliest = earliest
                    for row in page:
                        epoch = int(row["epoch"])
                        if epoch + seconds <= int(time.time()):
                            provider_rows[epoch] = row
                    end = earliest - 1
                    await asyncio.sleep(0.2)
                operations = []
                for epoch, row in sorted(provider_rows.items()):
                    operations.append(UpdateOne(
                        {
                            "source": "deriv",
                            "symbol": symbol,
                            "timeframe": timeframe,
                            "epoch": epoch,
                        },
                        {"$set": {
                            "source": "deriv",
                            "symbol": symbol,
                            "timeframe": timeframe,
                            "epoch": epoch,
                            "open": float(row["open"]),
                            "high": float(row["high"]),
                            "low": float(row["low"]),
                            "close": float(row["close"]),
                            "provider": "deriv",
                            "method": "DERIV_HISTORY",
                            "completeness": "PROVIDER_OHLC",
                        }},
                        upsert=True,
                    ))
                if operations:
                    db.market_candles.bulk_write(operations, ordered=False)
                stored = list(db.market_candles.find(
                    {"source": "deriv", "symbol": symbol, "timeframe": timeframe, "completeness": "PROVIDER_OHLC"},
                    {"_id": 0, "epoch": 1},
                ))
                gaps = gap_report(stored, seconds)
                report.append({
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "status": "OK",
                    "provider_pages": provider_rows and len(provider_rows) or 0,
                    "candles": len(stored),
                    "gap_count": len(gaps),
                    "largest_gap_seconds": max((gap["seconds"] for gap in gaps), default=0),
                })
    return report
