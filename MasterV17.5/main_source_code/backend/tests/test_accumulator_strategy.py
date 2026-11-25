import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "main_source_code" / "backend"))

from auto_trade.dashboard_engine import (
    AutoTradeEngine,
    accumulator_rate_percent,
    accumulator_stake_with_safety,
    is_supported_accumulator_growth_rate,
)
from auto_trade.dashboard_execution import DerivExecutor
import accumulator_signals
from ds.accumulator_strategy import (
    ACCUMULATOR_SHADOW_GROWTH_RATE_PERCENT,
    ACCUMULATOR_SHADOW_MAX_HOLD_TICKS,
    ACCUMULATOR_SHADOW_MAX_HOLD_MINUTES,
    ACCUMULATOR_SHADOW_MIN_SAMPLES,
    ACCUMULATOR_SHADOW_STRATEGY_VERSION,
    ACCUMULATOR_SHADOW_TAKE_PROFIT_PERCENT,
    accumulator_shadow_report,
    evaluate_accumulator,
)


def candles_for_ranges(ranges):
    rows = []
    price = 100.0
    for epoch, fraction in enumerate(ranges):
        half_range = price * fraction / 2
        rows.append({
            "epoch": epoch * 60,
            "open": price,
            "high": price + half_range,
            "low": price - half_range,
            "close": price,
            "completeness": "PROVIDER_OHLC",
        })
    return rows


class TradeHistoryCursor:
    def __init__(self, rows):
        self.rows = rows

    def sort(self, *_):
        return self

    def limit(self, *_):
        return self

    async def to_list(self, *_):
        return self.rows


class TradeHistory:
    def __init__(self, results):
        self.rows = [{"result": result} for result in results]

    def find(self, *_args, **_kwargs):
        return TradeHistoryCursor(self.rows)


def test_accumulator_heuristic_adapts_growth_and_labels_non_probability_score():
    rows = candles_for_ranges([0.002] * 25 + [0.001] * 5)

    signal = evaluate_accumulator("R_100", rows, [1, 2, 3, 4, 5])

    assert signal["contractType"] == "ACCU"
    assert signal["growthRate"] == 5
    assert signal["growthRatePercent"] == 5
    assert signal["takeProfitPercent"] == 5
    assert signal["maxHoldTicks"] == ACCUMULATOR_SHADOW_MAX_HOLD_TICKS
    assert signal["strategyTier"] == "UNVALIDATED_VOLATILITY_HEURISTIC"
    assert 0 <= signal["stabilityScore"] <= 100


def test_accumulator_heuristic_rejects_expanding_volatility_and_unavailable_rates():
    expanding = candles_for_ranges([0.001] * 25 + [0.002] * 5)
    stable = candles_for_ranges([0.002] * 25 + [0.001] * 5)

    assert evaluate_accumulator("R_100", expanding, [1, 2, 3, 4, 5]) is None
    assert evaluate_accumulator("R_100", stable, [6, 8]) is None
    assert evaluate_accumulator("R_100", stable[:29], [1, 2, 3, 4, 5]) is None


def test_accumulator_shadow_report_needs_out_of_sample_samples_and_positive_payout_aware_results():
    profitable = (
        [{"profit": 0.05, "stake": 1.0} for _ in range(170)]
        + [{"profit": -0.02, "stake": 1.0} for _ in range(100)]
        + [{"profit": 0.05, "stake": 1.0} for _ in range(30)]
    )
    report = accumulator_shadow_report(profitable)

    assert len(profitable) == ACCUMULATOR_SHADOW_MIN_SAMPLES
    assert report["readyForLive"] is True
    assert report["status"] == "VALIDATED"
    assert report["wilsonLowerBound"] > report["breakEvenWinRate"]
    assert report["profitFactor"] > 1
    assert report["recent30NetProfit"] > 0
    assert accumulator_shadow_report(profitable[:-1])["readyForLive"] is False

    losing = [{"profit": 0.01, "stake": 1.0} for _ in range(270)] + [
        {"profit": -0.5, "stake": 1.0} for _ in range(30)
    ]
    failed = accumulator_shadow_report(losing)
    assert failed["readyForLive"] is False
    assert failed["netProfit"] < 0
    assert failed["status"] == "SHADOW_VALIDATION_PENDING"


def test_accumulator_universe_uses_open_symbols_with_provider_confirmed_accu_contracts():
    class Deriv:
        requests = []

        async def request(self, payload):
            self.requests.append(payload)
            if "active_symbols" in payload:
                return {"active_symbols": [
                    {"symbol": "frxEURUSD", "underlying_symbol_name": "EUR/USD", "market": "forex", "exchange_is_open": 0},
                    {"symbol": "R_100", "underlying_symbol_name": "Volatility 100 Index", "market": "synthetic_index", "exchange_is_open": 1},
                    {"symbol": "stpRNG", "underlying_symbol_name": "Step Index", "market": "synthetic_index", "exchange_is_open": 1},
                    {"symbol": "frxGBPUSD", "underlying_symbol_name": "GBP/USD (OTC)", "market": "forex", "exchange_is_open": 1},
                ]}
            if payload["contracts_for"] == "R_100":
                return {"contracts_for": {"available": [{
                    "contract_type": "ACCU", "growth_rate_range": [0.01, 0.02, 0.03, 0.04, 0.05],
                }]}}
            return {"contracts_for": {"available": [{"contract_type": "CALL"}]}}

    async def verify():
        service = accumulator_signals.AccumulatorSignalService(SimpleNamespace(db=SimpleNamespace()), Deriv())
        universe = await service.refresh_universe(force=True)
        assert list(universe) == ["R_100"]
        assert universe["R_100"]["market"] == "synthetic_index"
        assert service.deriv.requests[0] == {"active_symbols": "brief"}
        assert service.deriv.requests[1:] == [
            {"contracts_for": "R_100"},
            {"contracts_for": "stpRNG"},
        ]

    asyncio.run(verify())


def test_growth_rate_percent_handles_deriv_decimal_and_percent_formats():
    assert accumulator_rate_percent(0.05) == 5
    assert accumulator_rate_percent(5) == 5


def test_accumulator_growth_rate_is_limited_to_four_or_five_percent():
    assert is_supported_accumulator_growth_rate(0.04)
    assert is_supported_accumulator_growth_rate(4)
    assert is_supported_accumulator_growth_rate(0.05)
    assert is_supported_accumulator_growth_rate(5)
    assert not is_supported_accumulator_growth_rate(3)
    assert not is_supported_accumulator_growth_rate(6)
    assert not is_supported_accumulator_growth_rate(float("nan"))


def test_risk_safety_caps_stake_and_reduces_it_after_account_losses():
    assert accumulator_stake_with_safety(2, 1, 1, {"consecutiveLosses": 0, "dailyPnl": 1}) == 1
    assert accumulator_stake_with_safety(2, 1, 0.5, {"consecutiveLosses": 0, "dailyPnl": 1}) == 0.5
    assert accumulator_stake_with_safety(2, 1, 1, {"consecutiveLosses": 2, "dailyPnl": 1}) == 0.5
    assert accumulator_stake_with_safety(1, 0.35, 0.5, {"consecutiveLosses": 0, "dailyPnl": 0}) == 0.35


def test_accumulator_growth_setting_rejects_values_below_four_percent():
    class Settings:
        async def replace_one(self, *_args, **_kwargs):
            pass

    async def verify():
        engine = AutoTradeEngine(SimpleNamespace(autotrade_settings=Settings()))
        await engine.update_settings({"accumulatorGrowthRate": 4})
        with pytest.raises(ValueError, match="between 4 and 5"):
            await engine.update_settings({"accumulatorGrowthRate": 3})
        await engine.update_settings({"accumulatorTakeProfitPercent": 5, "accumulatorMaxHoldTicks": 5})
        with pytest.raises(ValueError, match="between 1 and 100"):
            await engine.update_settings({"accumulatorMaxHoldTicks": 101})

    asyncio.run(verify())


def test_accumulator_strategy_skips_when_four_to_five_percent_is_unavailable():
    rows = candles_for_ranges([0.002] * 25 + [0.001] * 5)

    assert evaluate_accumulator("R_100", rows, [1, 2, 3]) is None


def test_accumulator_proposal_uses_growth_rate_and_deriv_limit_orders():
    class Connection:
        closed = False

        async def request(self, payload):
            self.payload = payload
            return {"proposal": {"id": "p1", "ask_price": 1}}

    executor = DerivExecutor("123", "token", "demo")
    executor.currency = "USD"
    executor.conn = Connection()

    proposal = asyncio.run(executor.proposal(
        "R_100", "ACCU", 1, 5, "m", growth_rate=5,
        limit_order={"take_profit": 0.2, "stop_loss": 0.5},
    ))

    assert proposal["id"] == "p1"
    assert executor.conn.payload["growth_rate"] == 5
    assert executor.conn.payload["limit_order"] == {"take_profit": 0.2}
    assert "stop_loss" not in executor.conn.payload
    assert "duration" not in executor.conn.payload


def test_accumulator_buy_parameters_include_growth_rate_and_limit_orders():
    class Connection:
        closed = False

        async def request(self, payload):
            self.payload = payload
            return {"buy": {"contract_id": 321}}

    executor = DerivExecutor("123", "token", "demo")
    executor.currency = "USD"
    executor.conn = Connection()

    contract = asyncio.run(executor.buy_parameters(
        "R_100", "ACCU", 1, 5, "m", growth_rate=4,
        limit_order={"take_profit": 0.05, "stop_loss": 0.05},
    ))

    assert contract["contract_id"] == 321
    assert executor.conn.payload["parameters"]["growth_rate"] == 4
    assert executor.conn.payload["parameters"]["limit_order"] == {"take_profit": 0.05}
    assert "stop_loss" not in executor.conn.payload["parameters"]


def test_demo_accumulator_uses_take_profit_only_and_settled_trade_counts_for_validation():
    class Cursor:
        def __init__(self, rows):
            self.rows = rows

        def sort(self, key, direction):
            self.rows.sort(key=lambda row: row.get(key) or "", reverse=direction < 0)
            return self

        def limit(self, count):
            self.rows = self.rows[:count]
            return self

        async def to_list(self, _count):
            return self.rows

    class Collection:
        def __init__(self):
            self.rows = []

        @staticmethod
        def value_at(row, path):
            for part in path.split("."):
                if not isinstance(row, dict):
                    return None
                row = row.get(part)
            return row

        @staticmethod
        def matches(row, query):
            for key, expected in query.items():
                actual = Collection.value_at(row, key)
                if isinstance(expected, dict) and "$in" in expected:
                    if actual not in expected["$in"]:
                        return False
                elif actual != expected:
                    return False
            return True

        async def find_one(self, query, *_args, **_kwargs):
            return next((row for row in self.rows if self.matches(row, query)), None)

        def find(self, query, *_args, **_kwargs):
            return Cursor([row.copy() for row in self.rows if self.matches(row, query)])

        async def insert_one(self, document):
            self.rows.append(document.copy())

        async def update_one(self, query, update, upsert=False):
            row = next((item for item in self.rows if self.matches(item, query)), None)
            if row is None:
                if not upsert:
                    return
                row = dict(query)
                self.rows.append(row)
            for key, value in update.get("$set", {}).items():
                target = row
                parts = key.split(".")
                for part in parts[:-1]:
                    target = target.setdefault(part, {})
                target[parts[-1]] = value
            for key, value in update.get("$inc", {}).items():
                row[key] = row.get(key, 0) + value

        async def find_one_and_update(self, query, update, upsert=False, **_kwargs):
            await self.update_one(query, update, upsert=upsert)
            return await self.find_one(query)

    class DerivConnection:
        closed = False

        def __init__(self):
            self.payloads = []

        async def request(self, payload):
            self.payloads.append(payload)
            if "balance" in payload:
                return {"balance": {"balance": 100, "currency": "USD"}}
            if "proposal" in payload:
                return {"proposal": {"id": "p1", "ask_price": 1.0, "payout": 1.05}}
            if payload.get("buy") == "p1":
                return {"buy": {"contract_id": 321, "buy_price": 1.0, "payout": 1.05}}
            raise AssertionError(f"Unexpected Deriv request: {payload}")

    async def verify():
        trades = Collection()
        state = Collection()
        signals = Collection()

        async def quote(*_args, **_kwargs):
            from auto_trade.dashboard_engine import market_now

            return {"latestPrice": 100, "latestEpoch": market_now()}

        db = SimpleNamespace(
            autotrade_trades=trades,
            autotrade_state=state,
            accumulator_signals=signals,
            market_instruments=SimpleNamespace(find_one=quote),
        )
        engine = AutoTradeEngine(db)
        engine.settings["accumulatorShadowValidationEnabled"] = True
        executor = DerivExecutor("123", "mock-demo-token", "demo")
        executor.account_id = "demo-test"
        executor.currency = "USD"
        executor.balance = 100
        executor.conn = DerivConnection()
        observed_ticks = []

        async def settle_accumulator(_contract_id, _max_hold_seconds, max_hold_ticks=5, now_fn=None):
            observed_ticks.append(max_hold_ticks)
            return {"profit": 0.05, "entry_spot": 100, "exit_spot": 101}

        executor.wait_accumulator = settle_accumulator
        engine.executors["demo"] = executor
        policy = {
            "strategyVersion": ACCUMULATOR_SHADOW_STRATEGY_VERSION,
            "growthRatePercent": ACCUMULATOR_SHADOW_GROWTH_RATE_PERCENT,
            "takeProfitPercent": ACCUMULATOR_SHADOW_TAKE_PROFIT_PERCENT,
            "maxHoldTicks": ACCUMULATOR_SHADOW_MAX_HOLD_TICKS,
            "maxHoldMinutes": ACCUMULATOR_SHADOW_MAX_HOLD_MINUTES,
        }
        await engine._place_contract({
            "symbol": "R_100",
            "direction": "ACCU",
            "contractType": "ACCU",
            "growthRate": 0.04,
            "growthRatePercent": 4,
            "takeProfitPercent": 5,
            "maxHoldTicks": ACCUMULATOR_SHADOW_MAX_HOLD_TICKS,
            "maxHoldMinutes": ACCUMULATOR_SHADOW_MAX_HOLD_MINUTES,
            "duration": ACCUMULATOR_SHADOW_MAX_HOLD_MINUTES,
            "durationUnit": "m",
            "score": 90,
            "reason": "mocked validation candidate",
            "bucket": "accumulator:shadow",
            "label": "Volatility 100",
            "signalId": "accu:R_100:mock",
            "signalPrice": 100,
            "validationPolicy": policy,
            "shadowValidation": True,
        }, source="accumulator_shadow", mode="demo")

        assert observed_ticks == [ACCUMULATOR_SHADOW_MAX_HOLD_TICKS]
        assert len(trades.rows) == 1
        assert trades.rows[0]["mode"] == "demo"
        assert trades.rows[0]["status"] == "CLOSED"
        assert trades.rows[0]["result"] == "WIN"
        assert trades.rows[0]["validationPolicy"] == policy
        assert all(
            "stop_loss" not in str(payload).lower()
            for payload in executor.conn.payloads
        )
        proposal_payload = next(row for row in executor.conn.payloads if "proposal" in row)
        assert proposal_payload["limit_order"] == {"take_profit": 0.05}

        report = await accumulator_signals.AccumulatorSignalService(
            SimpleNamespace(db=db), object(),
        )._shadow_validation_report("R_100")
        assert report["samples"] == 1
        assert report["wins"] == 1

    asyncio.run(verify())


def test_accumulator_shadow_validation_is_explicit_demo_only_and_default_off():
    class Signals:
        universe = {"R_100": {}}
        candidate = {
            "id": "accu:R_100:60", "symbol": "R_100", "status": "PENDING",
            "source": "deriv", "timeframe": "1m", "validationTier": "PAPER_SHADOW",
            "validationReport": {"status": "SHADOW_VALIDATION_PENDING", "readyForLive": False},
            "availableGrowthRates": [0.04, 0.05], "signalPrice": 100,
            "stabilityScore": 90, "autoTradeByMode": {},
        }

        async def live(self, _limit):
            return {"items": [], "paperCandidates": [self.candidate]}

    class Collection:
        def __init__(self):
            self.updates = []

        async def update_one(self, query, update):
            self.updates.append((query, update))

    async def verify():
        db = SimpleNamespace(
            accumulator_signals=Collection(),
            autotrade_trades=TradeHistory([]),
        )
        engine = AutoTradeEngine(db)
        engine.accumulator_signal_service = Signals()
        engine.feed_connected = lambda: True
        engine.pair_open = lambda _symbol: True
        placed = []

        async def place(verdict, source, mode):
            placed.append((verdict, source, mode))

        engine._place_contract = place
        assert engine.settings["accumulatorShadowValidationEnabled"] is False
        await engine._accumulator_cycle(["demo"])
        await asyncio.sleep(0)
        assert placed == []

        engine.settings["accumulatorShadowValidationEnabled"] = True
        await engine._accumulator_cycle(["real"])
        await asyncio.sleep(0)
        assert placed == []

        await engine._accumulator_cycle(["demo"])
        await asyncio.sleep(0)
        assert len(placed) == 1
        verdict, source, mode = placed[0]
        assert source == "accumulator_shadow"
        assert mode == "demo"
        assert verdict["growthRatePercent"] == ACCUMULATOR_SHADOW_GROWTH_RATE_PERCENT
        assert verdict["validationPolicy"] == {
            "strategyVersion": ACCUMULATOR_SHADOW_STRATEGY_VERSION,
            "growthRatePercent": ACCUMULATOR_SHADOW_GROWTH_RATE_PERCENT,
            "takeProfitPercent": ACCUMULATOR_SHADOW_TAKE_PROFIT_PERCENT,
            "maxHoldTicks": ACCUMULATOR_SHADOW_MAX_HOLD_TICKS,
            "maxHoldMinutes": ACCUMULATOR_SHADOW_MAX_HOLD_MINUTES,
        }

    asyncio.run(verify())


@pytest.mark.parametrize("mode", ["real", "both"])
def test_accumulator_shadow_validation_blocks_real_start(mode):
    async def verify():
        engine = AutoTradeEngine(SimpleNamespace())
        engine.settings["accumulatorShadowValidationEnabled"] = True

        with pytest.raises(RuntimeError, match="ACCUMULATOR_SHADOW_VALIDATION_DEMO_ONLY"):
            await engine.start(mode)

    asyncio.run(verify())


def test_accumulator_cycle_dispatches_each_signal_to_demo_and_real():
    class Signals:
        universe = {"frxEURUSD": {}}

        async def live(self, limit):
            return {"items": [{
                "id": "accu:frxEURUSD:60", "symbol": "frxEURUSD", "status": "PENDING",
                "source": "deriv", "timeframe": "1m",
                "validationTier": "LIVE_VALIDATED", "strategyTier": "LIVE_VALIDATED",
                "validationReport": {
                    "status": "VALIDATED", "readyForLive": True,
                    "source": "deriv", "symbol": "frxEURUSD", "timeframe": "1m",
                    "validationMethod": "DEMO_SHADOW",
                    "strategyVersion": ACCUMULATOR_SHADOW_STRATEGY_VERSION,
                    "growthRatePercent": 4,
                    "takeProfitPercent": 5,
                    "maxHoldTicks": ACCUMULATOR_SHADOW_MAX_HOLD_TICKS,
                    "maxHoldMinutes": 5,
                },
                "stabilityScore": 90, "growthRate": 5, "availableGrowthRates": [1, 2, 3, 4, 5],
                "signalPrice": 100, "autoTradeByMode": {},
            }]}

    class Collection:
        def __init__(self):
            self.updates = []

        async def update_one(self, query, update):
            self.updates.append((query, update))

    async def verify():
        db = SimpleNamespace(accumulator_signals=Collection(), autotrade_trades=TradeHistory([]))
        engine = AutoTradeEngine(db)
        engine.accumulator_signal_service = Signals()
        engine.feed_connected = lambda: True
        engine.pair_open = lambda symbol: True
        placed = []

        async def place(verdict, source, mode):
            placed.append((verdict, source, mode))

        engine._place_contract = place
        await engine._accumulator_cycle(["demo", "real"])
        await asyncio.sleep(0)
        assert {mode for _, _, mode in placed} == {"demo", "real"}
        assert all(verdict["contractType"] == "ACCU" and source == "accumulator" for verdict, source, _ in placed)
        assert all(verdict["growthRatePercent"] == 4 and verdict["riskSafetyStakeFactor"] == 0.5 for verdict, _, _ in placed)
        assert len(db.accumulator_signals.updates) == 2

    asyncio.run(verify())


def test_accumulator_cycle_ignores_unvalidated_heuristic_signals():
    async def verify():
        class Signals:
            universe = {"frxEURUSD": {}}

            async def live(self, limit):
                return {"items": [{
                    "id": "accu:frxEURUSD:60", "symbol": "frxEURUSD", "source": "deriv",
                    "timeframe": "1m", "status": "PENDING",
                    "validationTier": "PAPER_SHADOW",
                    "strategyTier": "UNVALIDATED_VOLATILITY_HEURISTIC",
                    "validationReport": {"status": "UNVALIDATED", "readyForLive": False},
                    "stabilityScore": 99, "autoTradeByMode": {},
                }]}

        engine = AutoTradeEngine(SimpleNamespace())
        engine.accumulator_signal_service = Signals()
        engine.feed_connected = lambda: True
        placed = []
        engine._place_contract = lambda *args: placed.append(args)

        await engine._accumulator_cycle(["demo", "real"])
        await asyncio.sleep(0)
        assert placed == []

    asyncio.run(verify())


def test_accumulator_live_filters_out_fake_unvalidated_rows():
    async def verify():
        class Collection:
            def __init__(self):
                self.calls = []

            def find(self, query, projection=None):
                self.calls.append((query, projection))
                return self

            def sort(self, *args, **kwargs):
                return self

            def limit(self, *_args, **_kwargs):
                return self

            async def to_list(self, _limit):
                query = self.calls[-1][0]
                if query.get("validationTier") == "LIVE_VALIDATED":
                    return [{
                        "id": "accu:frxEURUSD:60", "status": "PENDING",
                        "validationTier": "LIVE_VALIDATED",
                        "strategyTier": "LIVE_VALIDATED",
                        "validationReport": {"status": "VALIDATED", "readyForLive": True, "source": "deriv"},
                    }]
                return [{
                    "id": "accu:frxEURUSD:61", "status": "PENDING",
                    "validationTier": "PAPER_SHADOW",
                    "strategyTier": "UNVALIDATED_VOLATILITY_HEURISTIC",
                    "validationReport": {"status": "UNVALIDATED", "readyForLive": False, "source": "deriv"},
                }]

        db = SimpleNamespace(accumulator_signals=Collection(), market_instruments=SimpleNamespace())
        service = accumulator_signals.AccumulatorSignalService(SimpleNamespace(db=db), None)
        service.universe = {"frxEURUSD": {"label": "EUR/USD", "market": "forex", "growthRates": [4, 5]}}
        rows = await service.live(limit=10)
        assert [row["id"] for row in rows["items"]] == ["accu:frxEURUSD:60"]
        assert [row["id"] for row in rows["paperCandidates"]] == ["accu:frxEURUSD:61"]

    asyncio.run(verify())


def test_accumulator_cycle_skips_growth_rate_outside_four_to_five_percent():
    async def verify():
        class Signals:
            universe = {"frxEURUSD": {}}

            async def live(self, limit):
                return {"items": [{
                    "id": "accu:frxEURUSD:60", "symbol": "frxEURUSD", "status": "PENDING",
                    "source": "deriv", "timeframe": "1m",
                    "validationTier": "LIVE_VALIDATED", "strategyTier": "LIVE_VALIDATED",
                    "validationReport": {
                        "status": "VALIDATED", "readyForLive": True,
                        "source": "deriv", "symbol": "frxEURUSD", "timeframe": "1m",
                        "validationMethod": "DEMO_SHADOW",
                        "strategyVersion": ACCUMULATOR_SHADOW_STRATEGY_VERSION,
                        "growthRatePercent": 4,
                        "takeProfitPercent": 5,
                        "maxHoldTicks": ACCUMULATOR_SHADOW_MAX_HOLD_TICKS,
                        "maxHoldMinutes": 5,
                    },
                    "stabilityScore": 90, "growthRate": 3, "availableGrowthRates": [3],
                    "signalPrice": 100, "autoTradeByMode": {},
                }]}

        class Collection:
            def __init__(self):
                self.updates = []

            async def update_one(self, query, update):
                self.updates.append((query, update))

        db = SimpleNamespace(accumulator_signals=Collection())
        engine = AutoTradeEngine(db)
        engine.accumulator_signal_service = Signals()
        engine.feed_connected = lambda: True
        engine.pair_open = lambda symbol: True
        placed = []
        engine._place_contract = lambda *args: placed.append(args)

        await engine._accumulator_cycle(["demo"])
        assert placed == []
        assert db.accumulator_signals.updates[0][1]["$set"]["autoTradeByMode.demo"]["result"] == "SKIPPED"

    asyncio.run(verify())


@pytest.mark.parametrize(
    ("history", "expected_orders", "expected_growth", "expected_factor"),
    [
        ([], 1, 4, 0.5),
        (["WIN"] * 8 + ["LOSS"] * 2, 1, 4, 1.0),
        (["LOSS"] * 3, 0, None, None),
    ],
)
def test_risk_safety_adapts_accumulator_growth_stake_and_pair_selection(
    history, expected_orders, expected_growth, expected_factor,
):
    async def verify():
        class Signals:
            universe = {"frxEURUSD": {}}

            async def live(self, limit):
                return {"items": [{
                    "id": "risk-safe-signal", "symbol": "frxEURUSD", "status": "PENDING",
                    "source": "deriv", "timeframe": "1m",
                    "validationTier": "LIVE_VALIDATED", "strategyTier": "LIVE_VALIDATED",
                    "validationReport": {
                        "status": "VALIDATED", "readyForLive": True,
                        "source": "deriv", "symbol": "frxEURUSD", "timeframe": "1m",
                        "validationMethod": "DEMO_SHADOW",
                        "strategyVersion": ACCUMULATOR_SHADOW_STRATEGY_VERSION,
                        "growthRatePercent": 4,
                        "takeProfitPercent": 5,
                        "maxHoldTicks": ACCUMULATOR_SHADOW_MAX_HOLD_TICKS,
                        "maxHoldMinutes": 5,
                    },
                    "stabilityScore": 90, "growthRate": 5, "availableGrowthRates": [4, 5],
                    "signalPrice": 100, "autoTradeByMode": {},
                }]}

        class Collection:
            def __init__(self):
                self.updates = []

            async def update_one(self, query, update):
                self.updates.append((query, update))

        db = SimpleNamespace(
            accumulator_signals=Collection(),
            autotrade_trades=TradeHistory(history),
        )
        engine = AutoTradeEngine(db)
        engine.accumulator_signal_service = Signals()
        engine.feed_connected = lambda: True
        engine.pair_open = lambda symbol: True
        placed = []
        engine._place_contract = lambda verdict, source, mode: placed.append((verdict, source, mode))

        await engine._accumulator_cycle(["demo"])
        await asyncio.sleep(0)
        assert len(placed) == expected_orders
        if expected_orders:
            verdict, source, mode = placed[0]
            assert source == "accumulator" and mode == "demo"
            assert verdict["growthRatePercent"] == expected_growth
            assert verdict["riskSafetyStakeFactor"] == expected_factor
        else:
            reason = db.accumulator_signals.updates[-1][1]["$set"]["autoTradeByMode.demo"]["reason"]
            assert "recent losses" in reason

    asyncio.run(verify())


def test_accumulator_order_placement_rejects_growth_rate_outside_band():
    async def verify():
        engine = AutoTradeEngine(SimpleNamespace())
        engine.executors["demo"] = object()
        with pytest.raises(ValueError, match="between 4% and 5%"):
            await engine._place_contract(
                {"contractType": "ACCU", "growthRate": 3},
                source="accumulator",
                mode="demo",
            )

    asyncio.run(verify())


def test_accumulator_live_board_separates_validated_and_paper_candidates():
    class Cursor:
        def sort(self, *_):
            return self

        def limit(self, *_):
            return self

        async def to_list(self, *_):
            return []

    class Collection:
        queries = []

        def find(self, query, *_):
            self.queries.append(query)
            return Cursor()

    async def verify():
        signals = Collection()
        service = accumulator_signals.AccumulatorSignalService(
            SimpleNamespace(db=SimpleNamespace(accumulator_signals=signals)),
            None,
        )
        service.universe = {
            "R_100": {
                "label": "Volatility 100 Index",
                "market": "synthetic_index",
                "growthRates": [0.04, 0.05],
            },
        }
        board = await service.live()
        live_query, paper_query = signals.queries
        assert live_query["validationTier"] == "LIVE_VALIDATED"
        assert live_query["strategyTier"] == "LIVE_VALIDATED"
        assert live_query["validationReport.status"] == "VALIDATED"
        assert live_query["validationReport.readyForLive"] is True
        assert paper_query["validationTier"] == "PAPER_SHADOW"
        assert paper_query["validationReport.readyForLive"] is False
        assert board["items"] == []
        assert board["paperCandidates"] == []
        assert board["supportedSymbols"] == [{
            "symbol": "R_100",
            "label": "Volatility 100 Index",
            "market": "synthetic_index",
            "growthRates": [0.04, 0.05],
        }]
        assert board["unvalidatedCandidates"] == 0
        assert board["lastScanAt"] is None
        assert service.last_scan_at is None
        assert service.last_candidate_count == 0

    asyncio.run(verify())


def test_accumulator_scan_promotes_only_after_demo_shadow_gate_passes(monkeypatch):
    now = 1800
    monkeypatch.setattr(accumulator_signals, "market_now", lambda: now)

    class Cursor:
        def sort(self, *_):
            return self

        def limit(self, *_):
            return self

        async def to_list(self, *_):
            return candles_for_ranges([0.002] * 25 + [0.001] * 5)

    class Instruments:
        async def find_one(self, *_args, **_kwargs):
            return {
                "source": "deriv", "symbol": "R_100", "market": "synthetic_index",
                "marketDataEligible": True, "latestEpoch": now, "latestPrice": 100,
                "exchangeIsOpen": True, "available": True,
            }

    class Signals:
        def __init__(self):
            self.inserted = None

        async def update_one(self, query, update, upsert):
            self.inserted = update["$setOnInsert"]
            return SimpleNamespace(upserted_id="new-signal")

    class Store:
        def __init__(self):
            self.db = SimpleNamespace(
                market_instruments=Instruments(),
                market_candles=SimpleNamespace(find=lambda *_args, **_kwargs: Cursor()),
                accumulator_signals=Signals(),
            )
            self.events = []

        async def event(self, *args):
            self.events.append(args)

    async def verify():
        store = Store()
        service = accumulator_signals.AccumulatorSignalService(store, object())
        service.universe = {
            "R_100": {"label": "Volatility 100 Index", "market": "synthetic_index", "growthRates": [0.04, 0.05]},
        }
        service.universe_updated_at = now

        async def validated(_symbol):
            return {
                "status": "VALIDATED", "readyForLive": True, "samples": 300,
                "minSamples": 300, "source": "deriv", "symbol": "R_100",
                "timeframe": "1m", "validationMethod": "DEMO_SHADOW",
                "strategyVersion": ACCUMULATOR_SHADOW_STRATEGY_VERSION,
                "growthRatePercent": 4, "takeProfitPercent": 5,
                "maxHoldTicks": ACCUMULATOR_SHADOW_MAX_HOLD_TICKS,
                "maxHoldMinutes": 5,
            }

        service._shadow_validation_report = validated
        items = await service.scan_once()

        assert items[0]["validationTier"] == "LIVE_VALIDATED"
        assert items[0]["strategyTier"] == "LIVE_VALIDATED"
        assert items[0]["growthRatePercent"] == 4
        assert items[0]["validationReport"]["samples"] == 300
        assert store.db.accumulator_signals.inserted["validationReport"]["readyForLive"] is True

    asyncio.run(verify())


@pytest.mark.parametrize(
    ("signal_source", "expected_cycle"),
    [("accumulator", "accumulator"), ("binary", "binary")],
)
def test_autotrade_cycle_uses_selected_signal_source(signal_source, expected_cycle):
    async def verify():
        engine = AutoTradeEngine(SimpleNamespace())
        engine.settings["signalSource"] = signal_source
        calls = []

        async def risk_gate():
            return ["demo", "real"]

        async def accumulator_cycle(modes):
            calls.append(("accumulator", modes))

        async def binary_cycle(modes):
            calls.append(("binary", modes))

        engine._risk_gate = risk_gate
        engine._accumulator_cycle = accumulator_cycle
        engine._stream_cycle = binary_cycle
        await engine._cycle()
        assert calls == [(expected_cycle, ["demo", "real"])]

    asyncio.run(verify())


def test_autotrade_signal_source_accepts_only_supported_profiles():
    assert AutoTradeEngine(SimpleNamespace()).settings["signalSource"] == "accumulator"

    async def verify():
        class Settings:
            async def replace_one(self, query, document, upsert):
                return None

        engine = AutoTradeEngine(SimpleNamespace(autotrade_settings=Settings()))
        for source in ("accumulator", "binary"):
            result = await engine.update_settings({"signalSource": source})
            assert result["signalSource"] == source
        for source in ("stream", "brain"):
            with pytest.raises(ValueError, match="accumulator.*binary"):
                await engine.update_settings({"signalSource": source})

    asyncio.run(verify())


def test_autotrade_load_preserves_saved_binary_signal_profile():
    class Collection:
        async def create_index(self, *args, **kwargs):
            return None

        async def find_one(self, query, projection):
            if query.get("id") == "settings":
                return {"signalSource": "binary"}
            return None

    async def verify():
        collection = Collection()
        engine = AutoTradeEngine(SimpleNamespace(
            autotrade_trades=collection,
            autotrade_settings=collection,
        ))
        await engine.load()
        assert engine.settings["signalSource"] == "binary"

    asyncio.run(verify())


def test_accumulator_scan_retries_when_persisting_a_setup_fails(monkeypatch):
    now = 1800
    monkeypatch.setattr(accumulator_signals, "market_now", lambda: now)

    class Cursor:
        def sort(self, *_):
            return self

        def limit(self, *_):
            return self

        async def to_list(self, *_):
            return candles_for_ranges([0.002] * 25 + [0.001] * 5)

    class Collection:
        def __init__(self):
            self.writes = 0

        async def find_one(self, query, *_):
            return {
                "source": "deriv", "symbol": query["symbol"], "market": "forex",
                "marketDataEligible": True, "latestEpoch": now, "latestPrice": 100,
            }

        def find(self, *_):
            return Cursor()

        async def update_one(self, *_args, **_kwargs):
            self.writes += 1
            if self.writes == 1:
                raise RuntimeError("temporary storage failure")
            return SimpleNamespace(upserted_id="new-signal")

    class Store:
        def __init__(self):
            self.db = SimpleNamespace(
                market_instruments=Collection(),
                market_candles=Collection(),
                accumulator_signals=Collection(),
            )

        async def event(self, *_):
            pass

    async def verify():
        service = accumulator_signals.AccumulatorSignalService(Store(), object())
        service.universe = {"frxEURUSD": {"label": "EUR/USD", "growthRates": [1, 2, 3, 4, 5]}}
        service.universe_updated_at = now

        try:
            await service.scan_once()
        except RuntimeError as exc:
            assert str(exc) == "temporary storage failure"
        else:
            raise AssertionError("storage failure should be surfaced")
        assert "frxEURUSD" not in service.last_evaluated

        items = await service.scan_once()
        assert len(items) == 1
        assert service.last_evaluated["frxEURUSD"] == items[0]["candleEpoch"]
        assert service.scan_diagnostics == {
            "supportedPairs": 1,
            "freshQuotes": 1,
            "closedCandleWindows": 1,
            "recentCandidates": 1,
        }

    asyncio.run(verify())
