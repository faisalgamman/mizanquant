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
    MAX_CONCURRENT_MODELS: int = 3       # 8GB RAM can run 3 models concurrently
    BG_WORKER_COUNT: int = 5
    SCREENER_WORKERS: int = 10           # 8GB RAM allows more parallel screener threads

    # --- Phase 2: Halal screening ---
    FMP_API_KEY: str = ""

    # --- Notifications ---
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""

    # --- Phase 5: API authentication ---
    # Empty string means no authentication is required.
    API_KEY: str = ""

    # --- Risk management ---
    RISK_CAPITAL: float = 3000.0
    RISK_PCT: float = 1.0

    # --- Auto-trading (Stage 1: Paper) ---
    AUTO_TRADE_ENABLED: bool = False  # MUST be explicitly enabled
    TRADE_RISK_PCT: float = 1.5      # max risk per trade (% of equity)
    MAX_POSITION_PCT: float = 15.0   # max single position size (% of equity)
    MAX_OPEN_POSITIONS: int = 6      # max concurrent positions
    DAILY_LOSS_LIMIT_PCT: float = 3.0  # stop trading if daily loss exceeds this %
    MIN_TRADE_CONFIDENCE: float = 65.0  # minimum consensus confidence to trade
    TRAILING_STOP_ENABLED: bool = True   # use trailing stops instead of static SL
    TRAILING_STOP_PCT: float = 2.5       # trailing stop distance (% from peak)

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


settings = Settings()
