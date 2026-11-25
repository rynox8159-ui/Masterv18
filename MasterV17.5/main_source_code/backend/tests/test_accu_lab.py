import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "main_source_code" / "backend"))

from lab.accu_simulator import (
    growth_percent,
    read_barrier_distance,
    read_barrier_reference_spot,
    read_payout,
    replay_ticks,
)
from lab.analysis import benjamini_hochberg, split_and_analyse
from lab.calibration import DemoTradeCalibration, is_accu_transaction, is_settled_contract
from lab.tick_recorder import TickStore


def test_barrier_and_payout_are_provider_fields_only():
    assert read_barrier_distance({"barrier": 0.25}) == 0.25
    proposal = {
        "contract_details": {
            "barrier_spot_distance": "0.228",
            "high_barrier": "447.238",
            "low_barrier": "446.782",
        }
    }
    assert read_barrier_distance(proposal) == 0.228
    assert read_payout({"potential_payout": 1.45}) == 1.45
    assert read_payout({"contract_details": {"maximum_payout": 6000}}) == 6000
    try:
        read_payout({"ask_price": 1})
    except KeyError as exc:
        assert "PROVIDER_PAYOUT_MISSING" in str(exc)
    else:
        assert False
    try:
        read_barrier_distance({})
    except KeyError as exc:
        assert "PROVIDER_BARRIER_DISTANCE_MISSING" in str(exc)
    else:
        assert False


def test_growth_rate_accepts_decimal_and_percent_forms():
    assert growth_percent(0.01) == 1
    assert growth_percent(5) == 5


def test_replay_marks_a_provider_barrier_cross_as_loss():
    ticks = [
        {"epoch": 10, "quote": 100.0},
        {"epoch": 11, "quote": 100.1},
        {"epoch": 12, "quote": 100.21},
    ]
    outcome = replay_ticks(ticks, 10, 100.0, 1, 0.2, max_ticks=100)
    assert outcome["outcome"] == "LOSS"
    assert outcome["exit_epoch"] == 12


def test_accu_replay_uses_previous_tick_relative_barrier_and_provider_distance():
    ticks = [
        {"epoch": 11, "quote": 100.4},
        {"epoch": 12, "quote": 100.9},
        {"epoch": 13, "quote": 101.41},
    ]

    outcome = replay_ticks(
        ticks,
        10,
        100.0,
        4,
        barrier_distance=0.5,
        barrier_reference_spot=100.0,
    )

    assert outcome["outcome"] == "LOSS"
    assert outcome["exit_epoch"] == 13


def test_accu_take_profit_waits_until_growth_reaches_provider_amount():
    ticks = [
        {"epoch": 11, "quote": 100.0},
        {"epoch": 12, "quote": 100.0},
        {"epoch": 13, "quote": 100.0},
    ]

    outcome = replay_ticks(
        ticks,
        10,
        100.0,
        0.04,
        barrier_distance=1.0,
        take_profit=0.05,
        max_ticks=3,
    )

    assert outcome["outcome"] == "TP"
    assert outcome["ticks"] == 2
    assert outcome["profit"] >= 0.05


def test_tick_store_deduplicates_and_flags_gaps():
    db = ROOT / "data" / "test_accu_lab.sqlite3"
    db.parent.mkdir(parents=True, exist_ok=True)
    db.unlink(missing_ok=True)
    store = TickStore(db)
    assert store.add("R_100", 100, 1.0) == 0
    assert store.add("R_100", 101, 1.1) == 0
    assert store.add("R_100", 108, 1.2) == 1
    store.add("R_100", 108, 1.3)
    assert len(store.history("R_100")) == 3
    store.close()
    db.unlink()


def test_strict_split_and_bh_no_edge_defaults_without_leakage():
    trades = [
        {"symbol": "R_100", "epoch": i, "pnl": (1.0 if i % 2 else -0.5)}
        for i in range(20)
    ]
    rows = split_and_analyse(trades)
    assert len(rows) == 1
    assert rows[0]["n_train"] == 14
    assert rows[0]["n_test"] == 6
    assert rows[0]["edge"] == "INSUFFICIENT_DATA"
    assert benjamini_hochberg([0.01, 0.04]) == [0.02, 0.04]


def test_calibration_does_not_report_mismatch_rate_before_50_comparable_trades():
    class TickStoreStub:
        def history(self, _symbol):
            return []

    calibration = DemoTradeCalibration("app", "demo", tick_store=TickStoreStub())

    result = calibration.compare([])

    assert result["compared"] == 0
    assert result["mismatch_rate"] is None
    assert result["status"] == "INSUFFICIENT_DATA"
    assert result["stop"] is False


def test_calibration_stops_when_50_comparable_trades_exceed_mismatch_limit():
    class TickStoreStub:
        def history(self, _symbol):
            return [{"epoch": 10, "quote": 100.0}]

    contract = {
        "underlying_symbol": "R_100",
        "date_start": 10,
        "entry_spot": 100.0,
        "current_spot": 100.0,
        "exit_spot_time": 10,
        "barrier": 1.0,
        "payout": 1.05,
        "buy_price": 1.0,
        "growth_rate": 0.04,
        "tick_count": 1,
        "profit": -1.0,
        "status": "lost",
    }
    trades = [
        {"contract_id": index, "amount": -1.0, "_contract": contract}
        for index in range(50)
    ]
    calibration = DemoTradeCalibration("app", "demo", tick_store=TickStoreStub())

    result = calibration.compare(trades)

    assert result["compared"] == 50
    assert result["mismatch_rate"] == 1.0
    assert result["status"] == "STOPPED"
    assert result["stop"] is True


def test_calibration_counts_only_settled_contracts():
    assert not is_settled_contract({"status": "open"})
    assert is_settled_contract({"is_sold": 1})
    assert is_settled_contract({"is_expired": 1})
    assert is_settled_contract({"status": "won"})


def test_calibration_recognizes_accu_statement_shortcodes():
    assert is_accu_transaction({"shortcode": "ACCU_R_100_1.00_0_0.04"})
    assert is_accu_transaction({"contract_type": "ACCU"})
    assert not is_accu_transaction({"shortcode": "CALL_FRXUSDCHF_1.00"})
