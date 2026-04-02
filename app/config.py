"""Application configuration loaded from environment variables and .env file."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Central configuration for the trading application.

    Values are loaded from environment variables first, then from a .env file
    as a fallback. Any extra env vars not listed here are silently ignored.
    """

    # --- Alpaca broker connection ---
    ALPACA_API_KEY: str = ""
    ALPACA_SECRET_KEY: str = ""
    ALPACA_BASE_URL: str = "https://paper-api.alpaca.markets"

    # --- Database ---
    # Empty string means no persistent DB; the app will use an in-memory fallback.
    DATABASE_URL: str = ""

    # --- Server ---
    PORT: int = 8000
    LOG_LEVEL: str = "INFO"

    # --- Cache TTLs (seconds) ---
    SIMPLE_CACHE_TTL: int = 300
    BG_CACHE_TTL: int = 3600
    MODEL_CACHE_TTL: int = 3600

    # --- Concurrency / workers ---
    MAX_CONCURRENT_MODELS: int = 1
    BG_WORKER_COUNT: int = 3
    SCREENER_WORKERS: int = 5

    # --- Phase 2: Halal screening ---
    FMP_API_KEY: str = ""

    # --- Notifications ---
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""

    # --- Phase 5: API authentication ---
    # Empty string means no authentication is required.
    API_KEY: str = ""

    # --- Risk management ---
    RISK_CAPITAL: float = 100000.0
    RISK_PCT: float = 1.0

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


settings = Settings()
