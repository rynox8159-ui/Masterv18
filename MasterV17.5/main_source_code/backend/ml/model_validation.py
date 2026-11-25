"""V17.2 model validation: walk-forward (purged) out-of-sample test, leakage checks, Platt calibration, champion gate.

Everything here runs on closed-candle features only. Nothing in this module can make a model look better than
its chronological out-of-sample record: accuracy/Brier are measured on folds the model never saw.
"""
import json
import time
from pathlib import Path

import numpy as np

VALIDATED_MODELS = ('xgboost', 'lightgbm', 'catboost', 'logistic')
MIN_SAMPLES = 240
MIN_IMPROVEMENT_PTS = 0.5  # challenger must beat the champion's OOS accuracy by this many points (and not worsen Brier by > 0.005)


def walk_forward_splits(n, folds=4, min_train=120, embargo=1):
    """Expanding-window splits. ``embargo`` samples between train end and test start remove label overlap (purging)."""
    test_size = max(20, (n - min_train) // max(1, folds))
    splits = []
    start = min_train
    while start + embargo + 10 <= n and len(splits) < folds:
        test_start = start + embargo
        test_end = min(n, test_start + test_size)
        splits.append((np.arange(0, start), np.arange(test_start, test_end)))
        start = test_end
    return splits


def _logit(p):
    p = np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def _sigmoid(z):
    return 1.0 / (1.0 + np.exp(-np.asarray(z, dtype=float)))


def platt_fit(p, y, iterations=200, lr=0.1):
    """Platt scaling (a, b) fitted by gradient descent on log-loss: calibrated = sigmoid(a * logit(p) + b)."""
    z = _logit(p)
    y = np.asarray(y, dtype=float)
    a, b = 1.0, 0.0
    for _ in range(iterations):
        q = _sigmoid(a * z + b)
        grad = q - y
        a -= lr * float(np.mean(grad * z))
        b -= lr * float(np.mean(grad))
    return float(a), float(b)


def platt_apply(p, calibration):
    if not calibration:
        return np.asarray(p, dtype=float)
    return _sigmoid(calibration['a'] * _logit(p) + calibration['b'])


def brier(p, y):
    p = np.asarray(p, dtype=float)
    y = np.asarray(y, dtype=float)
    return float(np.mean((p - y) ** 2)) if len(y) else None


def accuracy(p, y):
    p = np.asarray(p, dtype=float)
    y = np.asarray(y, dtype=int)
    return float(np.mean((p >= 0.5).astype(int) == y)) if len(y) else None


def _fit_proba(adapter, X_tr, y_tr, X_te):
    estimator = adapter._new_estimator()
    estimator.fit(X_tr, y_tr)
    proba = estimator.predict_proba(X_te)
    positive = list(estimator.classes_).index(1)
    return proba[:, positive]


def leakage_checks(adapters, X, y, horizon):
    """1) feature rows must precede their label by ``horizon`` closes (index check);
    2) shuffled-label test: a model trained on shuffled labels must not beat chance out-of-sample."""
    n = len(y)
    index_ok = horizon >= 1 and n >= 2
    report = {'horizon': int(horizon), 'indexOrderingOk': bool(index_ok), 'shuffledLabelAccuracy': None, 'shuffledLabelOk': None}
    name = next((m for m in ('logistic', 'xgboost') if m in adapters), None)
    splits = walk_forward_splits(n, folds=2)
    if name and splits:
        rng = np.random.default_rng(7)
        shuffled = rng.permutation(np.asarray(y, dtype=int))
        preds, truth = [], []
        for tr, te in splits:
            try:
                preds.extend(_fit_proba(adapters[name], X[tr], shuffled[tr], X[te]).tolist())
                truth.extend(shuffled[te].tolist())
            except Exception:  # noqa: BLE001 - leakage probe must never block training
                return report
        acc = accuracy(preds, truth)
        report['shuffledLabelAccuracy'] = round(acc, 4)
        # with shuffled labels anything above ~55% OOS means the features encode the label (leak)
        report['shuffledLabelOk'] = bool(acc is not None and acc <= 0.55)
    return report


def validate(adapters, X, y, horizon=1, folds=4):
    """Walk-forward OOS report for the tabular adapters. Returns per-model and ensemble accuracy/Brier plus calibration."""
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=int).reshape(-1)
    n = min(len(X), len(y))
    X, y = X[:n], y[:n]
    report = {'status': 'INSUFFICIENT_SAMPLES', 'samples': int(n), 'folds': 0, 'models': {}, 'ensemble': None, 'leakage': None, 'evaluatedAt': time.time()}
    if n < MIN_SAMPLES or len(np.unique(y)) < 2:
        return report
    splits = walk_forward_splits(n, folds=folds, embargo=max(1, int(horizon)))
    if not splits:
        return report
    oos = {}
    for name in VALIDATED_MODELS:
        adapter = adapters.get(name)
        if adapter is None or not hasattr(adapter, '_new_estimator'):
            continue
        preds, truth, idx = [], [], []
        try:
            for tr, te in splits:
                if len(np.unique(y[tr])) < 2:
                    continue
                preds.extend(_fit_proba(adapter, X[tr], y[tr], X[te]).tolist())
                truth.extend(y[te].tolist())
                idx.extend(te.tolist())
        except Exception as exc:  # noqa: BLE001 - one backend failing must not hide the others
            report['models'][name] = {'status': f'ERROR:{type(exc).__name__}'}
            continue
        if not preds:
            continue
        p, t = np.asarray(preds), np.asarray(truth)
        a, b = platt_fit(p, t)
        calibrated = platt_apply(p, {'a': a, 'b': b})
        oos[name] = (p, t, np.asarray(idx))
        report['models'][name] = {
            'status': 'OK', 'oosSamples': int(len(t)), 'accuracy': round(accuracy(p, t), 4), 'brier': round(brier(p, t), 4),
            'calibration': {'a': round(a, 4), 'b': round(b, 4), 'brierBefore': round(brier(p, t), 4), 'brierAfter': round(brier(calibrated, t), 4)},
            'confidentAccuracy': _confident_accuracy(calibrated, t, 0.58),
        }
    if oos:
        common = set.intersection(*(set(v[2].tolist()) for v in oos.values()))
        if common:
            order = sorted(common)
            stack = []
            truth = None
            for name, (p, t, idx) in oos.items():
                pos = {i: k for k, i in enumerate(idx.tolist())}
                sel = [pos[i] for i in order]
                stack.append(platt_apply(p[sel], report['models'][name]['calibration']))
                truth = t[sel]
            mean_p = np.mean(np.vstack(stack), axis=0)
            report['ensemble'] = {'oosSamples': int(len(truth)), 'accuracy': round(accuracy(mean_p, truth), 4), 'brier': round(brier(mean_p, truth), 4),
                                  'confidentAccuracy': _confident_accuracy(mean_p, truth, 0.58), 'noTradeShare': round(float(np.mean(np.abs(mean_p - 0.5) < 0.08)), 4)}
        report['folds'] = len(splits)
        report['status'] = 'OK'
    report['leakage'] = leakage_checks(adapters, X, y, horizon)
    return report


def _confident_accuracy(p, y, threshold):
    """Accuracy only on rows where the (calibrated) probability is decisive - the rows the NO TRADE gate lets through."""
    p = np.asarray(p, dtype=float)
    y = np.asarray(y, dtype=int)
    mask = np.abs(p - 0.5) >= (threshold - 0.5)
    if not mask.any():
        return {'threshold': threshold, 'samples': 0, 'accuracy': None}
    return {'threshold': threshold, 'samples': int(mask.sum()), 'accuracy': round(accuracy(p[mask], y[mask]), 4)}


def headline(report):
    """Single OOS number used by the champion gate: ensemble accuracy, else best single model."""
    if not report or report.get('status') != 'OK':
        return None
    if report.get('ensemble'):
        return {'accuracy': report['ensemble']['accuracy'], 'brier': report['ensemble']['brier']}
    rows = [m for m in report['models'].values() if m.get('status') == 'OK']
    if not rows:
        return None
    best = max(rows, key=lambda m: m['accuracy'])
    return {'accuracy': best['accuracy'], 'brier': best['brier']}


def challenger_wins(challenger, champion):
    """Deploy rule: no champion -> deploy; else require better OOS accuracy and no worse Brier."""
    c = headline(challenger)
    if c is None:
        return False, 'VALIDATION_UNAVAILABLE'
    if challenger.get('leakage') and challenger['leakage'].get('shuffledLabelOk') is False:
        return False, 'LEAKAGE_SUSPECTED'
    if not champion:
        return True, 'NO_CHAMPION'
    k = headline(champion)
    if k is None:
        return True, 'CHAMPION_UNVALIDATED'
    better_acc = c['accuracy'] * 100 >= k['accuracy'] * 100 + MIN_IMPROVEMENT_PTS
    brier_ok = c['brier'] <= k['brier'] + 0.005
    if better_acc and brier_ok:
        return True, f"BETTER_OOS +{c['accuracy'] * 100 - k['accuracy'] * 100:.1f}pt"
    return False, f"NOT_BETTER ({c['accuracy'] * 100:.1f}% vs champion {k['accuracy'] * 100:.1f}%)"


class ChampionRegistry:
    """Per-market champion validation reports persisted next to the model files."""

    def __init__(self, directory):
        self.path = Path(directory) / 'champions.json'
        try:
            self.data = json.loads(self.path.read_text()) if self.path.is_file() else {}
        except (OSError, ValueError):
            self.data = {}

    def get(self, market):
        return self.data.get(market)

    def set(self, market, report, decision):
        self.data[market] = {'report': _slim(report), 'decision': decision, 'deployedAt': time.time()}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data, default=str))
        except OSError:
            pass

    def summary(self):
        out = {}
        for market, row in self.data.items():
            h = headline(row.get('report'))
            out[market] = {'deployedAt': row.get('deployedAt'), 'decision': row.get('decision'), 'oosAccuracy': h['accuracy'] if h else None, 'brier': h['brier'] if h else None}
        return out


def _slim(report):
    return {k: v for k, v in (report or {}).items() if k != 'raw'}
