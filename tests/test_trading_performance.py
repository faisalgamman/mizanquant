"""Regression tests for operator trading performance responses."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

import halal_screener as hs


def test_trading_performance_serializes_no_loss_case(monkeypatch):
    from app.db import database as dbmod
    from app.db.models import TradeHistory

    artifacts_dir = Path.cwd() / "test_artifacts"
    artifacts_dir.mkdir(exist_ok=True)
    db_path = artifacts_dir / f"performance_{uuid.uuid4().hex}.db"
    test_engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    dbmod.Base.metadata.create_all(bind=test_engine)
    monkeypatch.setattr(dbmod, "SessionLocal", TestSessionLocal)
    monkeypatch.setattr(hs.settings, "API_KEY", "secret")

    with TestSessionLocal() as db:
        db.add(
            TradeHistory(
                symbol="AAPL",
                side="buy",
                qty=5,
                entry_price=100.0,
                exit_price=105.0,
                pnl=25.0,
                pnl_pct=5.0,
                status="submitted",
                strategy_id="A",
            )
        )
        db.commit()

    client = TestClient(hs.app)
    response = client.get("/api/v1/trading/performance", headers={"X-API-Key": "secret"})

    assert response.status_code == 200
    body = response.json()
    assert body["profit_factor"] is None
    assert body["profit_factor_note"] == "No losing trades yet"
    assert body["total_trades"] == 1
