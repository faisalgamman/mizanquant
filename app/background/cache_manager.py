"""DB-backed cache manager for model results and signal history.

Provides functions to read/write cached results from the database,
complementing the in-memory caches with persistence across restarts.
"""

import json
import logging
from datetime import datetime, timedelta
from sqlalchemy.exc import SQLAlchemyError

from app.db.database import SessionLocal
from app.db.models import ModelResultsCache, SignalHistory

logger = logging.getLogger("screener")


def db_cache_get(cache_key: str) -> dict | None:
    """Retrieve a cached model result from the database.

    Returns None if the key doesn't exist or has expired.
    """
    try:
        db = SessionLocal()
        try:
            row = db.query(ModelResultsCache).filter(
                ModelResultsCache.cache_key == cache_key
            ).first()
            if row is None:
                return None
            # Check TTL
            age = (datetime.utcnow() - row.created_at).total_seconds()
            if age > row.ttl_seconds:
                return None
            return row.result_json
        finally:
            db.close()
    except SQLAlchemyError as e:
        logger.error(f"DB cache read error: {e}")
        return None


def db_cache_set(cache_key: str, model_type: str, result: list | dict, ttl: int = 3600):
    """Store a model result in the database cache.

    Upserts: updates if key exists, inserts otherwise.
    """
    try:
        db = SessionLocal()
        try:
            row = db.query(ModelResultsCache).filter(
                ModelResultsCache.cache_key == cache_key
            ).first()
            if row:
                row.result_json = result
                row.model_type = model_type
                row.created_at = datetime.utcnow()
                row.ttl_seconds = ttl
            else:
                row = ModelResultsCache(
                    cache_key=cache_key,
                    model_type=model_type,
                    result_json=result,
                    ttl_seconds=ttl,
                )
                db.add(row)
            db.commit()
        finally:
            db.close()
    except SQLAlchemyError as e:
        logger.error(f"DB cache write error: {e}")


def record_signal(
    symbol: str,
    signal_type: str,
    signal: str,
    score: float = 0,
    price: float = 0,
    stop_loss: float = 0,
    take_profit: float = 0,
    confidence: float = 0,
    details: dict | None = None,
):
    """Record a trading signal for auditing and accuracy tracking."""
    try:
        db = SessionLocal()
        try:
            row = SignalHistory(
                symbol=symbol,
                signal_type=signal_type,
                signal=signal,
                score=score,
                price=price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                confidence=confidence,
                details=details,
            )
            db.add(row)
            db.commit()
        finally:
            db.close()
    except SQLAlchemyError as e:
        logger.error(f"Signal record error: {e}")


def cleanup_expired_cache():
    """Remove expired entries from the model results cache."""
    try:
        db = SessionLocal()
        try:
            rows = db.query(ModelResultsCache).all()
            removed = 0
            for row in rows:
                age = (datetime.utcnow() - row.created_at).total_seconds()
                if age > row.ttl_seconds:
                    db.delete(row)
                    removed += 1
            db.commit()
            if removed:
                logger.info(f"Cleaned up {removed} expired cache entries.")
        finally:
            db.close()
    except SQLAlchemyError as e:
        logger.error(f"Cache cleanup error: {e}")
