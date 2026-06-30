"""Tests for the probabilistic price forecast (Monte Carlo).

Validates the response shape + invariants offline (no network): the confidence
bands are ordered, probability of profit is a valid percentage, the horizon is
honoured, and short histories are rejected. Honesty checks: the result is flagged
probabilistic and carries the no-point-prediction disclaimer.
"""
from __future__ import annotations

import numpy as np

from app.services.price_forecast import monte_carlo_forecast


def _synthetic_prices(n=250, start=100.0, mu=0.0003, sigma=0.015, seed=7):
    rng = np.random.default_rng(seed)
    rets = rng.normal(mu, sigma, n)
    return list(start * np.cumprod(1.0 + rets))


def test_forecast_shape_and_band_ordering():
    prices = _synthetic_prices()
    out = monte_carlo_forecast(prices, horizon=20, sims=500)

    assert "error" not in out
    assert out["method"] == "monte_carlo_gbm" and out["is_probabilistic"] is True
    assert "NOT a point prediction" in out["disclaimer"]
    assert out["current_price"] == round(prices[-1], 2)
    assert out["horizon"] == 20

    # probability of profit is a valid percentage
    assert 0.0 <= out["prob_profit_pct"] <= 100.0
    # core fields present
    for k in ("expected_price", "expected_change_pct", "annual_vol_pct",
              "annual_drift_pct", "var_95_pct", "cvar_95_pct"):
        assert k in out

    bands = out["bands"]
    assert bands, "expected non-empty confidence bands"
    assert bands[-1]["day"] == 20  # last band reaches the horizon
    for b in bands:
        assert b["p5"] <= b["p25"] <= b["median"] <= b["p75"] <= b["p95"]


def test_bands_widen_with_time():
    prices = _synthetic_prices()
    out = monte_carlo_forecast(prices, horizon=30, sims=600)
    bands = out["bands"]
    # the P5..P95 spread should be wider at the horizon than near day 1
    first_spread = bands[0]["p95"] - bands[0]["p5"]
    last_spread = bands[-1]["p95"] - bands[-1]["p5"]
    assert last_spread >= first_spread


def test_short_history_is_rejected():
    out = monte_carlo_forecast([100.0, 101.0, 102.0], horizon=20, sims=500)
    assert "error" in out


def test_horizon_is_honoured():
    prices = _synthetic_prices()
    out = monte_carlo_forecast(prices, horizon=5, sims=400)
    assert out["bands"][-1]["day"] == 5


def test_drift_shrink_pulls_prob_profit_toward_half():
    # A strong historical uptrend makes FULL drift inflate prob_profit. Shrinking
    # the drift toward zero pulls it back toward ~50% (less trend extrapolation) —
    # the calibration win. Vol must be untouched, and the raw drift still reported.
    from openbb_forecast.simulation.monte_carlo import MonteCarloSimulator
    up = np.array(_synthetic_prices(mu=0.003, sigma=0.012, seed=3))  # strong uptrend
    full = MonteCarloSimulator(seed=1).simulate(up, n_simulations=3000, forecast_days=20, drift_shrink=1.0)
    zero = MonteCarloSimulator(seed=1).simulate(up, n_simulations=3000, forecast_days=20, drift_shrink=0.0)
    pf_full = full["summary"]["prob_profit"]
    pf_zero = zero["summary"]["prob_profit"]
    assert pf_full > pf_zero, (pf_full, pf_zero)
    assert abs(pf_zero - 0.5) < abs(pf_full - 0.5)         # zero-drift closer to coin-flip
    assert zero["summary"]["annualized_drift"] == 0.0       # effective drift is zero
    assert zero["summary"]["annualized_drift_raw"] > 0.0    # raw trend still surfaced
    # drift shrink must NOT change the volatility estimate
    assert abs(full["summary"]["annualized_volatility"]
               - zero["summary"]["annualized_volatility"]) < 1e-9


def test_forecast_surfaces_drift_shrink(monkeypatch):
    monkeypatch.setenv("MC_DRIFT_SHRINK", "0.0")
    out = monte_carlo_forecast(_synthetic_prices(mu=0.003, seed=3), horizon=20, sims=500)
    assert out["drift_shrink"] == 0.0
    assert out["annual_drift_pct"] == 0.0          # effective drift used = 0
    assert out["annual_drift_raw_pct"] != 0.0      # historical trend still shown
