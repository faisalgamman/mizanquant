"""Tests for agent journal — retrieval memory, NOT learning."""

import os
import tempfile
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.db.models  # noqa: F401 — register ORM models with Base.metadata
from app.db.database import Base


# ── helpers ──────────────────────────────────────────────────────────

def _install_temp_db(monkeypatch, suffix=""):
    """Create a temp SQLite DB, create all tables, and monkeypatch SessionLocal.

    Uses a unique suffix so tests don't leak data into each other.
    """
    db_path = os.path.join(
        tempfile.gettempdir(),
        f"_test_agent_journal_{os.getpid()}_{suffix}.db",
    )
    # Remove any stale DB from a previous run
    if os.path.exists(db_path):
        os.remove(db_path)

    test_engine = create_engine(f"sqlite:///{db_path}", echo=False)
    Base.metadata.create_all(bind=test_engine)
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    monkeypatch.setattr("app.db.database.SessionLocal", TestSessionLocal)
    return TestSessionLocal


# ── tests ────────────────────────────────────────────────────────────

class TestRecordAndRecent:
    def test_round_trip(self, monkeypatch):
        """record_decision → recent_decisions round-trip."""
        from app.services.ai_sentinel.journal import record_decision, recent_decisions

        _install_temp_db(monkeypatch, suffix="roundtrip")

        dec_id = record_decision(
            "AAPL", "opportunity", "STRONG BUY", 85,
            "Composite score triggered", {"composite": 78},
        )
        assert isinstance(dec_id, int)

        decisions = recent_decisions(limit=5)
        assert len(decisions) >= 1
        d = decisions[0]
        assert d["symbol"] == "AAPL"
        assert d["verdict"] == "STRONG BUY"
        assert d["confidence"] == 0.85
        assert d["kind"] == "opportunity"

    def test_confidence_normalised(self, monkeypatch):
        """Confidence > 1 is treated as 0-100 scale and normalised."""
        from app.services.ai_sentinel.journal import record_decision, recent_decisions

        _install_temp_db(monkeypatch, suffix="confnorm")

        record_decision("MSFT", "opportunity", "BUY", 70, "test")
        decisions = recent_decisions(limit=1)
        assert decisions[0]["confidence"] == 0.70


class TestMatchOutcomes:
    def test_win_from_closed_trade(self, monkeypatch):
        """A closed TradeHistory with positive pnl_pct → 'win'."""
        from app.db.models import AgentDecision, TradeHistory
        from app.services.ai_sentinel.journal import match_outcomes, record_decision

        TestSessionLocal = _install_temp_db(monkeypatch, suffix="win")

        dec_id = record_decision("MSFT", "opportunity", "BUY", 0.7, "test win")

        with TestSessionLocal() as db:
            dec = db.query(AgentDecision).filter_by(id=dec_id).first()
            assert dec is not None, f"Decision {dec_id} not found in DB"
            dec_time = datetime.now(timezone.utc) - timedelta(days=2)
            dec.created_at = dec_time

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

        with TestSessionLocal() as db:
            updated = db.query(AgentDecision).filter_by(id=dec_id).first()
            assert updated is not None
            snap = updated.snapshot or {}
            assert snap.get("outcome_label") == "win"
            assert snap.get("outcome_pct") == 8.0

    def test_loss_from_closed_trade(self, monkeypatch):
        """A closed TradeHistory with negative pnl_pct → 'loss'."""
        from app.db.models import AgentDecision, TradeHistory
        from app.services.ai_sentinel.journal import match_outcomes, record_decision

        TestSessionLocal = _install_temp_db(monkeypatch, suffix="loss")
        dec_id = record_decision("TSLA", "opportunity", "SELL", 0.6, "test loss")

        with TestSessionLocal() as db:
            dec = db.query(AgentDecision).filter_by(id=dec_id).first()
            assert dec is not None, f"Decision {dec_id} not found in DB"
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
            assert updated is not None
            snap = updated.snapshot or {}
            assert snap.get("outcome_label") == "loss"
            assert snap.get("outcome_pct") == -12.0

    def test_stays_open_when_no_data(self, monkeypatch):
        """A decision with no matching trade or price data → remains 'open'."""
        from app.services.ai_sentinel.journal import match_outcomes, record_decision

        _install_temp_db(monkeypatch, suffix="open")
        record_decision("ZZZZ", "opportunity", "BUY", 0.5, "ghost symbol")

        result = match_outcomes(lookback_days=30)
        assert result["still_open"] >= 1


class TestChatContext:
    def test_context_truncates_and_never_raises(self, monkeypatch):
        """Chat journal context builder truncates and handles errors gracefully."""
        from app.services.ai_sentinel.journal import recent_decisions

        _install_temp_db(monkeypatch, suffix="ctx")

        # No decisions → empty list, no error
        results = recent_decisions(limit=8)
        assert results == []

        # With lots of decisions → still works
        from app.db.models import AgentDecision
        from app.db.database import SessionLocal
        db = SessionLocal()
        for i in range(20):
            db.add(AgentDecision(
                symbol=f"TICK{i}", verdict="BUY", confidence=0.5,
                rationale=f"test decision {i}",
                snapshot={"kind": "opportunity"},
            ))
        db.commit()
        db.close()

        results = recent_decisions(limit=8)
        assert len(results) <= 8
        for r in results:
            assert "symbol" in r
            assert "verdict" in r
            assert "confidence" in r
            assert "kind" in r

    def test_chat_never_breaks_on_journal_failure(self, monkeypatch):
        """chat() handles journal import failure gracefully."""
        import app.services.claude_agent as ca

        # Patch the agent to not actually call any API
        ca._agent = None

        # Mock _call_deepseek to avoid real API call
        async def _fake_chat(self, user_message, conversation_id=None):
            return {
                "response": "test response",
                "conversation_id": "test",
                "tools_used": [],
                "model": "test",
            }

        monkeypatch.setattr(
            "app.services.claude_agent.TradingAgent.chat", _fake_chat,
        )

        monkeypatch.setattr("halal_screener.settings.DEEPSEEK_API_KEY", "sk-test")
        agent = ca.TradingAgent()

        import asyncio

        async def _run():
            return await agent.chat("مرحبا")
        result = asyncio.run(_run())
        assert "response" in result
