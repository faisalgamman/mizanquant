"""Application configuration loaded from environment variables and .env file.

Fail-fast policy (Milestone 1, senior-engineer hardening):
- When AUTO_TRADE_ENABLED=True, the required broker + notification credentials
  must be present at boot, otherwise the app refuses to start. This prevents
  silent "paper-mode only" fallbacks that previously masked missing keys.
- Secrets are never logged in full. Use `settings.redacted()` if you need a
  safe-to-log representation.
"""

import logging
from dataclasses import dataclass
from typing import Optional

from pydantic_settings import BaseSettings
from pydantic import field_validator

logger = logging.getLogger("screener")


class ConfigurationError(RuntimeError):
    """Raised when required configuration is missing or inconsistent."""


class Settings(BaseSettings):
    """Central configuration for the trading application.

    Values are loaded from environment variables first, then from a .env file
    as a fallback. Any extra env vars not listed here are silently ignored.
    """

    # --- Alpaca broker connection (default / legacy single-account) ---
    ALPACA_API_KEY: str = ""
    ALPACA_SECRET_KEY: str = ""
    ALPACA_BASE_URL: str = "https://paper-api.alpaca.markets"

    # --- Alpaca market-DATA credentials (data.alpaca.markets) ---
    # The data feed often requires DIFFERENT credentials than the trading API.
    # When set, market_data.py uses these for bar/quote requests and falls back
    # to the trading key only if these are empty. Using the trading key against
    # the data endpoint is a common cause of 401 Unauthorized on /v2/stocks/bars.
    ALPACA_DATA_KEY: str = ""
    ALPACA_DATA_SECRET: str = ""

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
    MAX_CONCURRENT_MODELS: int = 1       # Railway 512 MB — only 1 model at a time
    BG_WORKER_COUNT: int = 2             # Railway 512 MB — reduced from 5
    SCREENER_WORKERS: int = 4            # Railway 512 MB — reduced from 10

    # --- Phase 2: Halal screening ---
    FMP_API_KEY: str = ""

    # --- OpenBB data providers (free tier) ---
    # FRED: federal reserve economic data (VIX, yield spread, CPI, unemployment)
    # Register free at: https://fred.stlouisfed.org/docs/api/api_key.html
    FRED_API_KEY: str = ""
    # Tiingo: OHLCV data that works on servers (no 401 like yfinance)
    # Register free at: https://api.tiingo.com
    TIINGO_TOKEN: str = ""

    # --- Notifications ---
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""
    TELEGRAM_WEBHOOK_SECRET: str = ""  # secret_token for setWebhook validation

    # --- Claude AI Agent ---
    ANTHROPIC_API_KEY: str = ""
    CLAUDE_MODEL: str = "claude-sonnet-4-6"
    # DeepSeek provider (consumed by app/routers/admin.py::agent_health)
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_MODEL: str = "deepseek-chat"

    # --- Groq AI (fast LLM inference — trading assistant) ---
    # Free: 14,400 req/day. Register at: https://console.groq.com
    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = "llama-3.3-70b-versatile"  # fast + function calling

    # --- Unified ecosystem integration ---
    # When False (default), the MarketContextBundle conditioning (macro-scaled
    # risk sizing, rotation sector boost) is computed and logged in SHADOW MODE
    # only — it never alters live paper-order sizing or stock selection. Flip to
    # True to let the conditioning drive the live pipeline once validated.
    CONTEXT_CONDITIONING_LIVE: bool = False

    # --- Conviction Engine: independent selection toggles ---
    # These are SEPARATE from CONTEXT_CONDITIONING_LIVE (which scopes risk
    # sizing + rotation). Each governs one selection behavior so they can be
    # shipped / rolled back independently. All default OFF — none alters the
    # live buy list until flipped, and only after the validation harness
    # (/api/selection/backtest) shows ENHANCED beats CURRENT.
    #
    #  SELECTION_CONDITIONING_LIVE — re-rank deep-picks by context_adjusted
    #      score (promote leading-sector names in risk_on, demote lagging in
    #      risk_off) instead of raw composite_score.
    #  CONVICTION_SIZING_LIVE — surface a risk-adjusted rank (upside/ATR) and a
    #      Kelly-lite suggested position size; when on, ranking uses rank_value.
    #  ADAPTIVE_GATES_LIVE — let the performance governor tighten min/strong
    #      gates when the trailing 30d win rate drops below the floor.
    SELECTION_CONDITIONING_LIVE: bool = False
    CONVICTION_SIZING_LIVE: bool = False
    ADAPTIVE_GATES_LIVE: bool = False

    # --- USX Pro V4: earnings fail-safe ---
    # When True, symbols with missing earnings data are BLOCKED (fail-safe).
    # When False, missing data emits a warning instead — recovers candidates
    # dropped by unreliable yfinance earnings lookups.
    BLOCK_ON_NO_EARNINGS: bool = False  # was True — blocks all when yfinance rate-limited

    # --- Signal archetypes (Chapter 3, Phase 3) ---
    # Each flag enables its detector IN ADDITION to USX Pro in the live
    # Stage-1 funnel. A flag must NEVER be raised before its paired
    # backtest clears the acceptance gate (DSR>=0.95, permutation p<0.05,
    # reality-check LB>0, walk-forward consistency >=3 windows).
    ARCHETYPE_PULLBACK_LIVE: bool = False
    ARCHETYPE_BREAKOUT_LIVE: bool = False
    ARCHETYPE_REVERSAL_LIVE: bool = False

    # --- Composite bridge (Chapter 3, Phase 4) ---
    # When True, enriches Stage-1 candidates with fundamental/forecast/conviction
    # layers and applies the multi-confirmation STRONG BUY gate (>=3 independent
    # confirmations required). This reduces false positives before the existing
    # Stage-2 AI consensus. Default OFF — raise only after validation.
    COMPOSITE_BRIDGE_LIVE: bool = False

    # --- Intraday confirmation (Chapter 3, Phase 5) ---
    # When True, each Stage-1 candidate must also pass an intraday (15-min)
    # confirmation check before advancing to Stage 2: close > VWAP + higher
    # 15-min high + RVOL > 1.5. Also completes gap_go archetype detection.
    INTRADAY_CONFIRM_LIVE: bool = False

    # --- Phase 5: API authentication ---
    # Required for operator/admin/trading endpoints. If left empty, those
    # endpoints fail closed and auto-trading validation will refuse to arm.
    API_KEY: str = ""

    # Telegram noise filter: when True, only messages tagged as BUY
    # signals reach the chat. Everything else (daily summaries, trade
    # confirmations, regime changes, reconciliation alerts, post-market
    # reports, etc.) is silently suppressed. Set False to restore the
    # full feed.
    TELEGRAM_BUY_ONLY: bool = True

    # --- Risk management (legacy single-account defaults) ---
    RISK_CAPITAL: float = 3000.0
    RISK_PCT: float = 1.0

    # --- Universe quality floor (penny / illiquid exclusion) ---
    # Stocks below these floors are excluded from BOTH the composite screener
    # and the signals/USX path, so low-priced manipulated names (e.g. a $1.53
    # pump) can never reach a STRONG BUY / auto-trade. ADV = avg 20d $ volume.
    MIN_PRICE: float = 5.0            # minimum share price (USD)
    MIN_ADV_DOLLAR_M: float = 5.0     # minimum avg daily $ volume (millions)

    # --- Auto-trading (Stage 1: Paper) ---
    AUTO_TRADE_ENABLED: bool = False  # MUST be explicitly enabled
    LIVE_CONFIRMED: bool = False      # Second factor: must be true for live execution
    TRADE_RISK_PCT: float = 1.5      # max risk per trade (% of equity)
    MAX_POSITION_PCT: float = 15.0   # max single position size (% of equity)
    MAX_OPEN_POSITIONS: int = 6      # max concurrent positions
    DAILY_LOSS_LIMIT_PCT: float = 3.0  # stop trading if daily loss exceeds this %
    MIN_TRADE_CONFIDENCE: float = 65.0  # minimum consensus confidence to trade
    TRAILING_STOP_ENABLED: bool = True   # use trailing stops instead of static SL
    TRAILING_STOP_PCT: float = 2.5       # trailing stop distance (% from peak)

    # --- Swing exit mode (validated 2026-05; default OFF, paper-validate first) ---
    # The exit-structure backtest (paired, 510 trades, technical+context legs)
    # proved the live exit stack (ATR×1.5 initial stop + 2.5% trail + TP1/2/3
    # ladder) exits 98.8% of trades by stop in ~2.9 days and earns ~0.96%/trade,
    # while a single WIDE trailing stop + a time exit earns ~2.82%/trade
    # (delta +1.85%/trade, bootstrap_lower +0.014, DSR 0.99). The tight stop sits
    # inside daily ATR noise and gets shaken out before the swing develops.
    #
    # When SWING_EXIT_ENABLED=True, execute_buy() replaces the incoming tight
    # stop/TP with: a FIXED (non-trailing) catastrophe stop = entry·(1 −
    # SWING_TRAIL_PCT) and a far take-profit (entry·(1 + 3·SWING_TRAIL_PCT)) so
    # neither binds in normal noise — the 20-day TIME exit governs instead.
    # Validation (2026-06, paired backtest on the technical selection):
    #   time-stop 20d         → mean +3.60%, win 58.9%, DSR 0.985 (PASSES ≥0.95)
    #   trailing 12%          → mean +1.48%, DSR 0.750 (cuts winners early)
    #   trailing 2.5% (old)   → mean +0.12%, DSR 0.198 (= the live PF 0.58 killer)
    # ⇒ the edge needs a TIME hold + a WIDE FIXED catastrophe stop, NOT a trailing
    # stop. The wider stop correctly SHRINKS position size (constant risk budget).
    # A scheduler job closes positions held >= SWING_MAX_HOLD_DAYS trading days.
    SWING_EXIT_ENABLED: bool = False
    # ── Sizing pipeline layers (shadow‑mode: compute always, apply when LIVE) ──
    XGB_SIGNAL_GATE_LIVE: bool = False
    WAVELET_SIZING_LIVE: bool = False
    KALMAN_SIZING_LIVE: bool = False
    GARCH_SIZING_LIVE: bool = False
    PORTFOLIO_COV_SIZING_LIVE: bool = False
    MR_QUALITY_SIZING_LIVE: bool = False
    SENTIMENT_SIZING_LIVE: bool = False
    FACTOR_SIZING_LIVE: bool = False
    SWING_TRAIL_PCT: float = 15.0        # FIXED wide catastrophe-stop distance (%) — not trailing
    SWING_MAX_HOLD_DAYS: int = 20        # time exit: close after this many trading days (the real edge)
    # Forward-outcome evaluation — MUST mirror the live exit policy (Option A) so the
    # measured forward PF reflects what is actually traded (not a stale 5-day snapshot).
    SIGNAL_OUTCOME_HOLD_DAYS: int = 20   # evaluate a signal at its time exit (trading days)
    SIGNAL_OUTCOME_STOP_PCT: float = 15.0  # ...or earlier if this fixed catastrophe stop is hit

    # --- Emergency Kill Switch ---
    # Set to True to halt ALL trading immediately (order submission blocked).
    # Can be toggled at runtime via Railway dashboard env var without redeploy.
    KILL_SWITCH: bool = False

    # --- Interactive Brokers (TWS/IB Gateway) ---
    # Default port unified to 4002 (IB Gateway paper). Previously 7497 here but
    # 4002 in the adapter — the mismatch let a bad Railway config go unnoticed.
    IBKR_HOST: str = "127.0.0.1"
    IBKR_PORT: int = 4002
    IBKR_CLIENT_ID: int = 1

    # --- Live whitelist (SF-3) ---
    # Comma-separated list of ticker symbols allowed for live trading.
    # When empty, validate_for_live() will refuse to arm auto-trade.
    # Example: "AAPL,MSFT,GOOGL"
    LIVE_WHITELIST: str = ""

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "allow",
    }

    @field_validator(
        "AUTO_TRADE_ENABLED", "LIVE_CONFIRMED", "CONTEXT_CONDITIONING_LIVE",
        "SELECTION_CONDITIONING_LIVE", "CONVICTION_SIZING_LIVE", "ADAPTIVE_GATES_LIVE",
        "BLOCK_ON_NO_EARNINGS",
        "ARCHETYPE_PULLBACK_LIVE", "ARCHETYPE_BREAKOUT_LIVE", "ARCHETYPE_REVERSAL_LIVE",
        "COMPOSITE_BRIDGE_LIVE",
        "INTRADAY_CONFIRM_LIVE",
        "SWING_EXIT_ENABLED", "KILL_SWITCH",
        mode="before",
    )
    @classmethod
    def _strip_bool(cls, v):
        if isinstance(v, str):
            v = v.strip()
            if v.lower() in ("true", "1", "yes"):
                return True
            if v.lower() in ("false", "0", "no"):
                return False
        return v

    # ------------------------------------------------------------------
    # Safety helpers
    # ------------------------------------------------------------------

    def validate_for_live(self) -> list[str]:
        """Return a list of human-readable errors preventing live trading.

        Called at boot when AUTO_TRADE_ENABLED=True. Empty list means OK.
        """
        errors: list[str] = []

        # LIVE_CONFIRMED must be explicitly set to true for live execution
        if not self.LIVE_CONFIRMED:
            errors.append(
                "AUTO_TRADE_ENABLED=True but LIVE_CONFIRMED is False. "
                "Set LIVE_CONFIRMED=true in environment to enable live execution. "
                "Until then, trading remains in shadow/paper mode."
            )

        # At least one Alpaca account must be configured
        any_strategy_keys = any(
            getattr(self, f"ALPACA_API_KEY_{sid}") and getattr(self, f"ALPACA_SECRET_KEY_{sid}")
            for sid in ("A", "B", "C")
        )
        legacy_keys = bool(self.ALPACA_API_KEY and self.ALPACA_SECRET_KEY)
        if not (any_strategy_keys or legacy_keys):
            errors.append(
                "AUTO_TRADE_ENABLED=True but no Alpaca credentials found "
                "(set ALPACA_API_KEY[_A/B/C] and ALPACA_SECRET_KEY[_A/B/C])."
            )

        # Base URL must be paper while we're still in M1; force explicit opt-in for live.
        if "paper-api" not in self.ALPACA_BASE_URL:
            errors.append(
                f"ALPACA_BASE_URL is '{self.ALPACA_BASE_URL}'. Live endpoints are "
                "blocked until Milestone 5 acceptance criteria are met. "
                "Use https://paper-api.alpaca.markets during hardening."
            )

        # Telegram for ops alerts (kill-switch notifications)
        if not (self.TELEGRAM_BOT_TOKEN and self.TELEGRAM_CHAT_ID):
            errors.append(
                "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID missing — "
                "kill-switch and reconciliation alerts would be invisible."
            )

        if not self.API_KEY:
            errors.append(
                "API_KEY missing — operator/trading endpoints would fail closed. "
                "Set a strong X-API-Key secret before enabling auto-trading."
            )

        # Sanity ranges
        if not 0 < self.TRADE_RISK_PCT <= 5:
            errors.append(f"TRADE_RISK_PCT={self.TRADE_RISK_PCT} outside (0, 5].")
        if not 0 < self.DAILY_LOSS_LIMIT_PCT <= 10:
            errors.append(f"DAILY_LOSS_LIMIT_PCT={self.DAILY_LOSS_LIMIT_PCT} outside (0, 10].")
        if not 0 < self.MAX_POSITION_PCT <= 100:
            errors.append(f"MAX_POSITION_PCT={self.MAX_POSITION_PCT} outside (0, 100].")
        if self.MAX_OPEN_POSITIONS < 1:
            errors.append("MAX_OPEN_POSITIONS must be >= 1.")

        # SF-3: live whitelist disabled — trade ALL halal symbols on STRONG BUY
        # (was: require at least one symbol in LIVE_WHITELIST)
        # if not self.live_whitelist:
        #     errors.append(
        #         "LIVE_WHITELIST is empty — at least one ticker symbol required "
        #         "for live trading (comma-separated, e.g. AAPL,MSFT)."
        #     )

        return errors

    @property
    def live_whitelist(self) -> list[str]:
        """Parse LIVE_WHITELIST into a list of uppercase symbols."""
        raw = (self.LIVE_WHITELIST or "").strip()
        if not raw:
            return []
        return [s.strip().upper() for s in raw.split(",") if s.strip()]

    def redacted(self) -> dict:
        """Return settings with secret fields masked — safe for logging."""
        secret_fields = {
            "ALPACA_API_KEY", "ALPACA_SECRET_KEY",
            "ALPACA_API_KEY_A", "ALPACA_SECRET_KEY_A",
            "ALPACA_API_KEY_B", "ALPACA_SECRET_KEY_B",
            "ALPACA_API_KEY_C", "ALPACA_SECRET_KEY_C",
            "FMP_API_KEY", "TELEGRAM_BOT_TOKEN", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY", "API_KEY",
        }
        out: dict = {}
        for k, v in self.model_dump().items():
            if k in secret_fields and v:
                out[k] = f"***{str(v)[-4:]}" if len(str(v)) >= 4 else "***"
            else:
                out[k] = v
        return out


settings = Settings()


def _check_ibkr_reachable() -> list[str]:
    """Return a list of errors if IBKR is the selected broker but unreachable.

    Only runs when IBKR is actually used (BROKER_TYPE=ibkr or any
    STRATEGY_BROKER_*=ibkr). Catches misconfigured host/port at boot rather
    than letting it fail silently on the first trade.
    """
    import os

    broker_type = os.environ.get("BROKER_TYPE", "alpaca").lower()
    strategy_brokers = [
        os.environ.get(f"STRATEGY_BROKER_{s}", "").lower()
        for s in ("A", "B", "C")
    ]
    if broker_type != "ibkr" and "ibkr" not in strategy_brokers:
        return []  # IBKR not used — skip

    import socket
    from app.services.broker.ibkr_config import get_ibkr_config

    cfg = get_ibkr_config()
    try:
        sock = socket.create_connection((cfg["host"], cfg["port"]), timeout=8)
        sock.close()
        logger.info(
            "IBKR gateway socket reachable: %s:%d (%s)", cfg["host"], cfg["port"], cfg["mode"],
        )
        return []
    except Exception as exc:
        return [
            f"IBKR unreachable at {cfg['host']}:{cfg['port']} ({cfg['mode']}): {exc}. "
            f"Check the Railway 'ibgateway' service and IBKR_HOST/IBKR_PORT vars."
        ]


def assert_ready_for_auto_trade() -> None:
    """Fail-fast guard — call at boot if AUTO_TRADE_ENABLED=True.

    Raises ConfigurationError if anything critical is missing.
    """
    if not settings.AUTO_TRADE_ENABLED:
        return
    errors = settings.validate_for_live()
    errors += _check_ibkr_reachable()
    if errors:
        bullet = "\n  - ".join(errors)
        raise ConfigurationError(
            f"Refusing to start auto-trading with invalid configuration:\n  - {bullet}"
        )
    logger.info("Configuration validated for auto-trading (paper).")


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

    # Strategy A: HANA — concentrated trend-following
    if settings.ALPACA_API_KEY_A:
        configs["A"] = StrategyConfig(
            strategy_id="A",
            name="HANA",
            alpaca_api_key=settings.ALPACA_API_KEY_A,
            alpaca_secret_key=settings.ALPACA_SECRET_KEY_A,
            max_positions=5,
            position_pct=50.0,
            trailing_stop_enabled=True,
            trailing_stop_pct=3.0,
            static_sl_pct=0,
            min_confidence=45.0,
            trade_risk_pct=2.0,
            daily_loss_limit_pct=3.0,
        )

    # Strategy B: balanced mean-reversion / dip-buying
    if settings.ALPACA_API_KEY_B:
        configs["B"] = StrategyConfig(
            strategy_id="B",
            name="Strategy B",
            alpaca_api_key=settings.ALPACA_API_KEY_B,
            alpaca_secret_key=settings.ALPACA_SECRET_KEY_B,
            max_positions=5,
            position_pct=50.0,
            trailing_stop_enabled=True,
            trailing_stop_pct=2.5,
            static_sl_pct=0,
            min_confidence=45.0,
            trade_risk_pct=1.5,
            daily_loss_limit_pct=3.0,
        )

    # Strategy C: mazem — pure ML decision-making
    if settings.ALPACA_API_KEY_C:
        configs["C"] = StrategyConfig(
            strategy_id="C",
            name="mazem",
            alpaca_api_key=settings.ALPACA_API_KEY_C,
            alpaca_secret_key=settings.ALPACA_SECRET_KEY_C,
            max_positions=5,
            position_pct=50.0,
            trailing_stop_enabled=True,
            trailing_stop_pct=2.5,
            static_sl_pct=0,
            min_confidence=45.0,
            trade_risk_pct=1.5,
            daily_loss_limit_pct=3.0,
        )

    return configs


STRATEGY_CONFIGS: dict[str, StrategyConfig] = _build_strategy_configs()
