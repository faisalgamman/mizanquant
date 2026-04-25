"""Phase 6: per-asset strategy regime classifier (Chan Ch.6).

Property tests on the same synthetic processes used in Phases 3 and 4 —
since the classifier reuses those estimators, the answers must agree:

- AR(1)+drift series → "trending" (favors momentum)
- OU process → "ranging" (favors mean reversion)
- Pure random walk → "noisy" (skip both strategies)
- Insufficient history → "noisy" with explicit reason
"""

from __future__ import annotations

import numpy as np

from app.services.strategy_regime import classify_strategy_regime


def _random_walk(n: int = 500, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return np.cumsum(rng.normal(0.0, 1.0, n)) + 100.0


def _ou_process(n: int = 500, theta: float = 0.10, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    y = np.empty(n)
    y[0] = 100.0
    for t in range(1, n):
        y[t] = y[t - 1] + theta * (100.0 - y[t - 1]) + rng.normal(0.0, 1.0)
    return y


def _trending(n: int = 500, phi: float = 0.6, drift: float = 0.002, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    rets = np.empty(n)
    rets[0] = drift + rng.normal(0.0, 0.01)
    for t in range(1, n):
        rets[t] = drift + phi * (rets[t - 1] - drift) + rng.normal(0.0, 0.01)
    return 100.0 * np.exp(np.cumsum(rets))


def test_trending_series_classified_trending():
    rep = classify_strategy_regime(_trending(n=600, drift=0.002, seed=1))
    assert rep.label == "trending", f"expected trending, got {rep.label}: {rep.reason}"


def test_ou_process_classified_ranging():
    rep = classify_strategy_regime(_ou_process(n=500, seed=2), ranging_max_half_life=50.0)
    assert rep.label == "ranging", f"expected ranging, got {rep.label}: {rep.reason}"


def test_random_walk_not_trending():
    """A random walk has no positive autocorrelation in returns; the
    classifier must NOT route it to a momentum strategy. It may end up
    "noisy" (most common) or rarely "ranging" by Type I — we only
    forbid the dangerous classification."""
    labels = []
    for k in range(8):
        rep = classify_strategy_regime(_random_walk(seed=200 + k))
        labels.append(rep.label)
    assert "trending" not in labels, f"random walk classified as trending: {labels}"


def test_insufficient_history_is_noisy():
    rep = classify_strategy_regime([100.0] * 30)
    assert rep.label == "noisy"
    assert "insufficient" in rep.reason


def test_trending_diagnostic_fields_finite():
    rep = classify_strategy_regime(_trending(n=600, drift=0.002, seed=3))
    d = rep.as_dict()
    assert isinstance(d["hurst"], float)
    assert isinstance(d["adf_pvalue"], float)
    assert isinstance(d["tstat"], float)
    assert d["label"] == "trending"
