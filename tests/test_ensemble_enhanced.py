"""Tests for enhanced StackingForecaster with configurable base learners, ARIMA, and XGBoost meta."""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

np = pytest.importorskip("numpy")
pytest.importorskip("xgboost")

from openbb_forecast.models.ensemble import StackingForecaster, BASE_LEARNER_NAMES


@pytest.fixture
def seq_data():
    np.random.seed(42)
    X = np.random.randn(60, 10, 1).astype(np.float32)
    y = (X[:, -1, :] + np.random.randn(60, 1) * 0.01).astype(np.float32)
    return X, y


class TestConfigurableBaseLearners:
    def test_default_base_learners(self, seq_data):
        X, y = seq_data
        model = StackingForecaster(n_inner_splits=2)
        model.fit(X, y)
        preds = model.predict(X[:3])
        assert preds.shape == (3, 1)

    def test_extra_base_learners(self, seq_data):
        X, y = seq_data
        model = StackingForecaster(base_learners=["xgb", "rf", "gbr", "ada", "et"], n_inner_splits=2)
        model.fit(X, y)
        preds = model.predict(X[:3])
        assert preds.shape == (3, 1)

    @pytest.mark.parametrize("meta", ["ridge", "xgb"])
    def test_meta_learners(self, seq_data, meta):
        X, y = seq_data
        model = StackingForecaster(meta_learner=meta, n_inner_splits=2)
        model.fit(X, y)
        preds = model.predict(X[:3])
        assert preds.shape == (3, 1)


class TestARIMAInEnsemble:
    def test_arima_base_learner(self, seq_data):
        X, y = seq_data
        model = StackingForecaster(base_learners=["xgb", "arima"], n_inner_splits=2, arima_order=(1, 0, 0))
        model.fit(X, y)
        preds = model.predict(X[:3])
        assert preds.shape == (3, 1)

    def test_arima_enabled_flag(self, seq_data):
        X, y = seq_data
        model = StackingForecaster(arima_enabled=True, n_inner_splits=2, arima_order=(1, 0, 0))
        model.fit(X, y)
        preds = model.predict(X[:3])
        assert preds.shape == (3, 1)


class TestAuxPredictions:
    def test_aux_predictions(self, seq_data):
        X, y = seq_data
        aux = np.random.randn(len(X)).astype(np.float32)
        model = StackingForecaster(n_inner_splits=2)
        model.fit(X, y, aux_predictions=aux)
        preds = model.predict(X[:3], aux_predictions=aux[:3])
        assert preds.shape == (3, 1)


class TestRoundTrip:
    def test_save_load(self, seq_data, tmp_path):
        X, y = seq_data
        model = StackingForecaster(base_learners=["xgb", "rf"], n_inner_splits=2)
        model.fit(X, y)
        preds_before = model.predict(X[:3])

        artifact = tmp_path / "ensemble_test.pkl"
        model.save(artifact)
        loaded = StackingForecaster.load(artifact)
        preds_after = loaded.predict(X[:3])

        assert np.allclose(preds_before, preds_after)

    def test_save_load_with_arima(self, seq_data, tmp_path):
        X, y = seq_data
        model = StackingForecaster(
            base_learners=["arima", "xgb"], n_inner_splits=2, arima_order=(1, 0, 0)
        )
        model.fit(X, y)
        preds_before = model.predict(X[:3])

        artifact = tmp_path / "ensemble_arima.pkl"
        model.save(artifact)
        loaded = StackingForecaster.load(artifact)
        preds_after = loaded.predict(X[:3])

        assert np.allclose(preds_before, preds_after, atol=0.05)


class TestBASE_LEARNER_NAMES:
    def test_registry(self):
        assert "xgb" in BASE_LEARNER_NAMES
        assert "rf" in BASE_LEARNER_NAMES
        assert "gbr" in BASE_LEARNER_NAMES
        assert "ada" in BASE_LEARNER_NAMES
        assert "et" in BASE_LEARNER_NAMES
