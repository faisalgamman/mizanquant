"""Trading environment with transaction costs and risk management.

This is the single biggest missing piece from the original codebase.
The original agents used `starting_money -= self.trend[t]` with:
  - No transaction costs
  - No position limits
  - No stop-losses
  - No drawdown controls

This environment wraps price data with realistic market mechanics.
"""

from __future__ import annotations

import numpy as np

from openbb_forecast.backtesting.transaction_costs import TransactionCostModel
from openbb_forecast.risk.manager import RiskManager


class TradingEnvironment:
    """Gym-like trading environment with costs and risk controls.

    State vector: [price_changes(window), position_ratio, unrealized_pnl_ratio, cash_ratio]
    Actions: 0=hold, 1=buy, 2=sell

    The reward uses differential Sharpe ratio instead of raw P&L,
    which encourages risk-adjusted returns over pure profit maximization.

    Args:
        prices: Array of daily close prices.
        window_size: Number of past price changes in the state.
        initial_capital: Starting cash.
        cost_model: Transaction cost model.
        risk_manager: Risk management controls.
    """

    # Action constants
    HOLD = 0
    BUY = 1
    SELL = 2

    def __init__(
        self,
        prices: np.ndarray,
        window_size: int = 30,
        initial_capital: float = 10_000.0,
        cost_model: TransactionCostModel | None = None,
        risk_manager: RiskManager | None = None,
    ):
        self.prices = np.asarray(prices, dtype=np.float64)
        self.window_size = window_size
        self.initial_capital = initial_capital
        self.cost_model = cost_model or TransactionCostModel()
        self.risk_manager = risk_manager or RiskManager()

        # State size: window of price changes + position_ratio + unrealized_pnl + cash_ratio
        self.state_size = window_size + 3
        self.action_size = 3

        # Episode state (set in reset())
        self._t: int = 0
        self._cash: float = 0.0
        self._inventory: list[float] = []  # Entry prices of held positions
        self._done: bool = False
        self._total_commissions: float = 0.0
        self._total_slippage: float = 0.0
        self._trade_returns: list[float] = []  # Return per completed round-trip
        self._equity_curve: list[float] = []
        self._actions_log: list[dict] = []

        # For differential Sharpe ratio reward
        self._A: float = 0.0  # Running mean of returns
        self._B: float = 0.0  # Running mean of squared returns
        self._n_reward_steps: int = 0

    @property
    def n_steps(self) -> int:
        return len(self.prices)

    def reset(self) -> np.ndarray:
        """Reset environment to initial state."""
        self._t = self.window_size
        self._cash = self.initial_capital
        self._inventory = []
        self._done = False
        self._total_commissions = 0.0
        self._total_slippage = 0.0
        self._trade_returns = []
        self._equity_curve = [self.initial_capital]
        self._actions_log = []
        self._A = 0.0
        self._B = 0.0
        self._n_reward_steps = 0
        self.risk_manager.reset(self.initial_capital)
        return self._get_state()

    def _get_state(self) -> np.ndarray:
        """Build state vector at current time step.

        Components:
            1. Normalized price changes over the window (backward-looking only)
            2. Position ratio: position_value / portfolio_value
            3. Unrealized P&L ratio: unrealized_pnl / portfolio_value
            4. Cash ratio: cash / portfolio_value
        """
        # Price changes (normalized by current price for scale invariance)
        start = max(0, self._t - self.window_size)
        price_window = self.prices[start : self._t + 1]
        if len(price_window) < 2:
            changes = np.zeros(self.window_size)
        else:
            raw_changes = np.diff(price_window) / price_window[:-1]
            # Pad to window_size if needed
            changes = np.zeros(self.window_size)
            changes[-len(raw_changes) :] = raw_changes

        # Portfolio metrics
        current_price = self.prices[self._t]
        position_value = len(self._inventory) * current_price
        portfolio_value = self._cash + position_value
        portfolio_value = max(portfolio_value, 1e-8)  # prevent division by zero

        position_ratio = position_value / portfolio_value
        unrealized_pnl = sum(current_price - entry for entry in self._inventory) / portfolio_value
        cash_ratio = self._cash / portfolio_value

        state = np.concatenate([changes, [position_ratio, unrealized_pnl, cash_ratio]])
        return state.astype(np.float32)

    def _portfolio_value(self) -> float:
        current_price = self.prices[self._t]
        return self._cash + len(self._inventory) * current_price

    def _differential_sharpe_reward(self, step_return: float) -> float:
        """Differential Sharpe ratio — reward that encourages risk-adjusted returns.

        Much better than raw P&L as a reward signal because it penalizes
        high-variance strategies that occasionally get lucky.
        """
        self._n_reward_steps += 1
        eta = 1.0 / max(self._n_reward_steps, 1)  # Decay rate

        delta_A = step_return - self._A
        delta_B = step_return**2 - self._B

        denom = (self._B - self._A**2)
        if denom > 1e-10:
            reward = (self._B * delta_A - 0.5 * self._A * delta_B) / (denom**1.5)
        else:
            reward = delta_A  # Fall back to simple return when variance is tiny

        # Update running statistics
        self._A += eta * delta_A
        self._B += eta * delta_B

        return float(reward)

    def step(self, action: int) -> tuple[np.ndarray, float, bool, dict]:
        """Execute one trading step.

        Args:
            action: 0=hold, 1=buy, 2=sell

        Returns:
            (next_state, reward, done, info)
        """
        if self._done:
            return self._get_state(), 0.0, True, {"action_taken": "none"}

        current_price = self.prices[self._t]
        info: dict = {"action_taken": "hold", "risk_event": None}
        prev_value = self._portfolio_value()

        # Execute action with risk controls
        if action == self.BUY:
            if self.risk_manager.can_buy(len(self._inventory)):
                eff_price, commission, slippage = self.cost_model.buy_cost(current_price)
                if self._cash >= eff_price:
                    self._cash -= eff_price
                    self._inventory.append(current_price)  # Track entry at market price
                    self._total_commissions += commission
                    self._total_slippage += slippage
                    info["action_taken"] = "buy"
                    info["trade_price"] = current_price
                    info["commission"] = commission
                    info["slippage"] = slippage
            else:
                info["risk_event"] = "position_limit"

        elif action == self.SELL and self._inventory:
            entry_price = self._inventory.pop(0)  # FIFO
            eff_price, commission, slippage = self.cost_model.sell_cost(current_price)
            self._cash += eff_price
            self._total_commissions += commission
            self._total_slippage += slippage

            trade_return = (eff_price - entry_price) / entry_price
            self._trade_returns.append(trade_return)
            info["action_taken"] = "sell"
            info["trade_price"] = current_price
            info["commission"] = commission
            info["slippage"] = slippage
            info["trade_return"] = trade_return

        # Check stop-losses
        if self._inventory:
            to_close = self.risk_manager.check_stop_loss(self._inventory, current_price, self._t)
            for idx in sorted(to_close, reverse=True):
                entry_price = self._inventory.pop(idx)
                eff_price, commission, slippage = self.cost_model.sell_cost(current_price)
                self._cash += eff_price
                self._total_commissions += commission
                self._total_slippage += slippage
                trade_return = (eff_price - entry_price) / entry_price
                self._trade_returns.append(trade_return)
                info["risk_event"] = "stop_loss"

        # Update portfolio tracking
        current_value = self._portfolio_value()
        self._equity_curve.append(current_value)
        can_continue = self.risk_manager.update_portfolio(current_value, self._t)

        if not can_continue:
            # Circuit breaker triggered — force close all positions
            for entry_price in self._inventory:
                eff_price, commission, slippage = self.cost_model.sell_cost(current_price)
                self._cash += eff_price
                self._total_commissions += commission
                self._total_slippage += slippage
                trade_return = (eff_price - entry_price) / entry_price
                self._trade_returns.append(trade_return)
            self._inventory = []
            info["risk_event"] = "drawdown_breaker"
            self._done = True

        # Compute reward
        step_return = (current_value - prev_value) / max(prev_value, 1e-8)
        reward = self._differential_sharpe_reward(step_return)

        # Penalise holding without positions to force active trading
        if action == self.HOLD and not self._inventory:
            reward -= 0.0005

        # Advance time
        self._t += 1
        if self._t >= len(self.prices) - 1:
            self._done = True
            # Close remaining positions at end
            final_price = self.prices[self._t] if self._t < len(self.prices) else current_price
            for entry_price in self._inventory:
                eff_price, _, _ = self.cost_model.sell_cost(final_price)
                self._cash += eff_price
                trade_return = (eff_price - entry_price) / entry_price
                self._trade_returns.append(trade_return)
            self._inventory = []

        # Log
        self._actions_log.append(info)

        next_state = self._get_state() if not self._done else np.zeros(self.state_size, dtype=np.float32)
        return next_state, float(reward), self._done, info

    @property
    def equity_curve(self) -> np.ndarray:
        return np.array(self._equity_curve)

    @property
    def trade_returns(self) -> np.ndarray:
        return np.array(self._trade_returns) if self._trade_returns else np.array([])

    @property
    def total_commissions(self) -> float:
        return self._total_commissions

    @property
    def total_slippage(self) -> float:
        return self._total_slippage

    @property
    def actions_log(self) -> list[dict]:
        return self._actions_log
