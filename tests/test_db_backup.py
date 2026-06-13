"""Tests for daily DB backup — gzip JSONL export + rotation.

Uses an in-memory SQLite DB (SessionLocal monkeypatched) plus seeded rows
so the tests are deterministic and offline. Verifies: backup writes correct
per-table .jsonl.gz files, rotation deletes old files, and one failing table
does not abort the others.
"""
from __future__ import annotations

import gzip
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
import app.db.models  # noqa: F401 — register ORM tables on Base.metadata
from app.db.models import SignalHistory, TradeHistory


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _seed_signal(session, symbol: str, signal_type: str, signal: str, score: float):
    session.add(SignalHistory(
        symbol=symbol, signal_type=signal_type, signal=signal, score=score,
        price=100.0, created_at=datetime.now(timezone.utc),
    ))


def _seed_trade(session, symbol: str, side: str, qty: float, entry_price: float):
    session.add(TradeHistory(
        symbol=symbol, side=side, qty=qty, entry_price=entry_price,
        status="open", created_at=datetime.now(timezone.utc),
    ))


def _temp_sqlite_session(monkeypatch):
    """Return a sessionmaker bound to an in-memory SQLite DB.

    Creates all ORM tables and monkeypatches ``scripts.backup_db._get_session``
    to use this test session factory.
    """
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    TestSession = sessionmaker(bind=eng)

    import scripts.backup_db as bdb
    monkeypatch.setattr(bdb, "_get_session", lambda: TestSession())
    return TestSession


# ---------------------------------------------------------------------------
# test 1 — backup writes correct per-table .jsonl.gz
# ---------------------------------------------------------------------------

def test_backup_tables_writes_gzip_jsonl(tmp_path, monkeypatch):
    """Seed SignalHistory + TradeHistory rows, back them up, check output."""
    TestSession = _temp_sqlite_session(monkeypatch)

    sess = TestSession()
    _seed_signal(sess, "AAPL", "swing", "STRONG BUY", 72.5)
    _seed_signal(sess, "MSFT", "swing", "BUY", 58.0)
    _seed_trade(sess, "AAPL", "buy", 10.0, 150.0)
    sess.commit()
    sess.close()

    import scripts.backup_db as bdb
    out = str(tmp_path / "backups")
    result = bdb.backup_tables(out_dir=out)

    # Basic structure
    assert result["tables"]["SignalHistory"] == 2
    assert result["tables"]["TradeHistory"] == 1
    assert result["dir"] == out
    assert result["bytes"] > 0
    assert "errors" not in result

    # Files exist
    sig_file = Path(out) / f"SignalHistory_{result['date']}.jsonl.gz"
    trade_file = Path(out) / f"TradeHistory_{result['date']}.jsonl.gz"
    assert sig_file.exists()
    assert trade_file.exists()

    # SignalHistory: 2 lines, valid JSON, dates serialized as strings
    with gzip.open(sig_file, "rt", encoding="utf-8") as fh:
        lines = fh.readlines()
    assert len(lines) == 2
    for line in lines:
        row = json.loads(line)
        assert row["symbol"] in ("AAPL", "MSFT")
        assert "score" in row
        # Dates must be strings (not dicts or numbers)
        assert isinstance(row.get("created_at"), str)

    # TradeHistory: 1 line
    with gzip.open(trade_file, "rt", encoding="utf-8") as fh:
        lines = fh.readlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["symbol"] == "AAPL"
    assert row["side"] == "buy"
    assert isinstance(row.get("created_at"), str)


# ---------------------------------------------------------------------------
# test 2 — rotate_backups deletes old files, keeps new ones
# ---------------------------------------------------------------------------

def test_rotate_backups_deletes_old_keeps_new(tmp_path):
    import scripts.backup_db as bdb

    out = str(tmp_path / "backups")
    os.makedirs(out, exist_ok=True)

    # Create an "old" file with mtime set 20 days ago
    old_path = Path(out) / "SignalHistory_2025-01-01.jsonl.gz"
    old_path.write_text("dummy")
    old_ts = time.time() - (20 * 86400)
    os.utime(old_path, (old_ts, old_ts))

    # Create a "new" file with current mtime
    new_path = Path(out) / "TradeHistory_2026-06-12.jsonl.gz"
    new_path.write_text("dummy")

    deleted = bdb.rotate_backups(out_dir=out, keep_days=14)
    assert deleted == 1
    assert not old_path.exists()   # deleted
    assert new_path.exists()       # kept


# ---------------------------------------------------------------------------
# test 3 — one table erroring does not abort the others
# ---------------------------------------------------------------------------

def test_backup_tables_one_failing_table_continues(tmp_path, monkeypatch):
    """Monkeypatch TradeHistory query to raise; SignalHistory still backed up."""
    _temp_sqlite_session(monkeypatch)

    # Seed SignalHistory so it has real data
    import scripts.backup_db as bdb

    # Monkeypatch _table_to_dicts to raise for TradeHistory only.
    orig_table_to_dicts = bdb._table_to_dicts

    def _broken_table_to_dicts(table):
        if table is TradeHistory:
            raise RuntimeError("simulated query failure")
        return orig_table_to_dicts(table)

    monkeypatch.setattr(bdb, "_table_to_dicts", _broken_table_to_dicts)

    # Seed SignalHistory via the unbroken path
    sess = bdb._get_session()
    _seed_signal(sess, "AAPL", "swing", "STRONG BUY", 72.5)
    sess.commit()
    sess.close()

    out = str(tmp_path / "backups")
    result = bdb.backup_tables(out_dir=out)

    # SignalHistory succeeded
    assert result["tables"]["SignalHistory"] == 1
    # TradeHistory failed — count might be 0 or absent
    assert result.get("errors") is not None
    assert "TradeHistory" in result["errors"]
    assert "simulated query failure" in result["errors"]["TradeHistory"]

    # SignalHistory file must exist
    sig_file = Path(out) / f"SignalHistory_{result['date']}.jsonl.gz"
    assert sig_file.exists()

    # No exception escaped
