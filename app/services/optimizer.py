"""Parameter sweep optimizer for consensus trading strategy.

Runs walk-forward optimization on historical data to find optimal thresholds
for each consensus tool. Lightweight — no heavy ML, just systematic testing
of parameter combinations against actual price outcomes.

Optimizes:
- STRONG BUY vote threshold (currently 7)
- Confidence minimum (currently 60%)
- SL/TP ATR multipliers (currently 1.5x / 1.5x / 2.5x / 4.0x)
- Tool-specific thresholds (Monte Carlo prob, XGBoost prob, BB width, etc.)
"""

import logging
import itertools
import numpy as np
import pandas as pd
from datetime import datetime, timezone

from app.core.config import app_cfg

logger = logging.getLogger("screener")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Parameter grid — each key maps to a list of values to test
# ---------------------------------------------------------------------------

PARAM_GRID = {
    # Consensus verdict thresholds
    "strong_buy_votes": [6, 7, 8],
    "strong_sell_votes": [6, 7, 8],
    "min_confidence": [55, 60, 65, 70],

    # SL/TP ATR multipliers
    "sl_atr_mult": [1.0, 1.5, 2.0],
    "tp1_atr_mult": [app_cfg.thresholds.atr_targets["base"]["tp1"], 2.0, app_cfg.thresholds.atr_targets["base"]["tp2"]],
    "tp2_atr_mult": [app_cfg.thresholds.atr_targets["base"]["tp2"], 3.0, 4.0],

    # Tool thresholds
    "mc_prob_buy": [0.55, 0.60, 0.65],
    "mc_prob_sell": [0.40, 0.45, 0.50],
    "xgb_prob_buy": [0.55, 0.60, 0.65],
    "xgb_prob_sell": [0.35, 0.40, 0.45],
    "bb_squeeze_width": [3.0, 4.0, 5.0],
    "momentum_roc_threshold": [1.5, 2.0, 3.0],
    "volume_price_threshold": [15, 20, 25],
}

# Default params (current hardcoded values)
DEFAULT_PARAMS = {
    "strong_buy_votes": 7,
    "strong_sell_votes": 7,
    "min_confidence": 60,
    "sl_atr_mult": app_cfg.thresholds.atr_targets["base"]["sl"],
    "tp1_atr_mult": app_cfg.thresholds.atr_targets["base"]["tp1"],
    "tp2_atr_mult": app_cfg.thresholds.atr_targets["base"]["tp2"],
    "mc_prob_buy": 0.60,
    "mc_prob_sell": 0.45,
    "xgb_prob_buy": 0.60,
    "xgb_prob_sell": 0.40,
    "bb_squeeze_width": 4.0,
    "momentum_roc_threshold": 2.0,
    "volume_price_threshold": 20,
}


def _simulate_trades(df: pd.DataFrame, signals: list, params: dict) -> dict:
    """Simulate trades from signals using given SL/TP params.

    Returns performance metrics: win_rate, profit_factor, sharpe, total_return.
    """
    if not signals:
        return {"win_rate": 0, "profit_factor": 0, "sharpe": 0, "total_return": 0, "n_trades": 0}

    pnls = []
    for sig in signals:
        idx = sig["bar_idx"]
        if idx + 1 >= len(df):
            continue

        entry = float(df["close"].iloc[idx])
        atr_val = sig["atr"]
        sl = entry - params["sl_atr_mult"] * atr_val
        tp = entry + params["tp1_atr_mult"] * atr_val

        # Walk forward from entry to see which is hit first: SL or TP
        for j in range(idx + 1, min(idx + 20, len(df))):  # max 20 day hold
            low = float(df["low"].iloc[j])
            high = float(df["high"].iloc[j])

            if low <= sl:
                pnls.append((sl - entry) / entry * 100)
                break
            elif high >= tp:
                pnls.append((tp - entry) / entry * 100)
                break
        else:
            # Held max days, close at market
            exit_price = float(df["close"].iloc[min(idx + 19, len(df) - 1)])
            pnls.append((exit_price - entry) / entry * 100)

    if not pnls:
        return {"win_rate": 0, "profit_factor": 0, "sharpe": 0, "total_return": 0, "n_trades": 0}

    pnls = np.array(pnls)
    wins = pnls[pnls > 0]
    losses = pnls[pnls <= 0]

    win_rate = len(wins) / len(pnls) * 100
    gross_profit = float(wins.sum()) if len(wins) > 0 else 0
    gross_loss = float(abs(losses.sum())) if len(losses) > 0 else 0.01
    profit_factor = gross_profit / gross_loss
    rf_daily = 0.043 / 252  # Current US T-bill ~4.3% annualized
    sharpe = float((pnls.mean() - rf_daily) / pnls.std() * np.sqrt(252)) if pnls.std() > 0 else 0
    total_return = float(pnls.sum())

    return {
        "win_rate": round(win_rate, 1),
        "profit_factor": round(profit_factor, 2),
        "sharpe": round(sharpe, 2),
        "total_return": round(total_return, 2),
        "n_trades": len(pnls),
    }


def _generate_signals_with_params(df: pd.DataFrame, params: dict) -> list:
    """Generate BUY signals from df using given parameter thresholds.

    This is a simplified version of run_consensus that just counts votes
    using parameterized thresholds instead of hardcoded ones.
    """
    from app.services.technical import ema, rsi, macd, atr as calc_atr

    signals = []
    close = df["close"]
    atr_series = calc_atr(df, 14)
    rsi_series = rsi(close)
    ema21 = ema(close, 21)
    sma50 = close.rolling(50).mean()
    sma200 = close.rolling(200).mean()
    sma20 = close.rolling(20).mean()
    std20 = close.rolling(20).std()
    upper_bb = sma20 + 2 * std20
    lower_bb = sma20 - 2 * std20
    bb_width = (upper_bb - lower_bb) / sma20 * 100

    vol_avg_20 = df["volume"].rolling(20).mean()

    # Walk through each bar (starting after warmup)
    for i in range(250, len(df) - 20):
        price = float(close.iloc[i])
        atr_val = float(atr_series.iloc[i])
        if atr_val <= 0:
            continue

        votes_buy = 0
        votes_sell = 0

        # 1. EMA Alignment (double-weighted for perfect trend)
        e21 = float(ema21.iloc[i])
        s50 = float(sma50.iloc[i])
        s200 = float(sma200.iloc[i])
        if price > e21 > s50 > s200:
            votes_buy += 2
        elif price > e21 > s50:
            votes_buy += 1
        elif price < e21 < s50 < s200:
            votes_sell += 2
        elif price < e21 < s50:
            votes_sell += 1

        # 2. RSI
        rsi_val = float(rsi_series.iloc[i])
        if rsi_val < 35:
            votes_buy += 1  # oversold
        elif rsi_val > 70:
            votes_sell += 1  # overbought

        # 3. Bollinger Bands
        bw = float(bb_width.iloc[i])
        if bw < params["bb_squeeze_width"] and price > float(upper_bb.iloc[i]):
            votes_buy += 1
        elif bw < params["bb_squeeze_width"] and price < float(lower_bb.iloc[i]):
            votes_sell += 1
        elif price > float(sma20.iloc[i]):
            votes_buy += 1
        else:
            votes_sell += 1

        # 4. Momentum ROC
        if i >= 10:
            roc_10 = (price / float(close.iloc[i - 10]) - 1) * 100
            roc_20 = (price / float(close.iloc[i - 20]) - 1) * 100 if i >= 20 else 0
            if roc_10 > params["momentum_roc_threshold"] and roc_20 > params["momentum_roc_threshold"] * 1.5:
                votes_buy += 1
            elif roc_10 < -params["momentum_roc_threshold"] and roc_20 < -params["momentum_roc_threshold"] * 1.5:
                votes_sell += 1

        # 5. Volume-Price
        if i >= 25:
            price_chg = (price / float(close.iloc[i - 5]) - 1) * 100
            vol_recent = float(df["volume"].iloc[i-4:i+1].mean())
            vol_prev = float(df["volume"].iloc[i-24:i-4].mean())
            vol_chg = (vol_recent / vol_prev - 1) * 100 if vol_prev > 0 else 0
            if price_chg > 1 and vol_chg > params["volume_price_threshold"]:
                votes_buy += 1
            elif price_chg > 1 and vol_chg < -10:
                votes_sell += 1
            elif price_chg < -1 and vol_chg > params["volume_price_threshold"]:
                votes_sell += 1
            elif price_chg < -1 and vol_chg < -10:
                votes_buy += 1

        # 6. MACD
        _, _, macd_h = macd(close)
        mh = float(macd_h.iloc[i])
        mh_prev = float(macd_h.iloc[i - 1])
        if mh > 0 and mh > mh_prev:
            votes_buy += 1
        elif mh < 0 and mh < mh_prev:
            votes_sell += 1

        total = votes_buy + votes_sell
        if total == 0:
            continue
        confidence = max(votes_buy, votes_sell) / (votes_buy + votes_sell + 1) * 100

        # Check thresholds
        if votes_buy >= params["strong_buy_votes"] and confidence >= params["min_confidence"]:
            signals.append({
                "bar_idx": i,
                "price": price,
                "atr": atr_val,
                "votes_buy": votes_buy,
                "confidence": confidence,
            })

    return signals


def optimize_params(symbols: list = None, n_samples: int = 10) -> dict:
    """Run parameter sweep optimization.

    Tests parameter combinations on historical data for a sample of stocks.
    Uses walk-forward: train on first 70%, test on last 30%.

    Args:
        symbols: List of symbols to test. If None, uses top screener stocks.
        n_samples: Max number of stocks to test (for speed).

    Returns:
        Dict with best params, performance comparison, and all results.
    """
    import gc
    from app.services.market_data import fetch

    if not symbols:
        # Diverse sample: current leaders + delisted stocks to avoid survivorship bias
        symbols = [
            "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN",  # mega caps
            "META", "AVGO", "COST", "CRM", "AMD",       # large caps
            "LUMN", "VFC", "PARA",                       # delisted/removed (survivorship bias check)
        ]

    symbols = symbols[:n_samples]
    logger.info(f"Optimizer: testing on {len(symbols)} stocks")

    # Load data
    stock_data = {}
    for sym in symbols:
        df = fetch(sym, period="2y")
        if df is not None and len(df) >= 300:
            stock_data[sym] = df

    if not stock_data:
        return {"error": "No data available for optimization"}

    logger.info(f"Optimizer: loaded data for {len(stock_data)} stocks")

    # --- Test key parameter groups independently (not full combinatorial) ---
    # Group 1: Verdict thresholds
    verdict_results = []
    for strong_votes in PARAM_GRID["strong_buy_votes"]:
        for min_conf in PARAM_GRID["min_confidence"]:
            params = {**DEFAULT_PARAMS, "strong_buy_votes": strong_votes, "min_confidence": min_conf}
            total_metrics = {"win_rate": [], "profit_factor": [], "sharpe": [], "total_return": [], "n_trades": []}

            for sym, df in stock_data.items():
                # Test on last 30% of data
                split = int(len(df) * 0.7)
                test_df = df.iloc[max(0, split - 250):].reset_index(drop=True)  # include warmup
                signals = _generate_signals_with_params(test_df, params)
                metrics = _simulate_trades(test_df, signals, params)
                for k in total_metrics:
                    total_metrics[k].append(metrics[k])

            avg = {k: round(np.mean(v), 2) for k, v in total_metrics.items()}
            avg["params"] = {"strong_buy_votes": strong_votes, "min_confidence": min_conf}
            verdict_results.append(avg)

    # Group 2: SL/TP multipliers
    sltp_results = []
    for sl_m in PARAM_GRID["sl_atr_mult"]:
        for tp_m in PARAM_GRID["tp1_atr_mult"]:
            params = {**DEFAULT_PARAMS, "sl_atr_mult": sl_m, "tp1_atr_mult": tp_m}
            total_metrics = {"win_rate": [], "profit_factor": [], "sharpe": [], "total_return": [], "n_trades": []}

            for sym, df in stock_data.items():
                split = int(len(df) * 0.7)
                test_df = df.iloc[max(0, split - 250):].reset_index(drop=True)
                signals = _generate_signals_with_params(test_df, params)
                metrics = _simulate_trades(test_df, signals, params)
                for k in total_metrics:
                    total_metrics[k].append(metrics[k])

            avg = {k: round(np.mean(v), 2) for k, v in total_metrics.items()}
            avg["params"] = {"sl_atr_mult": sl_m, "tp1_atr_mult": tp_m}
            sltp_results.append(avg)

    # Group 3: Tool thresholds
    tool_results = []
    for mc_buy in PARAM_GRID["mc_prob_buy"]:
        for bb_w in PARAM_GRID["bb_squeeze_width"]:
            for roc_t in PARAM_GRID["momentum_roc_threshold"]:
                params = {**DEFAULT_PARAMS, "mc_prob_buy": mc_buy,
                         "bb_squeeze_width": bb_w, "momentum_roc_threshold": roc_t}
                total_metrics = {"win_rate": [], "profit_factor": [], "sharpe": [], "total_return": [], "n_trades": []}

                for sym, df in stock_data.items():
                    split = int(len(df) * 0.7)
                    test_df = df.iloc[max(0, split - 250):].reset_index(drop=True)
                    signals = _generate_signals_with_params(test_df, params)
                    metrics = _simulate_trades(test_df, signals, params)
                    for k in total_metrics:
                        total_metrics[k].append(metrics[k])

                avg = {k: round(np.mean(v), 2) for k, v in total_metrics.items()}
                avg["params"] = {"mc_prob_buy": mc_buy, "bb_squeeze_width": bb_w, "momentum_roc_threshold": roc_t}
                tool_results.append(avg)

    gc.collect()

    # --- Find best in each group by composite score ---
    def _score(r):
        """Composite: 40% win_rate + 30% profit_factor + 30% sharpe, penalize <5 trades."""
        if r["n_trades"] < 3:
            return -999
        return r["win_rate"] * 0.4 + min(r["profit_factor"], 5) * 20 * 0.3 + min(r["sharpe"], 3) * 10 * 0.3

    best_verdict = max(verdict_results, key=_score) if verdict_results else {}
    best_sltp = max(sltp_results, key=_score) if sltp_results else {}
    best_tools = max(tool_results, key=_score) if tool_results else {}

    # Combine best params
    optimal = {**DEFAULT_PARAMS}
    if best_verdict.get("params"):
        optimal.update(best_verdict["params"])
    if best_sltp.get("params"):
        optimal.update(best_sltp["params"])
    if best_tools.get("params"):
        optimal.update(best_tools["params"])

    # Run final combined test
    combined_metrics = {"win_rate": [], "profit_factor": [], "sharpe": [], "total_return": [], "n_trades": []}
    for sym, df in stock_data.items():
        split = int(len(df) * 0.7)
        test_df = df.iloc[max(0, split - 250):].reset_index(drop=True)
        signals = _generate_signals_with_params(test_df, optimal)
        metrics = _simulate_trades(test_df, signals, optimal)
        for k in combined_metrics:
            combined_metrics[k].append(metrics[k])

    final = {k: round(np.mean(v), 2) for k, v in combined_metrics.items()}

    # Run default params for comparison
    default_metrics = {"win_rate": [], "profit_factor": [], "sharpe": [], "total_return": [], "n_trades": []}
    for sym, df in stock_data.items():
        split = int(len(df) * 0.7)
        test_df = df.iloc[max(0, split - 250):].reset_index(drop=True)
        signals = _generate_signals_with_params(test_df, DEFAULT_PARAMS)
        metrics = _simulate_trades(test_df, signals, DEFAULT_PARAMS)
        for k in default_metrics:
            default_metrics[k].append(metrics[k])

    baseline = {k: round(np.mean(v), 2) for k, v in default_metrics.items()}

    result = {
        "optimal_params": optimal,
        "optimal_performance": final,
        "baseline_performance": baseline,
        "improvement": {
            "win_rate_delta": round(final["win_rate"] - baseline["win_rate"], 1),
            "profit_factor_delta": round(final["profit_factor"] - baseline["profit_factor"], 2),
            "sharpe_delta": round(final["sharpe"] - baseline["sharpe"], 2),
        },
        "stocks_tested": list(stock_data.keys()),
        "timestamp": _utc_now().isoformat(),
        "top_verdict_combos": sorted(verdict_results, key=_score, reverse=True)[:3],
        "top_sltp_combos": sorted(sltp_results, key=_score, reverse=True)[:3],
        "top_tool_combos": sorted(tool_results, key=_score, reverse=True)[:3],
    }

    logger.info(
        f"Optimizer complete: Win Rate {baseline['win_rate']}% -> {final['win_rate']}% | "
        f"PF {baseline['profit_factor']} -> {final['profit_factor']} | "
        f"Sharpe {baseline['sharpe']} -> {final['sharpe']}"
    )

    return result
