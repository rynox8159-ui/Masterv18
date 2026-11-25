import math

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from ds.feature_engineering import (
    _atr,
    _bbands,
    _ema,
    _macd,
    _rsi,
    _stochastic,
)


_LAB_FEATURE_NAMES = (
    "atr_ratio",
    "body_ratio",
    "close_above_open",
    "close_below_open",
    "close_vs_ema_21",
    "ema_5",
    "ema_21",
    "ema_55",
    "macd",
    "macd_hist",
    "macd_signal",
    "pct_b",
    "rsi_14",
    "roc_5",
    "roc_20",
    "stoch_d",
    "stoch_k",
    "volatility_std_20",
)


def _lab_indicator_features(candles):
    closes = [float(row["close"]) for row in candles]
    opens = [float(row["open"]) for row in candles]
    highs = [float(row["high"]) for row in candles]
    lows = [float(row["low"]) for row in candles]
    ema_5 = _ema(closes, 5)
    ema_21 = _ema(closes, 21)
    ema_55 = _ema(closes, 55)
    macd, macd_signal, macd_hist = _macd(closes)
    stoch_k, stoch_d = _stochastic(highs, lows, closes)
    _, _, _, _, pct_b = _bbands(closes)
    atr = _atr(highs, lows, closes)
    close = closes[-1]
    features = {
        "atr_ratio": atr / max(abs(close), 1e-9),
        "body_ratio": abs(close - opens[-1]) / max(highs[-1] - lows[-1], 1e-9),
        "close_above_open": float(close >= opens[-1]),
        "close_below_open": float(close < opens[-1]),
        "close_vs_ema_21": close - ema_21,
        "ema_5": ema_5,
        "ema_21": ema_21,
        "ema_55": ema_55,
        "macd": macd,
        "macd_hist": macd_hist,
        "macd_signal": macd_signal,
        "pct_b": pct_b,
        "rsi_14": _rsi(closes, 14),
        "roc_5": (close - closes[-5]) / max(abs(closes[-5]), 1e-9),
        "roc_20": (close - closes[-20]) / max(abs(closes[-20]), 1e-9),
        "stoch_d": stoch_d,
        "stoch_k": stoch_k,
        "volatility_std_20": float(np.std(closes[-20:])),
    }
    return [float(features[name]) for name in _LAB_FEATURE_NAMES]


def wilson_lower_bound(hits, total, z=1.96):
    if total <= 0:
        return 0.0
    p = hits / total
    denominator = 1 + z*z/total
    centre = p + z*z/(2*total)
    margin = z*math.sqrt(p*(1-p)/total + z*z/(4*total*total))
    return (centre - margin)/denominator


def wilson_interval(hits, total, z=1.96):
    if total <= 0:
        return 0.0, 1.0
    p = hits / total
    denominator = 1 + z*z/total
    centre = (p + z*z/(2*total))/denominator
    margin = z*math.sqrt(p*(1-p)/total + z*z/(4*total*total))/denominator
    return max(0.0, centre-margin), min(1.0, centre+margin)


def purged_walk_forward_splits(n, folds=5, min_train=500, embargo=1):
    """Expanding-window, purged and embargoed chronological folds."""
    if n < min_train + folds:
        return []
    remaining = n - min_train
    test_size = max(20, remaining // folds)
    splits = []
    test_start = min_train
    while test_start + test_size <= n and len(splits) < folds:
        purge_start = max(0, test_start - embargo)
        train_end = purge_start
        train = np.arange(0, train_end)
        test = np.arange(test_start + embargo, min(n, test_start + embargo + test_size))
        if len(train) >= 200 and len(test) >= 20:
            splits.append((train, test))
        test_start += test_size
    return splits


def _indicator_matrix(candles, horizon=1, seconds=60):
    ordered = sorted(candles, key=lambda row: int(row["epoch"]))
    X_rows = []
    y = []
    indices = []
    for i in range(200, len(ordered) - horizon, horizon):
        if any(
            int(ordered[j + 1]["epoch"]) - int(ordered[j]["epoch"]) != seconds
            for j in range(max(0, i - 200), i + horizon)
        ):
            continue
        window = ordered[max(0, i-200):i+1]
        X_rows.append(_lab_indicator_features(window))
        y.append(1 if float(ordered[i+horizon]["close"]) > float(ordered[i]["close"]) else 0)
        indices.append(i)
    if not X_rows:
        return np.empty((0, 0)), np.empty((0,), dtype=int), []
    X = np.asarray(X_rows, dtype=float)
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
    return X, np.asarray(y, dtype=int), indices


def _fit_lightgbm(X_train, y_train, X_test):
    try:
        import lightgbm as lgb
        model = lgb.LGBMClassifier(
            n_estimators=100,
            learning_rate=0.05,
            num_leaves=15,
            max_depth=-1,
            verbosity=-1,
            random_state=7,
            n_jobs=1,
        )
        model.fit(X_train, y_train)
        return model.predict_proba(X_test)[:, 1]
    except Exception:
        return None


def _new_model(name):
    if name == "lightgbm":
        import lightgbm as lgb
        return lgb.LGBMClassifier(
            n_estimators=100, learning_rate=0.05, num_leaves=15,
            verbosity=-1, random_state=7, n_jobs=1,
        )
    return make_pipeline(
        StandardScaler(),
        LogisticRegression(max_iter=1000, C=1.0, solver="lbfgs"),
    )


def _hard_predictions(model, X):
    return (model.predict_proba(X)[:, 1] >= 0.5).astype(int)


def locked_walk_forward(candles, horizon=1, seconds=60, folds=4, embargo=None):
    """Choose a model only on pre-cutoff folds, then evaluate the untouched final 20%."""
    candles = sorted(candles, key=lambda row: int(row["epoch"]))
    horizon = max(1, int(horizon))
    embargo = max(horizon, int(embargo or horizon))
    X, y, indices = _indicator_matrix(candles, horizon=horizon, seconds=seconds)
    count = len(y)
    candle_cutoff = int(len(candles)*0.8)
    result = {
        "status": "INSUFFICIENT_SAMPLES",
        "candles": len(candles),
        "oos": None,
        "locked": None,
        "model": None,
        "trainingCutoffEpoch": int(candles[candle_cutoff]["epoch"]) if candle_cutoff < len(candles) else None,
        "lockedStartEpoch": None,
        "featureBeforeEntry": True,
        "labelOverlap": False,
        "folds": 0,
        "permutationData": None,
    }
    if candle_cutoff >= len(candles):
        return result
    dev = np.flatnonzero(np.asarray(indices) < candle_cutoff)
    locked = np.flatnonzero(np.asarray(indices) >= candle_cutoff + embargo)
    if len(locked) < 20:
        return result

    model_names = ["logistic"]
    try:
        import lightgbm  # noqa: F401
        model_names.append("lightgbm")
    except ImportError:
        pass

    test_size = max(20, len(dev)//(folds+1))
    fold_predictions = {name: [] for name in model_names}
    fold_truth = []
    completed_folds = 0
    for fold in range(folds):
        test_start = len(dev) - (folds-fold)*test_size
        test_idx = dev[test_start:test_start+test_size]
        if len(test_idx) < 20:
            continue
        first_test_candle = indices[int(test_idx[0])]
        train_idx = np.asarray([
            idx for idx in dev[:test_start]
            if indices[int(idx)] + horizon + embargo < first_test_candle
        ], dtype=int)
        if len(train_idx) < 200 or len(np.unique(y[train_idx])) < 2:
            continue
        fold_truth.extend(y[test_idx].tolist())
        fitted = {}
        for name in model_names:
            try:
                model = _new_model(name)
                model.fit(X[train_idx], y[train_idx])
                fitted[name] = _hard_predictions(model, X[test_idx]).tolist()
            except Exception:
                fitted[name] = None
        if any(prediction is not None for prediction in fitted.values()):
            completed_folds += 1
            for name in model_names:
                if fitted[name] is not None:
                    fold_predictions[name].extend(fitted[name])

    valid_models = {
        name: predictions for name, predictions in fold_predictions.items()
        if len(predictions) == len(fold_truth) and predictions
    }
    if not valid_models:
        return result
    chosen = max(
        valid_models,
        key=lambda name: float(np.mean(np.asarray(valid_models[name]) == np.asarray(fold_truth))),
    )

    train_idx = np.asarray([
        idx for idx in range(count)
        if indices[idx] + horizon + embargo < candle_cutoff
    ], dtype=int)
    if len(train_idx) < 200 or len(np.unique(y[train_idx])) < 2:
        return result
    final_model = _new_model(chosen)
    final_model.fit(X[train_idx], y[train_idx])
    predictions = _hard_predictions(final_model, X[locked])
    truth = y[locked]
    oos_hits = int(np.sum(np.asarray(valid_models[chosen]) == np.asarray(fold_truth)))
    locked_hits = int(np.sum(predictions == truth))
    result.update({
        "status": "OK",
        "oos": {
            "samples": len(fold_truth),
            "hits": oos_hits,
            "winRate": oos_hits/len(fold_truth),
        },
        "locked": {
            "samples": int(len(truth)),
            "hits": locked_hits,
            "winRate": locked_hits/len(truth),
            "predictions": predictions.astype(int).tolist(),
            "truth": truth.astype(int).tolist(),
            "featureTimestamps": [
                int(candles[indices[int(idx)]]["epoch"]) + seconds
                for idx in locked
            ],
            "entryTimestamps": [
                int(candles[indices[int(idx)]]["epoch"]) + seconds + 1
                for idx in locked
            ],
        },
        "model": chosen,
        "folds": completed_folds,
        "lockedStartEpoch": (
            int(candles[indices[int(locked[0])]]["epoch"]) + seconds + 1
            if len(locked) else None
        ),
        "featureBeforeEntry": all(
            feature_time < entry_time
            for feature_time, entry_time in zip(
                [
                    int(candles[indices[int(idx)]]["epoch"]) + seconds
                    for idx in locked
                ],
                [
                    int(candles[indices[int(idx)]]["epoch"]) + seconds + 1
                    for idx in locked
                ],
            )
        ),
        "labelOverlap": any(
            indices[idx] + horizon >= candle_cutoff for idx in train_idx
        ),
        "permutationData": {
            "X_train": X[train_idx].copy(),
            "y_train": y[train_idx].copy(),
            "X_test": X[locked].copy(),
            "y_test": truth.copy(),
        },
    })
    return result


def shuffled_label_test(candles, horizon, seconds, model_name, permutations=1000, seed=7):
    """Refit the chosen model on shuffled training labels, testing against locked truth."""
    candles = sorted(candles, key=lambda row: int(row["epoch"]))
    horizon = max(1, int(horizon))
    X, y, indices = _indicator_matrix(candles, horizon=horizon, seconds=seconds)
    cutoff = int(len(candles)*0.8)
    embargo = horizon
    train_idx = np.asarray([
        idx for idx in range(len(y))
        if indices[idx] + horizon + embargo < cutoff
    ], dtype=int)
    locked_idx = np.asarray([
        idx for idx in range(len(y))
        if indices[idx] >= cutoff + embargo
    ], dtype=int)
    if len(train_idx) < 200 or len(locked_idx) < 20:
        return {"permutations": 0, "pass_count": 0, "p_value": None}
    X_train, y_train = X[train_idx], y[train_idx]
    X_test, y_test = X[locked_idx], y[locked_idx]
    real_model = _new_model(model_name)
    real_model.fit(X_train, y_train)
    observed_hits = int(np.sum(_hard_predictions(real_model, X_test) == y_test))
    rng = np.random.default_rng(seed)
    passes = 0
    completed = 0
    for _ in range(int(permutations)):
        try:
            model = _new_model(model_name)
            model.fit(X_train, rng.permutation(y_train))
            hits = int(np.sum(_hard_predictions(model, X_test) == y_test))
            passes += int(hits >= observed_hits)
            completed += 1
        except Exception:
            continue
    return {
        "permutations": completed,
        "pass_count": passes,
        "p_value": (passes+1)/(completed+1) if completed else None,
        "observed_hits": observed_hits,
        "test_samples": len(y_test),
    }


def binary_walk_forward(candles, folds=5, embargo=1):
    X, y, indices = _indicator_matrix(candles)
    n = len(y)
    result = {
        "status": "INSUFFICIENT_SAMPLES",
        "oosSamples": 0,
        "oosHitRate": None,
        "oosLowerBound": 0.0,
        "candles": len(candles),
        "folds": 0,
        "baseline": "logistic",
        "model": None,
    }
    if n < 520:
        return result
    splits = purged_walk_forward_splits(n, folds=folds, min_train=500, embargo=embargo)
    if not splits:
        return result
    hits = 0
    total = 0
    model_used = "logistic"
    lightgbm_predictions = []
    lightgbm_truth = []
    for train, test in splits:
        if len(np.unique(y[train])) < 2:
            continue
        baseline = make_pipeline(
            StandardScaler(),
            LogisticRegression(max_iter=2000, C=1.0, solver="lbfgs"),
        )
        try:
            baseline.fit(X[train], y[train])
            logistic_probability = baseline.predict_proba(X[test])[:, 1]
            logistic_prediction = (logistic_probability >= 0.5).astype(int)
            hits += int(np.sum(logistic_prediction == y[test]))
            total += int(len(test))
        except Exception:
            continue

        lgb_probability = _fit_lightgbm(X[train], y[train], X[test])
        if lgb_probability is not None:
            lightgbm_predictions.extend((lgb_probability >= 0.5).astype(int).tolist())
            lightgbm_truth.extend(y[test].tolist())

    if total:
        lgb_hits = int(np.sum(np.asarray(lightgbm_predictions) == np.asarray(lightgbm_truth))) if lightgbm_predictions else 0
        if lightgbm_predictions and lgb_hits / len(lightgbm_predictions) > hits / total:
            hits = lgb_hits
            total = len(lightgbm_predictions)
            model_used = "lightgbm"
    if not total:
        return result
    hit_rate = hits / total
    result.update({
        "status": "OK",
        "oosSamples": total,
        "oosHitRate": round(hit_rate, 6),
        "oosLowerBound": round(wilson_lower_bound(hits, total), 6),
        "folds": len(splits),
        "model": model_used,
    })
    return result


def benjamini_hochberg(pvalues):
    m = len(pvalues)
    if not m:
        return []
    order = sorted(range(m), key=lambda i: pvalues[i])
    adjusted = [1.0]*m
    running = 1.0
    for position in range(m-1, -1, -1):
        original = order[position]
        rank = position + 1
        running = min(running, pvalues[original]*m/rank)
        adjusted[original] = running
    return adjusted
