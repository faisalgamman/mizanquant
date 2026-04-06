"""EGX database models — completely separate from US stock models."""

from datetime import datetime
from sqlalchemy import (
    Column, Integer, Float, String, Text, DateTime, Boolean, JSON, Index,
)
from app.db.database import Base


class EgxStockData(Base):
    """Uploaded EGX OHLCV data from CSV files."""

    __tablename__ = "egx_stock_data"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False, index=True)
    date = Column(DateTime, nullable=False)
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    volume = Column(Float)
    value = Column(Float, nullable=True)  # قيمة التداول
    change_pct = Column(Float, nullable=True)
    prev_close = Column(Float, nullable=True)
    uploaded_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_egx_symbol_date", "symbol", "date", unique=True),
    )


class EgxWatchlist(Base):
    """EGX stocks being tracked for analysis."""

    __tablename__ = "egx_watchlist"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False, unique=True, index=True)
    name_ar = Column(String(200), nullable=True)  # Arabic name
    sector = Column(String(100), nullable=True)
    added_at = Column(DateTime, default=datetime.utcnow)
    last_analyzed = Column(DateTime, nullable=True)
    is_active = Column(Boolean, default=True)


class EgxSignalHistory(Base):
    """Signal history for EGX stocks."""

    __tablename__ = "egx_signal_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False, index=True)
    direction = Column(String(10), nullable=False)  # "LONG" or "SHORT"
    signal = Column(String(30), nullable=False)      # "STRONG BUY", "BUY", etc.
    score = Column(Integer)                          # V9 score (0-6)
    price = Column(Float)
    stop_loss = Column(Float)
    take_profit = Column(Float)
    atr = Column(Float)
    confidence = Column(Float, nullable=True)
    tool_votes = Column(JSON, nullable=True)  # per-tool breakdown
    details = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    # Outcome tracking
    outcome_price = Column(Float, nullable=True)
    outcome_date = Column(DateTime, nullable=True)
    outcome_return_pct = Column(Float, nullable=True)


class EgxBacktestResult(Base):
    """Stored backtest results for EGX stocks."""

    __tablename__ = "egx_backtest_results"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False, index=True)
    total_trades = Column(Integer)
    wins = Column(Integer)
    losses = Column(Integer)
    win_rate = Column(Float)
    total_pnl = Column(Float)
    profit_factor = Column(Float)
    expectancy = Column(Float)
    avg_hold_days = Column(Float)
    config_json = Column(JSON)  # StrategyConfig params used
    created_at = Column(DateTime, default=datetime.utcnow)
