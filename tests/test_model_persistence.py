"""Round-trip persistence tests for persisted ML artifacts."""

from __future__ import annotations

from pathlib import Path
import uuid

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("torch")
pytest.importorskip("xgboost")

from openbb_forecast.models.ensemble import StackingForecaster
from openbb_forecast.models.lstm import LSTMForecaster
from openbb_forecast.models.transformer import TransformerForecaster


@pytest.fixture
def sample_sequences():
    X = np.linspace(0.0, 1.0, 180, dtype=np.float32).reshape(60, 3, 1)
    y = (X[:, -1, :] + 0.01).astype(np.float32)
    return X, y


@pytest.mark.parametrize(
    ("factory", "suffix"),
    [
        (
            lambda: LSTMForecaster(
                hidden_size=8,
                num_layers=1,
                epochs=1,
                batch_size=8,
                patience=1,
                device="cpu",
                version="20260417",
            ),
            ".pt",
        ),
        (
            lambda: TransformerForecaster(
                d_model=8,
                n_heads=1,
                num_layers=1,
                dim_feedforward=16,
                epochs=1,
                batch_size=8,
                patience=1,
                device="cpu",
                version="20260417",
            ),
            ".pt",
        ),
        (
            lambda: StackingForecaster(
                n_inner_splits=2,
                xgb_params={
                    "n_estimators": 5,
                    "max_depth": 2,
                    "learning_rate": 0.1,
                    "subsample": 1.0,
                    "colsample_bytree": 1.0,
                },
                rf_params={"n_estimators": 5, "max_depth": 3, "min_samples_leaf": 1},
                gbr_params={"n_estimators": 5, "max_depth": 2, "learning_rate": 0.1, "subsample": 1.0},
                version="20260417",
            ),
            ".pkl",
        ),
    ],
)
def test_model_round_trip_predictions_are_identical(sample_sequences, factory, suffix):
    X, y = sample_sequences
    model = factory()
    model.fit(X, y)
    preds_before = model.predict(X[:5])

    artifacts_dir = Path.cwd() / "test_artifacts"
    artifacts_dir.mkdir(exist_ok=True)
    artifact = artifacts_dir / f"model_{uuid.uuid4().hex}{suffix}"
    model.save(artifact)
    loaded = model.__class__.load(artifact)
    preds_after = loaded.predict(X[:5])

    assert np.allclose(preds_before, preds_after)
