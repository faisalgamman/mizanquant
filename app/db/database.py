"""Database engine and session management.

Uses SQLite locally (file-based) and PostgreSQL on Railway.
Falls back to in-memory SQLite when DATABASE_URL is not configured.
"""

import logging
from sqlalchemy import create_engine
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
    logger.info("Database tables initialized.")
