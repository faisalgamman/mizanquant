"""Trade plan — auto TP1/TP2/TP3, Stop Loss, and position sizing."""

from __future__ import annotations

import logging
import os
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger("screener")

RISK_PER_TRADE = float(os.environ.get("RISK_PER_TRADE_PCT", "1.0"))  # % of portfolio
MAX_PORTFOLIO_PCT = float(os.environ.get("MAX_POSITION_PCT", "20.0"))  # max per position
MIN_RR = float(os.environ.get("MIN_RISK_REWARD", "1.5"))  # min risk:reward


def calculate_stop_loss(df: pd.DataFrame, atr_mult: float = 1.5) -> tuple[float, float]:
    """Calculate stop loss from recent low and ATR.

    Returns:
        (stop_loss_price, atr_value)
    """
    from app.services.technical import atr
    if len(df) < 10:
        return 0.0, 0.0
    atr_val = float(atr(df, 14).iloc[-1])
    recent_low = float(df["low"].iloc[-10:].min())
    entry = float(df["close"].iloc[-1])
    stop = min(recent_low - 0.005 * entry, entry - atr_val * atr_mult)
    return round(stop, 2), round(atr_val, 2)


def calculate_tp_levels(entry: float, stop: float) -> dict:
    """Calculate TP1 (50%), TP2 (30%), TP3 (20%).

    TP1 = Entry + (Entry - Stop) × 1.0  → closes 50%
    TP2 = Entry + (Entry - Stop) × 2.0  → closes 30%
    TP3 = Entry + (Entry - Stop) × 3.0  → closes 20%

    Returns:
        Dict with tp1, tp2, tp3, risk_amount, reward_amount, rr_ratio.
    """
    risk = entry - stop
    if risk <= 0:
        return {"tp1": 0, "tp2": 0, "tp3": 0, "risk_amount": 0, "reward_amount": 0, "rr_ratio": 0}
    tp1 = round(entry + risk * 1.0, 2)
    tp2 = round(entry + risk * 2.0, 2)
    tp3 = round(entry + risk * 3.0, 2)
    # Blended reward: 50% at TP1, 30% at TP2, 20% at TP3
    blended_reward = risk * 0.5 + risk * 2.0 * 0.3 + risk * 3.0 * 0.2  # = risk * 1.7
    rr = round(blended_reward / risk, 2)
    return {
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "risk_per_share": round(risk, 2),
        "reward_per_share": round(blended_reward, 2),
        "rr_ratio": rr,
    }


def calculate_position_size(
    entry: float,
    stop: float,
    portfolio_equity: float,
    current_price: float = 0,
) -> dict:
    """Calculate position size based on risk.

    Deprecated — use app.services.risk_manager.calculate_position_size instead.
    This version lacks Kelly integration, regime awareness, and config system
    integration that the risk_manager version provides.

    Args:
        entry: Entry price.
        stop: Stop loss price.
        portfolio_equity: Current portfolio equity.
        current_price: Current market price (defaults to entry).

    Returns:
        Dict with shares, risk_amount, position_value, portfolio_pct.
    """
    import warnings
    warnings.warn(
        "trade_plan.calculate_position_size is deprecated. "
        "Use risk_manager.calculate_position_size for Kelly+regime aware sizing.",
        DeprecationWarning,
        stacklevel=2,
    )
    from app.services.risk_manager import calculate_position_size as _rm_cps
    price = current_price or entry
    result = _rm_cps(
        price=price,
        stop_loss=stop,
        available_cash=portfolio_equity * 0.5,
        total_equity=portfolio_equity,
    )
    return {
        "shares": result.get("qty", 0),
        "risk_amount": result.get("risk_amount", 0),
        "position_value": result.get("position_value", 0),
        "portfolio_pct": result.get("position_pct", 0),
    }


def generate_trade_plan(
    df: pd.DataFrame,
    portfolio_equity: float = 100000.0,
    atr_mult: float = 1.5,
) -> dict:
    """Generate a complete trade plan using the validated Option-A exit.

    Option-A = a FIXED wide catastrophe stop at entry·(1 − SWING_TRAIL_PCT),
    default 15% below entry, plus a ~SWING_MAX_HOLD_DAYS (20) trading-day TIME
    exit. The 2026-06 paired backtest (DSR 0.985) showed the edge is the time
    hold + wide stop — the old tight ATR stop gets shaken out before the swing
    develops. ``atr`` is still returned for reference only (no longer the stop).

    Args:
        df: OHLCV DataFrame.
        portfolio_equity: Current portfolio equity.
        atr_mult: kept for the reference ATR value only.

    Returns:
        Dict with entry, stop_loss, tp levels, position sizing, rr_ratio,
        plus exit_mode / catastrophe_stop_pct / time_exit_days.
    """
    if df is None or len(df) < 10:
        return {"error": "insufficient data"}

    entry = float(df["close"].iloc[-1])
    _, atr_val = calculate_stop_loss(df, atr_mult)  # ATR kept for reference only

    # Option-A exit parameters (from config, with safe fallbacks).
    try:
        from app.config import settings
        stop_pct = float(getattr(settings, "SWING_TRAIL_PCT", 15.0))
        hold_days = int(getattr(settings, "SWING_MAX_HOLD_DAYS", 20))
    except Exception:
        stop_pct, hold_days = 15.0, 20

    stop_loss = round(entry * (1.0 - stop_pct / 100.0), 2)
    if stop_loss <= 0 or entry <= stop_loss:
        return {"error": "invalid stop loss"}

    tp_levels = calculate_tp_levels(entry, stop_loss)
    sizing = calculate_position_size(entry, stop_loss, portfolio_equity)

    if tp_levels["rr_ratio"] < MIN_RR:
        return {
            "error": f"risk:reward {tp_levels['rr_ratio']} below minimum {MIN_RR}",
            "rr_ratio": tp_levels["rr_ratio"],
            "entry": entry,
            "stop_loss": stop_loss,
        }

    return {
        "entry": entry,
        "stop_loss": stop_loss,
        "atr": atr_val,
        "exit_mode": "option_a",
        "catastrophe_stop_pct": round(stop_pct, 2),
        "time_exit_days": hold_days,
        **tp_levels,
        **sizing,
    }


# Default for direct import
generate_trade_plan.__defaults__ = (100000.0, 1.5)
