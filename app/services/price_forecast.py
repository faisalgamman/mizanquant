"""Probabilistic price forecast (Monte Carlo / Geometric Brownian Motion).

HONEST forecasting: there is no method that reliably predicts the exact future
price of a stock. What this returns instead is a *probability distribution* of
where the price could land over the next N trading days, derived from the
stock's OWN historical drift and volatility — an expected (median) path plus
confidence bands (P5/P25/P75/P95), the probability of profit, and tail risk
(VaR/CVaR). It never claims a point prediction.

Pure and side-effect free (numpy only, no network/torch) so it is cheap to
unit-test. It reuses the existing GBM engine in
``openbb_forecast.simulation.monte_carlo`` — see ``halal_screener.run_monte_carlo``
for the original field mapping.
"""
from __future__ import annotations

import numpy as np

_MIN_HISTORY = 30  # need enough closes to estimate drift + volatility
_MAX_BAND_POINTS = 8

_DISCLAIMER = (
    "Probabilistic range from the stock's own historical drift + volatility "
    "(GBM Monte Carlo) — NOT a point prediction."
)


def _sample_days(day_stats: list, max_points: int = _MAX_BAND_POINTS) -> list:
    """Down-sample the per-day stats to a few points, always keeping the last day."""
    n = len(day_stats)
    if n <= max_points:
        return day_stats
    step = max(1, n // max_points)
    sampled = day_stats[::step]
    if sampled[-1] is not day_stats[-1]:
        sampled.append(day_stats[-1])
    return sampled


def monte_carlo_forecast(prices, horizon: int, sims: int = 1000) -> dict:
    """Probabilistic price forecast for one symbol.

    Args:
        prices: historical close prices (array-like).
        horizon: trading days to simulate forward.
        sims: number of Monte Carlo paths.

    Returns:
        dict with current/expected price, prob_profit, vol/drift, VaR/CVaR, and
        a list of confidence ``bands`` over the horizon; or ``{"error": ...}``.
    """
    try:
        arr = np.asarray(prices, dtype=np.float64).flatten()
        arr = arr[~np.isnan(arr)]
    except Exception:
        return {"error": "invalid price series"}

    if arr.size < _MIN_HISTORY:
        return {"error": f"need >= {_MIN_HISTORY} closes, got {int(arr.size)}"}
    if horizon < 1:
        return {"error": "horizon must be >= 1"}

    from openbb_forecast.simulation.monte_carlo import MonteCarloSimulator

    result = MonteCarloSimulator(seed=42).simulate(
        arr, n_simulations=int(sims), forecast_days=int(horizon)
    )
    s = result["summary"]
    current = float(s["initial_price"])
    expected = float(s["expected_terminal_price"])

    bands = [
        {
            "day": int(d["day"]),
            "p5": round(float(d["percentile_5"]), 2),
            "p25": round(float(d["percentile_25"]), 2),
            "median": round(float(d["median_price"]), 2),
            "p75": round(float(d["percentile_75"]), 2),
            "p95": round(float(d["percentile_95"]), 2),
        }
        for d in _sample_days(result["day_stats"])
    ]

    return {
        "method": "monte_carlo_gbm",
        "is_probabilistic": True,
        "horizon": int(horizon),
        "sims": int(sims),
        "current_price": round(current, 2),
        "expected_price": round(expected, 2),
        "expected_change_pct": round((expected / current - 1.0) * 100.0, 2) if current else 0.0,
        "prob_profit_pct": round(float(s["prob_profit"]) * 100.0, 1),
        "annual_vol_pct": round(float(s["annualized_volatility"]) * 100.0, 1),
        "annual_drift_pct": round(float(s["annualized_drift"]) * 100.0, 1),
        "var_95_pct": round(float(s["terminal_var_95"]) * 100.0, 1),
        "cvar_95_pct": round(float(s["terminal_cvar_95"]) * 100.0, 1),
        "bands": bands,
        "disclaimer": _DISCLAIMER,
    }
