"""Lightweight strategy simulation sandbox.

Runs a strategy on historical OHLCV data with the same sizing cascade
as the live trading engine — no broker calls, no DB writes, pure what-if.

Use cases
---------
- "What if I turned on GARCH sizing for AAPL in Q1?"
- "Compare Kelly 0.3 vs Kelly 0.5 on TSLA last 6 months"
- "Show me every sizing layer's contribution to P&L"

Reference
---------
- Jansen (2020), Ch.8 — Backtesting simulation workflow
- Velu/Hardy/Nehren (2021), Ch.12 — Simulation technology stack
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class SimulatedTrade:
    entry_date: str
    entry_price: float
    exit_date: str
    exit_price: float
    qty: int
    side: str = "buy"
    stop_loss: float = 0.0
    take_profit: float = 0.0
    pnl: float = 0.0
    pnl_pct: float = 0.0
    holding_days: int = 0
    sizing_layers: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Signal generation (simple technical rules — pluggable)
# ---------------------------------------------------------------------------


def _generate_signals(df: pd.DataFrame, strategy_id: str = "A") -> pd.Series:
    """Return a boolean Series of entry signals (True = enter).

    Strategy A (HANA — trend-following):  MACD bullish crossover
    Strategy B (marem — mean-reversion):  RSI < 30 (oversold bounce)
    Strategy C (mazem — ML-driven):       RSI 30-70 + MACD bull (balanced)
    """
    close = df["Close"]
    if strategy_id == "B":
        # Mean-reversion: oversold bounce
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rs = gain / loss.replace(0, np.nan)
        rsi = 100.0 - (100.0 / (1.0 + rs))
        return rsi < 30
    elif strategy_id == "C":
        # Balanced: RSI 30-70 + MACD bullish
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd = ema12 - ema26
        signal_line = macd.ewm(span=9, adjust=False).mean()
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rs = gain / loss.replace(0, np.nan)
        rsi = 100.0 - (100.0 / (1.0 + rs))
        return (rsi > 30) & (rsi < 70) & (macd > signal_line)
    else:
        # Strategy A: trend-following MACD crossover
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd = ema12 - ema26
        signal_line = macd.ewm(span=9, adjust=False).mean()
        return macd > signal_line


# ---------------------------------------------------------------------------
# Sizing cascade (mirrors trading_engine.execute_buy sizing layers)
# ---------------------------------------------------------------------------


def _apply_sizing_layers(
    base_qty: int, symbol: str, df: pd.DataFrame, layers: dict | None = None,
) -> tuple[int, dict]:
    """Apply the same sizing modifiers as execute_buy().

    Returns (final_qty, layer_breakdown).
    All layers degrade silently to 1.0x on failure.
    """
    if layers is None:
        layers = {}
    breakdown: dict = {}
    qty = base_qty

    # GARCH vol regime
    if layers.get("garch"):
        try:
            from app.services.garch_volatility import garch_vol_multiplier
            m = garch_vol_multiplier(symbol)
            breakdown["garch_mult"] = round(m, 4)
            qty = max(1, int(qty * m))
        except Exception:
            breakdown["garch_mult"] = 1.0

    # Portfolio covariance
    if layers.get("covariance"):
        try:
            from app.services.portfolio_optimizer import get_strategy_multiplier
            m = get_strategy_multiplier(layers.get("strategy_id"))
            breakdown["cov_mult"] = round(m, 4)
            qty = max(1, int(qty * m))
        except Exception:
            breakdown["cov_mult"] = 1.0

    # Mean-reversion quality
    if layers.get("mr_quality"):
        try:
            from app.services.mean_reversion_util import get_mr_multiplier
            m = get_mr_multiplier(symbol)
            breakdown["mr_mult"] = round(m, 4)
            qty = max(1, int(qty * m))
        except Exception:
            breakdown["mr_mult"] = 1.0

    # News sentiment
    if layers.get("sentiment"):
        try:
            from app.services.sentiment_engine import get_sentiment_multiplier
            m = get_sentiment_multiplier(symbol)
            breakdown["sent_mult"] = round(m, 4)
            qty = max(1, int(qty * m))
        except Exception:
            breakdown["sent_mult"] = 1.0

    return qty, breakdown


# ---------------------------------------------------------------------------
# Main simulator
# ---------------------------------------------------------------------------


def simulate_strategy(
    symbol: str,
    start_date: str = "2024-01-01",
    end_date: str | None = None,
    strategy_id: str = "A",
    capital: float = 10000.0,
    risk_pct: float = 0.02,
    stop_pct: float = 0.05,
    tp_pct: float = 0.10,
    sizing_layers: dict | None = None,
    max_positions: int = 3,
) -> dict:
    """Simulate a strategy on historical OHLCV data.

    Parameters
    ----------
    symbol : str
        Ticker to simulate (e.g. "AAPL").
    start_date : str
        ISO date to start simulation.
    end_date : str or None
        ISO date to end (default: today).
    strategy_id : str
        "A" (trend), "B" (mean-rev), or "C" (balanced).
    capital : float
        Starting portfolio value in USD.
    risk_pct : float
        Fraction of capital to risk per trade (e.g. 0.02 = 2 pct).
    stop_pct : float
        Stop-loss distance from entry (e.g. 0.05 = 5 pct).
    tp_pct : float
        Take-profit distance from entry (e.g. 0.10 = 10 pct).
    sizing_layers : dict or None
        e.g. {"garch": True, "covariance": True, "mr_quality": True,
              "sentiment": True, "strategy_id": "A"}
    max_positions : int
        Max concurrent open positions.

    Returns
    -------
    dict with keys: trades, metrics, sizing_summary, config
    """
    if end_date is None:
        end_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # 1. Fetch data
    ticker = yf.Ticker(symbol)
    df = ticker.history(start=start_date, end=end_date)
    if df.empty:
        return {"error": f"No data for {symbol} {start_date}->{end_date}", "trades": [], "metrics": {}}

    # Use "Open" for next-day fills; close for exit triggers
    df["Open"] = df["Open"].ffill()
    closes = df["Close"].values
    opens = df["Open"].values

    # 2. Generate signals
    signals = _generate_signals(df, strategy_id)
    signal_dates = df.index[signals].tolist()

    # 3. Simulate trades
    trades: list[SimulatedTrade] = []
    open_positions: list[SimulatedTrade] = []

    for entry_date in signal_dates:
        # Position limit
        if len(open_positions) >= max_positions:
            continue

        idx = df.index.get_loc(entry_date)
        if idx >= len(df) - 1:
            continue

        entry_price = float(opens[idx + 1])  # fill at next open
        if entry_price <= 0:
            continue

        stop_price = round(entry_price * (1.0 - stop_pct), 2)
        tp_price = round(entry_price * (1.0 + tp_pct), 2)

        # Position sizing
        risk_per_share = entry_price - stop_price
        if risk_per_share <= 0:
            continue
        base_qty = max(1, int((capital * risk_pct) / risk_per_share))
        qty, layer_info = _apply_sizing_layers(base_qty, symbol, df, sizing_layers)

        trade = SimulatedTrade(
            entry_date=entry_date.strftime("%Y-%m-%d"),
            entry_price=entry_price,
            exit_date="",
            exit_price=0.0,
            qty=qty,
            side="buy",
            stop_loss=stop_price,
            take_profit=tp_price,
            sizing_layers=layer_info,
        )
        open_positions.append(trade)

        # Walk forward to find exit
        for j in range(idx + 2, len(df)):
            high = float(df["High"].iloc[j])
            low = float(df["Low"].iloc[j])
            close = float(closes[j])
            exit_date_str = df.index[j].strftime("%Y-%m-%d")

            # Check stop-loss
            if low <= stop_price:
                trade.exit_price = stop_price
                trade.exit_date = exit_date_str
                trade.pnl = (trade.exit_price - trade.entry_price) * trade.qty
                trade.pnl_pct = (trade.exit_price / trade.entry_price - 1.0) * 100.0
                trade.holding_days = (df.index[j] - entry_date).days
                trades.append(trade)
                open_positions.remove(trade)
                break

            # Check take-profit
            if high >= tp_price:
                trade.exit_price = tp_price
                trade.exit_date = exit_date_str
                trade.pnl = (trade.exit_price - trade.entry_price) * trade.qty
                trade.pnl_pct = (trade.exit_price / trade.entry_price - 1.0) * 100.0
                trade.holding_days = (df.index[j] - entry_date).days
                trades.append(trade)
                open_positions.remove(trade)
                break

            # End of simulation — close at market
            if j == len(df) - 1:
                trade.exit_price = close
                trade.exit_date = exit_date_str
                trade.pnl = (trade.exit_price - trade.entry_price) * trade.qty
                trade.pnl_pct = (trade.exit_price / trade.entry_price - 1.0) * 100.0
                trade.holding_days = (df.index[j] - entry_date).days
                trades.append(trade)
                open_positions.remove(trade)
                break

    # 4. Compute metrics
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    total_pnl = sum(t.pnl for t in trades)
    returns = [t.pnl_pct for t in trades]

    metrics = {
        "total_trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(trades) * 100, 1) if trades else 0,
        "total_pnl": round(total_pnl, 2),
        "avg_win": round(np.mean([t.pnl for t in wins]), 2) if wins else 0,
        "avg_loss": round(np.mean([t.pnl for t in losses]), 2) if losses else 0,
        "profit_factor": (
            round(abs(sum(t.pnl for t in wins) / sum(t.pnl for t in losses)), 2)
            if losses and sum(t.pnl for t in losses) != 0
            else (float("inf") if wins else 0)
        ),
        "avg_return_pct": round(np.mean(returns), 2) if returns else 0,
        "sharpe_approx": (
            round(np.mean(returns) / np.std(returns, ddof=1) * np.sqrt(252), 2)
            if len(returns) > 1 and np.std(returns, ddof=1) > 0
            else 0
        ),
        "avg_holding_days": round(np.mean([t.holding_days for t in trades]), 1) if trades else 0,
    }

    # Aggregate sizing layer info
    sizing_summary = {}
    if trades and trades[0].sizing_layers:
        for key in trades[0].sizing_layers:
            vals = [t.sizing_layers.get(key, 1.0) for t in trades]
            sizing_summary[key] = round(np.mean(vals), 4)

    return {
        "symbol": symbol,
        "strategy_id": strategy_id,
        "period": f"{start_date} -> {end_date}",
        "config": {
            "capital": capital,
            "risk_pct": risk_pct,
            "stop_pct": stop_pct,
            "tp_pct": tp_pct,
            "sizing_layers": sizing_layers,
        },
        "trades": [
            {
                "entry": t.entry_date,
                "exit": t.exit_date,
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "qty": t.qty,
                "pnl": round(t.pnl, 2),
                "pnl_pct": round(t.pnl_pct, 2),
                "holding_days": t.holding_days,
                "sizing": t.sizing_layers,
            }
            for t in trades
        ],
        "metrics": metrics,
        "sizing_summary": sizing_summary,
    }


def compare_scenarios(
    symbol: str,
    scenarios: list[dict],
    start_date: str = "2024-01-01",
    end_date: str | None = None,
) -> dict:
    """Run multiple scenarios and compare results side-by-side.

    Parameters
    ----------
    scenarios : list[dict]
        Each dict is kwargs for simulate_strategy() with an extra "label" key.
        e.g. [{"label": "Baseline", "strategy_id": "A", "risk_pct": 0.02},
              {"label": "Aggressive", "strategy_id": "A", "risk_pct": 0.04}]
    """
    results = []
    for sc in scenarios:
        label = sc.pop("label", f"Scenario {len(results)+1}")
        sim = simulate_strategy(symbol, start_date=start_date, end_date=end_date, **sc)
        sim["label"] = label
        results.append(sim)
        sc["label"] = label  # restore for caller

    return {
        "symbol": symbol,
        "period": f"{start_date} -> {end_date or 'today'}",
        "scenarios": results,
    }
