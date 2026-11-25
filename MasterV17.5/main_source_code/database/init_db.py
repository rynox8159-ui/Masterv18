"""MasterV17.3 - prepare any local MongoDB for the project.

Usage:  python main_source_code/database/init_db.py
Reads MONGO_URL / DB_NAME from main_source_code/backend/.env, pings the server, creates every collection
the backend uses (so the first run never fails on a missing collection) and the indexes the auto trader,
signal brain and market store rely on. Safe to run again at any time (idempotent).
"""
from __future__ import annotations

import sys
from pathlib import Path

from dotenv import dotenv_values
from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.errors import PyMongoError

ROOT = Path(__file__).resolve().parents[2]
ENV = ROOT / "main_source_code" / "backend" / ".env"

COLLECTIONS = [
    "market_instruments", "market_ticks", "market_candles", "market_analyses", "market_features", "market_memory_events",
    "live_signals", "signal_history", "signal_market_controls", "signal_calibration_runs", "signal_research_runs",
    "autotrade_settings", "autotrade_state", "autotrade_trades", "brain_stats", "brain_outcomes",
    "training_samples", "training_knowledge", "training_pipeline_runs", "deep_model_registry", "deep_model_training_runs",
    "observer_status", "observer_receipts", "runtime_events", "pair_promotions", "algorithm_memory", "demo_accounts",
]
INDEXES = {
    "market_candles": [([("source", ASCENDING), ("symbol", ASCENDING), ("timeframe", ASCENDING), ("epoch", DESCENDING)], {})],
    "market_ticks": [([("source", ASCENDING), ("symbol", ASCENDING), ("epoch", DESCENDING)], {})],
    "live_signals": [([("source", ASCENDING), ("symbol", ASCENDING), ("timeframe", ASCENDING), ("entryEpoch", DESCENDING)], {})],
    "autotrade_trades": [([("requestId", ASCENDING)], {"unique": True, "sparse": True}), ([("mode", ASCENDING), ("openedAt", DESCENDING)], {})],
    "brain_stats": [([("key", ASCENDING)], {"unique": True})],
    "brain_outcomes": [([("symbol", ASCENDING), ("contractType", ASCENDING), ("closedAt", DESCENDING)], {})],
}


def main() -> int:
    values = dotenv_values(ENV)
    url = (values.get("MONGO_URL") or "mongodb://127.0.0.1:27017").strip().strip('"')
    name = (values.get("DB_NAME") or "master_candel").strip().strip('"')
    print(f"MongoDB  : {url}\nDatabase : {name}")
    try:
        client = MongoClient(url, serverSelectionTimeoutMS=4000)
        client.admin.command("ping")
    except PyMongoError as exc:
        print(f"[X] MongoDB not reachable: {exc}\n    Start MongoDB (mongod) or fix MONGO_URL in {ENV}")
        return 1
    db = client[name]
    existing = set(db.list_collection_names())
    for collection in COLLECTIONS:
        if collection not in existing:
            db.create_collection(collection)
            print(f"[+] created {collection}")
    for collection, specs in INDEXES.items():
        for keys, options in specs:
            try:
                db[collection].create_index(keys, **options)
            except PyMongoError as exc:
                if getattr(exc, "code", None) != 85:  # 85 = same index already exists under another name
                    print(f"[!] index on {collection}: {exc}")
    print(f"[OK] {len(COLLECTIONS)} collections ready, indexes ensured. The backend can connect to this database.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
