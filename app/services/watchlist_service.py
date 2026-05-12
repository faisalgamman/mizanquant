"""Watchlist service — persist user watchlist to the database."""
from __future__ import annotations

import logging

from sqlalchemy import select

from app.db.database import SessionLocal
from app.db.models import Watchlist

logger = logging.getLogger("screener")


def get_watchlist() -> list[str]:
    """Return list of symbols in watchlist."""
    try:
        db = SessionLocal()
        rows = db.execute(select(Watchlist.symbol).order_by(Watchlist.created_at)).scalars().all()
        db.close()
        return rows or []
    except Exception:
        return []


def set_watchlist(symbols: list[str]) -> list[str]:
    """Replace entire watchlist. Returns saved list."""
    clean = list(dict.fromkeys(s.strip().upper() for s in symbols if s.strip()))
    try:
        db = SessionLocal()
        db.query(Watchlist).delete()
        for sym in clean:
            db.add(Watchlist(symbol=sym))
        db.commit()
        db.close()
    except Exception as e:
        logger.error("Failed to save watchlist: %s", e)
    return clean


def add_symbol(symbol: str) -> list[str]:
    """Add a symbol to watchlist. Returns updated list."""
    sym = symbol.strip().upper()
    if not sym:
        return get_watchlist()
    try:
        db = SessionLocal()
        existing = db.execute(select(Watchlist).where(Watchlist.symbol == sym)).scalar_one_or_none()
        if not existing:
            db.add(Watchlist(symbol=sym))
            db.commit()
            logger.info("Added %s to watchlist", sym)
        db.close()
    except Exception as e:
        logger.error("Failed to add %s to watchlist: %s", sym, e)
    return get_watchlist()


def remove_symbol(symbol: str) -> list[str]:
    """Remove a symbol from watchlist. Returns updated list."""
    sym = symbol.strip().upper()
    try:
        db = SessionLocal()
        row = db.execute(select(Watchlist).where(Watchlist.symbol == sym)).scalar_one_or_none()
        if row:
            db.delete(row)
            db.commit()
            logger.info("Removed %s from watchlist", sym)
        db.close()
    except Exception as e:
        logger.error("Failed to remove %s from watchlist: %s", sym, e)
    return get_watchlist()


def get_watchlist_set() -> set[str]:
    """Return watchlist as a set for fast lookup."""
    return set(get_watchlist())