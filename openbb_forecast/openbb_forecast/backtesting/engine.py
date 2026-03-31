"""Walk-forward backtesting engine.

Orchestrates the full backtest pipeline:
  1. Walk-forward splits
  2. Model training per fold
  3. Signal generation
  4. Trade execution with costs
  5. Performance measurement vs benchmark
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from openbb_forecast.backtesting.benchmarks import BuyAndHoldBenchmark
from openbb_forecast.backtesting.transaction_costs import TransactionCostModel
from openbb_forecast.data.validation import WalkForwardSplit
from openbb_forecast.risk.metrics import compute_backtest_summary


class WalkForwardBacktester:
    """Backtesting engine for forecast-based trading strategies.

    Converts model predictions into trading signals and executes them
    with transaction costs, then computes performance metrics.

    Trading logic:
        - If predicted price > current price * (1 + threshold) -> BUY
        - If predicted price < current price * (1 - threshold) -> SELL
        - Otherwise -> HOLD

    The threshold accounts for transaction costs: no point trading
    if expected move is smaller than cost.

    Args:
        initial_capital: Starting cash.
        commission_bps: Commission in basis points.
        slippage_bps: Slippage in basis points.
        spread_bps: Spread in basis points.
        max_position: Maximum shares held.
        signal_threshold: Minimum predicted move to generate a signal (as fraction).
    """

    def __init__(
        self,
        initial_capital: float = 10_000.0,
        commission_bps: float = 10.0,
        slippage_bps: float = 5.0,
        spread_bps: float = 5.0,
        max_position: int = 5,
        signal_threshold: float | None = None,
    ):
        self.initial_capital = initial_capital
        self.cost_model = TransactionCostModel(
            commission_bps=commission_bps,
            slippage_bps=slippage_bps,
            spread_bps=spread_bps,
        )
        self.max_position = max_position
        # Default threshold = round-trip cost (no point trading for less)
        self.signal_threshold = signal_threshold or (self.cost_model.total_bps * 2 / 10_000)

    def run(
        self,
        prices: np.ndarray,
        predictions: np.ndarray,
        dates: list[str] | None = None,
    ) -> dict:
        """Execute backtest from predictions and prices.

        Args:
            prices: Actual close prices for the test period.
            predictions: Predicted prices (same length as prices).
            dates: Optional date strings for each time step.

        Returns:
            Dict with daily results, trade log, and summary metrics.
        """
        prices = np.asarray(prices, dtype=np.float64)
        predictions = np.asarray(predictions, dtype=np.float64)
        n = len(prices)

        if dates is None:
            dates = [str(i) for i in range(n)]

        cash = self.initial_capital
        position = 0
        inventory: list[float] = []  # Entry prices
        total_commissions = 0.0
        total_slippage = 0.0
        trade_returns: list[float] = []

        daily_results = []
        benchmark = BuyAndHoldBenchmark(self.initial_capital)
        benchmark_curve = benchmark.evaluate(prices)

        for t in range(n):
            current_price = prices[t]
            predicted_price = predictions[t]

            # Generate signal
            expected_move = (predicted_price - current_price) / current_price
            action = "hold"

            if expected_move > self.signal_threshold and position < self.max_position:
                # BUY signal
                eff_price, commission, slippage = self.cost_model.buy_cost(current_price)
                if cash >= eff_price:
                    cash -= eff_price
                    inventory.append(current_price)
                    position += 1
                    total_commissions += commission
                    total_slippage += slippage
                    action = "buy"

            elif expected_move < -self.signal_threshold and position > 0:
                # SELL signal
                entry_price = inventory.pop(0)
                eff_price, commission, slippage = self.cost_model.sell_cost(current_price)
                cash += eff_price
                position -= 1
                total_commissions += commission
                total_slippage += slippage
                trade_returns.append((eff_price - entry_price) / entry_price)
                action = "sell"

            portfolio_value = cash + position * current_price

            daily_results.append({
                "date": dates[t],
                "portfolio_value": portfolio_value,
                "benchmark_value": float(benchmark_curve[t]),
                "position": position,
                "action": action,
                "trade_price": current_price if action != "hold" else None,
                "commission": total_commissions if action != "hold" else None,
                "slippage": total_slippage if action != "hold" else None,
                "cumulative_return": (portfolio_value / self.initial_capital) - 1.0,
                "drawdown": 0.0,  # Filled below
            })

        # Compute drawdown series
        equity = np.array([r["portfolio_value"] for r in daily_results])
        running_max = np.maximum.accumulate(equity)
        drawdowns = (running_max - equity) / np.where(running_max > 0, running_max, 1.0)
        for i, r in enumerate(daily_results):
            r["drawdown"] = float(drawdowns[i])

        # Summary
        summary = compute_backtest_summary(
            equity_curve=equity,
            benchmark_curve=benchmark_curve,
            trade_returns=np.array(trade_returns) if trade_returns else np.array([]),
            total_commissions=total_commissions,
            total_slippage=total_slippage,
        )
        summary["test_start"] = dates[0]
        summary["test_end"] = dates[-1]

        return {
            "daily_results": daily_results,
            "trade_returns": trade_returns,
            "summary": summary,
        }
