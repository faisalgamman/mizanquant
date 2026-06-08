"""Tests for ai_sentinel journal — honest retrieval memory, not learning."""

import os
import tempfile
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.db.models  # noqa: F401 — register ORM models with Base.metadata
from app.db.database import Base


# ── helpers ────────────────────────────────────────────────────────


def _install_temp_db(monkeypatch, extra_bases=None):
    """Create a temp SQLite DB, create all tables, and monkeypatch SessionLocal.

    Pattern matches existing tests (test_guard_audit, test_trading_engine_entry, etc.).
    """
    db_path = os.path.join(tempfile.gettempdir(), f"_test_journal_{os.getpid()}.db")
    test_engine = create_engine(f"sqlite:///{db_path}", echo=False)
    Base.metadata.create_all(bind=test_engine)
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    monkeypatch.setattr("app.db.database.SessionLocal", TestSessionLocal)
    return TestSessionLocal


# ── tests ───────────────────────────────────────────────────────────


class TestRecordAndRecent:
    def test_round_trip(self, monkeypatch):
        from app.services.ai_sentinel.journal import record_decision, recent_decisions

        _install_temp_db(monkeypatch)

        rec = record_decision(
            "AAPL", "opportunity", "STRONG BUY", 0.85,
            "Composite score triggered", {"composite": 78},
        )
        assert rec is not None
        assert isinstance(rec, int)

        decisions = recent_decisions(limit=5)
        assert len(decisions) >= 1
        d = decisions[-1]
        assert d["symbol"] == "AAPL"
        assert d["verdict"] == "STRONG BUY"
        assert d["confidence"] == 0.85
        assert d["kind"] == "opportunity"


class TestMatchOutcomes:
    def test_win_from_closed_trade(self, monkeypatch):
        """A closed TradeHistory with positive pnl_pct => 'win'."""
        from app.db.models import AgentDecision, TradeHistory
        from app.services.ai_sentinel.journal import (
            match_outcomes, record_decision,
        )

        TestSessionLocal = _install_temp_db(monkeypatch)

        # Insert a decision.
        dec_id = record_decision(
            "MSFT", "opportunity", "BUY", 0.7, "test win",
        )

        # Write decision timestamp explicitly to control ordering.
        with TestSessionLocal() as db:
            dec = db.query(AgentDecision).filter_by(id=dec_id).first()
            dec_time = datetime.now(timezone.utc) - timedelta(days=2)
            dec.created_at = dec_time

            # Insert a closed trade AFTER the decision with positive P&L.
            trade = TradeHistory(
                symbol="MSFT",
                side="buy",
                status="closed",
                pnl_pct=8.0,
                created_at=dec_time + timedelta(days=1),
            )
            db.add(trade)
            db.commit()

        result = match_outcomes(lookback_days=10)
        assert result["matched"] >= 1

        # Read back the decision.
        with TestSessionLocal() as db:
            updated = db.query(AgentDecision).filter_by(id=dec_id).first()
            snap = updated.snapshot or {}
            assert snap.get("outcome_label") == "win"
            assert snap.get("outcome_pct") == 8.0

    def test_loss_from_closed_trade(self, monkeypatch):
        """A closed TradeHistory with negative pnl_pct => 'loss'."""
        from app.db.models import AgentDecision, TradeHistory
        from app.services.ai_sentinel.journal import (
            match_outcomes, record_decision,
        )

        TestSessionLocal = _install_temp_db(monkeypatch)
        dec_id = record_decision(
            "TSLA", "opportunity", "SELL", 0.6, "test loss",
        )

        with TestSessionLocal() as db:
            dec = db.query(AgentDecision).filter_by(id=dec_id).first()
            dec_time = datetime.now(timezone.utc) - timedelta(days=3)
            dec.created_at = dec_time

            trade = TradeHistory(
                symbol="TSLA",
                side="buy",
                status="closed",
                pnl_pct=-12.0,
                created_at=dec_time + timedelta(days=2),
            )
            db.add(trade)
            db.commit()

        result = match_outcomes(lookback_days=10)
        assert result["matched"] >= 1

        with TestSessionLocal() as db:
            updated = db.query(AgentDecision).filter_by(id=dec_id).first()
            snap = updated.snapshot or {}
            assert snap.get("outcome_label") == "loss"
            assert snap.get("outcome_pct") == -12.0

    def test_stays_open_when_no_data(self, monkeypatch):
        """A decision with no matching trade or price data => remains 'open'."""
        from app.services.ai_sentinel.journal import (
            match_outcomes, record_decision,
        )

        _install_temp_db(monkeypatch)
        _dec_id = record_decision(
            "ZZZZ", "opportunity", "BUY", 0.5, "ghost symbol",
        )

        # No trades, and market_data.fetch will fail for ZZZZ.
        result = match_outcomes(lookback_days=30)
        assert result["still_open"] >= 1
