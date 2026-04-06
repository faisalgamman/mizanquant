"""EGX backtesting engine — based on EGX Pro V9.

Features:
- ATR-based stop loss and take profit
- EOD (end-of-day) exits for day trading
- Position sizing by risk per trade
- Full P&L tracking with win rate, profit factor, expectancy
- Parameter optimization grid search
"""

from dataclasses import dataclass, asdict
from typing import List, Dict, Optional

import numpy as np
import pandas as pd

from app.egx.indicators import compute_indicators
from app.egx.scoring import EgxStrategyConfig, generate_signals


@dataclass
class EgxTrade:
    """Single EGX trade record."""
    symbol: str
    direction: int         # 1=long, -1=short
    entry_date: str
    entry_price: float
    stop_loss: float
    take_profit: float
    position_size: int
    position_value: float
    atr_at_entry: float
    score: int = 0
    exit_date: Optional[str] = None
    exit_price: Optional[float] = None
    exit_reason: str = ""
    pnl: float = 0.0
    pnl_pct: float = 0.0
    hold_days: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def run_backtest(df: pd.DataFrame, symbol: str, cfg: EgxStrategyConfig) -> List[EgxTrade]:
    """Run backtest with ATR-based SL/TP and EOD exits.

    Args:
        df: Raw OHLCV DataFrame
        symbol: Stock symbol
        cfg: Strategy configuration

    Returns:
        List of completed trades
    """
    df = compute_indicators(df, cfg)
    df = generate_signals(df, cfg)

    trades = []
    in_trade = False
    current_trade = None

    for i in range(len(df)):
        row = df.iloc[i]

        # Exit existing trade
        if in_trade and current_trade is not None:
            current_trade.hold_days += 1
            d = current_trade.direction
            ep = current_trade.entry_price
            sl = current_trade.stop_loss
            tp = current_trade.take_profit

            if d == 1:  # Long
                if row["low"] <= sl:
                    _close_trade(current_trade, row, sl, "SL", trades)
                    in_trade = False
                    continue
                if row["high"] >= tp:
                    _close_trade(current_trade, row, tp, "TP", trades)
                    in_trade = False
                    continue
            else:  # Short
                if row["high"] >= sl:
                    _close_trade(current_trade, row, sl, "SL", trades)
                    in_trade = False
                    continue
                if row["low"] <= tp:
                    _close_trade(current_trade, row, tp, "TP", trades)
                    in_trade = False
                    continue

            # EOD exit (day trading)
            if current_trade.hold_days >= 1:
                _close_trade(current_trade, row, row["close"], "EOD", trades)
                in_trade = False
                continue

        # New entry
        if not in_trade and row["signal"] != 0:
            atr_val = row["atr"]
            if pd.isna(atr_val) or atr_val <= 0:
                continue

            entry_price = row["close"]
            direction = row["signal"]

            # Position sizing: risk_per_trade * capital / sl_distance
            risk_amount = cfg.initial_capital * cfg.risk_per_trade
            sl_distance = cfg.sl_atr_mult * atr_val
            position_size = int(risk_amount / sl_distance)
            if position_size <= 0:
                continue

            if direction == 1:
                sl = entry_price - sl_distance
                tp = entry_price + cfg.tp_atr_mult * atr_val
            else:
                sl = entry_price + sl_distance
                tp = entry_price - cfg.tp_atr_mult * atr_val

            date_str = str(row["date"])
            current_trade = EgxTrade(
                symbol=symbol,
                direction=direction,
                entry_date=date_str,
                entry_price=entry_price,
                stop_loss=sl,
                take_profit=tp,
                position_size=position_size,
                position_value=position_size * entry_price,
                atr_at_entry=atr_val,
                score=int(row["score"]),
            )
            in_trade = True

    # Close open trade at end of data
    if in_trade and current_trade is not None:
        last = df.iloc[-1]
        _close_trade(current_trade, last, last["close"], "End", trades)

    return trades


def _close_trade(trade: EgxTrade, row, exit_price: float, reason: str, trades: list):
    """Close a trade and append to trades list."""
    trade.exit_date = str(row["date"])
    trade.exit_price = exit_price
    trade.exit_reason = reason
    ep = trade.entry_price
    d = trade.direction

    if d == 1:
        trade.pnl = (exit_price - ep) * trade.position_size
        trade.pnl_pct = (exit_price / ep - 1) * 100
    else:
        trade.pnl = (ep - exit_price) * trade.position_size
        trade.pnl_pct = (ep / exit_price - 1) * 100

    trades.append(trade)


def analyze_performance(trades: List[EgxTrade]) -> Dict:
    """Analyze trading performance metrics."""
    if not trades:
        return {"total_trades": 0, "win_rate": 0, "total_pnl": 0}

    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    total_pnl = sum(t.pnl for t in trades)
    win_rate = len(wins) / len(trades) * 100

    avg_win = np.mean([t.pnl for t in wins]) if wins else 0
    avg_loss = np.mean([t.pnl for t in losses]) if losses else 0

    gross_loss = sum(t.pnl for t in losses)
    gross_win = sum(t.pnl for t in wins)
    profit_factor = abs(gross_win / gross_loss) if gross_loss != 0 else float("inf")

    # Risk:Reward from average trade
    avg_win_pct = np.mean([t.pnl_pct for t in wins]) if wins else 0
    avg_loss_pct = np.mean([abs(t.pnl_pct) for t in losses]) if losses else 0
    risk_reward = avg_win_pct / avg_loss_pct if avg_loss_pct > 0 else 0

    # Max drawdown
    equity = []
    running = 0
    for t in trades:
        running += t.pnl
        equity.append(running)
    peak = 0
    max_dd = 0
    for e in equity:
        if e > peak:
            peak = e
        dd = peak - e
        if dd > max_dd:
            max_dd = dd

    return {
        "total_trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate, 1),
        "total_pnl": round(total_pnl, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "profit_factor": round(min(profit_factor, 99.9), 2),
        "risk_reward": round(risk_reward, 2),
        "expectancy": round(total_pnl / len(trades), 2),
        "avg_hold_days": round(np.mean([t.hold_days for t in trades]), 1),
        "max_drawdown": round(max_dd, 2),
        "best_trade": round(max(t.pnl for t in trades), 2),
        "worst_trade": round(min(t.pnl for t in trades), 2),
    }


def optimize_parameters(df: pd.DataFrame, symbol: str) -> Dict:
    """Grid search for best strategy parameters.

    Tests 72 combinations of SL multiplier, TP multiplier, min score, and volume surge.

    Returns:
        Dict with best_config and best_stats
    """
    best_cfg = EgxStrategyConfig()
    best_score = -999
    best_stats = {}

    # Pre-compute indicators once
    base_cfg = EgxStrategyConfig()
    df_ind = compute_indicators(df.copy(), base_cfg)

    # Reduce grid for small datasets to avoid timeout
    if len(df) < 100:
        sl_values = [1.5, 2.0]
        tp_values = [2.5, 3.5]
        min_sc_values = [4, 5]
        vol_values = [1.2, 1.5]
    else:
        sl_values = [1.5, 2.0, 2.5]
        tp_values = [2.5, 3.0, 3.5, 4.0]
        min_sc_values = [4, 5]
        vol_values = [1.2, 1.5, 2.0]

    combos = 0
    for sl_m in sl_values:
        for tp_m in tp_values:
            for min_sc in min_sc_values:
                for vol_s in vol_values:
                    combos += 1
                    cfg = EgxStrategyConfig(
                        sl_atr_mult=sl_m,
                        tp_atr_mult=tp_m,
                        min_score=min_sc,
                        vol_surge=vol_s,
                    )
                    df_sig = generate_signals(df_ind.copy(), cfg)

                    # Run backtest from signals
                    trades = run_backtest(df_sig, symbol, cfg)
                    if len(trades) < 2:
                        continue

                    stats = analyze_performance(trades)
                    wr = stats["win_rate"]
                    pf = min(stats["profit_factor"], 10)

                    # Composite score
                    composite = wr * 2 + pf * 10
                    if wr >= 70:
                        composite += 50
                    if stats["total_pnl"] > 0:
                        composite += 30
                    if wr >= 80:
                        composite += 50

                    if composite > best_score:
                        best_score = composite
                        best_cfg = cfg
                        best_stats = stats

    return {
        "best_config": {
            "sl_atr_mult": best_cfg.sl_atr_mult,
            "tp_atr_mult": best_cfg.tp_atr_mult,
            "min_score": best_cfg.min_score,
            "vol_surge": best_cfg.vol_surge,
        },
        "best_stats": best_stats,
        "combinations_tested": combos,
    }
