"""Output models for backtesting commands."""

from pydantic import BaseModel, Field


class BacktestResult(BaseModel):
    """Single time step in a backtest."""

    date: str = Field(description="Date of this time step")
    portfolio_value: float = Field(description="Total portfolio value (cash + positions)")
    benchmark_value: float = Field(description="Buy-and-hold benchmark value")
    position: int = Field(description="Number of shares/units held")
    action: str = Field(description="Action taken: buy, sell, or hold")
    trade_price: float | None = Field(default=None, description="Execution price if traded")
    commission: float | None = Field(default=None, description="Commission paid on this trade")
    slippage: float | None = Field(default=None, description="Slippage cost on this trade")
    cumulative_return: float = Field(description="Cumulative return since start")
    drawdown: float = Field(description="Current drawdown from peak")


class BacktestSummary(BaseModel):
    """Aggregated backtest performance metrics."""

    total_return: float = Field(description="Total return over test period")
    annualized_return: float = Field(description="Annualized return")
    sharpe_ratio: float = Field(description="Annualized Sharpe ratio")
    sortino_ratio: float = Field(description="Annualized Sortino ratio")
    max_drawdown: float = Field(description="Maximum peak-to-trough drawdown")
    calmar_ratio: float = Field(description="Annualized return / max drawdown")
    win_rate: float = Field(description="Fraction of profitable trades")
    profit_factor: float = Field(description="Gross profit / gross loss")
    total_trades: int = Field(description="Total number of completed round-trip trades")
    avg_trade_return: float = Field(description="Mean return per trade")
    benchmark_return: float = Field(description="Buy-and-hold return over same period")
    alpha: float = Field(description="Excess return over benchmark")
    total_commissions: float = Field(description="Total commission costs paid")
    total_slippage: float = Field(description="Total slippage costs incurred")
    test_start: str = Field(description="Start date of test period")
    test_end: str = Field(description="End date of test period")
