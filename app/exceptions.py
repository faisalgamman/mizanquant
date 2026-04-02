"""Custom exception hierarchy for the trading application."""


class AppError(Exception):
    """Base exception for all application errors."""

    def __init__(self, message: str, detail: str | None = None) -> None:
        self.message = message
        self.detail = detail
        super().__init__(message)


class DataFetchError(AppError):
    """Raised when fetching market data fails."""


class ModelTrainingError(AppError):
    """Raised when ML model training fails."""


class ValidationError(AppError):
    """Raised on input validation errors."""


class AlpacaAPIError(AppError):
    """Raised when the Alpaca API returns an error or is unreachable."""


class ScreeningError(AppError):
    """Raised when halal screening encounters an error."""


class CacheError(AppError):
    """Raised when a cache read/write operation fails."""
