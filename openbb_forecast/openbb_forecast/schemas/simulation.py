"""Output models for Monte Carlo simulation commands."""

from pydantic import BaseModel, Field


class MonteCarloResult(BaseModel):
    """Statistics for a single forecast day from Monte Carlo simulation."""

    day: int = Field(description="Forecast day number (1-indexed)")
    mean_price: float = Field(description="Mean simulated price")
    median_price: float = Field(description="Median simulated price")
    std_price: float = Field(description="Standard deviation of simulated prices")
    percentile_5: float = Field(description="5th percentile (bearish scenario)")
    percentile_25: float = Field(description="25th percentile")
    percentile_75: float = Field(description="75th percentile")
    percentile_95: float = Field(description="95th percentile (bullish scenario)")
    var_95: float = Field(description="95% Value at Risk (max loss at 95% confidence)")
    cvar_95: float = Field(description="95% Conditional VaR (expected shortfall)")


class MonteCarloSummary(BaseModel):
    """Overall summary of Monte Carlo simulation."""

    n_simulations: int = Field(description="Number of simulation paths")
    forecast_days: int = Field(description="Number of days forecasted")
    initial_price: float = Field(description="Starting price")
    annualized_drift: float = Field(description="Estimated annualized drift (mu)")
    annualized_volatility: float = Field(description="Estimated annualized volatility (sigma)")
    prob_profit: float = Field(description="Probability of price increase")
    expected_terminal_price: float = Field(description="Mean terminal price")
    terminal_var_95: float = Field(description="95% VaR at terminal day")
    terminal_cvar_95: float = Field(description="95% CVaR at terminal day")
