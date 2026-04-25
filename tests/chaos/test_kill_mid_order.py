"""Deterministic restart drill for the mid-order crash path."""

from __future__ import annotations

import sys
import types
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def engine(monkeypatch):
    from app import config as cfg

    monkeypatch.setattr(cfg.settings, "AUTO_TRADE_ENABLED", True)
    from app.services import trading_engine as te

    return te


def _install_temp_db(monkeypatch):
    from app.db import database as dbmod

    artifacts_dir = Path.cwd() / "test_artifacts"
    artifacts_dir.mkdir(exist_ok=True)
    db_path = artifacts_dir / f"chaos_{uuid.uuid4().hex}.db"
    test_engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    dbmod.Base.metadata.create_all(bind=test_engine)
    monkeypatch.setattr(dbmod, "SessionLocal", TestSessionLocal)
    return TestSessionLocal


def test_retry_after_mid_order_crash_does_not_duplicate(monkeypatch, engine):
    from app.db.models import TradeHistory

    fake_halal = types.ModuleType("halal_screener")
    fake_halal.verify_halal = lambda symbol: (True, "ok")
    monkeypatch.setitem(sys.modules, "halal_screener", fake_halal)

    TestSessionLocal = _install_temp_db(monkeypatch)
    broker_orders: dict[str, dict] = {}
    telegram_calls: list[str] = []

    monkeypatch.setattr(
        engine,
        "alpaca_get_account",
        lambda strategy_id=None: {
            "equity": 10_000,
            "cash": 10_000,
            "last_equity": 10_000,
            "trading_blocked": False,
            "account_blocked": False,
        },
    )
    monkeypatch.setattr(engine, "alpaca_get_positions", lambda strategy_id=None: [])
    monkeypatch.setattr(
        engine,
        "check_trade_eligibility",
        lambda **kwargs: {
            "eligible": True,
            "reason": "ok",
            "sizing": {
                "qty": 10,
                "position_value": 1_000.0,
                "risk_amount": 50.0,
                "risk_pct": 1.0,
                "regime": "NEUTRAL",
            },
            "guards": [{"name": "ok"}],
        },
    )
    monkeypatch.setattr(engine, "_arm_bracket", lambda order, strategy_id=None: True)
    monkeypatch.setattr(engine, "_notify_trade", lambda trade_result: None)
    monkeypatch.setattr(engine, "tg_send", lambda message, *args, **kwargs: telegram_calls.append(message) or True)

    def fake_submit(order_payload, strategy_id=None):
        coid = order_payload["client_order_id"]
        order = broker_orders.get(coid)
        if order is None:
            order = {
                "id": "broker-1",
                "client_order_id": coid,
                "symbol": order_payload["symbol"],
                "status": "filled",
                "filled_qty": order_payload["qty"],
                "filled_avg_price": "100.0",
                "order_class": order_payload.get("order_class"),
                "legs": [{"type": "stop"}, {"type": "limit"}],
            }
            broker_orders[coid] = order
        return dict(order)

    monkeypatch.setattr(engine, "_submit_order", fake_submit)

    signal_details = {
        "verdict": "STRONG BUY",
        "votes_buy": 9,
        "votes_sell": 0,
        "votes_hold": 0,
        "session_date": "2026-04-17",
    }

    real_record_trade = engine._record_trade
    crashed = {"done": False}

    def crash_once(trade_result, payload):
        if not crashed["done"]:
            crashed["done"] = True
            raise RuntimeError("simulated crash after broker accept")
        return real_record_trade(trade_result, payload)

    monkeypatch.setattr(engine, "_record_trade", crash_once)

    with pytest.raises(RuntimeError):
        engine.execute_buy(
            "AAPL",
            100.0,
            95.0,
            110.0,
            80.0,
            dict(signal_details),
            strategy_id="A",
        )

    monkeypatch.setattr(engine, "_record_trade", real_record_trade)

    retry = engine.execute_buy(
        "AAPL",
        100.0,
        95.0,
        110.0,
        80.0,
        dict(signal_details),
        strategy_id="A",
    )

    assert retry["executed"] is True
    assert len(broker_orders) == 1

    with TestSessionLocal() as db:
        rows = db.query(TradeHistory).all()
        assert len(rows) == 1
        assert rows[0].client_order_id == retry["client_order_id"]
        assert rows[0].status == "armed"

    monkeypatch.setattr(engine, "alpaca_get_positions", lambda strategy_id=None: [{"symbol": "AAPL"}])
    monkeypatch.setattr(engine, "alpaca_get_orders", lambda **kwargs: list(broker_orders.values()))

    summary = engine.reconcile_positions(strategy_id="A")

    with TestSessionLocal() as db:
        trade = db.query(TradeHistory).one()
        assert trade.status == "filled"
        assert trade.filled_qty == 10

    assert summary["orphan_positions"] == []
    assert summary["orphan_brackets"] == []
    assert len(telegram_calls) <= 1
