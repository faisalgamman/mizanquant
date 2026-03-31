"""Pydantic output models for all forecast extension commands."""

from openbb_forecast.schemas.forecast import ForecastResult, ForecastSummary
from openbb_forecast.schemas.backtest import BacktestResult, BacktestSummary
from openbb_forecast.schemas.agent import AgentBacktestResult, AgentSummary
from openbb_forecast.schemas.simulation import MonteCarloResult, MonteCarloSummary

__all__ = [
    "ForecastResult",
    "ForecastSummary",
    "BacktestResult",
    "BacktestSummary",
    "AgentBacktestResult",
    "AgentSummary",
    "MonteCarloResult",
    "MonteCarloSummary",
]
