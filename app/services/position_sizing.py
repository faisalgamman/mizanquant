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


def vol_target_multiplier(realized_vol: float | None, target_vol: float | None = None,
                          cap: float | None = None) -> float:
    """③ Volatility targeting — the standard quant-fund book control. Scale the book so its
    realised annualised vol tracks a constant target: low-vol tape → lean in, high-vol →
    trim automatically. multiplier = target / realised, clamped to [0.3, cap]. Fails soft
    to 1.0 when vol is unknown. Env: VOL_TARGET (default 0.14), VOL_TARGET_CAP (1.5)."""
    if realized_vol is None or realized_vol <= 0:
        return 1.0
    tgt = float(os.environ.get("VOL_TARGET", "0.14")) if target_vol is None else float(target_vol)
    cp = float(os.environ.get("VOL_TARGET_CAP", "1.5")) if cap is None else float(cap)
    try:
        return float(max(0.3, min(cp, tgt / float(realized_vol))))
    except (TypeError, ValueError, ZeroDivisionError):
        return 1.0


def fractional_kelly(p_win: float | None, win_loss_ratio: float | None = 1.0,
                     fraction: float | None = None) -> float:
    """③ Fractional-Kelly size fraction from a CALIBRATED win probability. Full Kelly
    f* = p − (1−p)/b (b = avg win / avg loss); scaled by ``fraction`` (¼ Kelly by default —
    full Kelly is too aggressive on estimation error) and clamped to [0, 1]. p None → 0.5
    (no edge → base size). Env: KELLY_FRACTION (default 0.25)."""
    if p_win is None:
        return 1.0
    try:
        p = float(p_win)
        b = float(win_loss_ratio) if win_loss_ratio else 1.0
    except (TypeError, ValueError):
        return 1.0
    if b <= 0:
        b = 1.0
    frac = float(os.environ.get("KELLY_FRACTION", "0.25")) if fraction is None else float(fraction)
    kelly = p - (1.0 - p) / b            # full-Kelly optimal fraction
    return float(max(0.0, min(1.0, kelly * frac / 0.25)))   # normalise so ¼-Kelly@p_edge≈1×


def _flag(name: str, default: str = "true") -> bool:
    return os.environ.get(name, default).strip().lower() in ("true", "1", "yes", "on")


def book_multiplier() -> dict:
    """Combined BOOK-level multiplier: discrete regime × HMM crisis-aware × vol-target,
    clamped [0.3, 1.4]. Each overlay is env-flagged (HMM_SIZING, VOL_TARGET_SIZING) and
    fails soft to 1.0; SPY is fetched once and shared. Returns {mult, parts} for logging."""
    reg = regime_size_multiplier()
    hmm = vt = 1.0
    closes = None
    if _flag("HMM_SIZING") or _flag("VOL_TARGET_SIZING"):
        try:
            from app.services.market_data import fetch
            df = fetch("SPY", period="1y")
            if df is not None and len(df) > 60:
                closes = df["close"].astype(float).values
        except Exception:
            closes = None
    if closes is not None and _flag("HMM_SIZING"):
        try:
            from app.services.regime_hmm import hmm_size_multiplier
            hmm = hmm_size_multiplier(closes)
        except Exception:
            hmm = 1.0
    if closes is not None and _flag("VOL_TARGET_SIZING"):
        try:
            import numpy as np
            rets = np.diff(np.log(closes[-22:]))
            realized = float(np.std(rets)) * (252 ** 0.5)
            vt = vol_target_multiplier(realized)
        except Exception:
            vt = 1.0
    mult = float(max(0.3, min(1.4, reg * hmm * vt)))
    return {"mult": round(mult, 3), "regime": round(reg, 3),
            "hmm": round(hmm, 3), "vol_target": round(vt, 3)}


__all__ = ["regime_size_multiplier", "conviction_size_multiplier", "position_multiplier",
           "vol_target_multiplier", "fractional_kelly", "book_multiplier"]
