"""Weighted Score System — 100-point composite scoring for trading signals."""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd

from app.services.technical import ema, rsi, macd, bollinger_bands, adx
from app.services.market_context import get_market_context

logger = logging.getLogger("screener")


def weighted_score(
    df: pd.DataFrame,
    spy_df: Optional[pd.DataFrame] = None,
    vix: Optional[float] = None,
) -> dict:
    """Compute the 100-point weighted score for a symbol.

    Score components:
        Daily Trend (EMA):     20 pts
        Regime (SPY+VIX):      15 pts
        RSI Zone:               8 pts
        ADX Trend:              7 pts
        RS vs SPY:             20 pts
        Volume:                 6 pts
        Gap:                    4 pts
        BB Squeeze:             5 pts
        VWAP:                   5 pts
        MACD Histogram:        10 pts
        ─────────────────────────────
        Total:                 100 pts

    Args:
        df: OHLCV DataFrame for the symbol.
        spy_df: SPY OHLCV DataFrame (fetched if None).
        vix: Current VIX value (fetched if None).

    Returns:
        Dict with component scores and total.
    """
    scores = {}
    details = {}

    close = df["close"].astype(float)
    latest_close = float(close.iloc[-1])

    # ── 1. Daily Trend (EMA) — 20 pts ──
    ema_20 = float(ema(close, 20).iloc[-1])
    ema_50 = float(ema(close, 50).iloc[-1]) if len(close) >= 50 else latest_close

    trend_score = 0
    if latest_close > ema_20 > ema_50:
        trend_score = 20  # strong uptrend
    elif latest_close > ema_20:
        trend_score = 14  # above short-term EMA
    elif latest_close > ema_50:
        trend_score = 8   # above long-term EMA
    elif latest_close < ema_50 and latest_close < ema_20:
        trend_score = 0   # downtrend
    else:
        trend_score = 4
    scores["trend"] = trend_score

    # ── 2. Regime (SPY+VIX) — 15 pts ──
    mc = get_market_context()
    spy_regime = mc.get("spy_regime", {}).get("regime", "neutral")
    vix_val = vix or mc.get("vix", {}).get("vix", 20)

    if spy_regime == "bull" and vix_val < 20:
        regime_score = 15
    elif spy_regime == "bull":
        regime_score = 10
    elif spy_regime == "neutral" and vix_val < 25:
        regime_score = 7
    elif spy_regime == "bear":
        regime_score = 0
    else:
        regime_score = 3
    scores["regime"] = regime_score

    # ── 3. RSI Zone — 8 pts ──
    rsi_val = float(rsi(close, 14).iloc[-1])
    if 40 <= rsi_val <= 60:
        rsi_score = 8   # neutral zone, room to run
    elif 30 <= rsi_val <= 70:
        rsi_score = 5
    elif rsi_val < 30:
        rsi_score = 3   # oversold
    else:
        rsi_score = 1   # overbought
    scores["rsi"] = rsi_score

    # ── 4. ADX Trend — 7 pts ──
    if all(c in df.columns for c in ("high", "low")):
        adx_val, plus_di, minus_di = adx(df, 14)
        latest_adx = float(adx_val.iloc[-1])
        latest_pdi = float(plus_di.iloc[-1])
        latest_mdi = float(minus_di.iloc[-1])
        if latest_adx > 25 and latest_pdi > latest_mdi:
            adx_score = 7  # strong uptrend
        elif latest_adx > 25:
            adx_score = 5  # strong trend (direction unknown)
        elif latest_adx >= 20:
            adx_score = 3  # developing
        else:
            adx_score = 0  # choppy
    else:
        adx_score = 0
    scores["adx"] = adx_score

    # ── 5. RS vs SPY — 20 pts ──
    if spy_df is not None and len(spy_df) >= 20:
        spy_close = spy_df["close"].astype(float)
        sym_ret = latest_close / float(close.iloc[-21]) if len(close) >= 21 else 1
        spy_ret = float(spy_close.iloc[-1]) / float(spy_close.iloc[-21]) if len(spy_close) >= 21 else 1
        rs = sym_ret / spy_ret if spy_ret != 0 else 1
        if rs > 1.05:
            rs_score = 20
        elif rs > 1.0:
            rs_score = 14
        elif rs > 0.95:
            rs_score = 8
        else:
            rs_score = 2
    else:
        rs_score = 10  # neutral if no SPY data
    scores["rs"] = rs_score

    # ── 6. Volume — 6 pts ──
    if "volume" in df.columns:
        volume = df["volume"].astype(float)
        vol_ratio = float(volume.iloc[-1] / volume.iloc[-21:].mean()) if len(volume) >= 21 else 1
        if vol_ratio >= 1.5:
            vol_score = 6
        elif vol_ratio >= 1.2:
            vol_score = 4
        elif vol_ratio >= 0.8:
            vol_score = 2
        else:
            vol_score = 0
    else:
        vol_score = 0
    scores["volume"] = vol_score

    # ── 7. Gap — 4 pts ──
    if len(df) >= 2:
        prev_close = float(close.iloc[-2])
        curr_open = float(df["open"].iloc[-1]) if "open" in df.columns else latest_close
        gap_pct = (curr_open / prev_close - 1) * 100 if prev_close != 0 else 0
        if gap_pct > 1:
            gap_score = 4  # gap up
        elif gap_pct < -1:
            gap_score = 0  # gap down
        else:
            gap_score = 2  # no gap
    else:
        gap_score = 0
    scores["gap"] = gap_score

    # ── 8. BB Squeeze — 5 pts ──
    try:
        upper, middle, lower, bandwidth = bollinger_bands(close, 20, 2)
        latest_bw = float(bandwidth.iloc[-1])
        avg_bw = float(bandwidth.iloc[-20:].mean()) if len(bandwidth) >= 20 else latest_bw
        if latest_bw < avg_bw * 0.8:
            bb_score = 5  # squeeze
        elif latest_bw < avg_bw:
            bb_score = 3  # tightening
        else:
            bb_score = 1  # wide
    except Exception:
        bb_score = 0
    scores["bb"] = bb_score

    # ── 9. VWAP — 5 pts ──
    if all(c in df.columns for c in ("high", "low", "volume")):
        typical_price = (df["high"].astype(float) + df["low"].astype(float) + close) / 3
        cum_vwap = (typical_price * df["volume"].astype(float)).cumsum() / df["volume"].astype(float).cumsum()
        vwap_val = float(cum_vwap.iloc[-1])
        if latest_close > vwap_val:
            vwap_score = 5  # above VWAP = bullish
        elif latest_close > vwap_val * 0.99:
            vwap_score = 3  # near VWAP
        else:
            vwap_score = 0  # below VWAP
    else:
        vwap_score = 0
    scores["vwap"] = vwap_score

    # ── 10. MACD Histogram — 10 pts ──
    macd_line, signal_line, histogram = macd(close)
    latest_hist = float(histogram.iloc[-1])
    prev_hist = float(histogram.iloc[-2]) if len(histogram) >= 2 else 0
    if latest_hist > 0 and prev_hist <= 0:
        macd_score = 10  # bullish crossover
    elif latest_hist > 0 and latest_hist > prev_hist:
        macd_score = 7   # increasing momentum
    elif latest_hist > 0:
        macd_score = 4   # positive but slowing
    elif latest_hist < 0 and latest_hist > prev_hist:
        macd_score = 2   # improving from below
    else:
        macd_score = 0   # negative
    scores["macd"] = macd_score

    total = sum(scores.values())
    confidence = round(total / 100 * 100, 1)

    return {
        "total": total,
        "confidence": confidence,
        "components": scores,
        "thresholds": {
            "strong": 80,
            "signal": 65,
            "minimum": 65,
        },
    }


def is_signal_ready(score_result: dict, min_score: int = 65) -> bool:
    """Check if weighted score meets the minimum threshold."""
    return score_result.get("total", 0) >= min_score
