import asyncio
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "main_source_code" / "backend"))

from auto_trade.dashboard_engine import AutoTradeEngine, stream_contract
import auto_trade.dashboard_engine as dashboard_engine
from autotrade_routes import autotrade_router


def test_credentials_are_held_in_engine_memory_without_entering_settings():
    engine = AutoTradeEngine(SimpleNamespace())
    engine.update_credentials({"app_id": "12345", "demo": "demo-token"})

    assert engine.credentials()["app_id"] == "12345"
    assert engine.credentials()["demo"] == "demo-token"
    assert "demo" not in engine.settings


def test_demo_credentials_never_fallback_to_real_token(monkeypatch):
    monkeypatch.setattr(dashboard_engine, "dotenv_values", lambda _path: {
        "DERIV_APP_ID": "12345",
        "DERIV_LIVE_TOKEN": "real-only-token",
        "DERIV_API_TOKEN_2": "real-legacy-token",
    })
    credentials = dashboard_engine.load_credentials()

    assert credentials["demo"] == ""
    assert credentials["real"] == "real-only-token"


def test_demo_executor_fails_closed_without_active_demo_account(monkeypatch):
    from auto_trade.dashboard_execution import DerivExecutor, DerivRequestError

    async def no_demo_accounts(_app_id, _token):
        return [{"account_id": "real-1", "account_type": "real", "status": "active"}]

    monkeypatch.setattr(
        "auto_trade.dashboard_execution.list_accounts",
        no_demo_accounts,
    )

    async def verify():
        executor = DerivExecutor("12345", "token", "demo")
        with pytest.raises(DerivRequestError, match="No active Deriv demo"):
            await executor.connect()

    asyncio.run(verify())


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"app_id": "not-a-number"}, "digits only"),
        ({"demo": "x" * 257}, "too long"),
        ({}, "Provide"),
    ],
)
def test_credentials_reject_invalid_updates(changes, message):
    engine = AutoTradeEngine(SimpleNamespace())
    with pytest.raises(ValueError, match=message):
        engine.update_credentials(changes)


def test_credentials_cannot_change_while_engine_is_running():
    engine = AutoTradeEngine(SimpleNamespace())
    engine.task = SimpleNamespace(done=lambda: False)

    with pytest.raises(RuntimeError, match="CREDENTIALS_CANNOT_CHANGE_WHILE_RUNNING"):
        engine.update_credentials({"demo": "new-token"})


def test_inactive_real_token_can_be_saved_while_demo_engine_is_running():
    engine = AutoTradeEngine(SimpleNamespace())
    engine.task = SimpleNamespace(done=lambda: False)

    result = engine.update_credentials({"real": "real-token"})

    assert result["realToken"] is True
    assert engine.credentials()["real"] == "real-token"


def test_shared_app_id_cannot_change_while_engine_is_running():
    engine = AutoTradeEngine(SimpleNamespace())
    engine.task = SimpleNamespace(done=lambda: False)

    with pytest.raises(RuntimeError, match="CREDENTIALS_CANNOT_CHANGE_WHILE_RUNNING"):
        engine.update_credentials({"app_id": "12345"})


def test_credentials_route_never_returns_submitted_tokens():
    engine = AutoTradeEngine(SimpleNamespace())
    app = FastAPI()
    app.include_router(autotrade_router(engine))

    response = TestClient(app).post(
        "/api/v1/autotrade/credentials",
        json={"appId": "12345", "demoToken": "not-a-real-secret", "realToken": "not-a-real-live-secret"},
    )

    assert response.status_code == 200
    assert response.json()["appId"] is True
    assert response.json()["demoToken"] is True
    assert response.json()["realToken"] is True
    assert "not-a-real-secret" not in response.text
    assert "not-a-real-live-secret" not in response.text


def test_auto_trade_eligibility_is_limited_to_real_deriv_forex_pairs():
    class Instruments:
        def __init__(self, rows):
            self.rows = rows

        async def find_one(self, query, projection):
            return self.rows.get(query["symbol"])

    rows = {
        "frxEURUSD": {"source": "deriv", "symbol": "frxEURUSD", "market": "forex", "marketDataEligible": True, "signalEligible": True},
        "R_100": {"source": "deriv", "symbol": "R_100", "market": "synthetic_index", "marketDataEligible": True, "signalEligible": True},
        "cryBTCUSD": {"source": "deriv", "symbol": "cryBTCUSD", "market": "cryptocurrency", "marketDataEligible": True, "signalEligible": True},
        "frxGBPUSD": {"source": "deriv", "symbol": "frxGBPUSD", "market": "forex", "marketDataEligible": False, "signalEligible": True},
        "frxUSDJPY": {"source": "deriv", "symbol": "frxUSDJPY", "market": "forex", "marketDataEligible": True, "signalEligible": False},
        "GOLD_OTC": {"source": "deriv", "symbol": "GOLD_OTC", "market": "commodities", "marketDataEligible": True, "signalEligible": True},
        "frxAUDUSD": {"source": "deriv", "symbol": "frxAUDUSD", "market": "forex", "marketDataEligible": True, "signalEligible": True, "is_otc": True},
    }
    engine = AutoTradeEngine(SimpleNamespace(market_instruments=Instruments(rows)))

    async def verify():
        assert await engine.is_deriv_instrument_eligible("frxEURUSD")
        assert not await engine.is_deriv_instrument_eligible("R_100")
        assert not await engine.is_deriv_instrument_eligible("cryBTCUSD")
        assert not await engine.is_deriv_instrument_eligible("frxGBPUSD")
        assert not await engine.is_deriv_instrument_eligible("frxUSDJPY")
        assert not await engine.is_deriv_instrument_eligible("GOLD_OTC")
        assert not await engine.is_deriv_instrument_eligible("frxAUDUSD")
        assert not await engine.is_deriv_instrument_eligible("missing")

    asyncio.run(verify())


def test_stream_auto_trade_rejects_non_forex_signals(monkeypatch):
    symbols = ["frxEURUSD", "R_100", "cryBTCUSD"]
    signal_specs = [("1m", 60, "CALL"), ("5m", 300, "PUT"), ("1m", 60, "CALL")]
    signals = [
        {
            "id": f"signal-{symbol}",
            "symbol": symbol,
            "label": symbol,
            "status": "PENDING",
            "direction": direction,
            "timeframe": timeframe,
            "entryEpoch": 1030,
            "expiryEpoch": 1030 + duration,
            "timeframeSeconds": duration,
            "confidence": 90,
        }
        for symbol, (timeframe, duration, direction) in zip(symbols, signal_specs, strict=True)
    ]

    class Instruments:
        async def find_one(self, query, projection):
            return {
                "source": "deriv",
                "symbol": query["symbol"],
                "market": {
                    "frxEURUSD": "forex",
                    "R_100": "synthetic_index",
                    "cryBTCUSD": "cryptocurrency",
                }[query["symbol"]],
                "marketDataEligible": True,
                "signalEligible": True,
            }

    class Signals:
        async def live(self, source, limit):
            assert source == "deriv"
            assert limit == 500
            return {"upcoming": signals}

    class LiveSignals:
        def __init__(self):
            self.updates = []

        async def update_one(self, query, update):
            self.updates.append(update)

    live_signals = LiveSignals()
    engine = AutoTradeEngine(SimpleNamespace(market_instruments=Instruments(), live_signals=live_signals))
    engine.signal_service = Signals()
    engine.feed_connected = lambda: True
    engine.pair_open = lambda symbol: True
    captured = []

    async def capture(verdict, mode):
        captured.append((verdict, mode))

    engine._stream_task = capture
    monkeypatch.setattr(dashboard_engine, "market_now", lambda: 1000)

    async def exercise():
        await engine._stream_cycle(["demo"])
        await asyncio.sleep(0)

    asyncio.run(exercise())

    assert {verdict["symbol"] for verdict, _ in captured} == {"frxEURUSD"}
    assert {mode for _, mode in captured} == {"demo"}
    for verdict, _ in captured:
        signal = next(item for item in signals if item["symbol"] == verdict["symbol"])
        assert verdict["duration"] == signal["timeframeSeconds"] // 60
        assert verdict["durationUnit"] == "m"
        expected_contract, _ = stream_contract(signal["symbol"], signal["direction"])
        assert verdict["contractType"] == expected_contract
    assert len(live_signals.updates) == 2
