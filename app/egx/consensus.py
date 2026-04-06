"""EGX multi-tool consensus system.

Combines 7 independent analysis tools to produce a final verdict:

1. V9 Score      — 6-criteria scoring system (core)
2. Bollinger     — Squeeze/breakout detection
3. Stochastic    — StochRSI crossover signals
4. OBV           — On-Balance Volume trend
5. ADX Strength  — Trend strength confirmation
6. Backtest      — Historical win rate for this symbol
7. Monte Carlo   — Simulated probability of profit

Verdict scale:
  7/7 or 6/7 → STRONG BUY / STRONG SELL
  5/7        → BUY / SELL
  4/7        → WEAK (no action)
  < 4        → NEUTRAL
"""

import logging
from datetime import datetime
from typing import Dict, Optional

import numpy as np
import pandas as pd

from app.egx.indicators import compute_indicators
from app.egx.scoring import EgxStrategyConfig, score_signal, classify_signal
from app.egx.backtest import run_backtest, analyze_performance

logger = logging.getLogger("egx")


def run_consensus(
    df: pd.DataFrame,
    symbol: str,
    cfg: EgxStrategyConfig = None,
) -> Dict:
    """Run full 7-tool consensus analysis on an EGX stock.

    Args:
        df: OHLCV DataFrame (minimum 50 rows)
        symbol: Stock symbol
        cfg: Strategy config (uses defaults if None)

    Returns:
        Dict with verdict, confidence, per-tool votes, and trade levels.
    """
    if cfg is None:
        cfg = EgxStrategyConfig()

    if df is None or len(df) < 5:
        return {"error": f"Insufficient data for {symbol} ({len(df) if df is not None else 0} rows)"}

    # Use last 300 rows max for indicator computation (speed)
    if len(df) > 300:
        df = df.tail(300).copy().reset_index(drop=True)

    # Compute all indicators
    df_ind = compute_indicators(df, cfg)
    if len(df_ind) < 5:
        return {"error": "Not enough data after indicator computation"}

    row = df_ind.iloc[-1]
    prev = df_ind.iloc[-2]

    # Current price and ATR for trade levels
    price = row["close"]
    atr_val = row["atr"]
    if pd.isna(atr_val) or atr_val <= 0:
        return {"error": f"Invalid ATR for {symbol}"}

    # === TOOL 1: V9 Score ===
    v9_scores = score_signal(row, prev, cfg)
    v9_long = v9_scores["long"]
    v9_short = v9_scores["short"]
    v9_direction = "LONG" if v9_long >= v9_short else "SHORT"
    v9_best = max(v9_long, v9_short)
    tool_1 = "BUY" if v9_long >= 4 else ("SELL" if v9_short >= 4 else "HOLD")

    # === TOOL 2: Bollinger Band ===
    bb_pct = row.get("bb_pct", 0.5)
    bb_width = row.get("bb_width", 0)
    if pd.isna(bb_pct):
        bb_pct = 0.5
    squeeze = bb_width < 0.04 if not pd.isna(bb_width) else False

    if bb_pct < 0.2 and not squeeze:
        tool_2 = "BUY"  # Near lower band
    elif bb_pct > 0.8 and not squeeze:
        tool_2 = "SELL"  # Near upper band
    elif squeeze:
        tool_2 = "BUY" if row["close"] > row.get("bb_mid", price) else "SELL"
    else:
        tool_2 = "HOLD"

    # === TOOL 3: Stochastic RSI ===
    stoch_k = row.get("stoch_rsi_k", 0.5)
    stoch_d = row.get("stoch_rsi_d", 0.5)
    if pd.isna(stoch_k):
        stoch_k = 0.5
    if pd.isna(stoch_d):
        stoch_d = 0.5

    if stoch_k < 0.2 and stoch_k > stoch_d:
        tool_3 = "BUY"  # Oversold + crossing up
    elif stoch_k > 0.8 and stoch_k < stoch_d:
        tool_3 = "SELL"  # Overbought + crossing down
    else:
        tool_3 = "HOLD"

    # === TOOL 4: OBV Trend ===
    obv = row.get("obv", 0)
    obv_ema = row.get("obv_ema", 0)
    if not pd.isna(obv) and not pd.isna(obv_ema):
        if obv > obv_ema * 1.02:
            tool_4 = "BUY"  # Volume accumulation
        elif obv < obv_ema * 0.98:
            tool_4 = "SELL"  # Volume distribution
        else:
            tool_4 = "HOLD"
    else:
        tool_4 = "HOLD"

    # === TOOL 5: ADX Strength ===
    adx = row.get("adx", 0)
    plus_di = row.get("plus_di", 0)
    minus_di = row.get("minus_di", 0)
    if not pd.isna(adx) and adx > 20:
        if plus_di > minus_di:
            tool_5 = "BUY"  # Strong uptrend
        else:
            tool_5 = "SELL"  # Strong downtrend
    else:
        tool_5 = "HOLD"  # No trend

    # === TOOL 6: Backtest Win Rate (use last 200 rows for speed) ===
    try:
        bt_df = df.tail(200).copy() if len(df) > 200 else df.copy()
        trades = run_backtest(bt_df, symbol, cfg)
        stats = analyze_performance(trades)
        bt_wr = stats.get("win_rate", 0)
        bt_pf = stats.get("profit_factor", 0)

        if bt_wr >= 65 and bt_pf >= 1.5:
            tool_6 = "BUY"
        elif bt_wr < 40 or bt_pf < 0.8:
            tool_6 = "SELL"
        else:
            tool_6 = "HOLD"
    except Exception:
        tool_6 = "HOLD"
        stats = {}

    # === TOOL 7: Monte Carlo (use last 100 rows, 200 sims for speed) ===
    try:
        mc_df = df_ind.tail(100) if len(df_ind) > 100 else df_ind
        mc_result = _monte_carlo_sim(mc_df, n_sims=200, horizon=5)
        if mc_result["prob_profit"] >= 0.60:
            tool_7 = "BUY"
        elif mc_result["prob_profit"] <= 0.40:
            tool_7 = "SELL"
        else:
            tool_7 = "HOLD"
    except Exception:
        tool_7 = "HOLD"
        mc_result = {"prob_profit": 0.5, "median_return": 0}

    # === AGGREGATE VOTES ===
    tools = {
        "V9 Score": tool_1,
        "Bollinger": tool_2,
        "StochRSI": tool_3,
        "OBV": tool_4,
        "ADX Trend": tool_5,
        "Backtest": tool_6,
        "Monte Carlo": tool_7,
    }

    votes_buy = sum(1 for v in tools.values() if v == "BUY")
    votes_sell = sum(1 for v in tools.values() if v == "SELL")
    votes_hold = sum(1 for v in tools.values() if v == "HOLD")

    # Determine verdict
    if votes_buy >= 6:
        verdict = "STRONG BUY"
    elif votes_buy >= 5:
        verdict = "BUY"
    elif votes_sell >= 6:
        verdict = "STRONG SELL"
    elif votes_sell >= 5:
        verdict = "SELL"
    elif votes_buy >= 4:
        verdict = "WEAK BUY"
    elif votes_sell >= 4:
        verdict = "WEAK SELL"
    else:
        verdict = "NEUTRAL"

    # Confidence
    max_votes = max(votes_buy, votes_sell)
    confidence = (max_votes / 7) * 100

    # Trade levels (ATR-based)
    is_buy = votes_buy > votes_sell
    if is_buy:
        stop_loss = price - cfg.sl_atr_mult * atr_val
        tp1 = price + cfg.tp_atr_mult * atr_val
        tp2 = price + (cfg.tp_atr_mult + 1) * atr_val
        tp3 = price + (cfg.tp_atr_mult + 2) * atr_val
    else:
        stop_loss = price + cfg.sl_atr_mult * atr_val
        tp1 = price - cfg.tp_atr_mult * atr_val
        tp2 = price - (cfg.tp_atr_mult + 1) * atr_val
        tp3 = price - (cfg.tp_atr_mult + 2) * atr_val

    risk = abs(price - stop_loss)
    reward = abs(tp1 - price)
    risk_reward = round(reward / risk, 1) if risk > 0 else 0

    return {
        "symbol": symbol,
        "verdict": verdict,
        "confidence": round(confidence, 1),
        "votes_buy": votes_buy,
        "votes_sell": votes_sell,
        "votes_hold": votes_hold,
        "tools": tools,
        "v9_score": v9_best,
        "v9_tools": v9_scores.get("long_tools" if is_buy else "short_tools", {}),
        "price": round(price, 2),
        "stop_loss": round(stop_loss, 2),
        "tp1": round(tp1, 2),
        "tp2": round(tp2, 2),
        "tp3": round(tp3, 2),
        "risk_reward": risk_reward,
        "atr": round(atr_val, 2),
        "rsi": round(row["rsi"], 1) if not pd.isna(row["rsi"]) else None,
        "adx": round(adx, 1) if not pd.isna(adx) else None,
        "backtest_stats": stats if stats else None,
        "monte_carlo": mc_result,
        "timestamp": datetime.utcnow().isoformat(),
    }


def _monte_carlo_sim(
    df: pd.DataFrame,
    n_sims: int = 500,
    horizon: int = 5,
) -> Dict:
    """Run Monte Carlo simulation using GBM (Geometric Brownian Motion).

    Simulates future price paths based on historical returns distribution.
    """
    closes = df["close"].dropna().values
    if len(closes) < 30:
        return {"prob_profit": 0.5, "median_return": 0}

    # Daily log returns
    log_returns = np.diff(np.log(closes))
    mu = np.mean(log_returns)
    sigma = np.std(log_returns)

    if sigma == 0:
        return {"prob_profit": 0.5, "median_return": 0}

    last_price = closes[-1]
    final_prices = []

    np.random.seed(42)
    for _ in range(n_sims):
        daily_returns = np.random.normal(mu, sigma, horizon)
        price_path = last_price * np.exp(np.cumsum(daily_returns))
        final_prices.append(price_path[-1])

    final_prices = np.array(final_prices)
    returns = (final_prices - last_price) / last_price

    return {
        "prob_profit": round(float(np.mean(returns > 0)), 3),
        "median_return": round(float(np.median(returns) * 100), 2),
        "mean_return": round(float(np.mean(returns) * 100), 2),
        "p10": round(float(np.percentile(returns, 10) * 100), 2),
        "p90": round(float(np.percentile(returns, 90) * 100), 2),
        "simulations": n_sims,
        "horizon_days": horizon,
    }
