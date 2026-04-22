"""Tests for consensus drift auditing."""

from __future__ import annotations

import uuid
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, ConsensusLog
from scripts import audit_signals


def test_recent_signal_distribution_uses_consensus_log(monkeypatch):
    artifacts_dir = Path.cwd() / "test_artifacts"
    artifacts_dir.mkdir(exist_ok=True)
    db_path = artifacts_dir / f"audit_{uuid.uuid4().hex}.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(audit_signals, "SessionLocal", SessionLocal)

    with SessionLocal() as db:
        db.add_all(
            [
                ConsensusLog(symbol="AAPL", profile="momentum", verdict="BUY"),
                ConsensusLog(symbol="MSFT", profile="momentum", verdict="BUY"),
                ConsensusLog(symbol="NVDA", profile="momentum", verdict="SELL"),
                ConsensusLog(symbol="AAPL", profile="ml", verdict="STRONG BUY"),
            ]
        )
        db.commit()

    dist = audit_signals.recent_signal_distribution(days=30, profile="momentum")

    assert dist["BUY"] == 66.67
    assert dist["SELL"] == 33.33


def test_detect_drift_only_returns_large_buckets():
    actual = {"BUY": 70.0, "SELL": 30.0}
    expected = {"BUY": 50.0, "SELL": 45.0, "NEUTRAL": 5.0}

    drift = audit_signals.detect_drift(actual, expected, threshold=15.0)

    assert drift == {"BUY": 20.0}


def test_audit_collapses_live_verdicts_and_notifies(monkeypatch):
    artifacts_dir = Path.cwd() / "test_artifacts"
    artifacts_dir.mkdir(exist_ok=True)
    db_path = artifacts_dir / f"audit_{uuid.uuid4().hex}.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(audit_signals, "SessionLocal", SessionLocal)

    with SessionLocal() as db:
        db.add_all(
            [
                ConsensusLog(symbol="AAPL", profile="momentum", verdict="STRONG BUY"),
                ConsensusLog(symbol="MSFT", profile="momentum", verdict="BUY"),
                ConsensusLog(symbol="NVDA", profile="momentum", verdict="NEUTRAL"),
            ]
        )
        db.commit()

    sent = {}
    monkeypatch.setattr(
        audit_signals,
        "notify",
        lambda event_type, dedup_key="", message="", **kwargs: sent.update(
            {"event_type": event_type, "dedup_key": dedup_key, "message": message}
        )
        or True,
    )

    result = audit_signals.audit(
        {"momentum": {"BUY": 20.0, "SELL": 20.0, "NEUTRAL": 60.0}},
        days=30,
        threshold=15.0,
    )

    assert result["actual"]["momentum"] == {"BUY": 66.67, "NEUTRAL": 33.33}
    assert result["drift"]["momentum"]["BUY"] == 46.67
    assert sent["event_type"] == "critical_error"
    assert sent["dedup_key"] == "signal-drift"
