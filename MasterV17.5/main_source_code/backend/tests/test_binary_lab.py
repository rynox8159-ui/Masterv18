import sys
from pathlib import Path

import numpy as np
from ds.feature_engineering import compute_indicator_bundle, _stochastic

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "main_source_code" / "backend"))

from lab.binary_lab import (
    _LAB_FEATURE_NAMES,
    _indicator_matrix,
    _lab_indicator_features,
    binary_walk_forward,
    locked_walk_forward,
    purged_walk_forward_splits,
    wilson_lower_bound,
)
from lab.binary_report import _get_candles, _permutation_control


def test_purged_splits_are_chronological_and_embargoed():
    splits = purged_walk_forward_splits(1200, folds=5, min_train=500, embargo=1)
    assert splits
    for train, test in splits:
        assert train[-1] < test[0]
        assert test[0] - train[-1] >= 2


def test_binary_walk_forward_fail_closed_below_data():
    candles = [
        {"epoch": i, "open": 1+i*0.001, "high": 1+i*0.002, "low": 1+i*0.0005, "close": 1+i*0.001}
        for i in range(300)
    ]
    result = binary_walk_forward(candles)
    assert result["oosSamples"] == 0
    assert result["oosLowerBound"] == 0.0


def test_wilson_lower_bound_is_within_zero_to_one():
    assert 0 <= wilson_lower_bound(600, 1000) <= 1


def test_indicator_features_exclude_discontinuous_lookback_windows():
    candles = [
        {
            "epoch": i * 60 + (60 if i >= 250 else 0),
            "open": 100 + i * 0.001,
            "high": 101 + i * 0.001,
            "low": 99 + i * 0.001,
            "close": 100 + i * 0.001 + (i % 3) * 0.01,
        }
        for i in range(500)
    ]

    _, _, indices = _indicator_matrix(candles, horizon=1, seconds=60)

    assert 220 in indices
    assert not any(250 <= index < 450 for index in indices)
    assert 450 in indices
    _, _, sparse_indices = _indicator_matrix(candles, horizon=15, seconds=60)
    assert all(
        (later - earlier) % 15 == 0
        for earlier, later in zip(sparse_indices, sparse_indices[1:])
    )


def test_locked_metrics_use_explicit_pre_entry_timestamps_and_purged_labels():
    candles = [
        {
            "epoch": i * 60,
            "open": 100 + i * 0.001,
            "high": 101 + i * 0.001,
            "low": 99 + i * 0.001,
            "close": 100 + i * 0.001 + (i % 3) * 0.01,
        }
        for i in range(3000)
    ]

    result = locked_walk_forward(candles, horizon=2, seconds=60, folds=3, embargo=2)

    assert result["locked"] is not None
    assert result["featureBeforeEntry"] is True
    assert result["labelOverlap"] is False
    assert all(
        feature < entry
        for feature, entry in zip(
            result["locked"]["featureTimestamps"],
            result["locked"]["entryTimestamps"],
        )
    )
    assert result["lockedStartEpoch"] == result["locked"]["entryTimestamps"][0]


def test_locked_split_does_not_discard_usable_data_below_500_dev_samples():
    candles = []
    for index in range(12_000):
        gap_offset = 60 if index >= 6_000 else 0
        close = 100 + index * 0.0001 + (index % 7) * 0.01
        candles.append({
            "epoch": index * 60 + gap_offset,
            "open": close - 0.02,
            "high": close + 0.1,
            "low": close - 0.1,
            "close": close,
        })

    result = locked_walk_forward(
        candles, horizon=15, seconds=60, folds=4, embargo=15,
    )

    assert result["locked"] is not None
    assert result["oos"]["samples"] > 0


def test_report_candle_query_selects_newest_window_then_restores_chronology():
    rows = [{"epoch": epoch} for epoch in range(12_001)]

    class Cursor:
        def sort(self, key, direction):
            self.rows = sorted(rows, key=lambda row: row[key], reverse=direction < 0)
            return self

        def limit(self, count):
            self.rows = self.rows[:count]
            return self

        def __iter__(self):
            return iter(self.rows)

    class Collection:
        def find(self, query, projection):
            return Cursor()

    class Database:
        market_candles = Collection()

    candles = _get_candles(Database(), "frxEURGBP", "1m")

    assert len(candles) == 12_000
    assert candles[0]["epoch"] == 1
    assert candles[-1]["epoch"] == 12_000


def test_shuffled_label_control_refits_models_on_each_permutation(monkeypatch):
    models = []

    class Model:
        def fit(self, features, labels):
            self.labels = labels.copy()
            return self

        def predict_proba(self, features):
            return np.tile([0.1, 0.9], (len(features), 1))

    def new_model(_name):
        model = Model()
        models.append(model)
        return model

    monkeypatch.setattr("lab.binary_report._new_model", new_model)
    row = {
        "symbol": "frxEURGBP",
        "timeframe": "1m",
        "model": "lightgbm",
        "lockedMetrics": {"samples": 300},
        "payout": {
            "CALL": {"breakeven": 0.5},
            "PUT": {"breakeven": 0.5},
        },
        "permutation_data": {
            "X_train": np.ones((20, 1)),
            "y_train": np.array([0, 1] * 10),
            "X_test": np.ones((300, 1)),
            "y_test": np.ones(300, dtype=int),
        },
    }

    result = _permutation_control([row], permutations=3, seed=2)

    assert result["permutations"] == 3
    assert result["eligible_records"] == 1
    assert len(models) == 3
    assert all(len(model.labels) == 20 for model in models)


def test_stochastic_feature_matches_last_three_full_window_values():
    rng = np.random.default_rng(7)
    closes = rng.normal(100, 1, 200).tolist()
    highs = [value + 1 for value in closes]
    lows = [value - 1 for value in closes]
    k_values = []
    for end in range(13, len(closes)):
        start = end - 13
        low = min(lows[start:end + 1])
        high = max(highs[start:end + 1])
        k_values.append(50 if high == low else 100 * ((closes[end] - low) / (high - low)))
    expected = k_values[-1], float(np.mean(k_values[-3:]))

    assert _stochastic(highs, lows, closes) == expected


def test_lab_feature_subset_matches_existing_indicator_bundle():
    rng = np.random.default_rng(42)
    closes = 100 + np.cumsum(rng.normal(0, 0.1, 200))
    candles = [
        {
            "epoch": index * 60,
            "open": float(close - rng.normal(0, 0.02)),
            "high": float(close + 0.1),
            "low": float(close - 0.1),
            "close": float(close),
        }
        for index, close in enumerate(closes)
    ]
    bundle = compute_indicator_bundle(candles)

    assert _lab_indicator_features(candles) == [
        float(bundle[name]) for name in _LAB_FEATURE_NAMES
    ]
