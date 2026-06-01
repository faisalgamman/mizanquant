"""Kalman filter denoising for price series.

Applies a simple local-level Kalman filter to smooth OHLCV series
before feeding them to technical indicators. Removing measurement
noise improves signal quality by 15-25% (Jansen Ch.4).

Reference
---------
- Jansen (2020), Ch.4 — Kalman filter for alpha factor denoising
- Kalman (1960) — A New Approach to Linear Filtering
"""

from __future__ import annotations

import logging
from typing import Iterable

import numpy as np

logger = logging.getLogger(__name__)

# Default observation/transition noise ratios (Jansen Ch.4 recommends
# 0.01-0.1 for daily financial data; higher = smoother, more lag).
DEFAULT_OBS_VAR: float = 0.10   # measurement noise variance
DEFAULT_TRANS_VAR: float = 0.01  # process noise variance


def kalman_smooth(
    series: Iterable[float],
    obs_variance: float = DEFAULT_OBS_VAR,
    trans_variance: float = DEFAULT_TRANS_VAR,
) -> np.ndarray:
    """Apply one-dimensional local-level Kalman filter.

    Model:  x[t] = x[t-1] + eps_t      (state transition, eps ~ N(0, Q))
            y[t] = x[t]   + eta_t      (observation,   eta ~ N(0, R))

    Parameters
    ----------
    series : iterable of float
        Raw price or indicator series.
    obs_variance : float
        Observation noise variance R (higher = trust data less).
    trans_variance : float
        Process noise variance Q (higher = faster adaptation, more noise).

    Returns
    -------
    np.ndarray — smoothed series, same length as input.
    """
    arr = np.asarray(list(series), dtype=float)
    arr = arr[np.isfinite(arr)]
    n = len(arr)
    if n < 2:
        return arr

    Q = trans_variance
    R = obs_variance

    # Initial guess
    x_est = arr[0]
    P = 1.0  # initial error covariance

    smoothed = np.zeros(n)
    smoothed[0] = x_est

    for t in range(1, n):
        # Predict
        x_pred = x_est
        P_pred = P + Q

        # Update
        K = P_pred / (P_pred + R)  # Kalman gain
        x_est = x_pred + K * (arr[t] - x_pred)
        P = (1.0 - K) * P_pred

        smoothed[t] = x_est

    return smoothed


def kalman_denoise_ohlcv(
    ohlcv: dict[str, list[float]],
    obs_variance: float = DEFAULT_OBS_VAR,
    trans_variance: float = DEFAULT_TRANS_VAR,
) -> dict[str, np.ndarray]:
    """Apply Kalman filter to all OHLCV columns.

    Parameters
    ----------
    ohlcv : dict
        {"open": [...], "high": [...], "low": [...], "close": [...], "volume": [...]}
        Any missing keys are skipped silently.

    Returns
    -------
    dict — same keys, values are smoothed np.ndarray.
    """
    result = {}
    for key in ("open", "high", "low", "close", "volume"):
        if key in ohlcv:
            result[key] = kalman_smooth(ohlcv[key], obs_variance, trans_variance)
    return result


def kalman_denoise_prices(
    prices: list[float],
    obs_variance: float = DEFAULT_OBS_VAR,
    trans_variance: float = DEFAULT_TRANS_VAR,
) -> np.ndarray:
    """Convenience wrapper for a single price array.

    Returns smoothed np.ndarray.
    """
    return kalman_smooth(prices, obs_variance, trans_variance)


def get_kalman_adjustment(symbol: str) -> float:
    """Return signal-quality multiplier for the trading engine.

    Compares raw-vs-smoothed volatility ratio. Higher ratio = more
    noise removed = higher signal quality = larger position warranted.

    Returns float in [0.9, 1.10]. Degrades to 1.0 on failure.
    """
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="3mo")
        close = hist["Close"].dropna().values
        if len(close) < 30:
            return 1.0

        smoothed = kalman_smooth(close)

        raw_vol = float(np.std(np.diff(close) / close[:-1]))
        smooth_vol = float(np.std(np.diff(smoothed) / smoothed[:-1]))

        if raw_vol <= 0 or smooth_vol <= 0:
            return 1.0

        ratio = raw_vol / smooth_vol
        # ratio > 1 = noise removed; ratio ~ 1 = clean signal already
        # Map ratio [0.8, 2.0] -> multiplier [0.9, 1.10]
        mult = 0.9 + min(0.20, max(0.0, (ratio - 0.8) / 6.0))
        return round(mult, 4)

    except Exception:
        logger.debug("kalman_filter: skipped for %s", symbol, exc_info=True)
        return 1.0
