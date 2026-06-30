"""Time-varying hedge ratio for pairs trading via a Kalman filter.

A static OLS hedge ratio (β fixed over the whole window) drifts: the true
relationship between two cointegrated legs slowly changes, so a stale β biases
the spread and degrades the mean-reversion signal. A Kalman filter estimates a
**time-varying** β_t (and intercept α_t) recursively, adapting to that drift —
the classic, well-validated upgrade for pairs trading (Chan, *Algorithmic
Trading*).

State-space model (random-walk coefficients):

    state x_t   = [β_t, α_t]ᵀ                      (hedge ratio + intercept)
    transition  x_t = x_{t-1} + w_t,  w_t ~ N(0, Vw),  Vw = δ/(1-δ)·I
    observation y_t = Hₜ·x_t + v_t,   Hₜ = [x_t, 1],  v_t ~ N(0, R)

The one-step-ahead forecast error  eₜ = y_t − Hₜ·x_{t|t-1}  is the **spread**
we trade: it measures how far y is from the slowly-adapting fair-value line, with
no look-ahead (x_{t|t-1} uses only data up to t-1). With a small δ the line barely
moves, so eₜ ≈ the static OLS spread but with slow drift correction; a larger δ
adapts faster. We warm-start the state at the OLS estimate so the early,
pre-convergence innovations are already sensible.

Pure NumPy, no extra dependencies. Every entry point fails *closed* (returns
None) on degenerate input so a caller can fall back to the static OLS spread.
"""

from __future__ import annotations

from typing import Iterable

import numpy as np

__all__ = ["kalman_hedge_filter", "kalman_spread_series"]


def kalman_hedge_filter(
    y: Iterable[float],
    x: Iterable[float],
    delta: float = 1e-4,
    r: float | None = None,
) -> dict | None:
    """Run the filter over aligned series (y, x).

    Parameters
    ----------
    delta : process-noise knob in (0, 1). Smaller → β adapts more slowly
            (closer to static OLS); larger → tracks drift faster. Default 1e-4.
    r     : measurement-noise variance. None → variance of the OLS residuals
            (auto-scales to the data); this is the recommended default.

    Returns a dict with the per-bar ``spread`` (innovation) and ``beta`` arrays
    plus the latest ``beta_last`` / ``alpha_last`` — or None when the series are
    too short (< 60), misaligned, or non-finite/constant.
    """
    y_arr = np.asarray(list(y), dtype=float)
    x_arr = np.asarray(list(x), dtype=float)
    if y_arr.shape != x_arr.shape or y_arr.ndim != 1:
        return None
    mask = np.isfinite(y_arr) & np.isfinite(x_arr)
    y_arr = y_arr[mask]
    x_arr = x_arr[mask]
    n = len(y_arr)
    if n < 60 or np.ptp(x_arr) == 0 or np.ptp(y_arr) == 0:
        return None
    if not (0.0 < delta < 1.0):
        delta = 1e-4

    # Warm start: OLS β/α over the whole window, and R from its residuals.
    design = np.vstack([x_arr, np.ones(n)]).T
    try:
        (beta0, alpha0), *_ = np.linalg.lstsq(design, y_arr, rcond=None)
    except Exception:
        return None
    resid = y_arr - (beta0 * x_arr + alpha0)
    r_meas = float(np.var(resid)) if r is None else float(r)
    if not np.isfinite(r_meas) or r_meas <= 0:
        r_meas = 1.0

    vw = (delta / (1.0 - delta)) * np.eye(2)
    state = np.array([beta0, alpha0], dtype=float)
    cov = np.eye(2) * r_meas  # only affects the warm-up; the traded tail is converged

    spread = np.empty(n)
    betas = np.empty(n)
    for t in range(n):
        h = np.array([x_arr[t], 1.0])
        if t > 0:
            cov = cov + vw                 # state-covariance prediction (random walk)
        innov = y_arr[t] - float(h @ state)  # forecast error = adaptive spread level
        s = float(h @ cov @ h) + r_meas      # innovation variance (scalar)
        if not np.isfinite(s) or s <= 0:
            return None
        gain = (cov @ h) / s                 # Kalman gain (2-vector)
        state = state + gain * innov
        cov = cov - np.outer(gain, h) @ cov
        spread[t] = innov
        betas[t] = state[0]

    if not np.all(np.isfinite(spread)):
        return None
    return {
        "spread": spread,
        "beta": betas,
        "beta_last": float(state[0]),
        "alpha_last": float(state[1]),
    }


def kalman_spread_series(
    y: Iterable[float],
    x: Iterable[float],
    delta: float = 1e-4,
    r: float | None = None,
) -> np.ndarray | None:
    """Just the innovation (spread) series from :func:`kalman_hedge_filter`,
    or None when the filter cannot run."""
    out = kalman_hedge_filter(y, x, delta=delta, r=r)
    return None if out is None else out["spread"]
