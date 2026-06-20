"""SQLAlchemy ORM models for persistent storage."""

from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, Float, String, Text, DateTime, Boolean, JSON, Index, ForeignKey,
)
from app.db.database import Base


def _utcnow():
    return datetime.now(timezone.utc)


class MarketDataCache(Base):
    """Cached daily OHLCV bars for a symbol."""

    __tablename__ = "market_data_cache"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(10), nullable=False, index=True)
    date = Column(DateTime, nullable=False)
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    volume = Column(Float)
    cached_at = Column(DateTime, default=_utcnow)

    __table_args__ = (
        Index("ix_mdc_symbol_date", "symbol", "date", unique=True),
    )


class SignalHistory(Base):
    """Audit trail of every signal the system generates."""

    __tablename__ = "signal_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(10), nullable=False, index=True)
    signal_type = Column(String(50), nullable=False)  # e.g. "swing", "usx", "consensus"
    signal = Column(String(30), nullable=False)  # e.g. "STRONG BUY", "SELL"
    score = Column(Float)
    price = Column(Float)
    stop_loss = Column(Float)
    take_profit = Column(Float)
    confidence = Column(Float)
    details = Column(JSON)  # full result dict for auditing
    created_at = Column(DateTime, default=_utcnow, index=True)

    # Phase 4.3: outcome tracking
    outcome_price = Column(Float, nullable=True)
    outcome_date = Column(DateTime, nullable=True)
    outcome_return_pct = Column(Float, nullable=True)


class ConsensusLog(Base):
    """Audit trail for each consensus decision by profile."""

    __tablename__ = "consensus_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(10), nullable=False, index=True)
    profile = Column(String(20), nullable=False, index=True)
    verdict = Column(String(30), nullable=False, index=True)
    confidence = Column(Float, nullable=True)
    votes_buy = Column(Integer, default=0)
    votes_sell = Column(Integer, default=0)
    votes_hold = Column(Integer, default=0)
    price = Column(Float, nullable=True)
    details = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=_utcnow, index=True)


class ModelResultsCache(Base):
    """Cached ML model results to survive restarts."""

    __tablename__ = "model_results_cache"

    id = Column(Integer, primary_key=True, autoincrement=True)
    cache_key = Column(String(255), nullable=False, unique=True, index=True)
    model_type = Column(String(50), nullable=False)  # lstm, transformer, ensemble, etc.
    result_json = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=_utcnow)
    ttl_seconds = Column(Integer, default=3600)


class ScreeningResult(Base):
    """AAOIFI Halal screening results (Phase 2)."""

    __tablename__ = "screening_results"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(10), nullable=False, unique=True, index=True)
    debt_ratio = Column(Float)
    interest_ratio = Column(Float)
    haram_revenue_ratio = Column(Float)
    liquidity_ratio = Column(Float)
    is_halal = Column(Boolean, default=False)
    sector = Column(String(100))
    last_screened = Column(DateTime, default=_utcnow)
    details = Column(JSON)  # raw financial data for audit


class PortfolioSnapshot(Base):
    """Snapshots of Alpaca portfolio state (Phase 3)."""

    __tablename__ = "portfolio_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    total_equity = Column(Float)
    cash = Column(Float)
    buying_power = Column(Float)
    positions_json = Column(JSON)
    created_at = Column(DateTime, default=_utcnow, index=True)


class RegimeLog(Base):
    """Regime transition audit trail."""

    __tablename__ = "regime_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ts_utc = Column(DateTime, default=_utcnow, index=True)
    state = Column(String(20), nullable=False, index=True)
    vix = Column(Float, nullable=True)
    vix_pctile = Column(Float, nullable=True)
    spy_slope = Column(Float, nullable=True)
    yield_spread = Column(Float, nullable=True)
    changed = Column(Boolean, default=False, nullable=False)


class GuardLog(Base):
    """Per-guard decision audit trail for every trade attempt."""

    __tablename__ = "guard_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ts = Column(DateTime, default=_utcnow, index=True)
    strategy_id = Column(String(5), nullable=True, index=True)
    symbol = Column(String(10), nullable=False, index=True)
    guard_name = Column(String(50), nullable=False, index=True)
    passed = Column(Boolean, nullable=False)
    code = Column(String(50), nullable=False, index=True)
    reason = Column(Text, nullable=False)
    regime = Column(String(20), nullable=True, index=True)


class Alert(Base):
    """Persistent alert log — every signal alert the system emits.

    Replaces the previous ephemeral Telegram-only flow. Each row records whether
    the alert passed the Risk Desk (17-guard) gate, the regime/posture at the time,
    and a correlation_id linking it to the originating signal/trade lineage.
    """

    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ts = Column(DateTime, default=_utcnow, index=True)
    symbol = Column(String(10), nullable=False, index=True)
    alert_type = Column(String(40), nullable=False, index=True)  # "strong_buy", "consensus", "system", ...
    signal = Column(String(30), nullable=True)                   # "STRONG BUY", "SELL", ...
    score = Column(Float, nullable=True)
    price = Column(Float, nullable=True)
    stop_loss = Column(Float, nullable=True)
    take_profit = Column(Float, nullable=True)
    confidence = Column(Float, nullable=True)
    regime = Column(String(20), nullable=True, index=True)
    risk_posture = Column(String(12), nullable=True, index=True)  # risk_on/neutral/risk_off
    guard_passed = Column(Boolean, default=True, nullable=False, index=True)
    guard_reason = Column(Text, nullable=True)
    sent = Column(Boolean, default=False, nullable=False)         # did it reach Telegram?
    correlation_id = Column(String(36), nullable=True, index=True)
    payload = Column(JSON, nullable=True)                         # full alert dict for audit


class TradeHistory(Base):
    """Auto-trade execution history (Stage 1: Paper Trading)."""

    __tablename__ = "trade_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(10), nullable=False, index=True)
    side = Column(String(10), nullable=False)  # "buy" or "sell"
    qty = Column(Float, default=0)
    entry_price = Column(Float)
    exit_price = Column(Float, nullable=True)
    stop_loss = Column(Float, nullable=True)
    take_profit = Column(Float, nullable=True)
    position_value = Column(Float, nullable=True)
    risk_amount = Column(Float, nullable=True)
    risk_pct = Column(Float, nullable=True)
    confidence = Column(Float, default=0)
    order_id = Column(String(100), default="")
    status = Column(String(20), default="submitted")  # submitted, rejected, filled, closed
    signal_details = Column(JSON, nullable=True)
    pnl = Column(Float, nullable=True)  # filled in when position closes
    pnl_pct = Column(Float, nullable=True)
    strategy_id = Column(String(5), nullable=True, index=True)  # "A", "B", "C" or NULL for legacy
    client_order_id = Column(String(100), default="", index=True)
    filled_qty = Column(Integer, nullable=True)
    filled_avg_price = Column(Float, nullable=True)
    armed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_utcnow, index=True)
    closed_at = Column(DateTime, nullable=True)

    agent_decision_id = Column(Integer, ForeignKey("agent_decisions.id"), nullable=True)
    rationale = Column(Text, nullable=True)
    snapshot = Column(JSON, nullable=True)
    reflection = Column(Text, nullable=True)


class AgentDecision(Base):
    __tablename__ = "agent_decisions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(10), nullable=False, index=True)
    verdict = Column(String(30), nullable=False)
    confidence = Column(Float, default=0)
    rationale = Column(Text, nullable=True)
    snapshot = Column(JSON, nullable=True)
    strategy_id = Column(String(5), nullable=True, index=True)
    created_at = Column(DateTime, default=_utcnow, index=True)


class AgentConversation(Base):
    """Durable chat memory — a clean transcript per conversation so the agent's advice
    and analyses survive past the in-memory 30-min TTL (and can be reviewed)."""

    __tablename__ = "agent_conversations"
    conversation_id = Column(String(64), primary_key=True)
    messages = Column(JSON, nullable=True)          # [{role, text, ts}] — text only, JSON-safe
    turns = Column(Integer, default=0)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow, index=True)
    created_at = Column(DateTime, default=_utcnow)


class TradingRulebook(Base):
    __tablename__ = "trading_rulebook"
    id = Column(Integer, primary_key=True, autoincrement=True)
    rule_text = Column(Text, nullable=False)
    category = Column(String(20), nullable=False, default="general")
    source_trade_id = Column(Integer, nullable=True)
    source_decision_id = Column(Integer, nullable=True)
    confidence = Column(Float, nullable=True)
    activations = Column(Integer, default=0)
    win_count = Column(Integer, default=0)
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=_utcnow, index=True)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class Universe(Base):
    """Master symbol universe — single source of truth for all tradable stocks.

    Replaces the old ``HALAL_STOCKS`` / ``RUSSELL_1000_HALAL`` dual lists.
    Populated via ``scripts/seed_universe.py`` and refreshed periodically.
    """

    __tablename__ = "universe"

    symbol = Column(String(10), primary_key=True)
    company_name = Column(String(255), default="")
    exchange = Column(String(20), default="")  # NYSE / NASDAQ / AMEX
    cik = Column(String(20), nullable=True)  # SEC EDGAR CIK
    sic_code = Column(String(10), nullable=True)
    sector = Column(String(100), nullable=True)
    industry = Column(String(100), nullable=True)
    is_active = Column(Boolean, default=True)
    added_at = Column(DateTime, default=_utcnow)
    last_updated = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class Watchlist(Base):
    """Persistent user watchlist — symbols the user wants to track."""

    __tablename__ = "watchlist"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(10), nullable=False, unique=True, index=True)
    created_at = Column(DateTime, default=_utcnow)


class BacktestRun(Base):
    """Reproducibility-gated backtest result (Phase 2).

    Every backtest emits a reproducibility seal so results can be
    traced to an exact code + data + config snapshot.
    """

    __tablename__ = "backtest_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime, default=_utcnow, index=True)

    # ── What was tested ──
    strategy_name = Column(String(50), nullable=False, index=True)
    symbols = Column(JSON, nullable=False)  # list[str]
    profile = Column(String(20), nullable=True)  # "momentum", "reversion", "ml", etc.

    # ── Performance summary ──
    sharpe = Column(Float, nullable=True)
    sortino = Column(Float, nullable=True)
    max_drawdown_pct = Column(Float, nullable=True)
    calmar = Column(Float, nullable=True)
    annualized_return_pct = Column(Float, nullable=True)
    win_rate = Column(Float, nullable=True)
    profit_factor = Column(Float, nullable=True)
    total_trades = Column(Integer, nullable=True)
    total_commissions = Column(Float, nullable=True)
    total_slippage = Column(Float, nullable=True)
    test_start = Column(DateTime, nullable=True)
    test_end = Column(DateTime, nullable=True)

    # ── QC checks (Chan Ch.2) ──
    deflated_sharpe = Column(Float, nullable=True)
    permutation_pvalue = Column(Float, nullable=True)
    bootstrap_lower_5pct = Column(Float, nullable=True)

    # ── Reproducibility seal ──
    git_sha = Column(String(40), nullable=False)
    is_dirty = Column(Boolean, default=False)
    data_hash = Column(String(16), nullable=False)
    code_hash = Column(String(16), nullable=False)
    config_hash = Column(String(16), nullable=False)
    extra = Column(JSON, nullable=True)  # catch-all: agent params, model version, etc.


class AnalystForecast(Base):
    """Cached analyst forecasts (Buy/Sell/Hold, Target Price)."""
    __tablename__ = "analyst_forecasts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(10), nullable=False, index=True)
    firm = Column(String(100), nullable=True)
    recommendation = Column(String(50), nullable=True)  # e.g., "STRONG BUY", "HOLD"
    target_price = Column(Float, nullable=True)
    current_price = Column(Float, nullable=True)
    entry_date = Column(DateTime, default=_utcnow)
    created_at = Column(DateTime, default=_utcnow)


class FMPCache(Base):
    """Persistent FMP API response cache — survives container restarts.

    Two-tier strategy: in-memory dict (L1, sub-ms) + this table (L2, durable).
    TTLs mirror fmp_client._CACHE_TTL: 7 days for fundamentals, 1h for news.
    """

    __tablename__ = "fmp_cache"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    cache_key  = Column(String(512), nullable=False, unique=True, index=True)
    data       = Column(JSON, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)


class GoatInvestor(Base):
    """G.O.A.T Investors portfolio metadata."""
    __tablename__ = "goat_investors"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, unique=True)
    investor_type = Column(String(50), nullable=True)  # "Hedge Fund", "Congress", "Family Office"
    organization = Column(String(100), nullable=True)
    image_url = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=_utcnow)


class HotPick(Base):
    """Aggregated Hot Investors Picks."""
    __tablename__ = "hot_picks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(10), nullable=False, index=True)
    company_name = Column(String(100), nullable=True)
    avg_buy_price = Column(Float, nullable=True)
    current_price = Column(Float, nullable=True)
    buys_count = Column(Integer, default=0)
    total_value = Column(Float, nullable=True)
    investors = Column(JSON, nullable=True)  # List of investor IDs or names who bought
    created_at = Column(DateTime, default=_utcnow)
