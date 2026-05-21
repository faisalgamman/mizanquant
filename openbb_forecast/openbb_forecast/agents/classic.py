"""Classic algorithmic trading agents — Turtle, Moving Average, Signal Rolling, ABCD.

Ported from Stock-Prediction-Models. Each agent follows RLAgentBase for
backtest compatibility but can also be used standalone.
"""

from __future__ import annotations

import numpy as np

from openbb_forecast.agents.base import BaseAgent
from openbb_forecast.agents.environment import TradingEnvironment


class RLAgentBase:
    """Simple backtesting base for classic agents (not RL-based)."""

    def __init__(self, initial_balance: float = 10_000.0):
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.position = 0
        self.trades: list[tuple[str, float, int]] = []
        self.portfolio_value: list[float] = []

    def reset(self) -> None:
        self.balance = self.initial_balance
        self.position = 0
        self.trades = []
        self.portfolio_value = [self.initial_balance]

    def _buy(self, price: float, step: int) -> None:
        if self.position == 0 and self.balance >= price:
            self.position = 1
            self.balance -= price
            self.trades.append(("BUY", price, step))

    def _sell(self, price: float, step: int) -> None:
        if self.position == 1:
            self.position = 0
            self.balance += price
            self.trades.append(("SELL", price, step))

    def _update_portfolio(self, price: float) -> None:
        self.portfolio_value.append(self.balance + price if self.position == 1 else self.balance)

    def backtest(self, prices: np.ndarray) -> dict:
        self.reset()
        for step, price in enumerate(prices):
            action = self.act(price, step, prices)
            if action == 1:
                self._buy(price, step)
            elif action == -1:
                self._sell(price, step)
            self._update_portfolio(price)

        # Force close any open position at last price so win_rate is meaningful
        if self.position == 1:
            self._sell(float(prices[-1]), len(prices) - 1)
            self._update_portfolio(float(prices[-1]))

        final_value = self.portfolio_value[-1]
        profit = final_value - self.initial_balance
        return {
            "final_value": final_value,
            "profit": profit,
            "profit_pct": (profit / self.initial_balance) * 100,
            "num_trades": len(self.trades),
            "portfolio_value": np.array(self.portfolio_value),
        }

    def act(self, price: float, step: int, prices: np.ndarray) -> int:
        raise NotImplementedError

    def walk_forward_evaluate(
        self,
        prices: np.ndarray,
        train_ratio: float = 0.7,
        initial_capital: float = 10_000.0,
        **kwargs,
    ) -> dict:
        """Walk-forward evaluation for classic (non-RL) agents.

        Splits prices into train/test, backtests on the test portion,
        and returns metrics matching the Leaderboard schema.
        """
        prices = np.asarray(prices, dtype=np.float64)
        self.initial_balance = initial_capital
        split_idx = int(len(prices) * train_ratio)

        # Classic agents need only enough data for their lookback window
        min_required = 50
        if len(prices) < min_required:
            raise ValueError(f"Not enough data: {len(prices)} < {min_required}")

        test_prices = prices[split_idx:]

        result = self.backtest(test_prices)
        pv = result["portfolio_value"]

        # Total return
        total_return = result["profit_pct"]

        # Max drawdown
        peak = np.maximum.accumulate(pv)
        dd = (pv - peak) / peak
        max_dd = float(np.min(dd)) * 100 if len(dd) > 0 else 0.0

        # Simple Sharpe (annualized, assuming daily data)
        returns = np.diff(pv) / pv[:-1]
        _EPS = 1e-6
        if len(returns) < 2:
            sharpe = 0.0
        else:
            mean_r = float(np.mean(returns))
            std_r = float(np.std(returns))
            sharp_raw = mean_r / (std_r + _EPS)
            # Only annualize if enough observations to be meaningful
            if len(returns) >= 20:
                sharp_raw *= np.sqrt(252)
            sharpe = float(min(sharp_raw, 3.0))

        # Win rate from trades
        buys = [t for t in self.trades if t[0] == "BUY"]
        sells = [t for t in self.trades if t[0] == "SELL"]
        wins = sum(1 for i in range(min(len(sells), len(buys))) if sells[i][1] > buys[i][1])
        win_rate = wins / len(sells) if sells else 0.0

        return {
            "backtest_summary": {
                "total_return_pct": round(total_return * 100, 2),
                "total_return": round(total_return, 4),
                "sharpe_ratio": round(sharpe, 4),
                "sharpe": round(sharpe, 4),
                "max_drawdown": round(max_dd, 4),
                "n_trades": result["num_trades"],
                "win_rate": round(win_rate, 4),
            },
            "risk_events": 0,
        }


class TurtleAgent(RLAgentBase):
    """Turtle Trading — buy on 20-day high breakout, sell on 20-day low stop."""

    def __init__(self, initial_balance: float = 10_000.0, lookback: int = 20):
        super().__init__(initial_balance)
        self.lookback = lookback

    def act(self, price: float, step: int, prices: np.ndarray) -> int:
        if step < self.lookback:
            return 0
        window = prices[max(0, step - self.lookback):step]
        if price >= np.max(window) and self.position == 0:
            return 1
        if price <= np.min(window) and self.position == 1:
            return -1
        return 0


class MovingAverageAgent(RLAgentBase):
    """MA crossover — buy when short MA crosses above long MA, sell on cross below."""

    def __init__(self, initial_balance: float = 10_000.0, short_window: int = 10, long_window: int = 30):
        super().__init__(initial_balance)
        self.short_window = short_window
        self.long_window = long_window

    def act(self, price: float, step: int, prices: np.ndarray) -> int:
        if step < self.long_window:
            return 0
        short_ma = np.mean(prices[max(0, step - self.short_window):step])
        long_ma = np.mean(prices[max(0, step - self.long_window):step])
        if self.position == 1 and short_ma < long_ma:
            return -1
        if self.position == 0 and short_ma > long_ma:
            return 1
        return 0


class SignalRollingAgent(RLAgentBase):
    """Signal Rolling — buy on momentum above threshold, sell on drop below."""

    def __init__(self, initial_balance: float = 10_000.0, window: int = 10, threshold: float = 0.02):
        super().__init__(initial_balance)
        self.window = window
        self.threshold = threshold

    def act(self, price: float, step: int, prices: np.ndarray) -> int:
        if step < self.window:
            return 0
        change = (price - prices[step - self.window]) / prices[step - self.window]
        if change > self.threshold and self.position == 0:
            return 1
        if change < -self.threshold and self.position == 1:
            return -1
        return 0


class ABCDStrategyAgent(RLAgentBase):
    """ABCD pattern — buy after consecutive up days, sell after consecutive down."""

    def act(self, price: float, step: int, prices: np.ndarray) -> int:
        if step < 20:
            return 0
        last_5 = prices[step - 4:step + 1]
        up_days = sum(1 for i in range(1, len(last_5)) if last_5[i] > last_5[i - 1])
        if up_days >= 4 and self.position == 0:
            return 1
        if up_days <= 1 and self.position == 1:
            return -1
        return 0


CLASSIC_AGENTS = {
    "turtle": TurtleAgent,
    "moving_average": MovingAverageAgent,
    "signal_rolling": SignalRollingAgent,
    "abcd_strategy": ABCDStrategyAgent,
}
