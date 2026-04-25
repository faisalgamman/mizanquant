"""Phase 5: cointegration tests for pairs trading (Chan Ch.4).

Property tests on synthetic pairs whose cointegration status is known
in advance:

- Two independent random walks: NOT cointegrated (Engle–Granger p high,
  half-life infinite, gate rejects).
- Constructed cointegrated pair y = β·x + α + ε with ε an OU residual:
  EG p low, β recovered close to true value, half-life finite, gate
  accepts.
- Spread z-score crosses ±2 sigma when the residual is at extreme
  quantiles, ≈ 0 at the mean.
"""

from __future__ import annotations

import numpy as np

from app.services.cointegration import (
    cointegration_report,
    engle_granger_pvalue,
    hedge_ratio,
    spread_series,
    spread_zscore,
)


def _random_walk(n: int = 500, seed: int = 0, start: float = 100.0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return np.cumsum(rng.normal(0.0, 1.0, n)) + start


def _cointegrated_pair(
    n: int = 500,
    beta_true: float = 1.5,
    alpha_true: float = 5.0,
    theta_resid: float = 0.20,
    sigma_resid: float = 1.0,
    seed: int = 0,
) -> tuple[np.ndarray, np.ndarray]:
    """x is a random walk; y = α + β·x + ε where ε is OU(θ, σ).

    The OU residual is what makes the pair cointegrated — its
    stationarity is the structural property the gate must detect.
    """
    rng = np.random.default_rng(seed)
    x = np.cumsum(rng.normal(0.0, 1.0, n)) + 100.0
    eps = np.empty(n)
    eps[0] = 0.0
    for t in range(1, n):
        eps[t] = (1.0 - theta_resid) * eps[t - 1] + rng.normal(0.0, sigma_resid)
    y = alpha_true + beta_true * x + eps
    return y, x


# ---------------------------------------------------------------------------
# Hedge ratio
# ---------------------------------------------------------------------------

def test_hedge_ratio_recovers_true_beta():
    y, x = _cointegrated_pair(n=1000, beta_true=1.5, alpha_true=5.0, seed=1)
    beta, alpha = hedge_ratio(y, x)
    assert abs(beta - 1.5) < 0.05, f"β={beta} far from 1.5"
    assert abs(alpha - 5.0) < 5.0, f"α={alpha} far from 5.0 (loose tol)"


def test_hedge_ratio_zero_on_degenerate_input():
    beta, alpha = hedge_ratio([100.0] * 50, [100.0] * 50)
    assert beta == 0.0


# ---------------------------------------------------------------------------
# Engle–Granger
# ---------------------------------------------------------------------------

def test_engle_granger_rejects_random_walks():
    # Independent random walks: under the null we expect Type I error
    # at the nominal rate (~5%). Test the *distribution*, not a single
    # pair: across 10 independent pairs, the median p must be well
    # above the rejection threshold.
    pvals = []
    for k in range(10):
        y = _random_walk(seed=100 + 2 * k)
        x = _random_walk(seed=101 + 2 * k)
        pvals.append(engle_granger_pvalue(y, x))
    median_p = float(np.median(pvals))
    assert median_p > 0.20, f"median EG p={median_p} too low for independent random walks"


def test_engle_granger_accepts_cointegrated_pair():
    y, x = _cointegrated_pair(n=800, seed=4)
    p = engle_granger_pvalue(y, x)
    assert p < 0.05, f"EG p={p} too high for cointegrated pair"


# ---------------------------------------------------------------------------
# Spread z-score
# ---------------------------------------------------------------------------

def test_spread_zscore_near_zero_at_mean():
    y, x = _cointegrated_pair(n=500, seed=5)
    spread = spread_series(y, x)
    # Force the last value to the spread's own mean → z ≈ 0.
    spread[-1] = float(np.mean(spread[-60:-1]))
    z = spread_zscore(spread, lookback=60)
    assert abs(z) < 0.5, f"z={z} not near 0 at mean"


def test_spread_zscore_extreme_at_outlier():
    y, x = _cointegrated_pair(n=500, seed=6)
    spread = spread_series(y, x)
    sigma = float(np.std(spread[-60:], ddof=1))
    mu = float(np.mean(spread[-60:]))
    spread[-1] = mu + 4 * sigma
    z = spread_zscore(spread, lookback=60)
    assert z > 2.5, f"z={z} should be large for 4σ outlier"


# ---------------------------------------------------------------------------
# Combined report
# ---------------------------------------------------------------------------

def test_report_rejects_independent_random_walks():
    y = _random_walk(seed=7)
    x = _random_walk(seed=8)
    rep = cointegration_report(y, x)
    assert rep.is_cointegrated is False
    assert rep.reason != "cointegrated; tradable spread"


def test_report_accepts_cointegrated_pair():
    y, x = _cointegrated_pair(n=800, seed=9)
    rep = cointegration_report(y, x, max_half_life_bars=60.0)
    assert rep.is_cointegrated is True, f"expected cointegrated: {rep.reason}"
    assert abs(rep.hedge_ratio - 1.5) < 0.10
    assert np.isfinite(rep.half_life) and rep.half_life > 0
