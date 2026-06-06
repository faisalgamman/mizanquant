"""Tests for the isolated paper-validation ledger.

Uses an in-memory SQLite DB (SessionLocal monkeypatched) plus an injected funnel
and price fetch, so it is deterministic and offline. Verifies: picks become OPEN
PV trades (deduped), and the Option-A maturation closes a stopped trade with a
written pnl_pct while leaving an immature trade open.
"""
from __future__ import annotations

from datetime import datetime

import pytest

pd = pytest.importorskip("pandas")
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.db.database import Base  # noqa: E402
import app.db.models  # noqa: E402,F401  — register ORM tables on Base.metadata
from app.db.models import TradeHistory  # noqa: E402
from app.services import paper_validation as pv  # noqa: E402


@pytest.fixture
def tdb(monkeypatch):
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    TestSession = sessionmaker(bind=eng)
    monkeypatch.setattr(pv, "SessionLocal", TestSession)
    return TestSession


def _pick(sym, entry=100.0, shares=10, verdict="BUY", conf=60.0):
    return {
        "symbol": sym, "entry": entry, "catastrophe_stop": round(entry * 0.85, 2),
        "far_take_profit": round(entry * 1.45, 2), "shares": shares,
        "position_value": round(shares * entry, 2), "risk_amount": round(shares * entry * 0.15, 2),
        "risk_pct_realized": 1.5, "confidence": conf, "verdict": verdict,
        "hold_days": 20, "stop_pct": 15.0, "time_exit_date": "2026-07-06", "votes": "8/0/5",
    }


def test_paper_row_from_pick():
    row = pv._paper_row_from_pick(_pick("AAA", entry=100.0, shares=10))
    assert row["strategy_id"] == "PV" and row["side"] == "buy"
    assert row["stop_loss"] == 85.0 and row["qty"] == 10.0
    assert row["status"] == "open" and row["signal_details"]["source"] == "paper_validation"


def test_record_inserts_open_and_dedups(tdb, monkeypatch):
    report = {"picks": [_pick("AAA"), _pick("BBB"), _pick("CCC", shares=0)]}
    monkeypatch.setattr("app.services.weekly_report.build_weekly_report", lambda *a, **k: report)

    r1 = pv.record_weekly_picks(account=10000)
    assert r1["recorded"] == 2 and r1["skipped"] == 1   # CCC has 0 shares → skipped

    r2 = pv.record_weekly_picks(account=10000)            # AAA/BBB still open → deduped
    assert r2["recorded"] == 0

    db = tdb()
    try:
        n_open = db.query(TradeHistory).filter(
            TradeHistory.strategy_id == "PV", TradeHistory.pnl_pct.is_(None)).count()
        assert n_open == 2
        t = db.query(TradeHistory).filter(TradeHistory.symbol == "AAA").first()
        assert t.status == "open" and t.pnl_pct is None and t.stop_loss == 85.0
    finally:
        db.close()


def _seed_open(tdb, symbol="AAA", entry=100.0, qty=10):
    db = tdb()
    try:
        db.add(TradeHistory(strategy_id="PV", symbol=symbol, side="buy", qty=qty,
                            entry_price=entry, stop_loss=round(entry * 0.85, 2), status="open",
                            created_at=datetime(2024, 1, 1)))
        db.commit()
    finally:
        db.close()


def test_mature_closes_stopped_trade(tdb, monkeypatch):
    _seed_open(tdb, "AAA", entry=100.0, qty=10)
    idx = pd.date_range("2024-01-02", periods=5, freq="D")
    bars = pd.DataFrame({"low": [99, 84, 90, 90, 90], "high": [101, 100, 99, 96, 97],
                         "close": [99, 80, 90, 90, 95]}, index=idx)  # day2 low 84 <= 85 → stop
    monkeypatch.setattr("app.services.market_data.fetch", lambda *a, **k: bars)

    out = pv.mature_open_paper_trades()
    assert out["closed"] == 1

    db = tdb()
    try:
        t = db.query(TradeHistory).filter(TradeHistory.symbol == "AAA").first()
        assert t.status == "closed"
        assert t.pnl_pct == -15.0 and t.exit_price == 85.0
        assert t.pnl == round((85.0 - 100.0) * 10, 2) and t.closed_at is not None
    finally:
        db.close()


def test_mature_leaves_immature_open(tdb, monkeypatch):
    _seed_open(tdb, "BBB", entry=100.0, qty=10)
    idx = pd.date_range("2024-01-02", periods=5, freq="D")
    bars = pd.DataFrame({"low": [99, 98, 99, 100, 101], "high": [101, 102, 103, 104, 105],
                         "close": [100, 101, 102, 103, 104]}, index=idx)  # no stop, < 20 days
    monkeypatch.setattr("app.services.market_data.fetch", lambda *a, **k: bars)

    out = pv.mature_open_paper_trades()
    assert out["closed"] == 0

    db = tdb()
    try:
        t = db.query(TradeHistory).filter(TradeHistory.symbol == "BBB").first()
        assert t.status == "open" and t.pnl_pct is None
    finally:
        db.close()
