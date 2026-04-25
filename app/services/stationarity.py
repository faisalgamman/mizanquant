"""Stationarity / mean-reversion gates (Chan Ch.2 & Ch.3).

Mean-reversion strategies (RSI dip-buying, Bollinger band fade) are
profitable only on series that actually *mean-revert*. Running them on a
trending series — or on a non-stationary random walk — turns each
"oversold" signal into a chase of a falling knife. Chan §3.1 stresses
that stationarity must be tested *before* a mean-reversion model is
fit, not assumed.

This module provides three industry-standard tests and a combined
verdict:

1. **Augmented Dickey-Fuller (ADF)** — null hypothesis: unit root
   (non-stationary). Low p-value (≤ 0.10) ⇒ reject null ⇒ series is
   stationary. We test the price *level*, not differences.
2. **Hurst exponent (H)** — H < 0.5 mean-reverting, H ≈ 0.5 random
   walk, H > 0.5 trending. Implementation uses the rescaled-range (R/S)
   variance of price differences across multiple lags.
3. **Half-life of mean reversion** — fits an Ornstein–Uhlenbeck process
   `dy = -θ·(y - μ)·dt + σ·dW` via OLS regression of `Δy` on lagged
   `y`. The half-life `τ = ln(2) / θ` tells you how long, in bars, the
   series takes to revert halfway to its mean. Strategies need
   τ shorter than the holding horizon, otherwise the edge dissipates
   into noise before exit.

The combined `is_mean_reverting` gate is intentionally strict: ALL three
conditions must agree. False positives here cost real money on the live
strategy; false negatives just skip a marginal trade.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np

try:
    from statsmodels.tsa.stattools import adfuller as _adfuller
    _HAS_STATSMODELS = True
except Exception:  # pragma: no cover - statsmodels is in requirements
    _HAS_STATSMODELS = False


@dataclass
class StationarityReport:
    adf_pvalue: float
    hurst: float
    half_life: float
    is_mean_reverting: bool
    reason: str

    def as_dict(self) -> dict:
        return {
            "adf_pvalue": round(self.adf_pvalue, 4),
            "hurst": round(self.hurst, 4),
            "half_life_bars": round(self.half_life, 2),
            "is_mean_reverting": self.is_mean_reverting,
            "reason": self.reason,
        }


def adf_pvalue(series: Iterable[float]) -> float:
    """ADF p-value on the level series. Returns 1.0 if the test cannot
    be run (constant series, too few points, statsmodels missing)."""
    arr = np.asarray(list(series), dtype=float)
    arr = arr[np.isfinite(arr)]
    if len(arr) < 20 or np.ptp(arr) == 0:
        return 1.0
    if not _HAS_STATSMODELS:
        return 1.0
    try:
        result = _adfuller(arr, autolag="AIC")
        return float(result[1])
    except Exception:
        return 1.0


def hurst_exponent(series: Iterable[float], max_lag: int = 100) -> float:
    """Hurst exponent via the R/S method on log-spaced lags.

    Returns 0.5 (random-walk equivalent) on degenerate input rather than
    raising — the gate logic interprets 0.5 as "no signal".
    """
    arr = np.asarray(list(series), dtype=float)
    arr = arr[np.isfinite(arr)]
    n = len(arr)
    if n < 40:
        return 0.5

    upper = min(max_lag, n // 2)
    lags = np.unique(np.geomspace(2, upper, num=20).astype(int))
    if len(lags) < 4:
        return 0.5

    tau = []
    for lag in lags:
        diff = arr[lag:] - arr[:-lag]
        if len(diff) < 2:
            continue
        std = float(np.std(diff, ddof=1))
        if std <= 0:
            continue
        tau.append(std)
    if len(tau) < 4:
        return 0.5

    log_lags = np.log(lags[: len(tau)])
    log_tau = np.log(tau)
    slope, _ = np.polyfit(log_lags, log_tau, 1)
    return float(slope)


def half_life_ou(series: Iterable[float]) -> float:
    """Ornstein–Uhlenbeck half-life in bars via OLS.

    Returns +inf when the regression slope is non-negative (no mean
    reversion detected), so callers can compare against a finite max.
    """
    arr = np.asarray(list(series), dtype=float)
    arr = arr[np.isfinite(arr)]
    if len(arr) < 30:
        return float("inf")

    y_lag = arr[:-1]
    delta = arr[1:] - y_lag
    x = np.column_stack([np.ones_like(y_lag), y_lag])
    try:
        beta, *_ = np.linalg.lstsq(x, delta, rcond=None)
    except np.linalg.LinAlgError:
        return float("inf")
    slope = float(beta[1])
    if slope >= 0:
        return float("inf")
    return float(-np.log(2) / slope)


def stationarity_report(
    series: Iterable[float],
    adf_threshold: float = 0.10,
    hurst_threshold: float = 0.50,
    max_half_life_bars: float = 30.0,
) -> StationarityReport:
    """Combined gate. ALL three must agree for `is_mean_reverting=True`."""
    arr = list(series)
    p = adf_pvalue(arr)
    h = hurst_exponent(arr)
    hl = half_life_ou(arr)

    reasons: list[str] = []
    if p > adf_threshold:
        reasons.append(f"ADF p={p:.3f}>{adf_threshold}")
    if h >= hurst_threshold:
        reasons.append(f"Hurst={h:.3f}>={hurst_threshold}")
    if hl > max_half_life_bars or not np.isfinite(hl):
        reasons.append(f"half-life={hl:.1f}>{max_half_life_bars}")

    is_mr = len(reasons) == 0
    reason = "all gates passed" if is_mr else "; ".join(reasons)
    return StationarityReport(
        adf_pvalue=p,
        hurst=h,
        half_life=hl,
        is_mean_reverting=is_mr,
        reason=reason,
    )


__all__ = [
    "StationarityReport",
    "adf_pvalue",
    "hurst_exponent",
    "half_life_ou",
    "stationarity_report",
]
