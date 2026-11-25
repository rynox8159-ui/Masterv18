import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "main_source_code" / "backend"))

from ds.market_behaviour_finder import MarketBehaviourFinder


def _candle(epoch, opening, high, low, close):
    return {
        "epoch": epoch,
        "open": opening,
        "high": high,
        "low": low,
        "close": close,
    }


def test_finder_reports_price_gap_and_keeps_regular_cadence():
    candles = [_candle(index * 60, 100, 101, 99, 100) for index in range(6)]
    candles[-1] = _candle(300, 103, 104, 102, 103)

    report = MarketBehaviourFinder().scan(candles)

    assert report["gap"]["direction"] == "GAP_UP"
    assert report["gap"]["ratio"] == 1.5
    assert report["gap"]["temporalGap"] is False


def test_finder_marks_failed_breakout_and_temporal_gap():
    candles = [_candle(index * 60, 100, 101, 99, 100) for index in range(6)]
    candles[-1] = _candle(600, 100, 102, 99, 100.5)

    report = MarketBehaviourFinder().scan(candles)

    assert report["breakout"]["breakout"] == "FAILED_UPWARD_BREAK"
    assert report["gap"]["temporalGap"] is True
