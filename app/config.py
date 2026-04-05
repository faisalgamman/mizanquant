"""Application configuration loaded from environment variables and .env file."""

from dataclasses import dataclass
from typing import Optional

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Central configuration for the trading application.

    Values are loaded from environment variables first, then from a .env file
    as a fallback. Any extra env vars not listed here are silently ignored.
    """

    # --- Alpaca broker connection (default / legacy single-account) ---
    ALPACA_API_KEY: str = ""
    ALPACA_SECRET_KEY: str = ""
    ALPACA_BASE_URL: str = "https://paper-api.alpaca.markets"

    # --- Multi-Strategy Alpaca accounts ---
    # Strategy A: Momentum Alpha
    ALPACA_API_KEY_A: str = ""
    ALPACA_SECRET_KEY_A: str = ""
    # Strategy B: Mean Reversion
    ALPACA_API_KEY_B: str = ""
    ALPACA_SECRET_KEY_B: str = ""
    # Strategy C: AI Ensemble
    ALPACA_API_KEY_C: str = ""
    ALPACA_SECRET_KEY_C: str = ""

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

    # --- Risk management (legacy single-account defaults) ---
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


# ---------------------------------------------------------------------------
# Multi-Strategy Configuration
# ---------------------------------------------------------------------------

@dataclass
class StrategyConfig:
    """Per-strategy trading configuration."""
    strategy_id: str          # "A", "B", "C"
    name: str                 # Human-readable name
    alpaca_api_key: str
    alpaca_secret_key: str
    max_positions: int        # Max concurrent positions
    position_pct: float       # Max single position size (% of equity)
    trailing_stop_enabled: bool
    trailing_stop_pct: float  # Trailing stop distance (% from peak)
    static_sl_pct: float      # Static stop loss % (used if trailing disabled)
    min_confidence: float     # Min consensus confidence to auto-trade
    trade_risk_pct: float     # Max risk per trade (% of equity)
    daily_loss_limit_pct: float


def _build_strategy_configs() -> dict:
    """Build strategy configs from environment variables."""
    configs = {}

    # Strategy A: Momentum Alpha — concentrated trend-following
    if settings.ALPACA_API_KEY_A:
        configs["A"] = StrategyConfig(
            strategy_id="A",
            name="Momentum Alpha",
            alpaca_api_key=settings.ALPACA_API_KEY_A,
            alpaca_secret_key=settings.ALPACA_SECRET_KEY_A,
            max_positions=3,
            position_pct=33.0,
            trailing_stop_enabled=True,
            trailing_stop_pct=3.0,
            static_sl_pct=0,
            min_confidence=60.0,
            trade_risk_pct=2.0,
            daily_loss_limit_pct=3.0,
        )

    # Strategy B: Mean Reversion — diversified dip-buying
    if settings.ALPACA_API_KEY_B:
        configs["B"] = StrategyConfig(
            strategy_id="B",
            name="Mean Reversion",
            alpaca_api_key=settings.ALPACA_API_KEY_B,
            alpaca_secret_key=settings.ALPACA_SECRET_KEY_B,
            max_positions=5,
            position_pct=20.0,
            trailing_stop_enabled=False,
            trailing_stop_pct=0,
            static_sl_pct=2.0,
            min_confidence=55.0,
            trade_risk_pct=1.0,
            daily_loss_limit_pct=3.0,
        )

    # Strategy C: AI Ensemble — pure ML decision-making
    if settings.ALPACA_API_KEY_C:
        configs["C"] = StrategyConfig(
            strategy_id="C",
            name="AI Ensemble",
            alpaca_api_key=settings.ALPACA_API_KEY_C,
            alpaca_secret_key=settings.ALPACA_SECRET_KEY_C,
            max_positions=4,
            position_pct=25.0,
            trailing_stop_enabled=True,
            trailing_stop_pct=2.5,
            static_sl_pct=0,
            min_confidence=65.0,
            trade_risk_pct=1.5,
            daily_loss_limit_pct=3.0,
        )

    return configs


STRATEGY_CONFIGS: dict[str, StrategyConfig] = _build_strategy_configs()
