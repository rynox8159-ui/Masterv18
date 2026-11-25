import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "main_source_code" / "backend"))

from dl.deep_model_service import (  # noqa: E402
    DEEP_MODEL_ARCHITECTURES,
    DeepModelService,
    _CandlestickSequenceModel,
)


def _calibrator():
    return SimpleNamespace(
        X_thresholds_=np.asarray([0.0, 1.0]),
        y_thresholds_=np.asarray([0.1, 0.9]),
    )


def test_four_architecture_artifact_round_trips_and_produces_logits():
    models = {
        architecture: model_type(len(DeepModelService.FEATURE_NAMES))
        for architecture, model_type in DEEP_MODEL_ARCHITECTURES.items()
    }

    artifact = DeepModelService._export_artifact(
        models, _calibrator(), np.zeros(len(DeepModelService.FEATURE_NAMES)),
        np.ones(len(DeepModelService.FEATURE_NAMES)), DeepModelService.FEATURE_NAMES,
    )

    assert DeepModelService._valid_artifact(artifact)
    restored = DeepModelService._load_model(artifact)
    assert list(restored) == ["lstm", "gru", "transformer_encoder", "cnn_1d"]
    sample = torch.zeros(1, DeepModelService.SEQUENCE_WINDOW, len(DeepModelService.FEATURE_NAMES))
    with torch.no_grad():
        assert all(model(sample).shape == (1,) for model in restored.values())


def test_existing_lstm_artifacts_remain_loadable():
    model = _CandlestickSequenceModel(len(DeepModelService.FEATURE_NAMES), hidden_size=64)
    artifact = {
        "version": DeepModelService.LEGACY_MODEL_VERSION,
        "model": "pytorch_lstm",
        "hiddenSize": 64,
        "featureNames": list(DeepModelService.FEATURE_NAMES),
        "scalerMean": np.zeros(len(DeepModelService.FEATURE_NAMES)).tolist(),
        "scalerScale": np.ones(len(DeepModelService.FEATURE_NAMES)).tolist(),
        "stateDict": {
            name: value.detach().cpu().numpy().astype(float).tolist()
            for name, value in model.state_dict().items()
        },
        "calibration": {"method": "isotonic", "x": [0.0, 1.0], "y": [0.1, 0.9]},
    }

    assert DeepModelService._valid_artifact(artifact)
    assert list(DeepModelService._load_model(artifact)) == ["lstm"]
