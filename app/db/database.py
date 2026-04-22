"""Database engine and session management.

Uses SQLite locally (file-based) and PostgreSQL on Railway.
Falls back to in-memory SQLite when DATABASE_URL is not configured.
"""

import logging
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, declarative_base

from app.config import settings

logger = logging.getLogger("screener")

# Determine database URL
_db_url = settings.DATABASE_URL

if not _db_url:
    # In-memory SQLite fallback for local development
    _db_url = "sqlite:///./trading_app.db"
    logger.info("No DATABASE_URL configured — using local SQLite file.")

# Handle Railway's postgres:// vs postgresql:// naming
if _db_url.startswith("postgres://"):
    _db_url = _db_url.replace("postgres://", "postgresql://", 1)

# Create engine
_connect_args = {}
if _db_url.startswith("sqlite"):
    _connect_args = {"check_same_thread": False}

engine = create_engine(
    _db_url,
    pool_pre_ping=True,
    connect_args=_connect_args,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def _datetime_sql_type(dialect_name: str) -> str:
    """Return a portable datetime type for lightweight raw ALTER TABLE migrations."""
    if dialect_name == "postgresql":
        return "TIMESTAMP WITH TIME ZONE"
    return "DATETIME"


def get_db():
    """Yield a database session, closing it when done."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Create all tables if they don't exist."""
    Base.metadata.create_all(bind=engine)
    _run_schema_migrations()
    logger.info("Database tables initialized.")


def _run_schema_migrations():
    """Lightweight additive migrations for local SQLite / Railway Postgres."""
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    dialect_name = engine.dialect.name

    if "trade_history" in tables:
        existing_columns = {col["name"] for col in inspector.get_columns("trade_history")}
        statements = []
        if "client_order_id" not in existing_columns:
            statements.append("ALTER TABLE trade_history ADD COLUMN client_order_id VARCHAR(100) DEFAULT ''")
        if "filled_qty" not in existing_columns:
            statements.append("ALTER TABLE trade_history ADD COLUMN filled_qty INTEGER")
        if "filled_avg_price" not in existing_columns:
            statements.append("ALTER TABLE trade_history ADD COLUMN filled_avg_price FLOAT")
        if "armed_at" not in existing_columns:
            statements.append(
                f"ALTER TABLE trade_history ADD COLUMN armed_at {_datetime_sql_type(dialect_name)}"
            )
        for statement in statements:
            with engine.begin() as conn:
                conn.execute(text(statement))

    if "trade_history" in tables:
        with engine.begin() as conn:
            try:
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_trade_history_client_order_id ON trade_history (client_order_id)"))
            except Exception:
                logger.debug("Could not create trade_history client_order_id index", exc_info=True)
