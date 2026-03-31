"""Risk management — the single biggest missing piece from the original codebase.

The original agents had:
  - No position limits (unlimited inventory accumulation)
  - No stop-losses (single bad trade can wipe the account)
  - No drawdown controls (nothing stops trading during losing streaks)

RiskManager gates every trade decision with configurable limits.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RiskEvent:
    """Record of a triggered risk control."""

    event_type: str  # "position_limit", "stop_loss", "drawdown_breaker"
    timestamp: int
    details: str


@dataclass
class RiskManager:
    """Enforces risk limits during trading.

    Controls:
        max_position: Maximum units held at once.
        stop_loss_pct: Exit position if it loses more than this fraction (e.g., 0.02 = 2%).
        max_drawdown_pct: Halt all trading if portfolio drawdown exceeds this (e.g., 0.10 = 10%).
        daily_loss_limit_pct: Stop trading for the day if daily P&L is worse than this.

    Usage:
        rm = RiskManager(max_position=5, stop_loss_pct=0.02, max_drawdown_pct=0.10)
        if rm.can_buy(current_position):
            # proceed with buy
        rm.update_portfolio(portfolio_value, timestamp)
        events = rm.check_stop_loss(entry_prices, current_price, timestamp)
    """

    max_position: int = 5
    stop_loss_pct: float = 0.02
    max_drawdown_pct: float = 0.10
    daily_loss_limit_pct: float | None = None

    # Internal state
    peak_value: float = 0.0
    events: list[RiskEvent] = field(default_factory=list)
    halted: bool = False
    _day_start_value: float = 0.0
    _current_day: int = -1

    def reset(self, initial_value: float) -> None:
        """Reset risk manager state."""
        self.peak_value = initial_value
        self.events = []
        self.halted = False
        self._day_start_value = initial_value
        self._current_day = -1

    def can_buy(self, current_position: int) -> bool:
        """Check if a new buy is allowed given position limits."""
        if self.halted:
            return False
        return current_position < self.max_position

    def check_stop_loss(
        self,
        entry_prices: list[float],
        current_price: float,
        timestamp: int,
    ) -> list[int]:
        """Check which positions should be closed due to stop-loss.

        Returns indices of positions that breached the stop-loss threshold.
        """
        to_close = []
        for i, entry_price in enumerate(entry_prices):
            if entry_price <= 0:
                continue
            loss_pct = (entry_price - current_price) / entry_price
            if loss_pct >= self.stop_loss_pct:
                to_close.append(i)
                self.events.append(
                    RiskEvent(
                        event_type="stop_loss",
                        timestamp=timestamp,
                        details=f"Position entered at {entry_price:.2f}, "
                        f"current {current_price:.2f}, loss {loss_pct:.2%}",
                    )
                )
        return to_close

    def update_portfolio(self, portfolio_value: float, timestamp: int) -> bool:
        """Update portfolio tracking and check drawdown circuit breaker.

        Returns True if trading should continue, False if halted.
        """
        # Update peak
        if portfolio_value > self.peak_value:
            self.peak_value = portfolio_value

        # Check max drawdown
        if self.peak_value > 0:
            drawdown = (self.peak_value - portfolio_value) / self.peak_value
            if drawdown >= self.max_drawdown_pct:
                self.halted = True
                self.events.append(
                    RiskEvent(
                        event_type="drawdown_breaker",
                        timestamp=timestamp,
                        details=f"Drawdown {drawdown:.2%} exceeded limit {self.max_drawdown_pct:.2%}",
                    )
                )
                return False

        # Check daily loss limit
        if self.daily_loss_limit_pct is not None:
            if self._current_day != timestamp:
                self._day_start_value = portfolio_value
                self._current_day = timestamp
            elif self._day_start_value > 0:
                daily_loss = (self._day_start_value - portfolio_value) / self._day_start_value
                if daily_loss >= self.daily_loss_limit_pct:
                    self.events.append(
                        RiskEvent(
                            event_type="daily_limit",
                            timestamp=timestamp,
                            details=f"Daily loss {daily_loss:.2%} exceeded limit",
                        )
                    )
                    return False

        return True

    @property
    def stop_losses_triggered(self) -> int:
        return sum(1 for e in self.events if e.event_type == "stop_loss")

    @property
    def drawdown_breakers_triggered(self) -> int:
        return sum(1 for e in self.events if e.event_type == "drawdown_breaker")

    @property
    def total_risk_events(self) -> int:
        return len(self.events)
