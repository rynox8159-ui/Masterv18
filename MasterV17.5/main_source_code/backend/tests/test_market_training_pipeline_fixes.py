import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "main_source_code" / "backend"))

import training_orchestrator
from market_routes import market_router
from training_orchestrator import TrainingOrchestrator


def test_instrument_catalog_includes_closed_eligible_deriv_markets():
    rows = [
        {
            "source": "deriv",
            "symbol": "frxEURUSD",
            "market": "forex",
            "marketDataEligible": True,
            "exchangeIsOpen": False,
        },
        {
            "source": "deriv",
            "symbol": "R_100",
            "market": "synthetic_index",
            "marketDataEligible": True,
            "exchangeIsOpen": True,
        },
        {
            "source": "market-qx-observer-v2",
            "symbol": "EURUSD",
            "available": False,
        },
    ]

    class Cursor:
        def limit(self, _limit):
            return self

        async def to_list(self, _limit):
            return rows

    class Instruments:
        def find(self, _query, _projection):
            return Cursor()

    store = SimpleNamespace(db=SimpleNamespace(market_instruments=Instruments()))
    app = FastAPI()
    app.include_router(market_router(store, None, None))

    response = TestClient(app).get("/api/v1/instruments?source=deriv")

    assert response.status_code == 200
    items = response.json()["items"]
    assert [(item["symbol"], item["isOpen"]) for item in items] == [
        ("frxEURUSD", False),
        ("R_100", True),
    ]


def test_training_uses_discovered_forex_when_no_forex_candle_stream_is_open(monkeypatch):
    monkeypatch.setattr(training_orchestrator, "DERIV_CANDLE_SYMBOLS", ["ALL"])
    deriv = SimpleNamespace(
        candle_symbols=["R_100"],
        _symbol_markets={
            "frxGBPUSD": "forex",
            "frxEURUSD": "forex",
            "R_100": "synthetic_index",
        },
    )
    orchestrator = TrainingOrchestrator(None, deriv, None, None, None)

    assert orchestrator._markets() == [
        ("frxEURUSD", "1m"),
        ("frxEURUSD", "5m"),
        ("frxGBPUSD", "1m"),
        ("frxGBPUSD", "5m"),
    ]
    assert orchestrator.status()["eligibleMarkets"] == 2


def test_training_prefers_open_forex_candle_streams(monkeypatch):
    monkeypatch.setattr(training_orchestrator, "DERIV_CANDLE_SYMBOLS", ["ALL"])
    deriv = SimpleNamespace(
        candle_symbols=["frxEURUSD"],
        _symbol_markets={"frxEURUSD": "forex", "frxGBPUSD": "forex"},
    )
    orchestrator = TrainingOrchestrator(None, deriv, None, None, None)

    assert orchestrator._markets() == [
        ("frxEURUSD", "1m"),
        ("frxEURUSD", "5m"),
    ]


def test_training_falls_back_to_bounded_official_forex_catalog(monkeypatch):
    monkeypatch.setattr(training_orchestrator, "DERIV_CANDLE_SYMBOLS", ["ALL"])
    monkeypatch.setattr(training_orchestrator, "DERIV_SYMBOLS", ["ALL"])
    monkeypatch.setattr(training_orchestrator, "MAX_FOREX_TRAINING_PAIRS", 2)
    deriv = SimpleNamespace(candle_symbols=[], _symbol_markets={"R_100": "synthetic_index"})
    orchestrator = TrainingOrchestrator(None, deriv, None, None, None)

    assert orchestrator._markets() == [
        ("frxEURUSD", "1m"),
        ("frxEURUSD", "5m"),
        ("frxGBPUSD", "1m"),
        ("frxGBPUSD", "5m"),
    ]


def test_fetch_history_refreshes_features_for_the_backfilled_market():
    calls = []

    class Deriv:
        async def history(self, symbol, timeframe, count, force):
            calls.append(("history", symbol, timeframe, count, force))

    class Candles:
        async def count_documents(self, _query):
            return 500

    class Features:
        async def refresh_market(self, source, symbol, timeframe):
            calls.append(("features", source, symbol, timeframe))
            return 12

    orchestrator = TrainingOrchestrator(
        SimpleNamespace(market_candles=Candles()),
        Deriv(),
        None,
        None,
        None,
    )
    orchestrator.feature_store = Features()

    asyncio.run(orchestrator._train_one("frxEURUSD", "1m", backfill=True, train=False))

    assert calls[0][0:3] == ("history", "frxEURUSD", "1m")
    assert calls[1] == ("features", "deriv", "frxEURUSD", "1m")
    assert orchestrator.progress["results"][0]["features"] == 12
