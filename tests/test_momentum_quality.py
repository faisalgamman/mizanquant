"""Phase 4: momentum-quality gate tests (Chan Ch.5).

Property tests on synthetic series whose momentum properties are known
in advance:

- Pure noise: t≈0, 12-1 momentum random, Hurst≈0.5, verdict ∈ {neutral,
  mean_reverting} — never "trending".
- Strong trend with positive drift and momentum autocorrelation: large
  t, positive 12-1 return, Hurst > 0.55, verdict = "trending".
- Pure mean-reversion (OU): Hurst < 0.45, verdict = "mean_reverting".
"""

from __future__ import annotations

import numpy as np

from app.services.momentum_quality import (
    momentum_12_1,
    momentum_quality_score,
    tstat_returns,
)


def _noise_series(n: int = 500, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    rets = rng.normal(0.0, 0.01, n)
    return 100.0 * np.exp(np.cumsum(rets))


def _trending_series(n: int = 500, phi: float = 0.6, drift: float = 0.002, seed: int = 0) -> np.ndarray:
    """Trending price = AR(1) returns with positive drift."""
    rng = np.random.default_rng(seed)
    rets = np.empty(n)
    rets[0] = drift + rng.normal(0.0, 0.01)
    for t in range(1, n):
        rets[t] = drift + phi * (rets[t - 1] - drift) + rng.normal(0.0, 0.01)
    return 100.0 * np.exp(np.cumsum(rets))


def _ou_price(n: int = 500, theta: float = 0.10, seed: int = 0) -> np.ndarray:
    """OU process around a constant mean — pure mean-reversion."""
    rng = np.random.default_rng(seed)
    y = np.empty(n)
    y[0] = 100.0
    for t in range(1, n):
        y[t] = y[t - 1] + theta * (100.0 - y[t - 1]) + rng.normal(0.0, 1.0)
    return y


# ---------------------------------------------------------------------------
# tstat
# ---------------------------------------------------------------------------

def test_tstat_small_for_noise():
    rng = np.random.default_rng(1)
    rets = rng.normal(0.0, 0.01, 500)
    t = tstat_returns(rets)
    assert abs(t) < 2.0, f"|t|={abs(t)} too high for pure noise"


def test_tstat_large_for_clear_drift():
    rng = np.random.default_rng(2)
    rets = rng.normal(0.005, 0.01, 500)
    t = tstat_returns(rets)
    assert t > 5.0, f"t={t} too small for clear positive drift"


# ---------------------------------------------------------------------------
# 12-1 momentum
# ---------------------------------------------------------------------------

def test_momentum_12_1_positive_for_uptrend():
    s = _trending_series(n=400, drift=0.002, seed=3)
    m = momentum_12_1(s)
    assert m > 0.05, f"12-1 momentum {m} too small for uptrend"


def test_momentum_12_1_negative_for_downtrend():
    # Use a longer series and stronger drift so the lookback window
    # (idx -252 to -22) is dominated by the trend rather than AR(1) noise.
    s = _trending_series(n=600, drift=-0.005, seed=4)
    m = momentum_12_1(s)
    assert m < -0.05, f"12-1 momentum {m} too positive for downtrend"


def test_momentum_12_1_zero_on_short_history():
    m = momentum_12_1([100.0, 101.0, 102.0])
    assert m == 0.0


# ---------------------------------------------------------------------------
# Combined verdict
# ---------------------------------------------------------------------------

def test_score_trending_for_strong_uptrend():
    rep = momentum_quality_score(_trending_series(n=600, drift=0.002, seed=5))
    assert rep.verdict == "trending", f"expected trending, got {rep.verdict}: {rep.reason}"


def test_score_not_trending_for_noise():
    rep = momentum_quality_score(_noise_series(n=500, seed=6))
    assert rep.verdict != "trending", (
        f"noise should never be trending: verdict={rep.verdict} reason={rep.reason}"
    )


def test_score_mean_reverting_for_ou():
    rep = momentum_quality_score(_ou_price(n=500, seed=7))
    assert rep.verdict == "mean_reverting", (
        f"OU should be mean-reverting: verdict={rep.verdict} reason={rep.reason}"
    )


def test_score_neutral_on_short_history():
    rep = momentum_quality_score([100.0] * 10)
    assert rep.verdict == "neutral"
