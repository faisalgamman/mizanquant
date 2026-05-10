#!/usr/bin/env python3
"""Seed the ``Universe`` table from the curated symbol lists.

Usage::

    python scripts/seed_universe.py                   # seed from HALAL_STOCKS fallback
    python scripts/seed_universe.py --from-russell     # seed from russell1000_halal.py
    python scripts/seed_universe.py --dry-run          # print without inserting

Run this once after deploying the ``Universe`` table migration.
Subsequent runs upsert (update in-place, no duplicates).
"""

from __future__ import annotations

import argparse
import logging
import sys

from app.db.database import SessionLocal, init_db
from app.db.models import Universe
from app.services.universe import HALAL_STOCKS_FALLBACK, seed_from_fallback

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
logger = logging.getLogger("seed_universe")


def seed_from_russell(db):
    """Seed from calibration/stocks/russell1000_halal.py (curated ~120 symbols)."""
    sys.path.insert(0, ".")
    try:
        from calibration.stocks.russell1000_halal import RUSSELL_1000_HALAL
    except ImportError:
        logger.error("Could not import russell1000_halal — falling back to HALAL_STOCKS")
        return seed_from_fallback(db)

    count = 0
    for symbol in RUSSELL_1000_HALAL:
        existing = db.query(Universe).filter(Universe.symbol == symbol).first()
        if existing:
            existing.is_active = True
        else:
            db.add(Universe(symbol=symbol, is_active=True))
        count += 1
    db.commit()
    logger.info("Seeded %d symbols from russell1000_halal.py", count)


def main():
    parser = argparse.ArgumentParser(description="Seed Universe table")
    parser.add_argument("--from-russell", action="store_true",
                        help="Seed from russell1000_halal.py instead of HALAL_STOCKS")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would be done without inserting")
    args = parser.parse_args()

    init_db()
    db = SessionLocal()

    try:
        if args.dry_run:
            if args.from_russell:
                from calibration.stocks.russell1000_halal import RUSSELL_1000_HALAL
                symbols = set(RUSSELL_1000_HALAL)
            else:
                symbols = set(HALAL_STOCKS_FALLBACK)
            logger.info("DRY-RUN: would seed %d symbols", len(symbols))
            return

        if args.from_russell:
            seed_from_russell(db)
        else:
            seed_from_fallback(db)

        total = db.query(Universe).filter(Universe.is_active.is_(True)).count()
        logger.info("Universe table now has %d active symbols", total)
    finally:
        db.close()


if __name__ == "__main__":
    main()
