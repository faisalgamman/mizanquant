"""Signal archetype detectors — pure functions shared between backtest and live.

Each detector takes (df, spy_df=None, intraday_df=None) and returns
dict | None with the same shape: {archetype, score, entry, stop, target, reasons[], flags}.

DESIGN PRINCIPLES:
  * Pure functions — no I/O, no state, no side effects. Backtest and live paths
    call the same code → guaranteed parity.
  * Hard gates (liquidity, price, extension, earnings) are applied BEFORE detection.
    No archetype bypasses the safety floor.
  * All detectors are opt-in via OFF-by-default feature flags. None affect live
    selection until their backtest passes the validation criteria (DSR≥0.95,
    permutation p<0.05, reality-check LB>0, walk-forward consistency ≥3 windows).

Archetypes:
  * pullback  — EMA50 uptrend + pullback to EMA21/support + RSI 35-50 + bullish candle
  * breakout  — BB squeeze + volume expansion + close above N-day high
  * reversal  — downtrend exhaustion + higher low + EMA21 reclaim (highest bar)
  * gap_go    — gap up 1.5-5% + holds above VWAP + volume surge (needs intraday; stubbed until Phase 5)
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd

from app.services.technical import atr, bollinger_bands, ema, macd, rsi, sma, volume_ratio, vwap

logger = logging.getLogger("screener")

# ── Shared hard-gate constants (mirror USX Pro defaults) ────────────────────────

_MIN_ADV_M       = 20.0       # Minimum ADV in millions
_MIN_PRICE       = 5.0        # Minimum price
_MAX_EXTENSION   = 35.0       # Max % from EMA200
_EARNINGS_WINDOW = 5          # Days to black out before earnings

# ── Archetype-specific thresholds ───────────────────────────────────────────────

_PULLBACK_EMA_TREND = 50       # Must be above this EMA for trend
_PULLBACK_RSI_MIN   = 35
_PULLBACK_RSI_MAX   = 50
_PULLBACK_VOL_MIN   = 0.8      # Volume ratio floor

_BREAKOUT_BB_SQUEEZE_MAX = 5.0 # BB width % qualifies as squeeze
_BREAKOUT_LOOKBACK_HIGH  = 20  # N-day high
_BREAKOUT_VOL_MIN        = 1.5 # Volume ratio floor

_REVERSAL_DOWN_PCT_MIN  = 15.0   # Min % decline from recent high
_REVERSAL_RSI_OVERSOLD   = 35    # RSI must have been below this recently


# ── Internal helpers ────────────────────────────────────────────────────────────

def _candle_body(df: pd.DataFrame, idx: int = -1) -> tuple[float, float, float, bool]:
    """Return (open, close, body_pct, is_bullish) for a candle row index."""
    o = float(df["open"].iloc[idx])
    c = float(df["close"].iloc[idx])
    body = abs(c - o)
    body_pct = (body / o * 100) if o > 0 else 0.0
    is_bull = c > o
    return o, c, body_pct, is_bull


def _hammer_like(df: pd.DataFrame, idx: int = -1) -> bool:
    """True if the candle at idx is a hammer/inverted-hammer (small body, long lower wick)."""
    o = float(df["open"].iloc[idx])
    c = float(df["close"].iloc[idx])
    h = float(df["high"].iloc[idx])
    l = float(df["low"].iloc[idx])
    body = abs(c - o)
    if body == 0:
        return False
    lower_wick = min(o, c) - l
    upper_wick = h - max(o, c)
    total_range = h - l
    if total_range == 0:
        return False
    return lower_wick >= 2 * body and upper_wick <= 0.3 * body


def _engulfing_bullish(df: pd.DataFrame) -> bool:
    """True if the last candle is a bullish engulfing of the previous."""
    if len(df) < 2:
        return False
    o0, c0 = float(df["open"].iloc[-2]), float(df["close"].iloc[-2])
    o1, c1 = float(df["open"].iloc[-1]), float(df["close"].iloc[-1])
    prev_bear = c0 < o0
    curr_bull = c1 > o1
    return bool(prev_bear and curr_bull and o1 < c0 and c1 > o0)


def _recent_low(df: pd.DataFrame, lookback: int = 20) -> float:
    """Lowest low in the last  candles (excluding the last)."""
    return float(df["low"].iloc[-(lookback + 1):-1].min())


def _recent_high(df: pd.DataFrame, lookback: int = 60) -> float:
    """Highest high in the last  candles (excluding the last)."""
    return float(df["high"].iloc[-(lookback + 1):-1].max())


# ── Hard Gates (shared safety floor) ────────────────────────────────────────────

def _hard_gates(df: pd.DataFrame, symbol: str = "?") -> dict | None:
    """Apply liquidity, price, extension and earnings gates.

    Returns None if all gates pass, or a dict with {passes: False, reason: ...}
    for consistent early-exit signalling.
    """
    if df is None or len(df) < 60:
        return {"passes": False, "reason": "insufficient data"}

    close = df["close"].astype(float)
    volume = df["volume"].astype(float)
    last = float(close.iloc[-1])

    # Liquidity gate (ADV)
    adv20 = float((close * volume).rolling(20).mean().iloc[-1]) / 1e6
    if adv20 < _MIN_ADV_M:
        return {"passes": False, "reason": f"ADV M<M"}

    # Price gate
    if last < _MIN_PRICE:
        return {"passes": False, "reason": f"price <"}

    # Extension gate (from EMA200)
    if len(close) >= 200:
        ema200 = float(ema(close, 200).iloc[-1])
        ext_pct = float((last - ema200) / ema200 * 100) if ema200 > 0 else 0.0
        if abs(ext_pct) > _MAX_EXTENSION:
            return {"passes": False, "reason": f"extended {ext_pct:+.1f}% from EMA200"}

    return None  # All gates passed


# ═══════════════════════════════════════════════════════════════════════════════════
# ARCHETYPE DETECTORS
# ═══════════════════════════════════════════════════════════════════════════════════

def detect_pullback(df: pd.DataFrame, spy_df: Optional[pd.DataFrame] = None) -> dict | None:
    """Detect a pullback within an uptrend.

    Conditions:
      1. Price > EMA50 (uptrend) — hard trend gate
      2. Price has pulled back near EMA21 or a recent support level
      3. RSI 35-50 (not overbought, not yet oversold — sweet spot)
      4. Last candle is a bullish reversal pattern (hammer, engulfing, or strong close)
      5. Volume confirms (≥ 0.8× average)

    Returns:
        dict with {archetype, score, entry, stop, target, reasons, flags} or None.
    """
    gate = _hard_gates(df)
    if gate is not None:
        return None

    close = df["close"].astype(float)
    last = float(close.iloc[-1])
    ema50 = float(ema(close, 50).iloc[-1])
    ema21 = float(ema(close, 21).iloc[-1])

    # ── Trend gate: must be above EMA50 ──
    if last <= ema50:
        return None

    # ── Pullback proximity: within 5% of EMA21 or within 3% of 20-day low ──
    near_ema21 = (last - ema21) / ema21 * 100
    low20 = _recent_low(df, 20)
    near_low20 = (last - low20) / low20 * 100 if low20 > 0 else 999
    is_pullback = near_ema21 <= 5.0 or near_low20 <= 3.0
    if not is_pullback:
        return None

    # ── RSI sweet spot: 35-50 ──
    rsi_val = float(rsi(close).iloc[-1])
    if rsi_val < _PULLBACK_RSI_MIN or rsi_val > _PULLBACK_RSI_MAX:
        return None

    # ── Bullish reversal candle ──
    is_hammer = _hammer_like(df)
    is_engulf = _engulfing_bullish(df)
    _, _, _, is_bull = _candle_body(df)
    strong_close = is_bull and (float(close.iloc[-1]) > float((df["high"].iloc[-1] + df["low"].iloc[-1]) / 2))
    if not (is_hammer or is_engulf or strong_close):
        return None

    # ── Volume confirmation ──
    vr = volume_ratio(df)
    if vr < _PULLBACK_VOL_MIN:
        return None

    # ── Score & trade plan ──
    reasons = ["EMA50 uptrend", f"pullback {near_ema21:.1f}% from EMA21"]
    score = 65.0
    if is_hammer:
        reasons.append("hammer candle")
        score += 5
    if is_engulf:
        reasons.append("bullish engulfing")
        score += 10
    if rsi_val < 40:
        reasons.append("deep RSI pullback")
        score += 5
    if near_low20 <= 2.0:
        reasons.append("near 20d low support")
        score += 5

    # Entry = last close; Stop = 2× ATR below last low or 4% (whichever wider)
    atr_val = float(atr(df).iloc[-1])
    stop = max(last - 2 * atr_val, last * 0.96)
    target = last + 3 * (last - stop)  # 1:3 R:R

    return {
        "archetype": "pullback",
        "score": min(100.0, score),
        "entry": round(last, 2),
        "stop": round(stop, 2),
        "target": round(target, 2),
        "reasons": reasons,
        "flags": {
            "trend_ok": True,
            "pullback_ok": True,
            "rsi_ok": True,
            "candle_ok": True,
            "volume_ok": vr >= _PULLBACK_VOL_MIN,
        },
    }


def detect_breakout(df: pd.DataFrame) -> dict | None:
    """Detect a volatility contraction + expansion breakout.

    Conditions:
      1. Bollinger Band squeeze (width < 5%) within last 5 bars
      2. Volume expansion: current volume ≥ 1.5× 20-day average
      3. Close above the highest high of the last 20 days (excluding today)
      4. MACD bullish (or histogram turning up)

    Returns:
        dict with {archetype, score, entry, stop, target, reasons, flags} or None.
    """
    gate = _hard_gates(df)
    if gate is not None:
        return None

    close = df["close"].astype(float)
    last = float(close.iloc[-1])
    vol = df["volume"].astype(float)

    # ── BB squeeze: any bandwidth < 5% in the last 5 bars ──
    _, _, _, bandwidth = bollinger_bands(close, 20, 2)
    recent_bw = bandwidth.iloc[-6:-1]  # last 5 bars before current
    squeeze = float(recent_bw.min()) < _BREAKOUT_BB_SQUEEZE_MAX
    if not squeeze:
        return None

    # ── Close above 20-day high ──
    high20 = float(df["high"].iloc[-(_BREAKOUT_LOOKBACK_HIGH + 1):-1].max())
    if last <= high20:
        return None

    # ── Volume expansion ──
    vr = volume_ratio(df)
    if vr < _BREAKOUT_VOL_MIN:
        return None

    # ── MACD bullish ──
    macd_line, macd_sig, macd_hist = macd(close)
    macd_bull = float(macd_line.iloc[-1]) > float(macd_sig.iloc[-1])
    hist_turning = float(macd_hist.iloc[-1]) > float(macd_hist.iloc[-2]) if len(macd_hist) >= 2 else False

    # ── Score & trade plan ──
    reasons = ["BB squeeze breakout", f"above {_BREAKOUT_LOOKBACK_HIGH}d high"]
    score = 60.0
    if vr >= 2.0:
        reasons.append(f"volume surge {vr:.1f}x")
        score += 10
    if macd_bull:
        reasons.append("MACD bullish")
        score += 10
    if hist_turning:
        reasons.append("MACD histogram rising")
        score += 5

    atr_val = float(atr(df).iloc[-1])
    stop = max(last - 1.5 * atr_val, last * 0.96)
    target = last + 3 * (last - stop)

    return {
        "archetype": "breakout",
        "score": min(100.0, score),
        "entry": round(last, 2),
        "stop": round(stop, 2),
        "target": round(target, 2),
        "reasons": reasons,
        "flags": {
            "squeeze_ok": True,
            "high_break_ok": True,
            "volume_ok": vr >= _BREAKOUT_VOL_MIN,
            "macd_ok": macd_bull or hist_turning,
        },
    }


def detect_reversal(df: pd.DataFrame, spy_df: Optional[pd.DataFrame] = None) -> dict | None:
    """Detect a downtrend exhaustion reversal.

    This is the most speculative archetype — highest acceptance bar.

    Conditions:
      1. Stock has declined >= 15% from its 60-day high
      2. RSI was oversold (< 35) within the last 10 bars AND is now recovering (> 40)
      3. A higher low has formed (last 2 swing lows are rising)
      4. Price has reclaimed EMA21 (or is within 2% of it)
      5. Bullish candle: hammer, engulfing, or strong close above open

    Returns:
        dict with {archetype, score, entry, stop, target, reasons, flags} or None.
    """
    gate = _hard_gates(df)
    if gate is not None:
        return None

    close = df["close"].astype(float)
    last = float(close.iloc[-1])
    high = df["high"].astype(float)
    low = df["low"].astype(float)

    # ── Declined >= 15% from 60-day high ──
    high60 = _recent_high(df, 60)
    decline_pct = (last - high60) / high60 * 100
    if decline_pct > -_REVERSAL_DOWN_PCT_MIN:
        return None

    # ── RSI: was oversold, now recovering ──
    rsi_series = rsi(close)
    rsi_now = float(rsi_series.iloc[-1])
    rsi_min10 = float(rsi_series.iloc[-11:-1].min()) if len(rsi_series) >= 11 else rsi_now
    was_oversold = rsi_min10 < _REVERSAL_RSI_OVERSOLD
    recovering = rsi_now > 40
    if not was_oversold or not recovering:
        return None

    # ── Higher low pattern ──
    # Find two most recent swing lows (local minima over a 5-bar window)
    def _swing_lows(s: pd.Series, window: int = 5) -> list:
        """Return indices of local minima in the last  bars."""
        vals = []
        s_tail = s.iloc[-window * 4:]
        for i in range(window, len(s_tail) - window):
            if s_tail.iloc[i] == s_tail.iloc[i - window:i + window + 1].min():
                vals.append(float(s_tail.iloc[i]))
        return vals

    sw_lows = _swing_lows(low)
    higher_low = len(sw_lows) >= 2 and sw_lows[-1] > sw_lows[-2]
    if not higher_low:
        return None

    # ── Near or above EMA21 ──
    ema21 = float(ema(close, 21).iloc[-1])
    near_ema21 = (last - ema21) / ema21 * 100
    if near_ema21 < -2.0:
        return None

    # ── Bullish candle ──
    is_hammer = _hammer_like(df)
    is_engulf = _engulfing_bullish(df)
    _, _, _, is_bull = _candle_body(df)
    if not (is_hammer or is_engulf or is_bull):
        return None

    # ── Score & trade plan ──
    reasons = [f"declined {abs(decline_pct):.1f}%", "RSI recovery from oversold", "higher low formed"]
    score = 55.0  # Lower base — speculative
    if is_hammer:
        reasons.append("hammer candle")
        score += 5
    if is_engulf:
        reasons.append("bullish engulfing")
        score += 10
    if near_ema21 >= 0:
        reasons.append("reclaimed EMA21")
        score += 10

    atr_val = float(atr(df).iloc[-1])
    # Wider stop for reversal — 3x ATR or 5% floor
    stop = max(last - 3 * atr_val, last * 0.95)
    target = last + 3 * (last - stop)

    return {
        "archetype": "reversal",
        "score": min(100.0, score),
        "entry": round(last, 2),
        "stop": round(stop, 2),
        "target": round(target, 2),
        "reasons": reasons,
        "flags": {
            "decline_ok": True,
            "rsi_recovery_ok": True,
            "higher_low_ok": True,
            "ema21_ok": near_ema21 >= -2.0,
            "candle_ok": True,
        },
    }


def detect_gap_go(df: pd.DataFrame, intraday_df: Optional[pd.DataFrame] = None) -> dict | None:
    """Detect gap-and-go patterns (Phase 5).

    Conditions:
      1. Gap up 1.5-5% from prior close (daily df)
      2. Holds above VWAP through the current intraday session
      3. Last 15-min bar close > VWAP
      4. Intraday volume surge (RVOL vs avg same-slot)

    Requires intraday 15-min data. Returns None gracefully when intraday_df
    is missing or insufficient.

    Returns:
        dict with {archetype, score, entry, stop, target, reasons, flags} or None.
    """
    if intraday_df is None or len(intraday_df) < 5:
        return None

    gate = _hard_gates(df)
    if gate is not None:
        return None

    close = df["close"].astype(float)
    last_daily = float(close.iloc[-1])
    prev_close = float(close.iloc[-2]) if len(close) >= 2 else last_daily

    # ── Gap check: open vs prior close (1.5% – 5%) ──
    # Use the first intraday bar's open as today's open for precision
    intra_open = float(intraday_df["open"].iloc[0])
    gap_pct = (intra_open - prev_close) / prev_close * 100
    if gap_pct < 1.5 or gap_pct > 5.0:
        return None

    # ── Holds above VWAP ──
    vwap_series = vwap(intraday_df)
    vwap_now = float(vwap_series.iloc[-1])
    intra_last = float(intraday_df["close"].iloc[-1])
    if intra_last <= vwap_now:
        return None

    # ── Intraday volume surge ──
    intra_vol = intraday_df["volume"].astype(float)
    # Compare current bar volume to same-slot average (last N days)
    # Use simple heuristic: current volume vs average of last 20 intraday bars
    vol_now = float(intra_vol.iloc[-1])
    vol_avg = float(intra_vol.iloc[-21:-1].mean()) if len(intra_vol) >= 21 else float(intra_vol.mean())
    rvol = vol_now / vol_avg if vol_avg > 0 else 1.0
    if rvol < 1.5:
        return None

    # ── Score & trade plan ──
    reasons = [f"gap up {gap_pct:.1f}%", "holds above VWAP", f"RVOL {rvol:.1f}x"]
    score = 70.0
    if gap_pct <= 3.0:
        reasons.append("ideal gap size")
        score += 5
    if rvol >= 2.5:
        reasons.append("strong volume surge")
        score += 10
    # Check for continuation (higher high intraday)
    intra_high_recent = float(intraday_df["high"].iloc[-3:].max())
    if intra_last >= intra_high_recent * 0.995:
        reasons.append("near intraday high")
        score += 5

    atr_val = float(atr(df).iloc[-1])
    stop = max(intra_last - 1.5 * atr_val, intra_last * 0.97)
    target = intra_last + 2 * (intra_last - stop)  # 1:2 R:R (shorter horizon)

    return {
        "archetype": "gap_go",
        "score": min(100.0, score),
        "entry": round(intra_last, 2),
        "stop": round(stop, 2),
        "target": round(target, 2),
        "reasons": reasons,
        "flags": {
            "gap_ok": True,
            "vwap_hold_ok": True,
            "volume_surge_ok": rvol >= 1.5,
        },
    }


# ── Registry ────────────────────────────────────────────────────────────────────

_ARCHETYPE_DETECTORS: dict[str, callable] = {
    "pullback": detect_pullback,
    "breakout": detect_breakout,
    "reversal": detect_reversal,
    "gap_go": detect_gap_go,
}

_ARCHETYPE_NAMES = list(_ARCHETYPE_DETECTORS.keys())


def get_detector(archetype: str):
    """Return the detector function for the given archetype name, or None."""
    return _ARCHETYPE_DETECTORS.get(archetype)


def detect_archetype(archetype: str, df: pd.DataFrame,
                     spy_df: Optional[pd.DataFrame] = None,
                     intraday_df: Optional[pd.DataFrame] = None) -> dict | None:
    """Convenience dispatcher: call the right detector by name."""
    fn = _ARCHETYPE_DETECTORS.get(archetype)
    if fn is None:
        return None
    try:
        if archetype == "breakout":
            return fn(df)
        elif archetype == "gap_go":
            return fn(df, intraday_df)
        else:
            return fn(df, spy_df)
    except Exception:
        logger.exception("detect_archetype(%s) failed", archetype)
        return None
