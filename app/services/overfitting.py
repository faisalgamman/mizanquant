"""Probability of Backtest Overfitting (PBO) via CSCV — López de Prado's guard.

When we search a GRID of gate thresholds and keep the best, we risk selecting a lucky
configuration. CSCV (Combinatorially-Symmetric Cross-Validation) answers, honestly:
**how often does the in-sample-BEST configuration land below the OOS median?** A high PBO
means the "winner" is probably overfit and its live edge will disappear.

Pure NumPy; input is a (time-block × configuration) performance matrix. Used to stamp the
self-calibrating gate's recommendation with a trust score.
"""

from __future__ import annotations

import logging
from itertools import combinations

import numpy as np

logger = logging.getLogger("screener")


def pbo_cscv(M, s_splits: int = 10) -> dict | None:
    """PBO from a performance matrix ``M`` of shape (T periods × N configs).

    Split the T rows into ``s_splits`` contiguous blocks; over every balanced combination
    (half train / half test) pick the IS-best config, find its OOS rank, map to the logit
    ``λ = ln(rank/(1-rank))``. PBO = fraction of splits with λ ≤ 0 (IS-best below OOS
    median). Returns None if the matrix is too small to judge.
    """
    M = np.asarray(M, dtype=float)
    if M.ndim != 2:
        return None
    T, N = M.shape
    if N < 2 or T < s_splits or s_splits < 4 or s_splits % 2:
        return None

    rows_per = T // s_splits
    if rows_per < 1:
        return None
    M = M[: rows_per * s_splits]
    blocks = [M[i * rows_per:(i + 1) * rows_per] for i in range(s_splits)]

    lambdas = []
    for train_idx in combinations(range(s_splits), s_splits // 2):
        test_idx = [i for i in range(s_splits) if i not in train_idx]
        J = np.vstack([blocks[i] for i in train_idx])
        Jbar = np.vstack([blocks[i] for i in test_idx])
        is_perf = np.nanmean(J, axis=0)
        oos_perf = np.nanmean(Jbar, axis=0)
        if not np.isfinite(is_perf).any() or not np.isfinite(oos_perf).any():
            continue
        n_star = int(np.nanargmax(is_perf))
        # relative OOS rank of the IS-best config, in (0,1)
        ranks = np.argsort(np.argsort(oos_perf))
        w = (ranks[n_star] + 1.0) / (N + 1.0)
        w = min(max(w, 1e-6), 1 - 1e-6)
        lambdas.append(np.log(w / (1.0 - w)))

    if not lambdas:
        return None
    lam = np.asarray(lambdas, dtype=float)
    pbo = float(np.mean(lam <= 0.0))
    return {
        "pbo": round(pbo, 3),                 # ↑ = more likely overfit
        "n_splits": int(len(lam)),
        "n_configs": int(N),
        "median_logit": round(float(np.median(lam)), 3),
        "trust": "high" if pbo <= 0.20 else ("medium" if pbo <= 0.50 else "low"),
    }


__all__ = ["pbo_cscv"]
