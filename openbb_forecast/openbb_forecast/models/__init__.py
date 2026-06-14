"""Forecasting models — LSTM, Transformer, Ensemble, GRU, CNN, RNN variants, and ARIMA."""

# The torch-backed DL models + the model factory are OPTIONAL. When torch is not installed
# (the Railway cost-optimized build), they import as None / [] so this PACKAGE still loads —
# ARIMA, regime_engine and the Monte-Carlo simulator keep working. Importing
# `openbb_forecast.models` must NEVER hard-fail just because torch is absent: it used to,
# which silently 500'd every consumer (e.g. halal_screener -> smart_ensemble -> regime_engine,
# which broke the weekly /buys scanner).
try:
    from openbb_forecast.models.lstm import LSTMForecaster
    from openbb_forecast.models.transformer import TransformerForecaster
    from openbb_forecast.models.ensemble import StackingForecaster, BASE_LEARNER_NAMES
    from openbb_forecast.models.gru import GRUForecaster, GRUVariant
    from openbb_forecast.models.cnn import CNNForecaster
    from openbb_forecast.models.rnn_variants import RNNVariantForecaster
    from openbb_forecast.models.factory import create_model, get_model_class, get_model_suffix, MODEL_NAMES
except ImportError:  # torch (or another DL dependency) not installed
    LSTMForecaster = TransformerForecaster = StackingForecaster = None
    GRUForecaster = GRUVariant = CNNForecaster = RNNVariantForecaster = None
    create_model = get_model_class = get_model_suffix = None
    MODEL_NAMES = []
    BASE_LEARNER_NAMES = []

# ARIMA is pure statsmodels (no torch) — always available.
from openbb_forecast.models.arima import ARIMAForecaster

__all__ = [
    "LSTMForecaster", "TransformerForecaster", "StackingForecaster",
    "GRUForecaster", "GRUVariant", "CNNForecaster", "RNNVariantForecaster",
    "ARIMAForecaster",
    "create_model", "get_model_class", "get_model_suffix",
    "MODEL_NAMES", "BASE_LEARNER_NAMES",
]
