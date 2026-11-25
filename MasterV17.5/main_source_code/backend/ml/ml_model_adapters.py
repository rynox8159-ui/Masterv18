"""Step 8 model adapters for LSTM, Transformer, XGBoost, Random Forest and MARL.

These adapters are intentionally lightweight and dependency-safe: they use NumPy,
operate on the feature bundles prepared by the earlier stages, and provide a clear
training + inference loop that can be upgraded later to full PyTorch/Scikit-learn
implementations without changing the backend contract.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import importlib
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple
import threading
import time

import joblib
import numpy as np

from ml.market_ml_orchestrator import build_market_ml_orchestrator
from ml import model_validation
from training_workers import run_in_worker

logger = logging.getLogger(__name__)


def _sigmoid(value: float) -> float:
    value = float(np.clip(value, -40.0, 40.0))
    return 1.0 / (1.0 + np.exp(-value))


def _as_2d(matrix: Any) -> np.ndarray:
    arr = np.asarray(matrix, dtype=float)
    if arr.ndim == 1:
        return arr.reshape(1, -1)
    return arr


@dataclass
class ModelState:
    name: str
    trained: bool = False
    training_count: int = 0
    last_fit_at: float | None = None
    last_prediction: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)


class LSTMAdapter:
    """Minimal recurrent binary classifier.

    This is not a production PyTorch LSTM, but it preserves the same API and
    exposes a clear training/inference path that can later be replaced by a true
    sequence model without breaking the backend contract.
    """

    def __init__(self, hidden_size: int = 32):
        self.hidden_size = max(4, int(hidden_size))
        self.weights = np.random.default_rng(0).normal(0.0, 0.1, size=(self.hidden_size, 1))
        self.bias = 0.0
        self.state = ModelState(name='lstm')

    def _forward_state(self, sequence: np.ndarray) -> np.ndarray:
        seq = np.asarray(sequence, dtype=float)
        if seq.size == 0:
            return np.zeros(self.hidden_size)
        if seq.ndim == 1:
            seq = seq.reshape(1, -1)
        hidden = np.zeros(self.hidden_size)
        for idx, row in enumerate(seq[-min(seq.shape[0], self.hidden_size):]):
            scale = np.linspace(0.4, 1.0, len(row))
            hidden = np.tanh((hidden * 0.6) + (row[: min(len(row), self.hidden_size)] * scale[: min(len(row), self.hidden_size)]).sum())
            if hidden.shape[0] != self.hidden_size:
                hidden = np.pad(hidden, (0, self.hidden_size - hidden.shape[0]), 'constant')
        return hidden[:self.hidden_size]

    def fit(self, X: Any, y: Any) -> Dict[str, Any]:
        X_arr = _as_2d(X)
        y_arr = np.asarray(y, dtype=float)
        if X_arr.shape[0] == 0:
            return {'model': 'lstm', 'status': 'EMPTY_INPUT'}
        for _ in range(20):
            for idx, seq in enumerate(X_arr):
                target = float(y_arr[idx]) if idx < len(y_arr) else 0.5
                hidden = self._forward_state(seq)
                logit = float(hidden @ self.weights[:, 0]) + self.bias
                prob = _sigmoid(logit)
                error = target - prob
                gradient = error * prob * (1.0 - prob)
                self.weights[:, 0] += 0.02 * gradient * hidden
                self.bias += 0.02 * gradient
        self.state.trained = True
        self.state.training_count += 1
        self.state.last_fit_at = time.time()
        return {'model': 'lstm', 'status': 'trained', 'training_count': self.state.training_count}

    def predict(self, X: Any) -> Dict[str, Any]:
        X_arr = _as_2d(X)
        if X_arr.size == 0:
            return {'model': 'lstm', 'direction': 'NO_SIGNAL', 'confidence': 0.0, 'probability': 0.5}
        outputs = []
        for seq in X_arr:
            hidden = self._forward_state(seq)
            logit = float(hidden @ self.weights[:, 0]) + self.bias
            prob = _sigmoid(logit)
            outputs.append(prob)
        probability = float(np.mean(outputs)) if outputs else 0.5
        direction = 'CALL' if probability >= 0.5 else 'PUT'
        self.state.last_prediction = {'direction': direction, 'confidence': round(100.0 * max(probability, 1.0 - probability), 2), 'probability': probability}
        return {'model': 'lstm', 'direction': direction, 'confidence': round(100.0 * max(probability, 1.0 - probability), 2), 'probability': probability}


class TransformerAdapter:
    """A lightweight self-attention inspired adapter over the sequence window."""

    def __init__(self, heads: int = 4):
        self.heads = max(1, int(heads))
        self.weights = np.random.default_rng(1).normal(0.0, 0.1, size=(self.heads, 1))
        self.bias = 0.0
        self.state = ModelState(name='transformer')

    def _attention(self, sequence: np.ndarray) -> np.ndarray:
        seq = np.asarray(sequence, dtype=float)
        if seq.ndim == 1:
            seq = seq.reshape(1, -1)
        time_weights = np.linspace(0.5, 1.5, seq.shape[1])
        weighted = seq * time_weights
        return np.mean(weighted, axis=0)[:self.heads]

    def fit(self, X: Any, y: Any) -> Dict[str, Any]:
        X_arr = _as_2d(X)
        y_arr = np.asarray(y, dtype=float)
        for _ in range(15):
            for idx, seq in enumerate(X_arr):
                attention = self._attention(seq)
                target = float(y_arr[idx]) if idx < len(y_arr) else 0.5
                score = float(np.dot(attention, self.weights[:, 0])) + self.bias
                prob = _sigmoid(score)
                error = target - prob
                gradient = error * prob * (1.0 - prob)
                self.weights[:, 0] += 0.01 * gradient * attention
                self.bias += 0.01 * gradient
        self.state.trained = True
        self.state.training_count += 1
        self.state.last_fit_at = time.time()
        return {'model': 'transformer', 'status': 'trained', 'training_count': self.state.training_count}

    def predict(self, X: Any) -> Dict[str, Any]:
        X_arr = _as_2d(X)
        probabilities = []
        for seq in X_arr:
            attention = self._attention(seq)
            score = float(np.dot(attention, self.weights[:, 0])) + self.bias
            probabilities.append(_sigmoid(score))
        probability = float(np.mean(probabilities)) if probabilities else 0.5
        direction = 'CALL' if probability >= 0.5 else 'PUT'
        self.state.last_prediction = {'direction': direction, 'confidence': round(100.0 * max(probability, 1.0 - probability), 2), 'probability': probability}
        return {'model': 'transformer', 'direction': direction, 'confidence': round(100.0 * max(probability, 1.0 - probability), 2), 'probability': probability}


class GradientBoostedTreeAdapter:
    """Train a native booster when installed, otherwise use sklearn boosting."""

    MODEL_CONFIG = {
        'xgboost': ('xgboost', 'XGBClassifier'),
        'lightgbm': ('lightgbm', 'LGBMClassifier'),
        'catboost': ('catboost', 'CatBoostClassifier'),
    }

    def __init__(self, name: str):
        if name not in self.MODEL_CONFIG:
            raise ValueError(f'UNSUPPORTED_BOOSTED_TREE_MODEL:{name}')
        self.name = name
        self.estimator = None
        self.state = ModelState(name=name)

    def _new_estimator(self):
        module_name, class_name = self.MODEL_CONFIG[self.name]
        try:
            estimator_type = getattr(importlib.import_module(module_name), class_name)
        except (ImportError, AttributeError) as exc:
            raise RuntimeError(
                f'REQUIRED_MODEL_BACKEND_UNAVAILABLE:{self.name}:{module_name}.{class_name}',
            ) from exc

        self.state.metadata['backend'] = module_name
        if self.name == 'xgboost':
            return estimator_type(
                n_estimators=200, max_depth=4, learning_rate=0.05,
                subsample=0.8, colsample_bytree=0.8, eval_metric='logloss',
                n_jobs=1, random_state=17, verbosity=0,
            )
        if self.name == 'lightgbm':
            return estimator_type(
                n_estimators=200, max_depth=-1, learning_rate=0.05,
                num_leaves=15, n_jobs=1, random_state=17, verbosity=-1,
            )
        return estimator_type(
            iterations=200, depth=4, learning_rate=0.05,
            random_seed=17, verbose=False, allow_writing_files=False,
        )

    def fit(self, X: Any, y: Any) -> Dict[str, Any]:
        X_arr = np.asarray(X, dtype=float)
        y_arr = np.asarray(y, dtype=int).reshape(-1)
        if X_arr.size == 0 or not len(y_arr):
            return {'model': self.name, 'status': 'EMPTY_INPUT'}
        if X_arr.ndim == 1:
            X_arr = X_arr.reshape(1, -1)
        if X_arr.ndim != 2 or X_arr.shape[0] != len(y_arr):
            return {'model': self.name, 'status': 'INVALID_TABULAR_SHAPE'}
        if len(np.unique(y_arr)) < 2:
            return {'model': self.name, 'status': 'SINGLE_CLASS_TRAINING_DATA'}
        if not np.isfinite(X_arr).all() or not np.isin(y_arr, (0, 1)).all():
            return {'model': self.name, 'status': 'INVALID_TRAINING_DATA'}
        estimator = self._new_estimator()
        if estimator is None:
            return {'model': self.name, 'status': 'DEPENDENCY_UNAVAILABLE'}
        estimator.fit(X_arr, y_arr)
        self.estimator = estimator
        self.state.trained = True
        self.state.training_count += 1
        self.state.last_fit_at = time.time()
        self.state.metadata['featureCount'] = int(X_arr.shape[1])
        return {
            'model': self.name, 'status': 'trained',
            'backend': self.state.metadata['backend'],
            'training_count': self.state.training_count,
        }

    def predict(self, X: Any) -> Dict[str, Any]:
        X_arr = np.asarray(X, dtype=float)
        if X_arr.size == 0 or self.estimator is None:
            return {
                'model': self.name, 'direction': 'NO_SIGNAL',
                'confidence': 0.0, 'probability': 0.5,
            }
        if X_arr.ndim == 1:
            X_arr = X_arr.reshape(1, -1)
        expected = self.state.metadata.get('featureCount')
        if expected and int(expected) != int(X_arr.shape[1]):
            raise ValueError(f'FEATURE_SHAPE_DRIFT:{self.name}:{X_arr.shape[1]}!={expected}')
        probabilities = self.estimator.predict_proba(X_arr)
        classes = list(self.estimator.classes_)
        positive_index = classes.index(1)
        probability = float(np.mean(probabilities[:, positive_index]))
        calibration = self.state.metadata.get('calibration')
        if calibration:
            probability = float(model_validation.platt_apply([probability], calibration)[0])
        direction = 'CALL' if probability >= 0.5 else 'PUT'
        prediction = {
            'direction': direction,
            'confidence': round(100.0 * max(probability, 1.0 - probability), 2),
            'probability': probability,
            'calibrated': bool(calibration),
        }
        self.state.last_prediction = prediction
        return {'model': self.name, **prediction}


class XGBoostAdapter(GradientBoostedTreeAdapter):
    def __init__(self):
        super().__init__('xgboost')


class LightGBMAdapter(GradientBoostedTreeAdapter):
    def __init__(self):
        super().__init__('lightgbm')


class CatBoostAdapter(GradientBoostedTreeAdapter):
    def __init__(self):
        super().__init__('catboost')


class SklearnAdapter(GradientBoostedTreeAdapter):
    """Real scikit-learn estimators (V16.3): replaces toy models and widens the ensemble."""

    FACTORIES = {
        'random_forest': lambda: __import__('sklearn.ensemble', fromlist=['RandomForestClassifier']).RandomForestClassifier(
            n_estimators=150, max_depth=6, min_samples_leaf=5, max_features='sqrt', n_jobs=1, random_state=17, class_weight='balanced'),
        'extra_trees': lambda: __import__('sklearn.ensemble', fromlist=['ExtraTreesClassifier']).ExtraTreesClassifier(
            n_estimators=150, max_depth=7, min_samples_leaf=5, n_jobs=1, random_state=17, class_weight='balanced'),
        'hist_gbm': lambda: __import__('sklearn.ensemble', fromlist=['HistGradientBoostingClassifier']).HistGradientBoostingClassifier(
            max_iter=250, learning_rate=0.05, max_leaf_nodes=15, l2_regularization=1.0, early_stopping=True, random_state=17),
        'mlp': lambda: __import__('sklearn.pipeline', fromlist=['make_pipeline']).make_pipeline(
            __import__('sklearn.preprocessing', fromlist=['StandardScaler']).StandardScaler(),
            __import__('sklearn.neural_network', fromlist=['MLPClassifier']).MLPClassifier(
                hidden_layer_sizes=(64, 32), alpha=1e-3, max_iter=400, early_stopping=True, random_state=17)),
        'logistic': lambda: __import__('sklearn.pipeline', fromlist=['make_pipeline']).make_pipeline(
            __import__('sklearn.preprocessing', fromlist=['StandardScaler']).StandardScaler(),
            __import__('sklearn.linear_model', fromlist=['LogisticRegression']).LogisticRegression(C=0.5, max_iter=500, class_weight='balanced')),
    }

    def __init__(self, name: str):
        self.name = name
        self.estimator = None
        self.state = ModelState(name=name)

    def _new_estimator(self):
        self.state.metadata['backend'] = 'sklearn'
        return self.FACTORIES[self.name]()

    def predict(self, X: Any) -> Dict[str, Any]:
        X_arr = np.asarray(X, dtype=float)
        return super().predict(X_arr[-30:] if X_arr.ndim == 2 else X_arr)


class RandomForestAdapter(SklearnAdapter):
    def __init__(self, trees: int = 300):
        super().__init__('random_forest')


class ExtraTreesAdapter(SklearnAdapter):
    def __init__(self):
        super().__init__('extra_trees')


class HistGBMAdapter(SklearnAdapter):
    def __init__(self):
        super().__init__('hist_gbm')


class MLPAdapter(SklearnAdapter):
    def __init__(self):
        super().__init__('mlp')


class LogisticAdapter(SklearnAdapter):
    def __init__(self):
        super().__init__('logistic')


class LegacyRandomForestAdapter:
    """Tree-ensemble style classifier using random feature thresholds."""

    def __init__(self, trees: int = 8):
        self.trees = max(1, int(trees))
        self.thresholds = []
        self.weights = []
        self.state = ModelState(name='legacy_random_forest')

    def _fit_tree(self, X: np.ndarray, y: np.ndarray) -> Tuple[np.ndarray, float]:
        feature_index = int(np.argmax(np.var(X, axis=0))) if X.size else 0
        threshold = float(np.median(X[:, feature_index])) if X.shape[0] else 0.0
        tree_mask = X[:, feature_index] >= threshold
        positive = float(np.mean(y[tree_mask])) if np.any(tree_mask) else 0.5
        negative = float(np.mean(y[~tree_mask])) if np.any(~tree_mask) else 0.5
        return np.array([feature_index, threshold, positive, negative], dtype=float), float((positive - negative) / 2.0 + 0.5)

    def fit(self, X: Any, y: Any) -> Dict[str, Any]:
        X_arr = np.asarray(X, dtype=float)
        y_arr = np.asarray(y, dtype=float)
        if X_arr.size == 0:
            return {'model': 'random_forest', 'status': 'EMPTY_INPUT'}
        if X_arr.ndim == 1:
            X_arr = X_arr.reshape(1, -1)
        self.thresholds = []
        self.weights = []
        rng = np.random.default_rng(42)
        indices = np.arange(X_arr.shape[0])
        for _ in range(self.trees):
            sample_idx = rng.choice(indices, size=max(2, len(indices)), replace=True)
            tree_def, tree_weight = self._fit_tree(X_arr[sample_idx], y_arr[sample_idx])
            self.thresholds.append(tree_def)
            self.weights.append(tree_weight)
        self.state.trained = True
        self.state.training_count += 1
        self.state.last_fit_at = time.time()
        return {'model': 'random_forest', 'status': 'trained', 'training_count': self.state.training_count}

    def predict(self, X: Any) -> Dict[str, Any]:
        X_arr = _as_2d(X)
        probabilities = []
        for row in X_arr:
            votes = []
            for tree_def, weight in zip(self.thresholds, self.weights):
                feature_index = int(tree_def[0])
                threshold = float(tree_def[1])
                positive = float(tree_def[2])
                negative = float(tree_def[3])
                vote = positive if row[feature_index] >= threshold else negative
                votes.append((vote * weight) + 0.5 * (1.0 - weight))
            probabilities.append(float(np.mean(votes)) if votes else 0.5)
        final = float(np.mean(probabilities)) if probabilities else 0.5
        direction = 'CALL' if final >= 0.5 else 'PUT'
        self.state.last_prediction = {'direction': direction, 'confidence': round(100.0 * max(final, 1.0 - final), 2), 'probability': final}
        return {'model': 'random_forest', 'direction': direction, 'confidence': round(100.0 * max(final, 1.0 - final), 2), 'probability': final}


class MARLAdapter:
    """Minimal multi-agent reinforcement learning adapter.

    This is a deterministic, finite-horizon state-value approximator with a Q-like
    update loop that fits the Step 8 blueprint without introducing heavy runtime
    dependencies. It can be replaced by a full MARL framework later.
    """

    def __init__(self, agents: int = 5):
        self.agents = max(1, int(agents))
        self.q_table: Dict[Tuple[str, int], float] = {}
        self.reward_history: List[float] = []
        self.state = ModelState(name='marl')

    def _state_key(self, symbol: str, state_index: int) -> Tuple[str, int]:
        return (symbol, int(state_index))

    def fit(self, states: Sequence[Dict[str, Any]], rewards: Sequence[float]) -> Dict[str, Any]:
        for state, reward in zip(states, rewards):
            key = self._state_key(str(state.get('symbol', 'global')), int(state.get('state_index', 0)))
            current = self.q_table.get(key, 0.0)
            self.q_table[key] = float((current * 0.7) + (reward * 0.3))
        self.reward_history.extend(float(r) for r in rewards)
        self.state.trained = True
        self.state.training_count += 1
        self.state.last_fit_at = time.time()
        return {'model': 'marl', 'status': 'trained', 'training_count': self.state.training_count, 'agents': self.agents}

    def predict(self, state: Dict[str, Any]) -> Dict[str, Any]:
        symbol = str(state.get('symbol', 'global'))
        state_index = int(state.get('state_index', 0))
        reward = self.q_table.get((symbol, state_index), 0.0)
        q_value = max(0.0, min(1.0, (reward + 0.5)))
        direction = 'CALL' if q_value >= 0.5 else 'PUT'
        self.state.last_prediction = {'direction': direction, 'confidence': round(100.0 * q_value, 2), 'q_value': q_value}
        return {'model': 'marl', 'direction': direction, 'confidence': round(100.0 * q_value, 2), 'q_value': q_value}


MODEL_STORE_DIR = Path(os.environ.get('STEP8_MODEL_DIR') or Path(__file__).resolve().parents[1] / 'models' / 'step8')


class Step8ModelRunner:
    """Coordinates the market feature preparation and Step 8 training/inference."""

    def __init__(self, max_agents: int = 500):
        self.orchestrator = build_market_ml_orchestrator(max_agents=max_agents)
        self.models = self._new_model_set()
        self.market_models: Dict[str, Dict[str, Any]] = {}
        self.training_history: List[Dict[str, Any]] = []
        self.last_inference: Dict[str, Any] | None = None
        self.active_training: Dict[str, Dict[str, Any]] = {}
        self.model_training_samples: Dict[str, Dict[str, int]] = {}
        self.lock = threading.Lock()
        self.persisted_models: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self.legacy_artifacts_ignored = False
        self.champions = model_validation.ChampionRegistry(MODEL_STORE_DIR)
        self._load_persisted()

    @staticmethod
    def _new_model_set():
        return {
            'lstm': LSTMAdapter(),
            'transformer': TransformerAdapter(),
            'xgboost': XGBoostAdapter(),
            'lightgbm': LightGBMAdapter(),
            'catboost': CatBoostAdapter(),
            'random_forest': RandomForestAdapter(),
            'extra_trees': ExtraTreesAdapter(),
            'hist_gbm': HistGBMAdapter(),
            'mlp': MLPAdapter(),
            'logistic': LogisticAdapter(),
            'marl': MARLAdapter(),
        }

    @staticmethod
    def market_key(source: str, symbol: str, timeframe: str) -> str:
        return f'{source}:{symbol}:{timeframe}'

    @staticmethod
    def _market_directory(market: str) -> Path:
        digest = hashlib.sha256(market.encode('utf-8')).hexdigest()
        return MODEL_STORE_DIR / 'markets' / digest

    def models_for_market(self, source: str, symbol: str, timeframe: str) -> Dict[str, Any]:
        return self.market_models.get(self.market_key(source, symbol, timeframe), self.models)

    def has_trained_models(self, source: str, symbol: str, timeframe: str) -> bool:
        return any(
            model.state.trained
            for model in self.market_models.get(self.market_key(source, symbol, timeframe), {}).values()
        )

    def adapter_ready_for_live(self, name: str, source: str, symbol: str, timeframe: str) -> bool:
        model = self.models_for_market(source, symbol, timeframe).get(name)
        state = getattr(model, 'state', None)
        return bool(
            getattr(state, 'trained', False)
            and (getattr(state, 'metadata', {}) or {}).get('readyForLive') is True
        )

    def any_adapter_ready_for_live(self, name: str) -> bool:
        return any(
            (getattr(getattr(models.get(name), 'state', None), 'metadata', {}) or {}).get('readyForLive') is True
            for models in self.market_models.values()
        )

    def _load_persisted(self):
        """Restore only artifacts with an explicit market-specific path and matching metadata."""
        if not MODEL_STORE_DIR.is_dir():
            return
        market_root = MODEL_STORE_DIR / 'markets'
        if market_root.is_dir():
            for market_dir in market_root.iterdir():
                if not market_dir.is_dir():
                    continue
                for path in market_dir.glob('*.joblib'):
                    name = path.stem
                    if name not in self.models:
                        continue
                    try:
                        payload = joblib.load(path)
                        market = payload.get('market')
                        adapter = payload.get('adapter')
                        if (
                            not isinstance(market, str)
                            or self._market_directory(market) != market_dir
                            or adapter is None
                            or not getattr(getattr(adapter, 'state', None), 'trained', False)
                        ):
                            continue
                        if isinstance(self.models[name], SklearnAdapter) and getattr(adapter, 'estimator', None) is None:
                            continue
                        models = self.market_models.setdefault(market, self._new_model_set())
                        models[name] = adapter
                        self.model_training_samples.setdefault(market, {})[name] = int(payload.get('samples', 0))
                        self.persisted_models.setdefault(market, {})[name] = {
                            'path': str(path), 'savedAt': payload.get('savedAt'),
                        }
                    except Exception as exc:  # noqa: BLE001 - a corrupt file must not stop the backend
                        logger.warning('Could not load persisted Step8 model %s: %s', path.name, type(exc).__name__)
        # Legacy flat artifacts lack trustworthy per-adapter market provenance and are not loaded.
        self.legacy_artifacts_ignored = any(
            (MODEL_STORE_DIR / f'{name}.joblib').is_file()
            for name in self.models
        )
        if self.legacy_artifacts_ignored:
            logger.warning('Ignoring shared legacy Step8 artifacts; retrain markets to create isolated models')
        history = MODEL_STORE_DIR / 'training_history.json'
        if history.is_file():
            try:
                self.training_history = json.loads(history.read_text())[-200:]
            except (OSError, ValueError):
                self.training_history = []

    def _persist(self, train_record: Dict[str, Any]):
        try:
            MODEL_STORE_DIR.mkdir(parents=True, exist_ok=True)
            market = self.market_key(
                train_record['source'], train_record['symbol'], train_record['timeframe'],
            )
            market_dir = self._market_directory(market)
            market_dir.mkdir(parents=True, exist_ok=True)
            models = self.market_models.get(market, {})
            for name, model in models.items():
                if not model.state.trained:
                    continue
                path = market_dir / f'{name}.joblib'
                saved_at = time.time()
                joblib.dump({
                    'adapter': model,
                    'samples': self.model_training_samples.get(market, {}).get(name, 0),
                    'savedAt': saved_at,
                    'market': market,
                }, path, compress=3)
                self.persisted_models.setdefault(market, {})[name] = {
                    'path': str(path), 'savedAt': saved_at,
                }
            (MODEL_STORE_DIR / 'training_history.json').write_text(json.dumps(self.training_history[-200:], default=str))
        except Exception as exc:  # noqa: BLE001
            logger.warning('Could not persist Step8 models: %s', type(exc).__name__)

    def prepare_market(self, source: str, symbol: str, timeframe: str, candles: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        prepared = self.orchestrator.prepare(source, symbol, timeframe, candles)
        return prepared

    def train_all(self, source: str, symbol: str, timeframe: str, candles: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        key = f'{source}:{symbol}:{timeframe}'
        self.active_training[key] = {
            'source': source, 'symbol': symbol, 'timeframe': timeframe,
            'startedAt': time.time(), 'sampleCount': 0, 'phase': 'PREPARING',
        }
        try:
            result = self._train_all(source, symbol, timeframe, candles)
            self.active_training[key]['sampleCount'] = len(
                result['prepared']['feature_bundle']['targets']['lstm'],
            )
            return result
        finally:
            self.active_training.pop(key, None)

    def _train_all(self, source: str, symbol: str, timeframe: str, candles: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        prepared = self.prepare_market(source, symbol, timeframe, candles)
        labels = np.asarray(prepared['feature_bundle']['targets']['lstm'], dtype=int)
        key = self.market_key(source, symbol, timeframe)
        models = self.market_models.get(key)
        if models is None:
            models = self._new_model_set()
            self.market_models[key] = models
        if key in self.active_training:
            self.active_training[key].update({
                'sampleCount': len(labels), 'phase': 'FITTING',
            })
        if not prepared['feature_bundle']['metadata']['ml_ready'] or len(labels) == 0:
            return {
                'status': 'NOT_READY',
                'reason': 'INSUFFICIENT_CLOSED_CANDLE_FEATURES',
                'sampleCount': len(labels),
                'prepared': prepared,
                'models': {},
            }
        inputs = prepared['model_inputs']
        batch = {name: inputs[name] if name in inputs else inputs.get('xgboost') for name in models if name in inputs or 'xgboost' in inputs}
        with self.lock:
            model_results = {}
            for name, model in models.items():
                if name not in batch:
                    continue
                feature_matrix = batch[name]
                if name == 'marl':
                    sequence_states = feature_matrix['state']
                    state_count = min(len(sequence_states), len(labels))
                    states = [
                        {'symbol': symbol, 'state_index': index}
                        for index in range(state_count)
                    ]
                    fit_result = model.fit(states, labels[-state_count:])
                elif name in {'lstm', 'transformer'}:
                    sample_count = min(feature_matrix.shape[0], len(labels))
                    fit_result = model.fit(feature_matrix[-sample_count:], labels[-sample_count:])
                else:
                    sample_count = min(feature_matrix.shape[0], len(labels))
                    fit_result = model.fit(feature_matrix[:sample_count], labels[:sample_count])
                model_results[name] = fit_result
                model.state.metadata.pop('readyForLive', None)
                model.state.metadata.pop('promotionTier', None)
                self.model_training_samples.setdefault(key, {})[name] = len(labels)
            train_record = {
                'source': source,
                'symbol': symbol,
                'timeframe': timeframe,
                'timestamp': time.time(),
                'candles': len(candles),
                'samples': len(labels),
                'models': model_results,
                'feature_key': prepared['feature_bundle']['feature_key'],
            }
            self.training_history.append(train_record)
            self.training_history = self.training_history[-200:]
            self._persist(train_record)
            return {
                'status': 'TRAINED', 'sampleCount': len(labels),
                'prepared': prepared, 'models': model_results,
                'training_record': train_record,
            }

    async def train_all_async(self, source: str, symbol: str, timeframe: str, candles: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        """Fit every adapter in the worker process, then adopt the trained adapters here (keeps the event loop free)."""
        key = f'{source}:{symbol}:{timeframe}'
        self.active_training[key] = {'source': source, 'symbol': symbol, 'timeframe': timeframe, 'startedAt': time.time(), 'sampleCount': 0, 'phase': 'FITTING_IN_WORKER'}
        try:
            payload = await run_in_worker(_train_adapters_in_worker, source, symbol, timeframe, list(candles))
        finally:
            self.active_training.pop(key, None)
        if payload.get('status') == 'TRAINED':
            market = key
            validation = payload.get('validation') or {}
            champion = self.champions.get(market)
            deploy, decision = model_validation.challenger_wins(validation, (champion or {}).get('report'))
            payload['deployment'] = {'deployed': deploy, 'decision': decision, 'headline': model_validation.headline(validation),
                                     'champion': model_validation.headline((champion or {}).get('report'))}
            payload['training_record']['validation'] = {k: v for k, v in validation.items() if k in ('status', 'samples', 'folds', 'ensemble', 'leakage')}
            payload['training_record']['deployment'] = payload['deployment']
            if not deploy:
                logger.warning('Step8 challenger for %s NOT deployed: %s', market, decision)
                with self.lock:
                    self.training_history.append(payload['training_record'])
                    self.training_history = self.training_history[-200:]
                return {k: v for k, v in payload.items() if k != 'adapters'}
            with self.lock:
                for name, adapter in payload['adapters'].items():
                    validation_row = (validation.get('models') or {}).get(name, {})
                    leakage = validation.get('leakage') or {}
                    adapter.state.metadata['readyForLive'] = bool(
                        validation_row.get('status') == 'OK'
                        and leakage.get('indexOrderingOk') is True
                        and leakage.get('shuffledLabelOk') is True
                    )
                    adapter.state.metadata['promotionTier'] = (
                        'LIVE' if adapter.state.metadata['readyForLive'] else 'PAPER'
                    )
                    adapter.state.metadata['validation'] = validation_row
                self.market_models[market] = payload['adapters']
                self.model_training_samples[market] = {
                    name: payload['sampleCount'] for name in payload['adapters']
                }
                self.training_history.append(payload['training_record'])
                self.training_history = self.training_history[-200:]
                self._persist(payload['training_record'])
                self.champions.set(market, validation, decision)
        return {k: v for k, v in payload.items() if k != 'adapters'}

    def infer_all(self, source: str, symbol: str, timeframe: str, candles: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        # V16.3: one inference per (market, candle state, model version) - several callers per cycle share it.
        last = candles[-1] if candles else {}
        models = self.models_for_market(source, symbol, timeframe)
        version = max((getattr(m.state, 'last_fit_at', None) or 0) for m in models.values())
        trained = tuple(sorted(name for name, m in models.items() if m.state.trained))
        key = (source, symbol, timeframe, len(candles), last.get('epoch'), last.get('close'), version, trained)
        cache = self.__dict__.setdefault('_infer_cache', {})
        if key in cache:
            return cache[key]
        if len(cache) > 500:
            cache.clear()
        cache[key] = result = self._infer_all_uncached(source, symbol, timeframe, candles)
        return result

    def _infer_all_uncached(self, source: str, symbol: str, timeframe: str, candles: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        prepared = self.prepare_market(source, symbol, timeframe, candles)
        if not prepared['feature_bundle']['metadata']['ml_ready']:
            payload = {
                'source': source,
                'symbol': symbol,
                'timeframe': timeframe,
                'readyForLive': False,
                'promotionReason': 'STEP8_ADAPTERS_REQUIRE_OUT_OF_SAMPLE_PROMOTION',
                'reason': 'INSUFFICIENT_HISTORY_FOR_MODEL_INPUT',
                'decision': 'NO_SIGNAL',
                'confidence': 0.0,
                'predictions': {},
                'behaviour': prepared['behaviour'],
                'agent_snapshot': prepared['agent_snapshot'],
            }
            self.last_inference = payload
            return payload

        predictions = {}
        for name, model in self.models_for_market(source, symbol, timeframe).items():
            if not model.state.trained:
                continue
            feature_matrix = prepared['model_inputs'].get(name, prepared['model_inputs'].get('xgboost'))
            if feature_matrix is None:
                continue
            try:
                prediction = model.predict(feature_matrix)
            except Exception as exc:  # noqa: BLE001 - feature-shape drift / native booster errors must not kill the ensemble
                logger.warning('Step8 %s inference skipped for %s/%s: %s', name, symbol, timeframe, type(exc).__name__)
                continue
            predictions[name] = prediction

        if not predictions:
            payload = {
                'source': source,
                'symbol': symbol,
                'timeframe': timeframe,
                'readyForLive': False,
                'promotionReason': 'STEP8_ADAPTERS_REQUIRE_OUT_OF_SAMPLE_PROMOTION',
                'reason': 'NO_TRAINED_ADVISORY_MODELS',
                'decision': 'NO_SIGNAL',
                'confidence': 0.0,
                'predictions': {},
                'behaviour': prepared['behaviour'],
                'agent_snapshot': prepared['agent_snapshot'],
            }
            self.last_inference = payload
            return payload

        votes = [p['direction'] for p in predictions.values()]
        calls = sum(1 for v in votes if v == 'CALL')
        puts = sum(1 for v in votes if v == 'PUT')
        probs = [float(p['probability']) for p in predictions.values() if 'probability' in p]
        mean_call = float(np.mean(probs)) if probs else 0.5
        decision = 'CALL' if mean_call >= 0.5 else 'PUT'
        agreement = round((max(calls, puts) / max(1, len(votes))) * 100.0, 2)
        # Probability edge (not vote count) so seven ~50% models no longer report 85%+ confidence.
        confidence = round(100.0 * max(mean_call, 1.0 - mean_call), 2)

        payload = {
            'source': source,
            'symbol': symbol,
            'timeframe': timeframe,
            'readyForLive': False,
            'promotionReason': 'STEP8_ADAPTERS_REQUIRE_OUT_OF_SAMPLE_PROMOTION',
            'decision': decision,
            'confidence': confidence,
            'agreement': agreement,
            'predictions': predictions,
            'behaviour': prepared['behaviour'],
            'agent_snapshot': prepared['agent_snapshot'],
        }
        self.last_inference = payload
        return payload

    def status(self) -> Dict[str, Any]:
        market_status = {
            market: {
                'trainedAdapters': [
                    name for name, model in models.items() if model.state.trained
                ],
                'readyForLiveAdapters': [
                    name for name, model in models.items()
                    if model.state.metadata.get('readyForLive') is True
                ],
                'trainingSamples': max(
                    self.model_training_samples.get(market, {}).values(),
                    default=0,
                ),
                'persistedAdapters': list(self.persisted_models.get(market, {})),
            }
            for market, models in self.market_models.items()
        }
        return {
            'ready': any(
                model.state.trained
                for models in self.market_models.values()
                for model in models.values()
            ),
            'readyForLive': any(
                model.state.metadata.get('readyForLive') is True
                for models in self.market_models.values()
                for model in models.values()
            ),
            'promotionPolicy': 'V17.2: a retrained stack is deployed only when its walk-forward OOS accuracy beats the stored champion (and the shuffled-label leakage probe passes).',
            'champions': self.champions.summary(),
            'validation': {'method': 'expanding walk-forward, purged by label horizon', 'models': list(model_validation.VALIDATED_MODELS), 'minSamples': model_validation.MIN_SAMPLES, 'minImprovementPts': model_validation.MIN_IMPROVEMENT_PTS},
            'models': {
                name: {
                    'trained': any(models.get(name) and models[name].state.trained for models in self.market_models.values()),
                    'readyForLive': self.any_adapter_ready_for_live(name),
                    'status': 'TRAINED' if any(models.get(name) and models[name].state.trained for models in self.market_models.values()) else 'NOT_TRAINED',
                    'backend': next((
                        models[name].state.metadata.get('backend')
                        for models in self.market_models.values()
                        if name in models and models[name].state.metadata.get('backend')
                    ), None),
                    'loadedInMemory': any(name in models for models in self.market_models.values()),
                    'training_count': sum(
                        models[name].state.training_count
                        for models in self.market_models.values() if name in models
                    ),
                    'training_samples': max(
                        (samples.get(name, 0) for samples in self.model_training_samples.values()),
                        default=0,
                    ),
                    'last_fit_at': max(
                        (models[name].state.last_fit_at or 0 for models in self.market_models.values() if name in models),
                        default=0,
                    ) or None,
                    'last_prediction': next((
                        models[name].state.last_prediction
                        for models in self.market_models.values()
                        if name in models and models[name].state.last_prediction
                    ), {}),
                    'persisted': next((
                        persisted[name]
                        for persisted in self.persisted_models.values() if name in persisted
                    ), None),
                }
                for name, model in self.models.items()
            },
            'markets': market_status,
            'legacyArtifactsIgnored': self.legacy_artifacts_ignored,
            'modelStoreDir': str(MODEL_STORE_DIR),
            'active_training': list(self.active_training.values()),
            'last_training': self.training_history[-1] if self.training_history else None,
            'last_inference': self.last_inference,
            'model_stack': list(self.models),
        }


def _train_adapters_in_worker(source, symbol, timeframe, candles):
    runner = Step8ModelRunner.__new__(Step8ModelRunner)
    runner.orchestrator = build_market_ml_orchestrator(max_agents=50)
    runner.models = runner._new_model_set()
    runner.market_models = {
        runner.market_key(source, symbol, timeframe): runner.models,
    }
    runner.training_history, runner.active_training, runner.model_training_samples, runner.persisted_models = [], {}, {}, {}
    runner.lock = threading.Lock()
    runner._persist = lambda record: None
    result = runner._train_all(source, symbol, timeframe, candles)
    payload = {'status': result.get('status'), 'sampleCount': result.get('sampleCount', 0), 'models': result.get('models', {})}
    if result.get('reason'):
        payload['reason'] = result['reason']
    if result.get('status') == 'TRAINED':
        payload['training_record'] = result['training_record']
        payload['adapters'] = {name: adapter for name, adapter in runner.models.items() if adapter.state.trained}
        # V17.2: walk-forward OOS validation + leakage probe + Platt calibration (same worker, event loop stays free)
        prepared = result['prepared']
        X = np.asarray(prepared['model_inputs'].get('xgboost'), dtype=float)
        y = np.asarray(prepared['feature_bundle']['targets']['xgboost'], dtype=int)
        n = min(len(X), len(y))
        payload['validation'] = model_validation.validate(runner.models, X[:n], y[:n], horizon=runner.orchestrator.feature_pipeline.feature_horizon)
        for name, report in payload['validation'].get('models', {}).items():
            if report.get('status') == 'OK' and name in payload['adapters']:
                payload['adapters'][name].state.metadata['calibration'] = report['calibration']
    return payload


def build_step8_runner(max_agents: int = 500) -> Step8ModelRunner:
    return Step8ModelRunner(max_agents=max_agents)


def ml_router(runner: Step8ModelRunner | None = None, deep_models=None, signal_service=None):
    from fastapi import APIRouter, Body, Depends, HTTPException

    from market_auth import require_operator_key
    router = APIRouter(prefix='/api/v1')
    model_runner = runner or build_step8_runner()

    @router.get('/ml/status')
    async def get_ml_status():
        deep_status = deep_models.status() if deep_models is not None else {'readyMarketCount': 0}
        if deep_models is not None and hasattr(deep_models, 'runtime_status'):
            deep_status = await deep_models.runtime_status()
        if deep_models is not None and hasattr(deep_models, 'evaluation_status'):
            deep_status['evaluation'] = await deep_models.evaluation_status()
        payload = {
            **model_runner.status(),
            'deepModels': deep_status,
        }
        if signal_service is not None:
            signal_status = signal_service.status()
            payload['activeLearning'] = signal_status.get('onlineLearning', {})
            payload['trainingWorkers'] = {
                'onlineLearning': signal_status.get('onlineLearning', {}),
                'scheduledRefresh': signal_status.get('scheduledModelRefresh', {}),
                'bootstrap': signal_status.get('automaticModelBootstrap', {}),
            }
        return payload

    @router.post('/ml/train')
    async def train_ml(payload: Dict[str, Any] = Body(default_factory=dict), _operator=Depends(require_operator_key)):
        source = str(payload.get('source', 'deriv'))
        symbol = str(payload.get('symbol', 'EUR/USD'))
        timeframe = str(payload.get('timeframe', '1m'))
        if signal_service is not None and deep_models is not None:
            return await signal_service._train_market_models(source, symbol, timeframe)
        if deep_models is not None:
            return await deep_models.train_market(source, symbol, timeframe)
        candles = payload.get('candles') or payload.get('market_data') or []
        if not candles:
            raise HTTPException(status_code=400, detail='candles required for training')
        return await model_runner.train_all_async(source, symbol, timeframe, candles)

    @router.post('/ml/infer')
    async def infer_ml(payload: Dict[str, Any] = Body(default_factory=dict)):
        source = str(payload.get('source', 'deriv'))
        symbol = str(payload.get('symbol', 'EUR/USD'))
        timeframe = str(payload.get('timeframe', '1m'))
        if deep_models is not None:
            return await deep_models.infer_market(source, symbol, timeframe)
        candles = payload.get('candles') or payload.get('market_data') or []
        if not candles:
            raise HTTPException(status_code=400, detail='candles required for inference')
        return model_runner.infer_all(source, symbol, timeframe, candles)

    return router
