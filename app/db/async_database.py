"""Async database engine and session management.

Parallel to the sync ``database.py`` — uses ``create_async_engine``
so that FastAPI endpoints can ``await db.execute(...)`` without
blocking the event loop.

SQLite → aiosqlite (dev)
PostgreSQL → asyncpg (Railway/production)
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from functools import lru_cache
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.exc import SQLAlchemyError

from app.config import settings

logger = logging.getLogger("screener")


@lru_cache(maxsize=1)
def _get_async_engine():
    """Build and cache the async engine (lazy — avoids import-time side effects)."""
    db_url = settings.DATABASE_URL

    if not db_url:
        db_url = "sqlite+aiosqlite:///./trading_app.db"

    if db_url.startswith("sqlite") and not db_url.startswith("sqlite+aiosqlite"):
        db_url = db_url.replace("sqlite://", "sqlite+aiosqlite://", 1)
    elif db_url.startswith("postgresql://") or db_url.startswith("postgres://"):
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
        db_url = db_url.replace("postgres://", "postgresql+asyncpg://", 1)

    connect_args = {}
    if db_url.startswith("sqlite"):
        connect_args = {"check_same_thread": False}
    elif db_url.startswith("postgresql+asyncpg"):
        connect_args["connect_timeout"] = 10

    logger.info("Creating async engine for %s", db_url.split("://")[0] + "://...")
    engine_kwargs = dict(
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
        pool_timeout=30,
        connect_args=connect_args,
    )
    return create_async_engine(db_url, **engine_kwargs)


@lru_cache(maxsize=1)
def _get_async_session_factory():
    return async_sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=_get_async_engine(),
        class_=AsyncSession,
    )


_factory = _get_async_session_factory()


async def get_async_db() -> AsyncGenerator[Optional[AsyncSession], None]:
    """FastAPI dependency — yields an async DB session, closes on finish.

    Returns None when the database is unreachable (connection timeout, auth
    failure, etc.) so callers can respond with 503 instead of crashing.
    """
    try:
        async with _factory() as session:
            try:
                yield session
            finally:
                await session.close()
    except (SQLAlchemyError, asyncio.TimeoutError, OSError) as exc:
        logger.error("Async database unavailable: %s", exc)
        yield None
