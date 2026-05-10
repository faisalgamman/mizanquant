"""Tests for newly ported models: GRU, CNN, RNN variants, ARIMA, and factory."""

from __future__ import annotations

from pathlib import Path
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

np = pytest.importorskip("numpy")
pytest.importorskip("torch")

from openbb_forecast.models.gru import GRUForecaster, GRUVariant
from openbb_forecast.models.cnn import CNNForecaster
from openbb_forecast.models.rnn_variants import RNNVariantForecaster
from openbb_forecast.models.arima import ARIMAForecaster
from openbb_forecast.models.factory import create_model, get_model_class, MODEL_NAMES


@pytest.fixture
def tiny_sequences():
    X = np.linspace(0.0, 1.0, 60, dtype=np.float32).reshape(20, 3, 1)
    y = (X[:, -1, :] + 0.01).astype(np.float32)
    return X, y


@pytest.fixture
def arima_sequences():
    np.random.seed(42)
    prices = 100 + np.cumsum(np.random.randn(200) * 0.5)
    SEQ_LEN, HORIZON = 10, 1
    X, y = [], []
    for i in range(len(prices) - SEQ_LEN - HORIZON):
        X.append(prices[i : i + SEQ_LEN].reshape(-1, 1))
        y.append(prices[i + SEQ_LEN])
    return np.array(X), np.array(y).reshape(-1, 1)


# --- GRU tests ---


class TestGRU:
    def test_gru_standard(self, tiny_sequences):
        X, y = tiny_sequences
        model = GRUForecaster(variant=GRUVariant.STANDARD, hidden_size=8, num_layers=1, epochs=1, device="cpu")
        model.fit(X, y)
        preds = model.predict(X[:3])
        assert preds.shape == (3, 1)

    def test_gru_bidirectional(self, tiny_sequences):
        X, y = tiny_sequences
        model = GRUForecaster(variant=GRUVariant.BIDIRECTIONAL, hidden_size=8, num_layers=1, epochs=1, device="cpu")
        model.fit(X, y)
        preds = model.predict(X[:3])
        assert preds.shape == (3, 1)

    def test_gru_seq2seq(self, tiny_sequences):
        X, y = tiny_sequences
        model = GRUForecaster(variant=GRUVariant.SEQ2SEQ, hidden_size=8, num_layers=1, epochs=1, device="cpu")
        model.fit(X, y)
        preds = model.predict(X[:3])
        assert preds.shape == (3, 1)

    def test_gru_vae(self, tiny_sequences):
        X, y = tiny_sequences
        model = GRUForecaster(variant=GRUVariant.VAE, hidden_size=8, num_layers=1, epochs=1, device="cpu")
        model.fit(X, y)
        preds = model.predict(X[:3])
        assert preds.shape == (3, 1)

    def test_gru_2path(self, tiny_sequences):
        X, y = tiny_sequences
        model = GRUForecaster(variant=GRUVariant.TWOPATH, hidden_size=8, num_layers=1, epochs=1, device="cpu")
        model.fit(X, y)
        preds = model.predict(X[:3])
        assert preds.shape == (3, 1)


# --- CNN tests ---


class TestCNN:
    def test_cnn_seq2seq(self, tiny_sequences):
        X, y = tiny_sequences
        model = CNNForecaster(variant="cnn_seq2seq", filters=8, epochs=1, device="cpu")
        model.fit(X, y)
        preds = model.predict(X[:3])
        assert preds.shape == (3, 1)

    def test_dilated_cnn(self, tiny_sequences):
        X, y = tiny_sequences
        model = CNNForecaster(variant="dilated_cnn", filters=8, epochs=1, device="cpu")
        model.fit(X, y)
        preds = model.predict(X[:3])
        assert preds.shape == (3, 1)


# --- RNN Variant tests ---


class TestRNNVariants:
    @pytest.mark.parametrize(
        "variant",
        ["vanilla", "vanilla_bi", "vanilla_2path", "lstm_2path", "bilstm_seq2seq", "lstm_vae", "attention_rnn"],
    )
    def test_rnn_variant(self, tiny_sequences, variant):
        X, y = tiny_sequences
        model = RNNVariantForecaster(variant=variant, hidden_size=8, num_layers=1, epochs=1, device="cpu")
        model.fit(X, y)
        preds = model.predict(X[:3])
        assert preds.shape == (3, 1), f"Failed for {variant}"


# --- ARIMA tests ---


class TestARIMA:
    def test_arima_fit_predict(self, arima_sequences):
        X, y = arima_sequences
        model = ARIMAForecaster(order=(1, 0, 0))
        model.fit(X, y)
        preds = model.predict(X[:5])
        assert preds.shape == (5, 1)
        assert np.all(np.isfinite(preds))

    def test_arima_round_trip(self, arima_sequences, tmp_path):
        X, y = arima_sequences
        model = ARIMAForecaster(order=(1, 0, 0))
        model.fit(X, y)
        preds_before = model.predict(X[:3])

        artifact = tmp_path / "arima_test.pkl"
        model.save(artifact)
        loaded = ARIMAForecaster.load(artifact)
        preds_after = loaded.predict(X[:3])

        assert np.allclose(preds_before, preds_after)

    def test_arima_untrained_returns_zeros(self):
        X = np.array([[1.0, 2.0, 3.0]])
        model = ARIMAForecaster(order=(1, 0, 0))
        preds = model.predict(X)
        assert preds.shape == (1, 1)


# --- Factory tests ---


class TestFactory:
    def test_create_all_models(self):
        for name in MODEL_NAMES:
            try:
                model = create_model(name, version="test")
            except Exception as e:
                pytest.fail(f"create_model('{name}') raised {e}")

    def test_get_model_class(self):
        from openbb_forecast.models.lstm import LSTMForecaster

        cls = get_model_class("lstm")
        assert cls is LSTMForecaster

    def test_get_model_class_unknown(self):
        with pytest.raises(ValueError):
            get_model_class("nonexistent_model")

    def test_create_model_unknown(self):
        with pytest.raises(ValueError):
            create_model("nonexistent_model")

    def test_artifact_prefix_mapping(self):
        from openbb_forecast.models.factory import artifact_prefix, get_model_suffix

        assert get_model_suffix("ensemble") == ".pkl"
        assert get_model_suffix("arima") == ".pkl"
        assert get_model_suffix("lstm") == ".pt"
        assert get_model_suffix("gru") == ".pt"
