"""Tests for the Kalman time-varying hedge ratio (pairs #2).

Covers:
1. Drift tracking (the core value-add): when the true β ramps over time, the
   Kalman β_last is closer to the *current* β than a single static OLS β.
2. Small δ ≈ static OLS: with a tiny δ the Kalman spread tracks the OLS spread
   closely — confirming it's a conservative drop-in, not a behaviour change.
3. A stable cointegrated pair yields a finite, ~mean-zero spread and recovers β.
4. Fail-closed: too-short / constant / misaligned input → None (caller falls
   back to the static OLS spread).
"""

from __future__ import annotations

import numpy as np

from app.services.cointegration import hedge_ratio, spread_series
from app.services.kalman_hedge import kalman_hedge_filter, kalman_spread_series


def _stable_pair(n: int = 300, seed: int = 11):
    """Cointegrated (y, x) with a CONSTANT hedge ratio β=1.3 and AR(1) spread."""
    rng = np.random.default_rng(seed)
    x = np.cumsum(rng.normal(0.0, 1.0, n)) + 120.0
    eps = np.zeros(n)
    for t in range(1, n):
        eps[t] = 0.7 * eps[t - 1] + rng.normal(0.0, 1.0)
    y = 5.0 + 1.3 * x + eps
    return y, x


def _drifting_pair(n: int = 300, seed: int = 3):
    """(y, x) whose true hedge ratio DRIFTS linearly 1.0 → 2.0 over the window."""
    rng = np.random.default_rng(seed)
    x = np.cumsum(rng.normal(0.05, 1.0, n)) + 200.0   # positive, well away from 0
    beta_t = np.linspace(1.0, 2.0, n)
    y = 5.0 + beta_t * x + rng.normal(0.0, 0.5, n)
    return y, x, beta_t


# ── 1. drift tracking ─────────────────────────────────────────────────────────

def test_kalman_tracks_drifting_hedge_ratio():
    y, x, beta_t = _drifting_pair()
    out = kalman_hedge_filter(y, x, delta=0.02)
    assert out is not None

    true_final = float(beta_t[-1])              # 2.0
    ols_beta, _ = hedge_ratio(y, x)             # one stale number for the window
    kal_err = abs(out["beta_last"] - true_final)
    ols_err = abs(ols_beta - true_final)
    assert kal_err < ols_err, (
        f"Kalman should track the drift better than static OLS: "
        f"kalman β_last={out['beta_last']:.3f} (err {kal_err:.3f}) vs "
        f"OLS β={ols_beta:.3f} (err {ols_err:.3f})"
    )


# ── 2. small δ ≈ static OLS ───────────────────────────────────────────────────

def test_small_delta_close_to_static_ols():
    y, x = _stable_pair()
    ks = kalman_spread_series(y, x, delta=1e-6)
    assert ks is not None
    os_ = spread_series(y, x)
    # Compare the converged tail (skip warm-up): should be highly correlated.
    a = np.asarray(ks[-60:], dtype=float)
    b = np.asarray(os_[-60:], dtype=float)
    corr = float(np.corrcoef(a, b)[0, 1])
    assert corr > 0.9, f"tiny-δ Kalman spread should track the OLS spread, corr={corr:.3f}"


# ── 3. stable pair sanity ─────────────────────────────────────────────────────

def test_stable_pair_spread_is_finite_and_centered():
    y, x = _stable_pair()
    out = kalman_hedge_filter(y, x)
    assert out is not None
    spread = out["spread"]
    assert np.all(np.isfinite(spread))
    # mean-zero-ish innovations on the converged tail
    assert abs(float(np.mean(spread[-60:]))) < float(np.std(spread[-60:])) + 1.0
    # recovers a β near the true 1.3
    assert abs(out["beta_last"] - 1.3) < 0.3, out["beta_last"]


# ── 4. fail-closed ────────────────────────────────────────────────────────────

def test_fails_closed_on_short_input():
    assert kalman_hedge_filter(list(range(10)), list(range(10))) is None
    assert kalman_spread_series([1.0, 2.0, 3.0], [4.0, 5.0, 6.0]) is None


def test_fails_closed_on_constant_series():
    x = list(np.ones(100))                       # zero range → degenerate
    y = list(np.arange(100, dtype=float))
    assert kalman_hedge_filter(y, x) is None


def test_fails_closed_on_misaligned_lengths():
    assert kalman_spread_series(list(range(100)), list(range(80))) is None
