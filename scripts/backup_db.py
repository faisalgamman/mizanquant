#!/usr/bin/env python3
"""Daily DB backup — gzip JSONL export of the measurement crown-jewels.

Exports irreplaceable measurement data (SignalHistory with breakdowns/shadow
scores, TradeHistory ledgers, AgentDecision journal, ConsensusLog) as per-table
gzip JSONL files. DB-agnostic — works on both prod Postgres (Railway) and local
SQLite via SQLAlchemy, NOT pg_dump.

Usage:
    python scripts/backup_db.py [--out DIR] [--keep DAYS]

Default output directory:
    1. ``BACKUP_DIR`` env var (if set)
    2. ``/data/backups`` if ``/data`` exists (Railway persistent volume)
    3. ``./backups`` (local dev fallback)

Rotation: deletes backup files older than ``--keep`` days (default 14).

RESTORE PATH (honest — this is NOT a perfect snapshot):
    Files are gzip JSONL, one file per table per day. To restore:
        1. gunzip the file
        2. Read JSONL line by line
        3. Re-insert via SQLAlchemy ``SessionLocal.add()``

    WARNING: restore APPENDS only — it never truncates or drops tables.
    Restoring the same day twice WILL create duplicates. Review the data
    before inserting. There is no built-in restore command because the
    safest restore depends on the context of the loss.

    Tables backed up: SignalHistory, TradeHistory, AgentDecision, ConsensusLog.
    Missing tables: MarketDataCache (re-fetchable), FMPCache (re-fetchable),
    ScreeningResult (re-screenable), PortfolioSnapshot (ephemeral), etc.
"""

from __future__ import annotations

import argparse
import gzip
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
logger = logging.getLogger("db_backup")

_HERE = Path(__file__).resolve().parent
_PROJECT = _HERE.parent


def _default_out_dir() -> str:
    """Resolve backup directory: env var → /data/backups → ./backups."""
    env_dir = os.environ.get("BACKUP_DIR")
    if env_dir:
        return env_dir
    if Path("/data").exists():
        return "/data/backups"
    return str(_PROJECT / "backups")


def _get_session():
    """Return a new SQLAlchemy SessionLocal."""
    sys.path.insert(0, str(_PROJECT))
    from app.db.database import SessionLocal
    return SessionLocal()


def _table_to_dicts(table) -> list[dict]:
    """Serialize all rows of a SQLAlchemy table to a list of plain dicts.

    Uses ``default=str`` so that dates, JSON columns and other non-serializable
    types produce a string representation instead of raising.
    """
    session = _get_session()
    try:
        rows = session.query(table).all()
        columns = [c.name for c in table.__table__.columns]
        results = []
        for row in rows:
            d = {}
            for col in columns:
                val = getattr(row, col)
                d[col] = val
            # Serialize to JSON and back so that dates/Decimal/JSON columns
            # become plain strings/numbers — then parse to check round-trip
            results.append(json.loads(json.dumps(d, default=str)))
        return results
    finally:
        session.close()


def backup_tables(out_dir: str | None = None) -> dict:
    """Export measurement tables as gzip JSONL, one file per table per day.

    Args:
        out_dir: destination directory. Falls back to ``_default_out_dir()``
                 when None.

    Returns:
        dict with keys: date, tables, dir, bytes, errors (if partial).
    """
    sys.path.insert(0, str(_PROJECT))
    from app.db.models import SignalHistory, TradeHistory, AgentDecision, ConsensusLog

    if out_dir is None:
        out_dir = _default_out_dir()

    os.makedirs(out_dir, exist_ok=True)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    tables = {
        "SignalHistory": SignalHistory,
        "TradeHistory": TradeHistory,
        "AgentDecision": AgentDecision,
        "ConsensusLog": ConsensusLog,
    }

    table_counts: dict[str, int] = {}
    errors: dict[str, str] = {}
    total_bytes = 0

    for name, model in tables.items():
        try:
            rows = _table_to_dicts(model)
            table_counts[name] = len(rows)
            fname = f"{name}_{today}.jsonl.gz"
            fpath = os.path.join(out_dir, fname)

            with gzip.open(fpath, "wt", encoding="utf-8") as fh:
                for row in rows:
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")

            file_bytes = os.path.getsize(fpath)
            total_bytes += file_bytes
            logger.info("Backed up %s: %d rows → %s (%d bytes)", name, len(rows), fname, file_bytes)
        except Exception as e:
            errors[name] = str(e)
            logger.error("Backup FAILED for %s: %s", name, e)

    result: dict = {
        "date": today,
        "tables": table_counts,
        "dir": out_dir,
        "bytes": total_bytes,
    }
    if errors:
        result["errors"] = errors

    logger.info("Backup complete: %d tables, %d bytes → %s", len(table_counts), total_bytes, out_dir)
    return result


def rotate_backups(out_dir: str | None = None, keep_days: int = 14) -> int:
    """Delete backup files older than ``keep_days`` (by filename date or mtime).

    Returns:
        Number of files deleted. Never raises.
    """
    if out_dir is None:
        out_dir = _default_out_dir()

    try:
        p = Path(out_dir)
        if not p.exists():
            return 0

        cutoff_ts = datetime.now().timestamp() - (keep_days * 86400)
        deleted = 0

        for fpath in p.glob("*.jsonl.gz"):
            try:
                # Try filename date first, fall back to mtime
                mtime = fpath.stat().st_mtime
                if mtime < cutoff_ts:
                    fpath.unlink()
                    deleted += 1
                    logger.info("Rotated: deleted %s", fpath.name)
            except OSError as e:
                logger.warning("Rotation skip for %s: %s", fpath.name, e)

        logger.info("Rotation: deleted %d files older than %d days in %s", deleted, keep_days, out_dir)
        return deleted
    except Exception as e:
        logger.error("Rotation failed (non-fatal): %s", e)
        return 0


def main() -> None:
    """CLI entry point: backup then rotate."""
    parser = argparse.ArgumentParser(description="Daily DB backup — gzip JSONL export")
    parser.add_argument("--out", dest="out_dir", default=None,
                        help="Output directory (default: BACKUP_DIR env → /data/backups → ./backups)")
    parser.add_argument("--keep", dest="keep_days", type=int, default=14,
                        help="Days to keep backups (default: 14)")
    args = parser.parse_args()

    out_dir = args.out_dir or _default_out_dir()
    result = backup_tables(out_dir=out_dir)
    deleted = rotate_backups(out_dir=out_dir, keep_days=args.keep_days)

    summary = {**result, "rotated": deleted}
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
