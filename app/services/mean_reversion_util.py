"""Ornstein-Uhlenbeck parameter estimation and mean-reversion quality scoring.

Wraps the existing  from  with:
- Full OU parameter estimation (theta, mu, sigma)
- A normalised mean-reversion quality score (0-1)
- A position-size multiplier for the trading engine

Reference
---------
- Chan (2009), Ch.7 — OU process, half-life, mean-reversion strategies
- Jansen (2020), Ch.9 — Stationarity tests and OU calibration
"""

from __future__ import annotations

import logging
from typing import Iterable

import numpy as np

from app.services.stationarity import half_life_ou

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Thresholds (Chan Ch.7 recommends 5–20 bars as the sweet-spot for daily
# mean-reversion strategies; Jansen Ch.9 uses similar ranges).
# ---------------------------------------------------------------------------
HL_IDEAL: float = 5.0    # bars — excellent mean reversion
HL_GOOD: float = 20.0    # bars — still tradeable
HL_WEAK: float = 40.0    # bars — borderline; beyond this it is noise
HL_DEAD: float = 120.0   # bars — random walk

MIN_OBS: int = 30         # minimum price observations for reliable OU fit


# ── core OU estimation ──────────────────────────────────────────────────────


def ou_parameters(
    series: Iterable[float],
) -> dict[str, float]:
    """Fit Ornstein-Uhlenbeck parameters via OLS.

    dX[t] = theta * (mu - X[t-1]) * dt + sigma * dW[t]

    Returns
    -------
    dict with keys: theta, mu, sigma, half_life, n_obs
    All values are inf/nan when the fit is degenerate.
    """
    arr = np.asarray(list(series), dtype=float)
    arr = arr[np.isfinite(arr)]
    n = len(arr)
    if n < MIN_OBS:
        return {"theta": float("inf"), "mu": float("nan"),
                "sigma": float("nan"), "half_life": float("inf"), "n_obs": n}

    y_lag = arr[:-1]
    delta = arr[1:] - y_lag

    # OLS: delta = alpha + beta * y_lag + eps
    x_mat = np.column_stack([np.ones_like(y_lag), y_lag])
    try:
        beta, *_ = np.linalg.lstsq(x_mat, delta, rcond=None)
    except np.linalg.LinAlgError:
        return {"theta": float("inf"), "mu": float("nan"),
                "sigma": float("nan"), "half_life": float("inf"), "n_obs": n}

    slope = float(beta[1])
    intercept = float(beta[0])

    if slope >= 0:
        # No mean reversion — positive slope = explosive / random walk
        return {"theta": 0.0, "mu": float("nan"),
                "sigma": float("nan"), "half_life": float("inf"), "n_obs": n}

    theta = -slope                     # mean-reversion speed (per bar)
    mu = -intercept / slope if slope != 0 else float("nan")  # long-term mean
    residuals = delta - (x_mat @ beta)
    sigma = float(np.std(residuals, ddof=1))

    # Half-life: ln(2) / theta  (same formula as half_life_ou)
    hl = float(np.log(2) / theta) if theta > 0 else float("inf")

    return {
        "theta": round(theta, 8),
        "mu": round(mu, 6) if np.isfinite(mu) else float("nan"),
        "sigma": round(sigma, 6),
        "half_life": round(hl, 2),
        "n_obs": n,
    }


# ── quality scoring ─────────────────────────────────────────────────────────


def mr_quality_score(
    series: Iterable[float],
) -> float:
    """Mean-reversion quality score in [0, 1].

    1.0 = strong mean reversion (half-life <= HL_IDEAL)
    0.5 = tradeable (half-life <= HL_GOOD)
    0.0 = random walk (half-life >= HL_DEAD)

    Falls through to half_life_ou() for backwards compatibility with
    existing consumers.
    """
    hl = half_life_ou(series)
    if not np.isfinite(hl) or hl >= HL_DEAD:
        return 0.0
    if hl <= HL_IDEAL:
        return 1.0
    if hl <= HL_GOOD:
        # Linear interpolation: IDEAL→1.0, GOOD→0.5
        return 1.0 - 0.5 * (hl - HL_IDEAL) / (HL_GOOD - HL_IDEAL)
    if hl <= HL_WEAK:
        return 0.5 - 0.25 * (hl - HL_GOOD) / (HL_WEAK - HL_GOOD)
    # HL_WEAK → HL_DEAD: 0.25 → 0.0
    return 0.25 - 0.25 * (hl - HL_WEAK) / (HL_DEAD - HL_WEAK)


# ── trading-engine integration ───────────────────────────────────────────────


def get_mr_multiplier(symbol: str) -> float:
    """Return a position-size multiplier based on mean-reversion quality.

    Fetch recent prices for *symbol*, compute OU half-life quality,
    and return a multiplier clamped to [0.80, 1.15].

    - Weak / no reversion → shrink to 0.80×  (avoid random-walk traps)
    - Moderate           → 1.00× (neutral)
    - Strong reversion   → boost to 1.15× (lean into the edge)

    Degrades silently to 1.0 on any failure.
    """
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="3mo")
        prices = hist["Close"].dropna().tolist()
        if len(prices) < MIN_OBS:
            return 1.0

        score = mr_quality_score(prices)
        # Map score [0, 1] → multiplier [0.80, 1.15]
        mult = 0.80 + score * 0.35
        return float(round(max(0.80, min(1.15, mult)), 4))

    except Exception:
        logger.debug("mr_util: skipped for %s", symbol, exc_info=True)
        return 1.0


def get_mr_diagnostics(symbol: str) -> dict:
    """Return full diagnostics for one symbol (debug / dashboard use)."""
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="3mo")
        prices = hist["Close"].dropna().tolist()

        params = ou_parameters(prices)
        score = mr_quality_score(prices)
        params["mr_score"] = round(score, 4)
        params["mr_multiplier"] = round(0.80 + score * 0.35, 4)
        return params

    except Exception:
        return {"error": "Data fetch failed"}
