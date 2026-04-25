"""Tests for guard audit persistence."""
from __future__ import annotations

import uuid
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import database as dbmod
from app.db.models import Base, GuardLog
from app.services.guards import persist_results
from app.services.guards.base import GuardContext, GuardResult
from app.services.regime import RegimeSnapshot


def test_persist_results_writes_one_row_per_guard(monkeypatch):
    artifacts_dir = Path.cwd() / "test_artifacts"
    artifacts_dir.mkdir(exist_ok=True)
    db_path = artifacts_dir / f"guards_{uuid.uuid4().hex}.db"
    test_engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    Base.metadata.create_all(bind=test_engine)
    monkeypatch.setattr(dbmod, "SessionLocal", TestSessionLocal)

    ctx = GuardContext(
        strategy_id="A",
        symbol="AAPL",
        side="buy",
        price=100.0,
        stop_loss=95.0,
        qty=10,
        account={"equity": 10_000, "cash": 10_000},
        positions=[],
        regime=RegimeSnapshot(
            state="NEUTRAL",
            vix=20.0,
            vix_pctile=50.0,
            spy_ema_slope_pct=0.5,
            yield_spread_bps=10.0,
            computed_at=None,
            changed=False,
        ),
    )
    results = [
        GuardResult(name=f"guard_{idx}", passed=True, blocking=True, reason="ok", code=f"C{idx}")
        for idx in range(14)
    ]

    persist_results(ctx, results)

    with TestSessionLocal() as db:
        assert db.query(GuardLog).count() == 14
