"""V17.2 auto-trade rule tests: trade == signal, late-order cancel, idempotent orders, 4-loss cooldown, stale feed block.
Runs against the local MongoDB (MONGO_URL) in a throw-away database. No Deriv connection is made (fake executor)."""
import asyncio
import os
import sys
import time
from pathlib import Path

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "main_source_code" / "backend"))

from auto_trade import dashboard_engine as de  # noqa: E402
from auto_trade.dashboard_execution import DerivExecutor  # noqa: E402


class FakeExecutor:
    def __init__(self, profits):
        self.profits = list(profits)
        self.balance = 1000.0
        self.currency = "USD"
        self.account_id = "DOTTEST"
        self.buys = []
        self.sells = []

    async def ensure(self):
        return None

    async def refresh_balance(self):
        return self.balance

    async def proposal(self, symbol, direction, stake, duration, unit, barrier=None, multiplier=None, growth_rate=None, limit_order=None):
        payout = 0 if multiplier is not None else round(stake * 1.9, 2)
        return {"id": "p1", "ask_price": stake, "payout": payout}

    async def buy(self, proposal):
        self.buys.append(proposal)
        return {"contract_id": 1000 + len(self.buys)}

    async def buy_parameters(self, symbol, direction, stake, duration, unit, barrier=None, multiplier=None, growth_rate=None, limit_order=None):
        payout = 0 if multiplier is not None else round(stake * 1.9, 2)
        self.buys.append({"id": "direct", "ask_price": stake, "payout": payout, "contract_type": direction, "multiplier": multiplier})
        return {"contract_id": 1000 + len(self.buys), "buy_price": stake, "payout": payout}

    async def sell(self, contract_id):
        self.sells.append(contract_id)
        return {"sold_for": 1.5, "buy_price": 1.0}

    async def wait_contract(self, contract_id, timeout):
        profit = self.profits.pop(0) if self.profits else 0.9
        return {"profit": profit, "entry_spot": 1.0, "exit_spot": 1.1}

    async def wait_multiplier(self, contract_id, close_epoch, stake, stop_loss_percent, take_profit_percent, now_fn=time.time):
        self.sells.append(contract_id)
        return {"profit": 0.5, "buy_price": 1.0, "sold_for": 1.5, "exitReason": "SIGNAL_EXPIRY"}

    async def _settle_multiplier(self, contract_id, stake):
        profit = self.profits.pop(0) if self.profits else 0.9
        self.sells.append(contract_id)
        return {"profit": profit, "buy_price": stake, "sold_for": stake + profit, "exitReason": "SIGNAL_EXPIRY"}

    async def close(self):
        return None


class FakeFeed:
    state = "DATA_RECEIVING"
    stale_by = 0.0  # seconds; the tick is "fresh" at evaluation time unless a test makes it stale

    @property
    def last_tick(self):
        return time.time() - self.stale_by

    def market_is_open(self, symbol):
        return True


@pytest.fixture
def engine():
    client = AsyncIOMotorClient(os.environ.get("MONGO_URL", "mongodb://127.0.0.1:27017"))
    db = client[f"v172_autotrade_test_{int(time.time() * 1000)}"]
    eng = de.AutoTradeEngine(db)
    eng.deriv_feed = FakeFeed()
    eng.mode = "demo"
    loop = asyncio.new_event_loop()
    loop.run_until_complete(eng.load())
    yield eng, loop
    loop.run_until_complete(client.drop_database(db.name))
    loop.close()


def _verdict(entry_epoch, signal_id="sig-1", direction="CALL"):
    return {
        "symbol": "R_100", "direction": direction, "contractType": direction, "barrier": None, "score": 88.0, "duration": 1, "durationUnit": "m",
        "bucket": "stream:1m", "reason": "test", "label": "Rise", "learned": {}, "signalId": signal_id, "entryEpoch": entry_epoch, "expiryEpoch": entry_epoch + 60,
        "signalPrice": 100.0,
        "signal": {"id": signal_id, "symbol": "R_100", "direction": direction, "timeframe": "1m", "confidence": 88.0, "entryEpoch": entry_epoch, "expiryEpoch": entry_epoch + 60, "signalPrice": 100.0},
    }


@pytest.mark.parametrize(("symbol", "direction", "contract_type"), [
    ("BOOM1000", "CALL", "MULTUP"),
    ("CRASH1000", "PUT", "MULTDOWN"),
    ("cryBTCUSD", "CALL", "MULTUP"),
    ("R_100", "PUT", "PUT"),
])
def test_stream_contract_maps_direction_for_supported_markets(symbol, direction, contract_type):
    actual_type, multiplier = de.stream_contract(symbol, direction)
    assert actual_type == contract_type
    assert multiplier == (100 if contract_type.startswith("MULT") else None)


def test_stream_order_latency_default_matches_documented_limit():
    assert de.DEFAULT_SETTINGS["maxSignalLatencyMs"] == 500


def test_multiplier_proposal_and_buy_payloads_omit_duration():
    class CaptureConnection:
        def __init__(self):
            self.payloads = []

        async def request(self, payload, timeout=15):
            self.payloads.append(payload)
            return {"proposal": {"id": "proposal-1", "ask_price": 1.0}} if payload.get("proposal") else {"buy": {"contract_id": 1}}

    executor = DerivExecutor("app", "token", "demo")
    executor.currency = "USD"
    executor.conn = CaptureConnection()
    loop = asyncio.new_event_loop()
    loop.run_until_complete(executor.proposal("BOOM1000", "MULTUP", 1, 1, "m", multiplier=100))
    loop.run_until_complete(executor.buy_parameters("BOOM1000", "MULTUP", 1, 1, "m", multiplier=100))
    loop.close()

    proposal, buy = executor.conn.payloads
    assert proposal["multiplier"] == 100 and "duration" not in proposal
    assert buy["parameters"]["multiplier"] == 100 and "duration" not in buy["parameters"]


def test_trade_matches_signal_and_logs_latency(engine):
    eng, loop = engine
    eng.executor = FakeExecutor([0.9])
    loop.run_until_complete(eng._place_contract(_verdict(de.market_now() + 0.3), "stream"))
    trade = loop.run_until_complete(eng.db.autotrade_trades.find_one({"signalId": "sig-1"}, {"_id": 0}))
    assert trade["status"] == "CLOSED" and trade["result"] == "WIN"
    assert trade["direction"] == "CALL" and trade["duration"] == "1m"
    assert trade["match"]["sameDirection"] and trade["match"]["sameExpiry"]
    assert trade["match"]["orderLatencyMs"] <= 500
    assert trade["stakeMode"] == "flat"


def test_multiplier_stream_position_closes_at_signal_expiry(engine):
    eng, loop = engine
    eng.executor = FakeExecutor([0.9])
    entry_epoch = de.market_now() + 0.2
    verdict = _verdict(entry_epoch, "multiplier-1", "CALL")
    verdict.update({"symbol": "BOOM1000", "contractType": "MULTUP", "multiplier": 100, "expiryEpoch": entry_epoch + 0.25})
    verdict["signal"].update({"symbol": "BOOM1000", "expiryEpoch": entry_epoch + 0.25, "signalPrice": 100.0})
    loop.run_until_complete(eng.db.market_instruments.insert_one({
        "source": "deriv", "symbol": "BOOM1000", "latestPrice": 100.0, "latestEpoch": de.market_now(),
    }))

    loop.run_until_complete(eng._place_contract(verdict, "stream"))
    trade = loop.run_until_complete(eng.db.autotrade_trades.find_one({"signalId": "multiplier-1"}, {"_id": 0}))
    assert eng.executor.buys[0]["contract_type"] == "MULTUP"
    assert eng.executor.buys[0]["multiplier"] == 100
    assert eng.executor.sells == [1001]
    assert trade["status"] == "CLOSED" and trade["contractType"] == "MULTUP"
    assert trade["duration"] == "1m" and trade["match"]["sameDirection"] and trade["match"]["sameExpiry"]


def test_reconcile_multiplier_position_sells_after_restart(engine):
    eng, loop = engine
    eng.executor = FakeExecutor([])
    loop.run_until_complete(eng.db.autotrade_trades.insert_one({
        "id": "recovered-multiplier", "mode": "demo", "status": "OPEN", "contractId": 2001,
        "executionType": "MULTIPLIER", "closeAtEpoch": de.market_now() - 1, "stake": 1.0,
        "symbol": "BOOM1000", "direction": "CALL", "contractType": "MULTUP", "payoutRate": 0.0,
    }))

    loop.run_until_complete(eng._reconcile_open_trades())
    trade = loop.run_until_complete(eng.db.autotrade_trades.find_one({"id": "recovered-multiplier"}, {"_id": 0}))
    assert eng.executor.sells == [2001]
    assert trade["status"] == "CLOSED" and trade["profit"] == 0.5


def test_late_signal_is_cancelled_not_traded(engine):
    eng, loop = engine
    eng.executor = FakeExecutor([0.9])
    loop.run_until_complete(eng._place_contract(_verdict(de.market_now() - 2.0, "late-1"), "stream"))
    assert eng.executor.buys == []
    row = loop.run_until_complete(eng.db.autotrade_trades.find_one({"signalId": "late-1"}, {"_id": 0}))
    assert row["result"] == "CANCELLED_LATE" and row["match"]["withinLimit"] is False
    state = loop.run_until_complete(eng.get_state("demo"))
    assert state["cancelledLate"] == 1


def test_duplicate_signal_cannot_order_twice(engine):
    eng, loop = engine
    eng.executor = FakeExecutor([0.9, 0.9])
    verdict = _verdict(de.market_now() + 0.2, "dup-1")
    loop.run_until_complete(eng._place_contract(verdict, "stream"))
    loop.run_until_complete(eng._place_contract(verdict, "stream"))
    assert len(eng.executor.buys) == 1
    assert loop.run_until_complete(eng.db.autotrade_trades.count_documents({"signalId": "dup-1"})) == 1


def test_flat_stake_and_four_losses_trigger_cooldown(engine):
    eng, loop = engine
    eng.executor = FakeExecutor([-1.0, -1.0, -1.0, -1.0])
    for i in range(4):
        loop.run_until_complete(eng._place_contract(_verdict(de.market_now() + 0.2, f"loss-{i}"), "stream"))
    assert all(abs(p["ask_price"] - 1.0) < 1e-9 for p in eng.executor.buys), "stake must stay flat after losses"
    state = loop.run_until_complete(eng.get_state("demo"))
    assert state["consecutiveLosses"] == 4 and state["cooldownUntil"] > time.time() + 29 * 60
    assert state["streakPauses"] == 1
    # cooldown active -> next signal is skipped, no order
    loop.run_until_complete(eng._place_contract(_verdict(de.market_now() + 0.2, "after-pause"), "stream"))
    assert len(eng.executor.buys) == 4


def test_stale_feed_blocks_trading(engine):
    eng, loop = engine
    eng.executor = FakeExecutor([0.9])
    eng.deriv_feed.stale_by = 120
    assert eng.feed_connected() is False
    loop.run_until_complete(eng._place_contract(_verdict(de.market_now() + 0.2, "stale-1"), "stream"))
    assert eng.executor.buys == []


def test_settings_reject_martingale_keys(engine):
    eng, loop = engine
    with pytest.raises(ValueError):
        loop.run_until_complete(eng.update_settings({"maxSteps": 3}))
    with pytest.raises(ValueError):
        loop.run_until_complete(eng.update_settings({"signalSource": "layers"}))
    saved = loop.run_until_complete(eng.update_settings({"maxConsecutiveLosses": 4, "streakCooldownMinutes": 30, "maxSignalLatencyMs": 500}))
    assert saved["maxConsecutiveLosses"] == 4 and saved["streakCooldownMinutes"] == 30
