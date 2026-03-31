"""Leak-free data preprocessing for time-series forecasting.

Critical fix: the original repo fits MinMaxScaler on ALL data before splitting,
leaking test-set statistics into training. SafeScaler enforces fit-on-train-only.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


class SafeScaler:
    """Scaler that only fits on training data — prevents data leakage.

    Usage:
        scaler = SafeScaler(method="minmax")
        scaler.fit(train_data)                # fit on TRAIN only
        train_scaled = scaler.transform(train_data)
        test_scaled = scaler.transform(test_data)  # uses train statistics
        predictions = scaler.inverse_transform(model_output)
    """

    def __init__(self, method: str = "minmax", feature_range: tuple[float, float] = (0.0, 1.0)):
        if method not in ("minmax", "standard"):
            raise ValueError(f"method must be 'minmax' or 'standard', got '{method}'")
        self.method = method
        self.feature_range = feature_range
        self._fitted = False
        self._min: np.ndarray | None = None
        self._max: np.ndarray | None = None
        self._mean: np.ndarray | None = None
        self._std: np.ndarray | None = None

    def fit(self, data: np.ndarray) -> SafeScaler:
        """Fit scaler statistics from training data only."""
        data = np.asarray(data, dtype=np.float64)
        if self.method == "minmax":
            self._min = data.min(axis=0)
            self._max = data.max(axis=0)
            # Prevent division by zero for constant features
            range_val = self._max - self._min
            range_val[range_val == 0] = 1.0
            self._max = self._min + range_val
        else:
            self._mean = data.mean(axis=0)
            self._std = data.std(axis=0)
            self._std[self._std == 0] = 1.0
        self._fitted = True
        return self

    def transform(self, data: np.ndarray) -> np.ndarray:
        """Transform data using statistics computed during fit()."""
        if not self._fitted:
            raise RuntimeError("SafeScaler.fit() must be called before transform()")
        data = np.asarray(data, dtype=np.float64)
        if self.method == "minmax":
            lo, hi = self.feature_range
            scaled = (data - self._min) / (self._max - self._min)
            return scaled * (hi - lo) + lo
        return (data - self._mean) / self._std

    def inverse_transform(self, data: np.ndarray) -> np.ndarray:
        """Reverse the scaling transformation."""
        if not self._fitted:
            raise RuntimeError("SafeScaler.fit() must be called before inverse_transform()")
        data = np.asarray(data, dtype=np.float64)
        if self.method == "minmax":
            lo, hi = self.feature_range
            unscaled = (data - lo) / (hi - lo)
            return unscaled * (self._max - self._min) + self._min
        return data * self._std + self._mean


def create_sequences(
    data: np.ndarray,
    sequence_length: int,
    forecast_horizon: int = 1,
) -> tuple[np.ndarray, np.ndarray]:
    """Create input/target pairs for supervised time-series learning.

    X[i] = data[i : i + sequence_length]
    y[i] = data[i + sequence_length : i + sequence_length + forecast_horizon]

    No future information leaks by construction — each target is strictly
    after the corresponding input window.

    Args:
        data: Array of shape (n_samples,) or (n_samples, n_features).
        sequence_length: Number of past time steps per input.
        forecast_horizon: Number of future steps to predict.

    Returns:
        X: shape (n_sequences, sequence_length, n_features)
        y: shape (n_sequences, forecast_horizon) or (n_sequences, forecast_horizon, n_features)
    """
    data = np.asarray(data, dtype=np.float32)
    if data.ndim == 1:
        data = data.reshape(-1, 1)

    n = len(data) - sequence_length - forecast_horizon + 1
    if n <= 0:
        raise ValueError(
            f"Not enough data ({len(data)}) for sequence_length={sequence_length} "
            f"and forecast_horizon={forecast_horizon}. Need at least {sequence_length + forecast_horizon}."
        )

    X = np.array([data[i : i + sequence_length] for i in range(n)])
    y = np.array([data[i + sequence_length : i + sequence_length + forecast_horizon] for i in range(n)])

    # Squeeze single-feature targets to 2D
    if y.shape[-1] == 1:
        y = y.squeeze(-1)

    return X, y


def create_features(df: pd.DataFrame, target_col: str = "close") -> pd.DataFrame:
    """Engineer backward-looking features from OHLCV data.

    All features use only past/current data — no future information.

    Features created:
        - returns: log return ln(close_t / close_{t-1})
        - volatility_5/10/20: rolling std of returns
        - momentum_5/10/20: price change over N periods
        - rsi_14: Relative Strength Index (14-period)
        - macd/macd_signal: MACD and signal line
        - volume_ratio: volume / 20-period mean volume
        - high_low_range: (high - low) / close
        - open_close_range: (close - open) / open

    Args:
        df: DataFrame with OHLCV columns (open, high, low, close, volume).
        target_col: Name of the target column.

    Returns:
        DataFrame with original columns plus engineered features, NaN rows dropped.
    """
    df = df.copy()
    close = df[target_col].astype(float)

    # Log returns
    df["returns"] = np.log(close / close.shift(1))

    # Rolling volatility
    for w in (5, 10, 20):
        df[f"volatility_{w}"] = df["returns"].rolling(w).std()

    # Momentum (price change over N periods)
    for w in (5, 10, 20):
        df[f"momentum_{w}"] = close / close.shift(w) - 1.0

    # RSI (14-period)
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.rolling(14, min_periods=1).mean()
    avg_loss = loss.rolling(14, min_periods=1).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["rsi_14"] = 100.0 - (100.0 / (1.0 + rs))

    # MACD
    ema_12 = close.ewm(span=12, adjust=False).mean()
    ema_26 = close.ewm(span=26, adjust=False).mean()
    df["macd"] = ema_12 - ema_26
    df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()

    # Volume ratio
    if "volume" in df.columns:
        vol = df["volume"].astype(float)
        df["volume_ratio"] = vol / vol.rolling(20, min_periods=1).mean()
    else:
        df["volume_ratio"] = 1.0

    # Range features
    if "high" in df.columns and "low" in df.columns:
        df["high_low_range"] = (df["high"].astype(float) - df["low"].astype(float)) / close
    if "open" in df.columns:
        df["open_close_range"] = (close - df["open"].astype(float)) / df["open"].astype(float)

    # Drop rows with NaN from rolling calculations
    df.dropna(inplace=True)
    df.reset_index(drop=True, inplace=True)

    return df
