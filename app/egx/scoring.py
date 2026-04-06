"""EGX Pro V9 signal scoring — 6-criteria system.

Criteria (each worth 1 point, max 6):
1. TREND:        EMA alignment (close > EMA21 > EMA50)
2. MOMENTUM:     MACD histogram rising/falling
3. CONFIRMATION: Previous candle direction + close position
4. RSI:          Sweet spot (35-60 for longs, 40-65 for shorts)
5. VOLUME:       Surge above average (vol_ratio >= threshold)
6. VWAP:         Price above/below VWAP

Signal classification:
- Score 6/6: STRONG BUY / STRONG SELL
- Score 5/6: BUY / SELL
- Score 4/6: WEAK BUY / WEAK SELL
- Score < 4: NO SIGNAL
"""

from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np
import pandas as pd


@dataclass
class EgxStrategyConfig:
    """EGX Pro V9 strategy parameters."""
    initial_capital: float = 90_000.0

    # Risk Management
    risk_per_trade: float = 0.02
    max_daily_risk: float = 0.04

    # Trend
    ema_fast: int = 9
    ema_mid: int = 21
    ema_slow: int = 50

    # RSI
    rsi_period: int = 14
    rsi_oversold: float = 35
    rsi_overbought: float = 65

    # MACD
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9

    # ATR
    atr_period: int = 14
    sl_atr_mult: float = 2.0
    tp_atr_mult: float = 3.0

    # Volume
    vol_avg_period: int = 20
    vol_surge: float = 1.5

    # Signal quality
    min_score: int = 5

    # Filters
    min_price: float = 2.0
    min_avg_volume: float = 50_000


def score_signal(row, prev, cfg: EgxStrategyConfig) -> Dict[str, int]:
    """Score long and short signals using 6 criteria.

    Returns dict with 'long' score and 'short' score (0-6 each),
    plus per-tool breakdown.
    """
    long_score = 0
    short_score = 0
    long_tools = {}
    short_tools = {}

    # 1. TREND: EMA alignment
    if row["close"] > row["ema_mid"] > row["ema_slow"]:
        long_score += 1
        long_tools["EMA Trend"] = "BULLISH"
    else:
        long_tools["EMA Trend"] = "BEARISH"

    if row["close"] < row["ema_mid"] < row["ema_slow"]:
        short_score += 1
        short_tools["EMA Trend"] = "BEARISH"
    else:
        short_tools["EMA Trend"] = "BULLISH"

    # 2. MOMENTUM: MACD histogram
    if row["macd_hist"] > 0 and row["macd_hist"] > prev["macd_hist"]:
        long_score += 1
        long_tools["MACD Momentum"] = "RISING"
    else:
        long_tools["MACD Momentum"] = "FLAT/FALLING"

    if row["macd_hist"] < 0 and row["macd_hist"] < prev["macd_hist"]:
        short_score += 1
        short_tools["MACD Momentum"] = "FALLING"
    else:
        short_tools["MACD Momentum"] = "FLAT/RISING"

    # 3. CONFIRMATION: Previous candle
    if prev["is_green"] and prev["close_position"] > 0.5:
        long_score += 1
        long_tools["Candle Confirm"] = "GREEN STRONG"
    else:
        long_tools["Candle Confirm"] = "WEAK"

    if not prev["is_green"] and prev["close_position"] < 0.5:
        short_score += 1
        short_tools["Candle Confirm"] = "RED STRONG"
    else:
        short_tools["Candle Confirm"] = "WEAK"

    # 4. RSI SWEET SPOT
    if cfg.rsi_oversold <= row["rsi"] <= 60:
        long_score += 1
        long_tools["RSI Zone"] = f"SWEET ({row['rsi']:.1f})"
    else:
        long_tools["RSI Zone"] = f"OUT ({row['rsi']:.1f})"

    if 40 <= row["rsi"] <= cfg.rsi_overbought:
        short_score += 1
        short_tools["RSI Zone"] = f"SWEET ({row['rsi']:.1f})"
    else:
        short_tools["RSI Zone"] = f"OUT ({row['rsi']:.1f})"

    # 5. VOLUME SURGE
    if pd.notna(row["vol_ratio"]) and row["vol_ratio"] >= cfg.vol_surge:
        long_score += 1
        short_score += 1
        vol_label = f"SURGE ({row['vol_ratio']:.1f}x)"
        long_tools["Volume"] = vol_label
        short_tools["Volume"] = vol_label
    else:
        ratio = row["vol_ratio"] if pd.notna(row["vol_ratio"]) else 0
        long_tools["Volume"] = f"LOW ({ratio:.1f}x)"
        short_tools["Volume"] = f"LOW ({ratio:.1f}x)"

    # 6. VWAP CONFIRMATION
    if row["close"] > row["vwap"]:
        long_score += 1
        long_tools["VWAP"] = "ABOVE"
    else:
        long_tools["VWAP"] = "BELOW"

    if row["close"] < row["vwap"]:
        short_score += 1
        short_tools["VWAP"] = "BELOW"
    else:
        short_tools["VWAP"] = "ABOVE"

    return {
        "long": long_score,
        "short": short_score,
        "long_tools": long_tools,
        "short_tools": short_tools,
    }


def classify_signal(score: int) -> str:
    """Classify signal strength from V9 score."""
    if score >= 6:
        return "STRONG"
    elif score >= 5:
        return "BUY"
    elif score >= 4:
        return "WEAK"
    else:
        return "NONE"


def generate_signals(df: pd.DataFrame, cfg: EgxStrategyConfig) -> pd.DataFrame:
    """Generate trading signals for all rows.

    Adds columns: signal (1=long, -1=short, 0=none), score, signal_label, tool_votes
    """
    df = df.copy()
    df["signal"] = 0
    df["score"] = 0
    df["signal_label"] = ""
    df["tool_votes"] = None

    for i in range(2, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i - 1]

        # Skip incomplete data
        if pd.isna(row.get("ema_slow")) or pd.isna(row.get("rsi")) or pd.isna(row.get("atr")):
            continue

        # Price filter
        if row["close"] < cfg.min_price:
            continue

        # Volume filter
        if pd.notna(row.get("vol_avg")) and row["vol_avg"] < cfg.min_avg_volume:
            continue

        scores = score_signal(row, prev, cfg)

        # Long signal
        if scores["long"] >= cfg.min_score:
            df.iat[i, df.columns.get_loc("signal")] = 1
            df.iat[i, df.columns.get_loc("score")] = scores["long"]
            label = "STRONG BUY" if scores["long"] >= 6 else "BUY" if scores["long"] >= 5 else "WEAK BUY"
            df.iat[i, df.columns.get_loc("signal_label")] = label
            df.at[df.index[i], "tool_votes"] = scores["long_tools"]

        # Short signal
        elif scores["short"] >= cfg.min_score:
            df.iat[i, df.columns.get_loc("signal")] = -1
            df.iat[i, df.columns.get_loc("score")] = scores["short"]
            label = "STRONG SELL" if scores["short"] >= 6 else "SELL" if scores["short"] >= 5 else "WEAK SELL"
            df.iat[i, df.columns.get_loc("signal_label")] = label
            df.at[df.index[i], "tool_votes"] = scores["short_tools"]

    return df
