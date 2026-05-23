"""Unified transaction-cost model for all backtest engines.

All three backtests (backtest_service, halal_screener, strategies/backtest)
import from this single module so the 20 bps/side assumption is never
duplicated or silently diverged.

Override via environment variable for sensitivity testing:
    BACKTEST_COST_BPS=10 python -m pytest tests/
"""
from __future__ import annotations

import os

# 20 bps per side → 40 bps round-trip.
BACKTEST_COST_BPS: float = float(os.environ.get("BACKTEST_COST_BPS", "20.0"))


def apply_costs(price: float, side: str, bps: float | None = None) -> float:
    """Apply one-way slippage/commission to a fill price.

    Args:
        price: Raw fill price (open, close, TP level, SL level …).
        side:  ``'buy'``  → price worsened upward (you pay more).
               ``'sell'`` → price worsened downward (you receive less).
        bps:   Basis points per side. Defaults to ``BACKTEST_COST_BPS`` (20).

    Returns:
        Adjusted fill price.
    """
    _bps = bps if bps is not None else BACKTEST_COST_BPS
    slip = price * (_bps / 10_000.0)
    return price + slip if side == "buy" else price - slip


def round_trip_cost_pct(bps: float | None = None) -> float:
    """Return round-trip cost as a **fraction** of price (buy leg + sell leg).

    Example: 20 bps/side → 0.0040 (0.40 %).
    """
    _bps = bps if bps is not None else BACKTEST_COST_BPS
    return 2.0 * _bps / 10_000.0


__all__ = ["BACKTEST_COST_BPS", "apply_costs", "round_trip_cost_pct"]
