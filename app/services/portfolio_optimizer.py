"""Portfolio-level covariance-based capital allocation.

Computes a covariance matrix from per-strategy trade returns stored in
trade_history and derives Kelly-optimal weights via  Cov⁻¹ × μ.

Designed for additive integration into the execute_buy() sizing pipeline
in trading_engine.py — it reads existing data and returns a multiplier;
it never submits orders or touches any other file.

References
----------
- Jansen (2020), Ch.5 — Portfolio optimization & Kelly for multiple assets
- Chan  (2009), Ch.6 — Kelly, covariance, and capital distribution
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Simple in-process cache — recomputes every 24 h.  The matrix is light
# (3×3) so persistence to DB / filesystem would add complexity for no
# practical gain at the current strategy count.
# ---------------------------------------------------------------------------
_cache: dict = {"ts": 0.0, "weights": {}}


# ── helpers ────────────────────────────────────────────────────────────────


def _strategy_returns() -> dict[str, list[float]]:
    """Return {strategy_id: [pnl_pct, ...]} from closed trades."""
    try:
        from app.db.database import SessionLocal
        from app.db.models import TradeHistory

        db = SessionLocal()
        try:
            rows = (
                db.query(TradeHistory.strategy_id, TradeHistory.pnl_pct)
                .filter(
                    TradeHistory.status == "closed",
                    TradeHistory.pnl_pct.isnot(None),
                    TradeHistory.strategy_id.isnot(None),
                )
                .order_by(TradeHistory.closed_at.asc())
                .all()
            )
        finally:
            db.close()

        by_strat: dict[str, list[float]] = defaultdict(list)
        for sid, pnl_pct in rows:
            if sid:
                by_strat[sid].append(float(pnl_pct))
        return dict(by_strat)

    except Exception:
        logger.debug("portfolio_optimizer: DB query failed", exc_info=True)
        return {}


def _aligned_series(
    returns: dict[str, list[float]],
) -> tuple[list[str], np.ndarray, np.ndarray]:
    """Align per-strategy return series to equal length (truncate to min len).

    Returns (strategy_ids, returns_matrix, mean_returns).
      - returns_matrix: (n_strategies, n_periods) — each row is one strategy
      - mean_returns:   (n_strategies,) — per-strategy mean
    """
    sids = [s for s, r in returns.items() if len(r) >= 3]
    if not sids:
        return [], np.array([]), np.array([])

    min_len = min(len(returns[s]) for s in sids)
    aligned = np.array([returns[s][-min_len:] for s in sids], dtype=float)
    means = aligned.mean(axis=1)
    return sids, aligned, means


# ── public API ─────────────────────────────────────────────────────────────


def compute_covariance_matrix() -> tuple[list[str], np.ndarray, np.ndarray | None]:
    """Compute covariance matrix of per-strategy returns.

    Returns
    -------
    (strategy_ids, cov_matrix, mean_returns | None)
      cov_matrix: (n × n) where n ≥ 2, or empty if insufficient data.
    """
    returns = _strategy_returns()
    sids, aligned, means = _aligned_series(returns)

    n = len(sids)
    if n < 2:
        logger.debug("portfolio_optimizer: need ≥2 strategies with ≥3 trades each, got %d", n)
        return [], np.array([]), None

    cov = np.cov(aligned, ddof=1)
    if np.isnan(cov).any() or np.isinf(cov).any():
        return [], np.array([]), None

    return sids, cov, means


def portfolio_kelly_weights(
    shrinkage: float = 0.5,
    max_weight: float = 0.6,
) -> dict[str, float]:
    """Compute Kelly-optimal portfolio weights via Cov⁻¹ × μ.

    Parameters
    ----------
    shrinkage : float, default 0.5
        Fraction of full Kelly to use.
    max_weight : float, default 0.6
        Cap per-strategy weight (prevents corner solutions).

    Returns
    -------
    dict[str, float] — {strategy_id: weight} where sum(weights) == 1.0
    Empty dict if insufficient data.
    """
    sids, cov, means = compute_covariance_matrix()
    n = len(sids)
    if n < 2 or means is None:
        return {}

    try:
        inv_cov = np.linalg.inv(cov)
    except np.linalg.LinAlgError:
        # Fall back to pseudo-inverse
        try:
            inv_cov = np.linalg.pinv(cov)
        except Exception:
            return {}

    raw_w = inv_cov @ means
    raw_w = np.maximum(raw_w, 0.0)  # long-only
    total = raw_w.sum()
    if total <= 0:
        return {}

    w = raw_w / total

    # Cap weights
    w = np.minimum(w, max_weight)
    w = w / w.sum()

    # Kelly shrinkage: blend with equal-weight (Chan Ch.6, Jansen Ch.5)
    uniform = np.ones(n) / n
    w_shrunk = shrinkage * w + (1 - shrinkage) * uniform
    w_shrunk = w_shrunk / w_shrunk.sum()

    return {sids[i]: float(round(w_shrunk[i], 6)) for i in range(n)}


def get_strategy_multiplier(strategy_id: str | None) -> float:
    """Return a position-size multiplier for this strategy (1.0 = no change).

    - < 1.0 → strategy is over-weighted relative to optimal → reduce sizing
    - > 1.0 → strategy is under-weighted                        → increase sizing
    - Always clamped to [0.5, 1.5].

    Cached for 24 h.
    """
    if not strategy_id:
        return 1.0

    now = time.time()
    global _cache
    if now - _cache["ts"] < 86400:
        weights = _cache["weights"]
    else:
        weights = portfolio_kelly_weights()
        _cache = {"ts": now, "weights": weights}

    if not weights or strategy_id not in weights:
        return 1.0

    n = len(weights)
    weight = weights[strategy_id]
    uniform = 1.0 / n if n > 0 else weight
    multiplier = weight / uniform if uniform > 0 else 1.0

    return float(max(0.5, min(1.5, multiplier)))


def get_portfolio_diagnostics() -> dict:
    """Return diagnostic info for UI/debug endpoints."""
    sids, cov, means = compute_covariance_matrix()
    weights = portfolio_kelly_weights()

    corr = None
    if len(sids) >= 2 and cov.size:
        std = np.sqrt(np.diag(cov))
        with np.errstate(divide="ignore", invalid="ignore"):
            corr = cov / np.outer(std, std)
            corr = np.nan_to_num(corr)

    return {
        "strategies": sids,
        "n_strategies": len(sids),
        "weights": weights,
        "covariance": cov.tolist() if cov.size else None,
        "correlation": corr.tolist() if corr is not None and corr.size else None,
        "mean_returns": means.tolist() if means is not None and means.size else None,
    }
