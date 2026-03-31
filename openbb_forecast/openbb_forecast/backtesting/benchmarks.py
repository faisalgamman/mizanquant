"""Benchmark strategies — the original repo never compared against any baseline.

Without a benchmark, you can't tell if a model's returns come from skill or
from the underlying market trend. Buy-and-hold is the minimum benchmark:
if your model can't beat simply holding the stock, it has no value.
"""

from __future__ import annotations

import numpy as np


class BuyAndHoldBenchmark:
    """Invest all capital at the start, hold until the end.

    This is the simplest and most important benchmark. Any trading strategy
    that cannot beat buy-and-hold (after costs) is destroying value.
    """

    def __init__(self, initial_capital: float = 10_000.0):
        self.initial_capital = initial_capital

    def evaluate(self, prices: np.ndarray) -> np.ndarray:
        """Generate equity curve for buy-and-hold.

        Args:
            prices: Array of daily close prices.

        Returns:
            Array of daily portfolio values, same length as prices.
        """
        prices = np.asarray(prices, dtype=np.float64)
        if len(prices) == 0:
            return np.array([self.initial_capital])
        shares = self.initial_capital / prices[0]
        return shares * prices


class RandomBenchmark:
    """Random buy/sell/hold decisions — sanity check baseline.

    Any real strategy should comfortably beat random trading.
    If it doesn't, the model is likely overfitting to noise.
    """

    def __init__(
        self,
        initial_capital: float = 10_000.0,
        seed: int = 42,
        buy_prob: float = 0.33,
        sell_prob: float = 0.33,
    ):
        self.initial_capital = initial_capital
        self.seed = seed
        self.buy_prob = buy_prob
        self.sell_prob = sell_prob

    def evaluate(
        self,
        prices: np.ndarray,
        cost_per_trade_bps: float = 20.0,
        max_position: int = 5,
    ) -> np.ndarray:
        """Generate equity curve for random trading.

        Args:
            prices: Daily close prices.
            cost_per_trade_bps: Total transaction cost in basis points.
            max_position: Maximum position size.

        Returns:
            Array of daily portfolio values.
        """
        prices = np.asarray(prices, dtype=np.float64)
        rng = np.random.default_rng(self.seed)
        n = len(prices)

        cash = self.initial_capital
        position = 0
        equity = np.zeros(n)
        cost_frac = cost_per_trade_bps / 10_000

        for t in range(n):
            r = rng.random()
            if r < self.buy_prob and position < max_position and cash >= prices[t] * (1 + cost_frac):
                cash -= prices[t] * (1 + cost_frac)
                position += 1
            elif r < self.buy_prob + self.sell_prob and position > 0:
                cash += prices[t] * (1 - cost_frac)
                position -= 1

            equity[t] = cash + position * prices[t]

        return equity
