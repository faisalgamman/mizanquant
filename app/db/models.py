"""SQLAlchemy ORM models for persistent storage."""

from datetime import datetime
from sqlalchemy import (
    Column, Integer, Float, String, Text, DateTime, Boolean, JSON, Index,
)
from app.db.database import Base


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
    cached_at = Column(DateTime, default=datetime.utcnow)

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
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    # Phase 4.3: outcome tracking
    outcome_price = Column(Float, nullable=True)
    outcome_date = Column(DateTime, nullable=True)
    outcome_return_pct = Column(Float, nullable=True)


class ModelResultsCache(Base):
    """Cached ML model results to survive restarts."""

    __tablename__ = "model_results_cache"

    id = Column(Integer, primary_key=True, autoincrement=True)
    cache_key = Column(String(255), nullable=False, unique=True, index=True)
    model_type = Column(String(50), nullable=False)  # lstm, transformer, ensemble, etc.
    result_json = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
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
    last_screened = Column(DateTime, default=datetime.utcnow)
    details = Column(JSON)  # raw financial data for audit


class PortfolioSnapshot(Base):
    """Snapshots of Alpaca portfolio state (Phase 3)."""

    __tablename__ = "portfolio_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    total_equity = Column(Float)
    cash = Column(Float)
    buying_power = Column(Float)
    positions_json = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


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
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    closed_at = Column(DateTime, nullable=True)
