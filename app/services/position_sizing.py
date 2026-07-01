"""Regime- and conviction-aware position sizing.

Risk-adjusted return is made in the SIZE, not by loosening stops: run a smaller
book when the market regime is unfavourable, and lean into higher-conviction picks.
Everything is env-tunable and fails soft to a neutral 0.85×.
"""

from __future__ import annotations

import os


def regime_size_multiplier(regime: str | None = None) -> float:
    """GLOBAL book multiplier from the market regime — a smaller book in a weaker
    tape. BULL 1.0 / NEUTRAL 0.85 / BEAR 0.55 (env SIZE_MULT_*)."""
    if regime is None:
        try:
            from app.services.market_context import get_regime
            regime = get_regime().state
        except Exception:
            regime = "NEUTRAL"
    table = {
        "BULL": float(os.environ.get("SIZE_MULT_BULL", "1.0")),
        "NEUTRAL": float(os.environ.get("SIZE_MULT_NEUTRAL", "0.85")),
        "BEAR": float(os.environ.get("SIZE_MULT_BEAR", "0.55")),
    }
    return float(table.get((regime or "NEUTRAL").upper(), 0.85))


def conviction_size_multiplier(conviction: float | None) -> float:
    """PER-PICK multiplier from conviction (0-100): 50 = neutral (1.0), scaled to
    ~[0.7, 1.3]. None → 1.0. (Sizing, not stop width, carries conviction.)"""
    if conviction is None:
        return 1.0
    try:
        c = float(conviction)
    except Exception:
        return 1.0
    lo = float(os.environ.get("SIZE_CONV_FLOOR", "0.7"))
    hi = float(os.environ.get("SIZE_CONV_CAP", "1.3"))
    return float(max(lo, min(hi, lo + (c / 100.0) * (hi - lo))))


def position_multiplier(regime: str | None = None, conviction: float | None = None) -> float:
    """Combined regime × conviction multiplier, clamped to [0.3, 1.6]."""
    m = regime_size_multiplier(regime) * conviction_size_multiplier(conviction)
    return float(max(0.3, min(1.6, m)))


__all__ = ["regime_size_multiplier", "conviction_size_multiplier", "position_multiplier"]
