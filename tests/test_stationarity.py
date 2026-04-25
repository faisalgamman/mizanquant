"""Phase 3: stationarity tests for mean-reversion strategy gating
(Chan Ch.2 & Ch.3).

Property tests on synthetic processes whose answers are known a priori:

- Pure random walk: ADF cannot reject unit root; Hurst ≈ 0.5; OU
  half-life is infinite. The gate must say "not mean-reverting".
- Ornstein–Uhlenbeck process with known θ: ADF rejects unit root; Hurst
  comes out below 0.5; the recovered half-life is close to ln(2)/θ. The
  gate must say "mean-reverting".
- Trending random walk with drift: Hurst > 0.5. Gate rejects.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.services.stationarity import (
    adf_pvalue,
    half_life_ou,
    hurst_exponent,
    stationarity_report,
)


def _random_walk(n: int = 500, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    steps = rng.normal(0.0, 1.0, n)
    return np.cumsum(steps) + 100.0


def _ou_process(
    n: int = 500,
    theta: float = 0.10,
    mu: float = 100.0,
    sigma: float = 1.0,
    seed: int = 0,
) -> np.ndarray:
    """Discrete OU: y_{t+1} = y_t + θ·(μ - y_t) + σ·ε"""
    rng = np.random.default_rng(seed)
    y = np.empty(n)
    y[0] = mu
    for t in range(1, n):
        y[t] = y[t - 1] + theta * (mu - y[t - 1]) + sigma * rng.normal()
    return y


def _trending(n: int = 500, phi: float = 0.6, seed: int = 0) -> np.ndarray:
    """Momentum / persistent trend: AR(1) on returns with positive
    phi makes consecutive moves correlated, which is the form of trend
    that the R/S Hurst estimator actually detects (constant drift alone
    leaves Hurst ≈ 0.5)."""
    rng = np.random.default_rng(seed)
    rets = np.empty(n)
    rets[0] = rng.normal(0.0, 1.0)
    for t in range(1, n):
        rets[t] = phi * rets[t - 1] + rng.normal(0.0, 1.0)
    return np.cumsum(rets) + 100.0


# ---------------------------------------------------------------------------
# ADF
# ---------------------------------------------------------------------------

def test_adf_rejects_stationary_for_random_walk():
    p = adf_pvalue(_random_walk(seed=1))
    assert p > 0.10, f"ADF p={p} should be high for random walk"


def test_adf_accepts_stationary_for_ou():
    p = adf_pvalue(_ou_process(seed=2))
    assert p < 0.05, f"ADF p={p} should be low for OU process"


# ---------------------------------------------------------------------------
# Hurst
# ---------------------------------------------------------------------------

def test_hurst_around_half_for_random_walk():
    h = hurst_exponent(_random_walk(seed=3, n=2000))
    assert 0.40 <= h <= 0.60, f"Hurst {h} far from 0.5 for random walk"


def test_hurst_below_half_for_ou():
    h = hurst_exponent(_ou_process(seed=4, n=2000))
    assert h < 0.45, f"Hurst {h} should be < 0.45 for OU process"


def test_hurst_above_half_for_trending():
    h = hurst_exponent(_trending(seed=5, phi=0.6, n=2000))
    assert h > 0.55, f"Hurst {h} should be > 0.55 for momentum-trending series"


# ---------------------------------------------------------------------------
# Half-life
# ---------------------------------------------------------------------------

def test_half_life_recovers_known_theta():
    theta_true = 0.10
    expected = float(np.log(2) / theta_true)  # ≈ 6.93 bars
    hl = half_life_ou(_ou_process(seed=6, theta=theta_true, n=3000))
    # Loose tolerance — fitted slope is noisy on finite samples.
    assert abs(hl - expected) / expected < 0.30, (
        f"recovered half-life {hl:.2f} far from expected {expected:.2f}"
    )


def test_half_life_infinite_for_random_walk():
    hl = half_life_ou(_random_walk(seed=7, n=500))
    # Slope is ~0 for a random walk; fitted slope is occasionally
    # slightly negative by chance, so accept either inf or a very large
    # finite value (>> max gating threshold of 30).
    assert hl == float("inf") or hl > 100, f"half-life {hl} too small for random walk"


# ---------------------------------------------------------------------------
# Combined gate
# ---------------------------------------------------------------------------

def test_gate_blocks_random_walk():
    rep = stationarity_report(_random_walk(seed=8, n=1000))
    assert rep.is_mean_reverting is False
    assert "ADF" in rep.reason or "Hurst" in rep.reason or "half-life" in rep.reason


def test_gate_passes_ou_process():
    rep = stationarity_report(_ou_process(seed=9, n=1000), max_half_life_bars=50)
    assert rep.is_mean_reverting is True, f"OU should pass: {rep.reason}"


def test_gate_blocks_trending():
    rep = stationarity_report(_trending(seed=10, phi=0.6, n=1000))
    assert rep.is_mean_reverting is False
