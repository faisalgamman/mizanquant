"""Output models for RL trading agent commands."""

from pydantic import BaseModel, Field

from openbb_forecast.schemas.backtest import BacktestSummary


class AgentBacktestResult(BaseModel):
    """Single time step from an agent's out-of-sample evaluation."""

    date: str = Field(description="Date of this time step")
    portfolio_value: float = Field(description="Total portfolio value")
    benchmark_value: float = Field(description="Buy-and-hold benchmark value")
    position: int = Field(description="Number of units held")
    action: str = Field(description="Agent action: buy, sell, or hold")
    trade_price: float | None = Field(default=None, description="Execution price")
    commission: float | None = Field(default=None, description="Commission cost")
    slippage: float | None = Field(default=None, description="Slippage cost")
    reward: float = Field(description="Reward received at this step")
    cumulative_return: float = Field(description="Cumulative return since test start")
    drawdown: float = Field(description="Current drawdown from peak")
    risk_event: str | None = Field(
        default=None,
        description="Risk event triggered: stop_loss, position_limit, drawdown_breaker, or None",
    )


class AgentSummary(BaseModel):
    """Training and evaluation summary for a trading agent."""

    agent_name: str = Field(description="Name of the agent type")
    train_episodes: int = Field(description="Number of training episodes")
    train_period: str = Field(description="Training date range")
    test_period: str = Field(description="Out-of-sample test date range")
    final_train_reward: float = Field(description="Mean reward in last 10 training episodes")
    training_rewards: list[float] = Field(description="Reward per training episode")
    backtest: BacktestSummary = Field(description="Out-of-sample backtest metrics")
    risk_events: int = Field(description="Total risk events triggered during test")
    stop_losses_triggered: int = Field(description="Number of stop-loss exits")
    drawdown_breakers_triggered: int = Field(description="Number of drawdown circuit breakers")
