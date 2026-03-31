"""Output models for forecasting commands."""

from pydantic import BaseModel, Field


class ForecastResult(BaseModel):
    """Single forecast data point from walk-forward validation."""

    date: str = Field(description="Date of the observation or forecast")
    actual: float | None = Field(default=None, description="Actual observed value (None for future)")
    predicted: float = Field(description="Model prediction")
    lower_bound: float | None = Field(default=None, description="Lower confidence interval")
    upper_bound: float | None = Field(default=None, description="Upper confidence interval")
    fold: int | None = Field(default=None, description="Walk-forward fold number")
    is_train: bool = Field(default=False, description="Whether this point was in training set")


class ForecastSummary(BaseModel):
    """Aggregated forecast performance metrics across all walk-forward folds."""

    model_name: str = Field(description="Name of the forecasting model")
    n_folds: int = Field(description="Number of walk-forward folds")
    directional_accuracy: float = Field(
        description="Fraction of times predicted direction matched actual direction"
    )
    mae: float = Field(description="Mean Absolute Error across all test folds")
    rmse: float = Field(description="Root Mean Squared Error across all test folds")
    mape: float = Field(description="Mean Absolute Percentage Error")
    mean_forecast: list[float] = Field(description="Average forecast values across folds")
    std_forecast: list[float] = Field(description="Std deviation of forecasts across folds")
