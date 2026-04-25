"""Smoke tests for trading_engine.execute_buy guardrails (M1).

These tests do NOT hit the real Alpaca API — they stub all broker/DB I/O.
Their purpose is to verify that the validator/halal/auto-trade gates reject
bad inputs BEFORE any broker contact.
"""
from __future__ import annotations

import types
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def engine(monkeypatch):
    from app import config as cfg
    monkeypatch.setattr(cfg.settings, "AUTO_TRADE_ENABLED", True)
    from app.services import trading_engine as te
    return te


def _halal_ok(sym):
    return True, "ok"


def _halal_no(sym):
    return False, "interest-bearing debt"


def test_blocks_when_auto_trade_disabled(monkeypatch):
    from app import config as cfg
    monkeypatch.setattr(cfg.settings, "AUTO_TRADE_ENABLED", False)
    from app.services import trading_engine as te
    r = te.execute_buy("AAPL", 100.0, 95.0, 110.0, 80.0, {})
    assert r["executed"] is False
    assert "Auto-trading is disabled" in r["reason"]


def test_rejects_invalid_stop(engine, monkeypatch):
    fake_halal = types.ModuleType("halal_screener")
    fake_halal.verify_halal = _halal_ok
    monkeypatch.setitem(__import__("sys").modules, "halal_screener", fake_halal)

    # stop_loss > price -> should be rejected by validators BEFORE broker contact
    with patch.object(engine, "_submit_order") as submit, \
         patch.object(engine, "alpaca_get_account") as acct, \
         patch.object(engine, "alpaca_get_positions", return_value=[]):
        r = engine.execute_buy("AAPL", 100.0, 110.0, 120.0, 80.0, {})
        assert r["executed"] is False
        assert "VALIDATION" in r["reason"]
        assert "SL_ABOVE_PRICE" in r["reason"]
        submit.assert_not_called()
        acct.assert_not_called()


def test_rejects_malformed_symbol(engine, monkeypatch):
    fake_halal = types.ModuleType("halal_screener")
    fake_halal.verify_halal = _halal_ok
    monkeypatch.setitem(__import__("sys").modules, "halal_screener", fake_halal)
    with patch.object(engine, "_submit_order") as submit:
        r = engine.execute_buy("aapl123", 100.0, 95.0, 110.0, 80.0, {})
        assert not r["executed"]
        assert "SYMBOL_MALFORMED" in r["reason"]
        submit.assert_not_called()


def test_lock_creation_is_thread_safe(engine):
    """The double-checked locking fix ensures we always get the same Lock."""
    import threading
    got: list = []
    barrier = threading.Barrier(20)

    def grab():
        barrier.wait()
        got.append(engine._get_trade_lock("NEW_STRAT"))

    threads = [threading.Thread(target=grab) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len({id(x) for x in got}) == 1  # all threads see the same lock object


def test_client_order_id_attached_on_retry(engine):
    """Simulate a 422 duplicate-coid response and verify we fetch instead."""
    payload = {"symbol": "AAPL", "side": "buy", "qty": "10"}

    fake_resp_duplicate = types.SimpleNamespace(
        status_code=422,
        text='{"message":"client_order_id already exists"}',
        json=lambda: {"message": "client_order_id already exists"},
    )
    existing_order = {"id": "broker-1", "status": "filled", "symbol": "AAPL"}

    class DummyClient:
        def __init__(self, timeout=None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, headers=None, json=None):
            return fake_resp_duplicate

        def get(self, *a, **kw):
            return types.SimpleNamespace(status_code=200, json=lambda: existing_order)

    with patch("app.services.trading_engine.httpx.Client", DummyClient):
        with patch.object(engine, "_get_headers", return_value={"APCA-API-KEY-ID": "x", "APCA-API-SECRET-KEY": "y"}):
            out = engine._submit_order(payload, strategy_id="A")
            assert out is not None
            assert out["id"] == "broker-1"


def test_stable_client_order_id_replays_same_signal(engine):
    signal_details = {
        "verdict": "STRONG BUY",
        "votes_buy": 9,
        "votes_sell": 1,
        "votes_hold": 0,
        "session_date": "2026-04-17",
    }

    first = engine._stable_client_order_id(
        "A",
        "AAPL",
        "buy",
        price=100.0,
        stop_loss=95.0,
        take_profit=110.0,
        confidence=80.0,
        signal_details=signal_details,
    )
    second = engine._stable_client_order_id(
        "A",
        "AAPL",
        "buy",
        price=100.0,
        stop_loss=95.0,
        take_profit=110.0,
        confidence=80.0,
        signal_details=signal_details,
    )

    assert first == second
    assert first.startswith("A-AAPL-B-")


def test_reconcile_matches_by_client_order_id_without_false_orphan(engine, monkeypatch):
    from app.db import database as dbmod
    from app.db.models import TradeHistory

    artifacts_dir = Path.cwd() / "test_artifacts"
    artifacts_dir.mkdir(exist_ok=True)
    db_path = artifacts_dir / f"reconcile_{uuid.uuid4().hex}.db"
    test_engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    dbmod.Base.metadata.create_all(bind=test_engine)
    monkeypatch.setattr(dbmod, "SessionLocal", TestSessionLocal)

    with TestSessionLocal() as db:
        db.add(TradeHistory(
            symbol="AAPL",
            side="buy",
            qty=10,
            entry_price=100.0,
            order_id="",
            status="submitted",
            signal_details={"client_order_id": "coid-123"},
            strategy_id="A",
        ))
        db.commit()

    monkeypatch.setattr(engine, "alpaca_get_positions", lambda strategy_id=None: [{"symbol": "AAPL"}])
    monkeypatch.setattr(engine, "alpaca_get_orders", lambda **kwargs: [{
        "id": "broker-1",
        "symbol": "AAPL",
        "status": "filled",
        "client_order_id": "coid-123",
        "filled_qty": "7",
        "filled_avg_price": "101.25",
        "order_class": "bracket",
        "legs": [{"type": "stop"}, {"type": "limit"}],
    }])

    with patch.object(engine, "tg_send") as tg_send:
        summary = engine.reconcile_positions(strategy_id="A")

    with TestSessionLocal() as db:
        trade = db.query(TradeHistory).one()
        assert trade.status == "filled"
        assert trade.qty == 7
        assert trade.entry_price == 101.25

    assert summary["orphan_positions"] == []
    assert summary["orphan_brackets"] == []
    tg_send.assert_not_called()


def test_reconcile_updates_armed_trade_rows(engine, monkeypatch):
    from app.db import database as dbmod
    from app.db.models import TradeHistory

    artifacts_dir = Path.cwd() / "test_artifacts"
    artifacts_dir.mkdir(exist_ok=True)
    db_path = artifacts_dir / f"reconcile_armed_{uuid.uuid4().hex}.db"
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
                order_id="",
                client_order_id="coid-armed",
                status="armed",
                signal_details={"client_order_id": "coid-armed"},
                strategy_id="A",
            )
        )
        db.commit()

    monkeypatch.setattr(engine, "alpaca_get_positions", lambda strategy_id=None: [{"symbol": "AAPL"}])
    monkeypatch.setattr(
        engine,
        "alpaca_get_orders",
        lambda **kwargs: [
            {
                "id": "broker-armed",
                "symbol": "AAPL",
                "status": "filled",
                "client_order_id": "coid-armed",
                "filled_qty": "10",
                "filled_avg_price": "99.5",
                "order_class": "bracket",
                "legs": [{"type": "stop"}, {"type": "limit"}],
            }
        ],
    )

    summary = engine.reconcile_positions(strategy_id="A")

    with TestSessionLocal() as db:
        trade = db.query(TradeHistory).one()
        assert trade.status == "filled"
        assert trade.filled_qty == 10
        assert trade.filled_avg_price == 99.5

    assert summary["updated"] == 1
