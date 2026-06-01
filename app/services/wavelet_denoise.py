"""Wavelet denoising for financial time series.

Applies Discrete Wavelet Transform (DWT) to decompose a price series
into frequency bands, removes high-frequency noise, then reconstructs.
Preserves edges and trend changes better than Kalman/EMA alone.

Reference
---------
- Jansen (2020), Ch.4 — Wavelet denoising for alpha factors
- Mallat (1999) — A Wavelet Tour of Signal Processing
"""

from __future__ import annotations

import logging
from typing import Iterable

import numpy as np

logger = logging.getLogger(__name__)

_HAS_PYWT: bool = False
try:
    import pywt
    _HAS_PYWT = True
except ImportError:
    pass

# Default wavelet family and decomposition level.
# 'db4' (Daubechies 4) is standard for financial data (Jansen Ch.4).
DEFAULT_WAVELET: str = "db4"
DEFAULT_LEVEL: int = 3
MIN_OBS: int = 64  # 2^level minimum


def wavelet_denoise(
    series: Iterable[float],
    wavelet: str = DEFAULT_WAVELET,
    level: int = DEFAULT_LEVEL,
    threshold_mode: str = "soft",
) -> np.ndarray:
    """Denoise a 1-D series using wavelet thresholding.

    Parameters
    ----------
    series : iterable of float
        Raw price or indicator series.
    wavelet : str
        Wavelet name (e.g. 'db4', 'sym6', 'haar').
    level : int
        Decomposition level.
    threshold_mode : str
        'soft' or 'hard' thresholding.

    Returns
    -------
    np.ndarray — denoised series, same length as input.
    Falls back to input if pywt not installed or series too short.
    """
    arr = np.asarray(list(series), dtype=float)
    arr = arr[np.isfinite(arr)]
    n = len(arr)

    if not _HAS_PYWT or n < MIN_OBS:
        return arr

    try:
        # Pad to power-of-2 length for DWT
        pad_len = int(2 ** np.ceil(np.log2(n)))
        padded = np.pad(arr, (0, pad_len - n), mode="reflect")

        coeffs = pywt.wavedec(padded, wavelet, level=level)

        # Universal threshold (Donoho & Johnstone)
        sigma = np.median(np.abs(coeffs[-1])) / 0.6745
        threshold = sigma * np.sqrt(2 * np.log(n))

        # Threshold detail coefficients (keep approximation)
        denoised_coeffs = [coeffs[0]]  # approximation
        for detail in coeffs[1:]:
            if threshold_mode == "soft":
                denoised = pywt.threshold(detail, threshold, mode="soft")
            else:
                denoised = pywt.threshold(detail, threshold, mode="hard")
            denoised_coeffs.append(denoised)

        reconstructed = pywt.waverec(denoised_coeffs, wavelet)
        return reconstructed[:n]

    except Exception:
        logger.debug("wavelet_denoise: failed, returning raw series", exc_info=True)
        return arr


def wavelet_smooth_prices(
    prices: list[float],
    wavelet: str = DEFAULT_WAVELET,
) -> np.ndarray:
    """Convenience wrapper for price series denoising."""
    return wavelet_denoise(prices, wavelet=wavelet)


def get_wavelet_adjustment(symbol: str) -> float:
    """Return signal-quality multiplier based on wavelet noise reduction.

    Compares raw vs wavelet-denoised volatility. Higher noise reduction
    = cleaner signal = slightly larger position.

    Returns float in [0.92, 1.08]. Degrades to 1.0 on failure.
    """
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="3mo")
        close = hist["Close"].dropna().values
        if len(close) < MIN_OBS:
            return 1.0

        smoothed = wavelet_denoise(close)

        raw_vol = float(np.std(np.diff(close) / close[:-1]))
        smooth_vol = float(np.std(np.diff(smoothed) / smoothed[:-1]))

        if raw_vol <= 0 or smooth_vol <= 0:
            return 1.0

        ratio = raw_vol / smooth_vol
        mult = 0.92 + min(0.16, max(0.0, (ratio - 0.8) / 10.0))
        return round(mult, 4)

    except Exception:
        logger.debug("wavelet_denoise: skipped for %s", symbol, exc_info=True)
        return 1.0
