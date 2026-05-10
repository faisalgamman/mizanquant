"""ModelFactory — create any forecasting model by name.

Port of DeepLearningFactory from Stock-Prediction-Models, adapted to
the openbb_forecast BaseForecaster interface.
"""

from __future__ import annotations

from openbb_forecast.models.base import BaseForecaster
from openbb_forecast.models.lstm import LSTMForecaster
from openbb_forecast.models.transformer import TransformerForecaster
from openbb_forecast.models.ensemble import StackingForecaster
from openbb_forecast.models.gru import GRUForecaster, GRUVariant
from openbb_forecast.models.cnn import CNNForecaster
from openbb_forecast.models.rnn_variants import RNNVariantForecaster
from openbb_forecast.models.arima import ARIMAForecaster


MODEL_REGISTRY: dict[str, type[BaseForecaster]] = {
    # Original models
    "lstm": LSTMForecaster,
    "transformer": TransformerForecaster,
    "ensemble": StackingForecaster,
    "arima": ARIMAForecaster,
    # GRU variants
    "gru": lambda **kw: GRUForecaster(variant=GRUVariant.STANDARD, **kw),
    "bigru": lambda **kw: GRUForecaster(variant=GRUVariant.BIDIRECTIONAL, **kw),
    "gru_2path": lambda **kw: GRUForecaster(variant=GRUVariant.TWOPATH, **kw),
    "gru_seq2seq": lambda **kw: GRUForecaster(variant=GRUVariant.SEQ2SEQ, **kw),
    "bigru_seq2seq": lambda **kw: GRUForecaster(variant=GRUVariant.BIDIR_SEQ2SEQ, **kw),
    "gru_vae": lambda **kw: GRUForecaster(variant=GRUVariant.VAE, **kw),
    # CNN variants
    "cnn_seq2seq": lambda **kw: CNNForecaster(variant="cnn_seq2seq", **kw),
    "dilated_cnn": lambda **kw: CNNForecaster(variant="dilated_cnn", **kw),
    # RNN variants
    "vanilla": lambda **kw: RNNVariantForecaster(variant="vanilla", **kw),
    "vanilla_bi": lambda **kw: RNNVariantForecaster(variant="vanilla_bi", **kw),
    "vanilla_2path": lambda **kw: RNNVariantForecaster(variant="vanilla_2path", **kw),
    "lstm_2path": lambda **kw: RNNVariantForecaster(variant="lstm_2path", **kw),
    "bilstm_seq2seq": lambda **kw: RNNVariantForecaster(variant="bilstm_seq2seq", **kw),
    "lstm_vae": lambda **kw: RNNVariantForecaster(variant="lstm_vae", **kw),
    "attention_rnn": lambda **kw: RNNVariantForecaster(variant="attention_rnn", **kw),
}

MODEL_NAMES = list(MODEL_REGISTRY.keys())

# Map model names to their artifact prefixes
MODEL_ARTIFACT_PREFIX: dict[str, str] = {
    "lstm": "lstm",
    "transformer": "transformer",
    "ensemble": "ensemble",
    "arima": "arima",
    "gru": "gru", "bigru": "gru", "gru_2path": "gru", "gru_seq2seq": "gru", "bigru_seq2seq": "gru", "gru_vae": "gru",
    "cnn_seq2seq": "cnn", "dilated_cnn": "cnn",
    "vanilla": "rnn_variant", "vanilla_bi": "rnn_variant", "vanilla_2path": "rnn_variant",
    "lstm_2path": "rnn_variant", "bilstm_seq2seq": "rnn_variant", "lstm_vae": "rnn_variant",
    "attention_rnn": "rnn_variant",
}

# Class registry for persistence loading (each name maps to the actual class, not a lambda)
MODEL_CLASSES: dict[str, type[BaseForecaster]] = {
    "lstm": LSTMForecaster,
    "transformer": TransformerForecaster,
    "ensemble": StackingForecaster,
    "arima": ARIMAForecaster,
    "gru": GRUForecaster, "bigru": GRUForecaster, "gru_2path": GRUForecaster,
    "gru_seq2seq": GRUForecaster, "bigru_seq2seq": GRUForecaster, "gru_vae": GRUForecaster,
    "cnn_seq2seq": CNNForecaster, "dilated_cnn": CNNForecaster,
    "vanilla": RNNVariantForecaster, "vanilla_bi": RNNVariantForecaster,
    "vanilla_2path": RNNVariantForecaster, "lstm_2path": RNNVariantForecaster,
    "bilstm_seq2seq": RNNVariantForecaster, "lstm_vae": RNNVariantForecaster,
    "attention_rnn": RNNVariantForecaster,
}


def create_model(model_name: str, **kwargs) -> BaseForecaster:
    """Create a forecasting model by name.

    Args:
        model_name: One of MODEL_NAMES.
        **kwargs: Passed to the model constructor.

    Returns:
        A BaseForecaster instance.
    """
    key = model_name.lower().replace("-", "_")
    factory = MODEL_REGISTRY.get(key)
    if factory is None:
        raise ValueError(
            f"Unknown model '{model_name}'. "
            f"Available: {sorted(MODEL_NAMES)}"
        )
    return factory(**kwargs)


def get_model_class(model_name: str) -> type[BaseForecaster]:
    """Return the model class for persistence loading.

    Unlike create_model(), this returns the class itself (not an instance)
    so callers can use cls.load() on persisted artifacts.
    """
    key = model_name.lower().replace("-", "_")
    cls = MODEL_CLASSES.get(key)
    if cls is None:
        raise ValueError(
            f"Unknown model '{model_name}'. "
            f"Available: {sorted(MODEL_CLASSES)}"
        )
    return cls


def get_model_suffix(model_name: str) -> str:
    """Return the artifact file suffix for a model name."""
    prefix = artifact_prefix(model_name)
    if prefix in ("ensemble", "arima"):
        return ".pkl"
    return ".pt"


def artifact_prefix(model_name: str) -> str:
    """Return the artifact directory prefix for a model name."""
    key = model_name.lower().replace("-", "_")
    return MODEL_ARTIFACT_PREFIX.get(key, key)
