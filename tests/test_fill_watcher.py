"""Tests for fill watcher DB reconciliation."""

from __future__ import annotations

import uuid
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def test_apply_fill_metrics_updates_trade_notional(monkeypatch):
    from app.db import database as dbmod
    from app.db.models import TradeHistory
    from app.services import fill_watcher as fw

    artifacts_dir = Path.cwd() / "test_artifacts"
    artifacts_dir.mkdir(exist_ok=True)
    db_path = artifacts_dir / f"fill_{uuid.uuid4().hex}.db"
    test_engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    dbmod.Base.metadata.create_all(bind=test_engine)
    monkeypatch.setattr(dbmod, "SessionLocal", TestSessionLocal)

    with TestSessionLocal() as db:
        db.add(
            TradeHistory(
                symbol="AAPL",
                side="buy",
                qty=10,
                entry_price=100.0,
                stop_loss=95.0,
                position_value=1_000.0,
                risk_amount=50.0,
                order_id="broker-1",
                client_order_id="coid-1",
                status="armed",
                strategy_id="A",
            )
        )
        db.commit()

    fw._update_trade_row(
        "A",
        {
            "id": "broker-1",
            "client_order_id": "coid-1",
            "status": "partially_filled",
            "filled_qty": "4",
            "filled_avg_price": "101.5",
        },
    )

    with TestSessionLocal() as db:
        trade = db.query(TradeHistory).one()
        assert trade.status == "partially_filled"
        assert trade.qty == 4
        assert trade.filled_qty == 4
        assert trade.entry_price == 101.5
        assert trade.position_value == 406.0
        assert trade.risk_amount == 26.0
