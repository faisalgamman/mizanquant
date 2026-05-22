"""Financial performance metrics — replaces the original's meaningless NRMSE 'accuracy'.

The original repo reported accuracy as:
    (1 - sqrt(mean(((real - predict) / real)^2))) * 100
which measures how close price levels are, NOT trading viability.
A naive 'predict yesterday's price' model scores ~98% on this metric.

These metrics measure what actually matters for trading:
risk-adjusted returns, drawdowns, and trade quality.
"""

from __future__ import annotations

import numpy as np


TRADING_DAYS_PER_YEAR = 252


def sharpe_ratio(
    returns: np.ndarray,
    risk_free_rate: float = 0.0,
    annualize: bool = True,
) -> float:
    """Annualized Sharpe ratio.

    (mean(r) - rf_daily) / std(r) * sqrt(252)

    Returns 0.0 when sample is too small (< 20 observations) to be statistically
    meaningful. No upper cap is applied — the caller is responsible for interpreting
    high values (e.g. > 3.0) as a signal to investigate data quality.
    """
    returns = np.asarray(returns, dtype=np.float64)
    # Require at least 20 observations for a meaningful estimate
    if len(returns) < 20:
        return 0.0
    _EPS = 1e-8
    rf_daily = risk_free_rate / TRADING_DAYS_PER_YEAR
    excess = returns - rf_daily
    std = float(np.std(excess, ddof=1))
    if std < _EPS:
        # Near-zero variance: strategy is essentially flat, not a reliable signal
        return 0.0
    ratio = float(np.mean(excess) / std)
    if annualize:
        ratio *= np.sqrt(TRADING_DAYS_PER_YEAR)
    return float(ratio)


def sortino_ratio(
    returns: np.ndarray,
    risk_free_rate: float = 0.0,
    annualize: bool = True,
) -> float:
    """Annualized Sortino ratio — penalizes only downside volatility."""
    returns = np.asarray(returns, dtype=np.float64)
    rf_daily = risk_free_rate / TRADING_DAYS_PER_YEAR
    excess = returns - rf_daily
    downside = excess[excess < 0]
    if len(downside) < 2:
        return 0.0 if np.mean(excess) <= 0 else float("inf")
    downside_std = np.std(downside, ddof=1)
    if downside_std == 0:
        return 0.0
    ratio = np.mean(excess) / downside_std
    if annualize:
        ratio *= np.sqrt(TRADING_DAYS_PER_YEAR)
    return float(ratio)


def max_drawdown(equity_curve: np.ndarray) -> float:
    """Maximum peak-to-trough decline as a fraction (0.0 to 1.0).

    A drawdown of 0.15 means the portfolio lost 15% from its peak.
    """
    equity_curve = np.asarray(equity_curve, dtype=np.float64)
    if len(equity_curve) < 2:
        return 0.0
    running_max = np.maximum.accumulate(equity_curve)
    drawdowns = (running_max - equity_curve) / np.where(running_max > 0, running_max, 1.0)
    return float(np.max(drawdowns))


def drawdown_series(equity_curve: np.ndarray) -> np.ndarray:
    """Drawdown at each time step as a fraction."""
    equity_curve = np.asarray(equity_curve, dtype=np.float64)
    running_max = np.maximum.accumulate(equity_curve)
    return (running_max - equity_curve) / np.where(running_max > 0, running_max, 1.0)


def calmar_ratio(
    returns: np.ndarray,
    equity_curve: np.ndarray,
) -> float:
    """Calmar ratio: annualized return / max drawdown."""
    mdd = max_drawdown(equity_curve)
    if mdd == 0:
        return 0.0
    ann_return = annualized_return(returns)
    return float(ann_return / mdd)


def annualized_return(returns: np.ndarray) -> float:
    """Annualized return from daily returns (simple or log).

    Detects log returns by checking magnitude: if mean |return| < 0.1 and
    no return exceeds 0.5 in absolute value, assumes log returns and uses
    exp(sum) instead of prod(1+r).
    """
    returns = np.asarray(returns, dtype=np.float64)
    if len(returns) == 0:
        return 0.0
    n_years = len(returns) / TRADING_DAYS_PER_YEAR
    if n_years <= 0:
        return 0.0
    # Heuristic: log returns are typically small and can be negative beyond -1
    # Simple returns cannot be < -1 (can't lose more than 100%)
    if np.any(returns < -0.5) or (np.mean(np.abs(returns)) < 0.05 and np.max(np.abs(returns)) < 0.5):
        # Treat as log returns: total return = exp(sum(log_returns)) - 1
        total = np.exp(np.sum(returns)) - 1
    else:
        total = np.prod(1 + returns) - 1
    return float((1 + total) ** (1 / n_years) - 1)


_MIN_TRADES_FOR_WIN_RATE = 5


def win_rate(trade_returns: np.ndarray) -> float:
    """Fraction of trades with positive return.

    Returns 0.0 when fewer than _MIN_TRADES_FOR_WIN_RATE trades are available —
    a single winning trade must not appear as 100% win rate.
    """
    trade_returns = np.asarray(trade_returns, dtype=np.float64)
    if len(trade_returns) < _MIN_TRADES_FOR_WIN_RATE:
        return 0.0
    return float(np.sum(trade_returns > 0) / len(trade_returns))


def profit_factor(trade_returns: np.ndarray) -> float:
    """Sum of winning trades / abs(sum of losing trades).

    > 1.0 means gross profits exceed gross losses.
    """
    trade_returns = np.asarray(trade_returns, dtype=np.float64)
    wins = trade_returns[trade_returns > 0].sum()
    losses = np.abs(trade_returns[trade_returns < 0].sum())
    if losses == 0:
        return float("inf") if wins > 0 else 0.0
    return float(wins / losses)


def value_at_risk(returns: np.ndarray, confidence: float = 0.95) -> float:
    """Historical Value at Risk.

    Returns the maximum expected loss at the given confidence level.
    A VaR of 0.02 at 95% means there's a 5% chance of losing more than 2%.
    """
    returns = np.asarray(returns, dtype=np.float64)
    if len(returns) == 0:
        return 0.0
    return float(-np.percentile(returns, (1 - confidence) * 100))


def conditional_var(returns: np.ndarray, confidence: float = 0.95) -> float:
    """Conditional Value at Risk (Expected Shortfall).

    Mean loss in the worst (1-confidence)% of scenarios.
    """
    returns = np.asarray(returns, dtype=np.float64)
    if len(returns) == 0:
        return 0.0
    alpha = (1 - confidence) * 100  # e.g. 5 for 95% confidence
    threshold = float(np.percentile(returns, alpha))
    var = max(0.0, -threshold)
    tail = returns[returns <= threshold]
    if len(tail) == 0:
        return float(var)
    return float(-np.mean(tail))


def compute_backtest_summary(
    equity_curve: np.ndarray,
    benchmark_curve: np.ndarray,
    trade_returns: np.ndarray,
    total_commissions: float,
    total_slippage: float,
    risk_free_rate: float = 0.0,
) -> dict:
    """Compute all backtest metrics from equity curves and trade returns.

    Returns a dict matching BacktestSummary fields.
    """
    equity_curve = np.asarray(equity_curve, dtype=np.float64)
    benchmark_curve = np.asarray(benchmark_curve, dtype=np.float64)
    trade_returns = np.asarray(trade_returns, dtype=np.float64)

    # Daily returns from equity curve
    daily_returns = np.diff(equity_curve) / equity_curve[:-1]
    daily_returns = np.nan_to_num(daily_returns, nan=0.0)

    benchmark_daily = np.diff(benchmark_curve) / benchmark_curve[:-1]
    benchmark_daily = np.nan_to_num(benchmark_daily, nan=0.0)

    total_ret = (equity_curve[-1] / equity_curve[0]) - 1.0 if equity_curve[0] > 0 else 0.0
    bench_ret = (benchmark_curve[-1] / benchmark_curve[0]) - 1.0 if benchmark_curve[0] > 0 else 0.0

    return {
        "total_return": float(total_ret),
        "annualized_return": annualized_return(daily_returns),
        "sharpe_ratio": sharpe_ratio(daily_returns, risk_free_rate),
        "sortino_ratio": sortino_ratio(daily_returns, risk_free_rate),
        "max_drawdown": max_drawdown(equity_curve),
        "calmar_ratio": calmar_ratio(daily_returns, equity_curve),
        "win_rate": win_rate(trade_returns),
        "profit_factor": profit_factor(trade_returns),
        "total_trades": len(trade_returns),
        "avg_trade_return": float(np.mean(trade_returns)) if len(trade_returns) > 0 else 0.0,
        "benchmark_return": float(bench_ret),
        "alpha": float(total_ret - bench_ret),
        "total_commissions": total_commissions,
        "total_slippage": total_slippage,
    }
