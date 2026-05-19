"""Abstract base class for all trading agents with walk-forward evaluation.

The walk_forward_evaluate method enforces proper train/test separation:
  - Agent trains on the first portion of data
  - Agent is evaluated (greedy, no exploration) on the unseen test portion
  - Buy-and-hold benchmark is computed on the same test period
  - All metrics are computed on out-of-sample results only
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np

from openbb_forecast.agents.environment import TradingEnvironment
from openbb_forecast.backtesting.benchmarks import BuyAndHoldBenchmark
from openbb_forecast.backtesting.transaction_costs import TransactionCostModel
from openbb_forecast.risk.manager import RiskManager
from openbb_forecast.risk.metrics import compute_backtest_summary


class BaseAgent(ABC):
    """Abstract base for all RL trading agents."""

    @abstractmethod
    def select_action(self, state: np.ndarray, explore: bool = True) -> int:
        """Choose an action given the current state.

        Args:
            state: Environment state vector.
            explore: If True, use exploration (epsilon-greedy, stochastic policy, etc.).
                     If False, use greedy/deterministic policy.
        """

    @abstractmethod
    def train(self, env: TradingEnvironment, episodes: int) -> dict:
        """Train the agent on the given environment.

        Args:
            env: Training environment.
            episodes: Number of training episodes.

        Returns:
            Dict with episode_rewards, best_epoch, best_reward,
            early_stopped, sharpe_stopped, checkpoint_path, training_history.
        """

    @abstractmethod
    def reset(self) -> None:
        """Reset agent to fresh state (new weights/parameters)."""

    def evaluate(self, env: TradingEnvironment) -> dict:
        """Run agent through environment with greedy policy (no exploration).

        Returns dict with equity_curve, trade_returns, actions, commissions, slippage.
        """
        state = env.reset()
        total_reward = 0.0
        rewards = []

        while True:
            action = self.select_action(state, explore=False)
            next_state, reward, done, info = env.step(action)
            total_reward += reward
            rewards.append(reward)
            state = next_state
            if done:
                break

        return {
            "equity_curve": env.equity_curve,
            "trade_returns": env.trade_returns,
            "actions_log": env.actions_log,
            "total_commissions": env.total_commissions,
            "total_slippage": env.total_slippage,
            "total_reward": total_reward,
            "rewards": rewards,
        }

    def walk_forward_evaluate(
        self,
        prices: np.ndarray,
        train_ratio: float = 0.7,
        initial_capital: float = 10_000.0,
        commission_bps: float = 10.0,
        slippage_bps: float = 5.0,
        spread_bps: float = 5.0,
        window_size: int = 30,
        episodes: int = 200,
        max_position: int = 5,
        stop_loss_pct: float = 0.02,
        max_drawdown_pct: float = 0.10,
    ) -> dict:
        """Walk-forward evaluation: train on first portion, test on second.

        1. Split prices into train/test by train_ratio
        2. Create train environment, train agent
        3. Create test environment, evaluate agent (greedy)
        4. Compute buy-and-hold benchmark on test period
        5. Compute all metrics on test results only

        Returns:
            Dict with training_rewards, test results, backtest_summary, benchmark.
        """
        prices = np.asarray(prices, dtype=np.float64)
        split_idx = int(len(prices) * train_ratio)

        train_prices = prices[:split_idx]
        test_prices = prices[split_idx:]

        if len(train_prices) < window_size + 10 or len(test_prices) < window_size + 10:
            raise ValueError(
                f"Not enough data for walk-forward evaluation. "
                f"Train: {len(train_prices)}, Test: {len(test_prices)}, "
                f"Need at least {window_size + 10} each."
            )

        cost_model = TransactionCostModel(
            commission_bps=commission_bps,
            slippage_bps=slippage_bps,
            spread_bps=spread_bps,
        )

        # Train
        train_risk = RiskManager(
            max_position=max_position,
            stop_loss_pct=stop_loss_pct,
            max_drawdown_pct=max_drawdown_pct,
        )
        train_env = TradingEnvironment(
            prices=train_prices,
            window_size=window_size,
            initial_capital=initial_capital,
            cost_model=cost_model,
            risk_manager=train_risk,
        )

        self.reset()
        train_result = self.train(train_env, episodes=episodes)
        training_rewards = train_result if isinstance(train_result, dict) else train_result

        # Test (fresh risk manager, same cost model)
        test_risk = RiskManager(
            max_position=max_position,
            stop_loss_pct=stop_loss_pct,
            max_drawdown_pct=max_drawdown_pct,
        )
        test_env = TradingEnvironment(
            prices=test_prices,
            window_size=window_size,
            initial_capital=initial_capital,
            cost_model=cost_model,
            risk_manager=test_risk,
        )

        test_results = self.evaluate(test_env)

        # Benchmark
        benchmark = BuyAndHoldBenchmark(initial_capital=initial_capital)
        benchmark_curve = benchmark.evaluate(test_prices)

        # Metrics
        backtest_summary = compute_backtest_summary(
            equity_curve=test_results["equity_curve"],
            benchmark_curve=benchmark_curve,
            trade_returns=test_results["trade_returns"],
            total_commissions=test_results["total_commissions"],
            total_slippage=test_results["total_slippage"],
        )

        return {
            "training_rewards": training_rewards,
            "test_results": test_results,
            "backtest_summary": backtest_summary,
            "benchmark_curve": benchmark_curve,
            "risk_events": test_risk.total_risk_events,
            "stop_losses": test_risk.stop_losses_triggered,
            "drawdown_breakers": test_risk.drawdown_breakers_triggered,
        }
