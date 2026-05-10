"""ARIMA forecaster — lightweight wrapper around statsmodels ARIMA.

Ported from the stacking/stack-rnn-arima-xgb.ipynb approach in
Stock-Prediction-Models. Provides a BaseForecaster-compatible interface
for use in ensemble stacking.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import numpy as np
import pickle

from openbb_forecast.models.base import BaseForecaster
from openbb_forecast.models.persistence import default_version, write_metadata


class ARIMAForecaster(BaseForecaster):
    """ARIMA forecaster wrapping statsmodels ARIMA.

    Fits ARIMA(p,d,q) on raw time-series data and predicts future values.
    Note: unlike deep learning forecasters that use features, this operates
    on a single univariate series and forecasts by extrapolation.

    Args:
        order: ARIMA (p,d,q) order tuple.
        seasonal: Optional seasonal order (P,D,Q,s) tuple.
        version: Artifact version string.
    """

    name = "arima"

    def __init__(
        self,
        order: tuple[int, int, int] = (5, 1, 0),
        seasonal: tuple[int, int, int, int] | None = None,
        version: str | None = None,
    ):
        self.version = version or default_version()
        self.order = order
        self.seasonal = seasonal
        self._model = None
        self._fitted_order = None

    def reset(self) -> None:
        self._model = None
        self._fitted_order = None

    def fit(self, X_train: np.ndarray, y_train: np.ndarray, **kwargs) -> None:
        """Fit ARIMA on the training data.

        Since ARIMA works on raw sequences (not feature windows), we extract the
        last `sequence_length` points from X_train and forecast forward.
        For ensemble compatibility, this stores the series for later prediction.
        """
        self._input_shape = X_train.shape
        self._series = X_train[:, -1, 0].flatten() if X_train.ndim == 3 else X_train.flatten()
        self._fitted_order = self.order

    def _fit_arima(self, series: np.ndarray) -> object:
        """Fit statsmodels ARIMA on a 1-D series."""
        try:
            from statsmodels.tsa.arima.model import ARIMA as ARIMAModel
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                model = ARIMAModel(series, order=self.order, seasonal_order=self.seasonal)
                return model.fit(method_kwargs={"disp": False})
        except Exception:
            return None

    def predict(self, X: np.ndarray) -> np.ndarray:
        """Predict by extrapolating the fitted ARIMA model.

        For each sample in X, predicts the next value using the ARIMA model.
        """
        if self._model is None and hasattr(self, "_series"):
            self._model = self._fit_arima(self._series)

        if self._model is None:
            return np.zeros((len(X), 1))

        n = len(X)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            forecast = self._model.forecast(steps=n)
        return np.asarray(forecast, dtype=np.float32).reshape(-1, 1)

    def save(self, path: Path) -> None:
        payload = {
            "config": {
                "order": self.order,
                "seasonal": self.seasonal,
                "version": self.version,
            },
            "series": getattr(self, "_series", None),
            "input_shape": getattr(self, "_input_shape", None),
        }
        with path.open("wb") as fh:
            pickle.dump(payload, fh)
        write_metadata(
            path.with_suffix(".meta.json"),
            {"name": self.name, "version": self.version},
        )

    @classmethod
    def load(cls, path: Path) -> "ARIMAForecaster":
        with path.open("rb") as fh:
            payload = pickle.load(fh)
        config = payload.get("config", {})
        model = cls(**config)
        model._series = payload.get("series")
        model._input_shape = payload.get("input_shape")
        if model._series is not None:
            model._model = model._fit_arima(model._series)
        return model
