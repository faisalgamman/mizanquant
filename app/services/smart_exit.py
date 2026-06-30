"""Smart exit manager — lock winners, cut weakening ones.

The validation ledgers' original exit (`signal_tracker._simulate_fixed_exit`)
only ever closed on a fixed catastrophe stop or a time limit — so a winner that
ran past its target was never locked: it rode to the time exit (giving profit
back) or down to the stop. This module adds active profit management on top of
the catastrophe stop:

  1. catastrophe stop  — hard floor, low ≤ entry·(1−stop_pct).               [loss cap]
  2. trailing stop     — once the position has run +EXIT_TRAIL_ARM_PCT, exit
                         if it gives back EXIT_TRAIL_GIVEBACK_PCT from its peak.[lock gains]
  3. technical weakening — when in profit (≥ EXIT_TECH_MIN_PROFIT_PCT) and the
                         chart rolls over (close < EMA10 AND (RSI<50 or MACD<0)),
                         sell to protect the gain.                            [smart exit]
  4. time exit         — fall back to the max-hold close.                     [backstop]

`simulate_smart_exit` walks the post-entry bars and returns the FIRST trigger —
the same contract as `_simulate_fixed_exit` (None until matured), so it drops
into the ledger maturation. The live IBKR-paper monitor runs the same function
over a held position's bars to decide "exit now?". Pure pandas/NumPy; the
technical leg degrades to a no-op when indicator columns are absent.
"""

from __future__ import annotations

import os

import numpy as np

# ── Config (env-driven, all default-on with kill-switches) ───────────────────

SMART_EXIT: bool = os.environ.get("SMART_EXIT", "true").lower() not in ("false", "0", "no")
EXIT_TRAIL_ARM_PCT: float = float(os.environ.get("EXIT_TRAIL_ARM_PCT", "8"))       # arm once +8% in profit
EXIT_TRAIL_GIVEBACK_PCT: float = float(os.environ.get("EXIT_TRAIL_GIVEBACK_PCT", "5"))  # exit on 5% off the peak
EXIT_TECH_WEAKENING: bool = (
    os.environ.get("EXIT_TECH_WEAKENING", "true").lower() not in ("false", "0", "no")
)
EXIT_TECH_MIN_PROFIT_PCT: float = float(os.environ.get("EXIT_TECH_MIN_PROFIT_PCT", "3"))  # only protect a real gain

__all__ = ["SMART_EXIT", "compute_exit_indicators", "simulate_smart_exit"]


def compute_exit_indicators(df):
    """Return a copy of *df* with `_ema10`, `_rsi`, `_macd_hist` columns attached
    (close-based, EMA-smoothed). Compute on the FULL series so the post-entry
    values are properly warmed up. Safe no-op (returns df unchanged) on error or
    short data — the technical exit leg then simply doesn't fire."""
    try:
        out = df.copy()
        c = out["close"].astype(float)
        out["_ema10"] = c.ewm(span=10, adjust=False).mean()
        delta = c.diff()
        up = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
        down = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
        rs = up / down.replace(0, np.nan)
        out["_rsi"] = 100.0 - 100.0 / (1.0 + rs)
        ema12 = c.ewm(span=12, adjust=False).mean()
        ema26 = c.ewm(span=26, adjust=False).mean()
        macd = ema12 - ema26
        out["_macd_hist"] = macd - macd.ewm(span=9, adjust=False).mean()
        return out
    except Exception:
        return df


def _ret(exit_price: float, entry_price: float) -> float:
    return round((exit_price / entry_price - 1.0) * 100.0, 2)


def simulate_smart_exit(
    post_bars,
    entry_price: float,
    *,
    stop_pct: float,
    hold_days: int,
    trail_arm_pct: float = EXIT_TRAIL_ARM_PCT,
    trail_giveback_pct: float = EXIT_TRAIL_GIVEBACK_PCT,
    use_technical: bool = True,
    tech_min_profit_pct: float = EXIT_TECH_MIN_PROFIT_PCT,
):
    """Walk *post_bars* (bars after entry, optionally carrying the indicator
    columns from :func:`compute_exit_indicators`) and return
    ``(ret_pct, exit_price, reason)`` at the FIRST exit trigger, or None when the
    position hasn't matured (no trigger and the time limit not yet reached).

    reason ∈ {"stop", "trailing", "weakening", "time"}. Long-only.
    """
    if entry_price <= 0 or post_bars is None or len(post_bars) == 0:
        return None

    stop_price = entry_price * (1.0 - stop_pct / 100.0)
    arm_level = entry_price * (1.0 + trail_arm_pct / 100.0)
    giveback = trail_giveback_pct / 100.0
    tech_floor = entry_price * (1.0 + tech_min_profit_pct / 100.0)

    n_avail = len(post_bars)
    n = min(hold_days, n_avail)
    peak = entry_price
    has_tech = (
        use_technical
        and EXIT_TECH_WEAKENING
        and all(col in post_bars.columns for col in ("_ema10", "_rsi", "_macd_hist"))
    )

    for i in range(n):
        low = float(post_bars["low"].iloc[i])
        high = float(post_bars["high"].iloc[i])
        close = float(post_bars["close"].iloc[i])

        # 1) catastrophe stop (intrabar) — the hard loss cap
        if low <= stop_price:
            return _ret(stop_price, entry_price), round(stop_price, 2), "stop"

        if high > peak:
            peak = high

        # 2) trailing stop — armed only after a real run, so it locks a gain
        if peak >= arm_level and close <= peak * (1.0 - giveback):
            return _ret(close, entry_price), round(close, 2), "trailing"

        # 3) technical weakening — protect a profit when the chart rolls over
        if has_tech and close >= tech_floor:
            ema10 = float(post_bars["_ema10"].iloc[i])
            rsi = float(post_bars["_rsi"].iloc[i])
            mh = float(post_bars["_macd_hist"].iloc[i])
            if (np.isfinite(ema10) and np.isfinite(rsi) and np.isfinite(mh)
                    and close < ema10 and (rsi < 50.0 or mh < 0.0)):
                return _ret(close, entry_price), round(close, 2), "weakening"

    # 4) time exit — backstop at the max-hold close
    if n_avail >= hold_days:
        px = float(post_bars["close"].iloc[hold_days - 1])
        return _ret(px, entry_price), round(px, 2), "time"

    return None  # not matured yet
