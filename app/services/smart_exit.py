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
# Partial take-profit (scale-out): lock a slice of a winner at a modest target,
# then let the rest ride the trailing/weakening exit — banks profit early without
# capping the big winners a pure trailing stop is there to catch.
EXIT_TP_ENABLED: bool = os.environ.get("EXIT_TP_ENABLED", "true").lower() not in ("false", "0", "no")
EXIT_TP1_PCT: float = float(os.environ.get("EXIT_TP1_PCT", "6"))        # lock once +6%
EXIT_TP1_FRACTION: float = float(os.environ.get("EXIT_TP1_FRACTION", "0.5"))  # sell half
# Loss discipline (report's ①②③⑤): a volatility-scaled stop clamped to a hard
# safety net, a technical-breakdown exit that cuts a loser when the thesis breaks
# (not only at the price stop), and a move-to-break-even after the partial.
EXIT_ATR_STOP: bool = os.environ.get("EXIT_ATR_STOP", "true").lower() not in ("false", "0", "no")
EXIT_ATR_MULT: float = float(os.environ.get("EXIT_ATR_MULT", "2.5"))         # stop = k·ATR%
EXIT_STOP_MIN_PCT: float = float(os.environ.get("EXIT_STOP_MIN_PCT", "5"))   # tightest
EXIT_STOP_MAX_PCT: float = float(os.environ.get("EXIT_STOP_MAX_PCT", "10"))  # safety net (③)
EXIT_BREAKDOWN: bool = os.environ.get("EXIT_BREAKDOWN", "true").lower() not in ("false", "0", "no")
EXIT_BREAKEVEN: bool = os.environ.get("EXIT_BREAKEVEN", "true").lower() not in ("false", "0", "no")
EXIT_BREAKEVEN_BUFFER_PCT: float = float(os.environ.get("EXIT_BREAKEVEN_BUFFER_PCT", "0"))

__all__ = ["SMART_EXIT", "compute_exit_indicators", "simulate_smart_exit",
           "live_exit_decision", "post_entry_bars", "partial_tp_hit",
           "EXIT_TP_ENABLED", "EXIT_TP1_PCT", "EXIT_TP1_FRACTION"]


def partial_tp_hit(post_bars, entry_price: float, tp1_pct: float = EXIT_TP1_PCT):
    """Is the position AT/ABOVE its take-profit target now? (forward — latest bar).

    Returns ``(price, ret_pct)`` at the current price when the gain ≥ tp1_pct, else
    None. The caller sells a fraction and marks the partial taken so it fires once.
    """
    if entry_price <= 0 or post_bars is None or len(post_bars) == 0:
        return None
    close = float(post_bars["close"].iloc[-1])
    if close >= entry_price * (1.0 + tp1_pct / 100.0):
        return round(close, 2), round((close / entry_price - 1.0) * 100.0, 2)
    return None


def post_entry_bars(df, created_at, tail: int = 25):
    """Bars strictly AFTER the entry timestamp.

    market_data.fetch returns a RangeIndex frame with a tz-aware ``date`` COLUMN
    (not a DatetimeIndex), so the natural ``df[df.index > created_at]`` raises
    int-vs-datetime and silently falls back to a wrong trailing window. This
    slices correctly off the ``date`` column (or a DatetimeIndex), returning an
    EMPTY frame when entry is after the last bar (→ "not matured"), and only
    falling back to the last ``tail`` bars when there's genuinely no timestamp."""
    if df is None or len(df) == 0:
        return df
    if created_at is None:
        return df.tail(tail)
    try:
        import pandas as pd
        ca = pd.Timestamp(created_at)
        if ca.tz is None:
            ca = ca.tz_localize("UTC")
        if "date" in df.columns:
            d = pd.to_datetime(df["date"], utc=True)
            return df[(d > ca).values]
        if isinstance(df.index, pd.DatetimeIndex):
            idx = df.index
            if idx.tz is None:
                idx = idx.tz_localize("UTC")
            return df[idx > ca]
        return df.tail(tail)
    except Exception:
        return df.tail(tail)


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
        out["_ema20"] = c.ewm(span=20, adjust=False).mean()
        # ATR% (14) for the volatility-scaled stop — only when H/L are present.
        if "high" in out.columns and "low" in out.columns:
            import pandas as pd
            h = out["high"].astype(float)
            lo = out["low"].astype(float)
            pc = c.shift(1)
            tr = pd.concat([(h - lo), (h - pc).abs(), (lo - pc).abs()], axis=1).max(axis=1)
            out["_atr_pct"] = tr.ewm(alpha=1 / 14, adjust=False).mean() / c * 100.0
        return out
    except Exception:
        return df


def _effective_stop_pct(post_bars, base_stop_pct: float) -> float:
    """Volatility-scaled stop distance (%). When ATR% is available and EXIT_ATR_STOP
    is on: k·ATR% clamped to [EXIT_STOP_MIN_PCT, EXIT_STOP_MAX_PCT] — the safety net
    is the ceiling, so a stop is never wider than EXIT_STOP_MAX_PCT. Else the caller's
    base_stop_pct (also capped by the safety net)."""
    stop_pct = float(base_stop_pct)
    if EXIT_ATR_STOP and post_bars is not None and "_atr_pct" in getattr(post_bars, "columns", []):
        try:
            atr_pct = float(post_bars["_atr_pct"].iloc[-1])
            if np.isfinite(atr_pct) and atr_pct > 0:
                stop_pct = EXIT_ATR_MULT * atr_pct
        except Exception:
            pass
    return float(min(EXIT_STOP_MAX_PCT, max(EXIT_STOP_MIN_PCT, stop_pct)))


def _technical_breakdown(post_bars) -> bool:
    """Thesis broken NOW (any P&L): close below EMA20 AND MACD histogram negative.
    Two confirming bearish conditions → cut a loser before the price stop (②)."""
    if not EXIT_BREAKDOWN or post_bars is None or len(post_bars) == 0:
        return False
    if not all(col in getattr(post_bars, "columns", []) for col in ("_ema20", "_macd_hist")):
        return False
    try:
        close = float(post_bars["close"].iloc[-1])
        ema20 = float(post_bars["_ema20"].iloc[-1])
        mh = float(post_bars["_macd_hist"].iloc[-1])
        return bool(np.isfinite(ema20) and np.isfinite(mh) and close < ema20 and mh < 0.0)
    except Exception:
        return False


def _ret(exit_price: float, entry_price: float) -> float:
    return round((exit_price / entry_price - 1.0) * 100.0, 2)


def simulate_smart_exit(
    post_bars,
    entry_price: float,
    *,
    stop_pct: float,
    hold_days: int,
    breakeven: bool = False,
    trail_arm_pct: float = EXIT_TRAIL_ARM_PCT,
    trail_giveback_pct: float = EXIT_TRAIL_GIVEBACK_PCT,
    use_technical: bool = True,
    tech_min_profit_pct: float = EXIT_TECH_MIN_PROFIT_PCT,
):
    """Walk *post_bars* (bars after entry, optionally carrying the indicator
    columns from :func:`compute_exit_indicators`) and return
    ``(ret_pct, exit_price, reason)`` at the FIRST exit trigger, or None when the
    position hasn't matured. The stop is ATR-scaled + safety-netted (and raised to
    break-even when ``breakeven``); a technical breakdown cuts a loser early.

    reason ∈ {"stop", "breakeven", "trailing", "breakdown", "weakening", "time"}.
    """
    if entry_price <= 0 or post_bars is None or len(post_bars) == 0:
        return None

    eff = _effective_stop_pct(post_bars, stop_pct)
    stop_price = entry_price * (1.0 - eff / 100.0)
    at_be = False
    if breakeven and EXIT_BREAKEVEN:
        be = entry_price * (1.0 - EXIT_BREAKEVEN_BUFFER_PCT / 100.0)
        if be > stop_price:
            stop_price, at_be = be, True
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
    has_bd = EXIT_BREAKDOWN and all(col in post_bars.columns for col in ("_ema20", "_macd_hist"))

    for i in range(n):
        low = float(post_bars["low"].iloc[i])
        high = float(post_bars["high"].iloc[i])
        close = float(post_bars["close"].iloc[i])

        # 1) stop (intrabar) — ATR-scaled / break-even loss cap
        if low <= stop_price:
            return _ret(stop_price, entry_price), round(stop_price, 2), ("breakeven" if at_be else "stop")

        if high > peak:
            peak = high

        # 2) trailing stop — armed only after a real run, so it locks a gain
        if peak >= arm_level and close <= peak * (1.0 - giveback):
            return _ret(close, entry_price), round(close, 2), "trailing"

        # 3) technical breakdown (ANY P&L) — cut a loser when the thesis breaks
        if has_bd:
            ema20 = float(post_bars["_ema20"].iloc[i])
            mh = float(post_bars["_macd_hist"].iloc[i])
            if np.isfinite(ema20) and np.isfinite(mh) and close < ema20 and mh < 0.0:
                return _ret(close, entry_price), round(close, 2), "breakdown"

        # 4) technical weakening — protect a profit when the chart rolls over
        if has_tech and close >= tech_floor:
            ema10 = float(post_bars["_ema10"].iloc[i])
            rsi = float(post_bars["_rsi"].iloc[i])
            mh = float(post_bars["_macd_hist"].iloc[i])
            if (np.isfinite(ema10) and np.isfinite(rsi) and np.isfinite(mh)
                    and close < ema10 and (rsi < 50.0 or mh < 0.0)):
                return _ret(close, entry_price), round(close, 2), "weakening"

    # 5) time exit — backstop at the max-hold close
    if n_avail >= hold_days:
        px = float(post_bars["close"].iloc[hold_days - 1])
        return _ret(px, entry_price), round(px, 2), "time"

    return None  # not matured yet


def live_exit_decision(
    post_bars,
    entry_price: float,
    *,
    stop_pct: float,
    hold_days: int | None,
    breakeven: bool = False,
    trail_arm_pct: float = EXIT_TRAIL_ARM_PCT,
    trail_giveback_pct: float = EXIT_TRAIL_GIVEBACK_PCT,
    use_technical: bool = True,
    tech_min_profit_pct: float = EXIT_TECH_MIN_PROFIT_PCT,
):
    """Decide whether to exit a CURRENTLY-OPEN position from its LATEST bar
    (forward management), using the peak SINCE ENTRY.

    Loss discipline: the stop is **ATR-scaled** and clamped to the safety net
    (`_effective_stop_pct`); once ``breakeven`` (a partial has been banked) it can't
    fall below entry; and a **technical-breakdown** exit cuts a loser on a thesis
    break (close < EMA20 AND MACD < 0) BEFORE the price stop — so a −4% loser that's
    breaking down doesn't wait to become −10%.

    Unlike :func:`simulate_smart_exit` (replay), this acts on the current state only,
    so a winner at its peak is HELD, not closed at a stale trigger. Returns
    ``(reason, exit_price, ret_pct)`` or None to hold. ``hold_days=None`` disables
    the time backstop.
    """
    if entry_price <= 0 or post_bars is None or len(post_bars) == 0:
        return None
    n = len(post_bars)
    close = float(post_bars["close"].iloc[-1])
    low = float(post_bars["low"].iloc[-1])
    peak = max(float(entry_price), float(post_bars["high"].max()))

    # 1) time backstop (optional)
    if hold_days is not None and n >= hold_days:
        return "time", round(close, 2), _ret(close, entry_price)
    # 2) stop — ATR-scaled + safety net; raised to break-even after a partial
    eff = _effective_stop_pct(post_bars, stop_pct)
    stop_price = entry_price * (1.0 - eff / 100.0)
    at_be = False
    if breakeven and EXIT_BREAKEVEN:
        be = entry_price * (1.0 - EXIT_BREAKEVEN_BUFFER_PCT / 100.0)
        if be > stop_price:
            stop_price, at_be = be, True
    if low <= stop_price:
        return ("breakeven" if at_be else "stop"), round(stop_price, 2), _ret(stop_price, entry_price)
    # 3) trailing — armed by the peak since entry, giving back from THAT peak
    if peak >= entry_price * (1.0 + trail_arm_pct / 100.0) and close <= peak * (1.0 - trail_giveback_pct / 100.0):
        return "trailing", round(close, 2), _ret(close, entry_price)
    # 4) technical breakdown (ANY P&L) — cut a loser when the thesis breaks
    if _technical_breakdown(post_bars):
        return "breakdown", round(close, 2), _ret(close, entry_price)
    # 5) technical weakening — protect a real profit when the chart rolls over
    if (use_technical and EXIT_TECH_WEAKENING and close >= entry_price * (1.0 + tech_min_profit_pct / 100.0)
            and all(c in post_bars.columns for c in ("_ema10", "_rsi", "_macd_hist"))):
        ema10 = float(post_bars["_ema10"].iloc[-1])
        rsi = float(post_bars["_rsi"].iloc[-1])
        mh = float(post_bars["_macd_hist"].iloc[-1])
        if (np.isfinite(ema10) and np.isfinite(rsi) and np.isfinite(mh)
                and close < ema10 and (rsi < 50.0 or mh < 0.0)):
            return "weakening", round(close, 2), _ret(close, entry_price)
    return None  # hold
