"""Probability-calibration diagnostics — is a predicted probability trustworthy?

Turns "the model said 55% prob-profit" into evidence: bin the predictions, compare
each bin to the realized hit rate, and score the gap. A well-calibrated 55%
prediction should be right ~55% of the time; if it isn't, a hand-picked 55/45
threshold is unjustified. Pure NumPy, no I/O — the measurement primitive the tuning
step builds on.

Metrics:
  • reliability curve — per-bin (mean predicted, mean actual, count)
  • Brier score       — mean squared error of the probability (lower better)
  • ECE               — count-weighted mean |predicted − actual| across bins (0=perfect)
"""

from __future__ import annotations

from typing import Iterable

import numpy as np


def _clean(probs: Iterable[float], outcomes: Iterable[float]):
    p = np.asarray(list(probs), dtype=float)
    y = np.asarray(list(outcomes), dtype=float)
    if p.shape != y.shape:
        n = min(len(p), len(y))
        p, y = p[:n], y[:n]
    mask = np.isfinite(p) & np.isfinite(y)
    return p[mask], y[mask]


def reliability_curve(probs, outcomes, n_bins: int = 10) -> list[dict]:
    """Per-bin (mean_pred, mean_actual, n) over [0,1]. Empty bins are dropped."""
    p, y = _clean(probs, outcomes)
    if len(p) == 0:
        return []
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    out = []
    for i in range(n_bins):
        lo, hi = edges[i], edges[i + 1]
        sel = (p >= lo) & (p <= hi) if i == n_bins - 1 else (p >= lo) & (p < hi)
        k = int(sel.sum())
        if k == 0:
            continue
        out.append({
            "lo": round(float(lo), 2), "hi": round(float(hi), 2),
            "mean_pred": round(float(p[sel].mean()), 4),
            "mean_actual": round(float(y[sel].mean()), 4),
            "n": k,
        })
    return out


def brier_score(probs, outcomes):
    p, y = _clean(probs, outcomes)
    return None if len(p) == 0 else float(np.mean((p - y) ** 2))


def expected_calibration_error(probs, outcomes, n_bins: int = 10):
    """Count-weighted mean |mean_pred − mean_actual| across bins (0 = perfect)."""
    curve = reliability_curve(probs, outcomes, n_bins)
    n = sum(b["n"] for b in curve)
    if n == 0:
        return None
    return float(sum(b["n"] * abs(b["mean_pred"] - b["mean_actual"]) for b in curve) / n)


def calibration_report(probs, outcomes, n_bins: int = 10) -> dict:
    """Full report: sample size, base rate, mean prediction, Brier, ECE, curve.

    A large gap between mean_pred and base_rate means the model is biased
    (e.g. over-optimistic); a high ECE means it's unreliable bin-by-bin.
    """
    p, y = _clean(probs, outcomes)
    return {
        "n": int(len(p)),
        "base_rate": round(float(y.mean()), 4) if len(y) else None,
        "mean_pred": round(float(p.mean()), 4) if len(p) else None,
        "brier": brier_score(p, y),
        "ece": expected_calibration_error(p, y, n_bins),
        "curve": reliability_curve(p, y, n_bins),
    }


__all__ = ["reliability_curve", "brier_score", "expected_calibration_error", "calibration_report"]
