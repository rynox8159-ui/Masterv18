import asyncio
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "main_source_code" / "backend"))

import ml.ml_model_adapters as step8
from signal_service import SignalService


def test_step8_persistence_is_scoped_to_one_market(tmp_path, monkeypatch):
    monkeypatch.setattr(step8, "MODEL_STORE_DIR", tmp_path)
    runner = step8.Step8ModelRunner(max_agents=5)
    market = runner.market_key("deriv", "R_100", "1m")
    models = runner._new_model_set()
    models["lstm"].state.trained = True
    runner.market_models[market] = models
    runner.model_training_samples[market] = {"lstm": 300}
    runner._persist({"source": "deriv", "symbol": "R_100", "timeframe": "1m"})

    restored = step8.Step8ModelRunner(max_agents=5)

    assert restored.models_for_market("deriv", "R_100", "1m")["lstm"].state.trained
    assert not restored.models_for_market("deriv", "R_75", "1m")["lstm"].state.trained
    assert restored.model_training_samples[market]["lstm"] == 300


def test_step8_inference_never_uses_another_markets_adapter():
    class Model:
        def __init__(self, direction):
            self.direction = direction
            self.state = SimpleNamespace(
                trained=True, last_fit_at=1, last_prediction={},
                metadata={"readyForLive": True},
            )

        def predict(self, _features):
            probability = 0.8 if self.direction == "CALL" else 0.2
            return {"direction": self.direction, "probability": probability}

    runner = step8.Step8ModelRunner.__new__(step8.Step8ModelRunner)
    runner.models = runner._new_model_set()
    runner.market_models = {
        "deriv:R_100:1m": {**runner._new_model_set(), "xgboost": Model("CALL")},
    }
    runner.last_inference = None
    runner.__dict__["_infer_cache"] = {}
    runner.prepare_market = lambda *_args: {
        "feature_bundle": {"metadata": {"ml_ready": True}},
        "model_inputs": {"xgboost": np.ones((1, 2))},
        "behaviour": {},
        "agent_snapshot": {},
    }

    matched = runner.infer_all("deriv", "R_100", "1m", [{"epoch": 60, "close": 100}])
    isolated = runner.infer_all("deriv", "R_75", "1m", [{"epoch": 60, "close": 100}])

    assert matched["predictions"]["xgboost"]["direction"] == "CALL"
    assert isolated["predictions"] == {}
    assert isolated["reason"] == "NO_TRAINED_ADVISORY_MODELS"
    assert runner.adapter_ready_for_live("xgboost", "deriv", "R_100", "1m")
    assert not runner.adapter_ready_for_live("xgboost", "deriv", "R_75", "1m")


def test_binary_live_board_excludes_paper_shadow_signals():
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
        collection = Collection()
        signals = SignalService.__new__(SignalService)
        signals.db = SimpleNamespace(live_signals=collection)
        signals._pre_signals = {
            ("deriv", "R_100", "1m"): {
                "source": "deriv", "validationTier": "PAPER_SHADOW",
                "entryEpoch": time.time() + 10,
            },
            ("deriv", "R_75", "1m"): {
                "source": "deriv", "validationTier": "LIVE_VALIDATED",
                "entryEpoch": time.time() + 10, "setupConfidence": 90,
            },
        }
        signals.status = lambda: {}

        board = await signals.live(source="deriv")

        assert all(
            query["validationTier"]["$in"] == ["LIVE_VALIDATED"]
            for query in collection.queries
        )
        assert [signal["validationTier"] for signal in board["preSignals"]] == ["LIVE_VALIDATED"]

    asyncio.run(verify())
