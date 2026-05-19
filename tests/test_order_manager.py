"""Tests for OrderManager — 35+ tests covering full lifecycle."""
from __future__ import annotations

import threading
import time
from unittest.mock import patch, MagicMock, PropertyMock

import pytest
from app.services.order_state import (
    OrderRecord,
    OrderState,
    can_transition,
    assert_transition,
    is_active,
    is_terminal,
    is_cancelable,
    ACTIVE_STATES,
    TERMINAL_STATES,
    CANCELABLE_STATES,
)
from app.services.order_store import OrderStore
from app.services.order_manager import OrderManager, get_order_manager

# ══════════════════════════════════════════════════════════════════
# OrderState tests
# ══════════════════════════════════════════════════════════════════

def test_valid_transitions():
    assert can_transition(OrderState.CREATED, OrderState.SUBMITTED)
    assert can_transition(OrderState.SUBMITTED, OrderState.ACKNOWLEDGED)
    assert can_transition(OrderState.ACKNOWLEDGED, OrderState.PARTIALLY_FILLED)
    assert can_transition(OrderState.PARTIALLY_FILLED, OrderState.FILLED)
    assert can_transition(OrderState.SUBMITTED, OrderState.CANCELED)

def test_invalid_transitions():
    assert not can_transition(OrderState.CREATED, OrderState.FILLED)
    assert not can_transition(OrderState.FILLED, OrderState.SUBMITTED)
    assert not can_transition(OrderState.CANCELED, OrderState.SUBMITTED)
    assert not can_transition(OrderState.REJECTED, OrderState.SUBMITTED)
    assert not can_transition(OrderState.FILLED, OrderState.CANCELED)

def test_assert_transition_raises():
    with pytest.raises(ValueError, match="Invalid order state transition"):
        assert_transition(OrderState.FILLED, OrderState.SUBMITTED)

def test_assert_transition_valid():
    assert_transition(OrderState.CREATED, OrderState.SUBMITTED)

def test_active_states():
    assert is_active(OrderState.SUBMITTED)
    assert is_active(OrderState.ACKNOWLEDGED)
    assert is_active(OrderState.PARTIALLY_FILLED)
    assert not is_active(OrderState.FILLED)
    assert not is_active(OrderState.CANCELED)
    assert not is_active(OrderState.CREATED)

def test_terminal_states():
    assert is_terminal(OrderState.FILLED)
    assert is_terminal(OrderState.CANCELED)
    assert is_terminal(OrderState.REJECTED)
    assert is_terminal(OrderState.EXPIRED)
    assert not is_terminal(OrderState.CREATED)
    assert not is_terminal(OrderState.SUBMITTED)

def test_cancelable_states():
    assert is_cancelable(OrderState.SUBMITTED)
    assert is_cancelable(OrderState.ACKNOWLEDGED)
    assert is_cancelable(OrderState.PARTIALLY_FILLED)
    assert not is_cancelable(OrderState.FILLED)
    assert not is_cancelable(OrderState.CREATED)

# ══════════════════════════════════════════════════════════════════
# OrderRecord tests
# ══════════════════════════════════════════════════════════════════

def test_order_record_defaults():
    o = OrderRecord(order_id="", client_order_id="coid-1", symbol="AAPL",
                    side="buy", order_type="market", time_in_force="gtc",
                    qty=10)
    assert o.status == OrderState.CREATED
    assert o.is_buy
    assert not o.is_sell
    assert o.remaining_qty == 10
    assert o.fill_ratio == 0.0

def test_order_record_is_sell():
    o = OrderRecord(order_id="", client_order_id="coid-2", symbol="TSLA",
                    side="sell", order_type="market", time_in_force="gtc",
                    qty=5)
    assert o.is_sell
    assert not o.is_buy

def test_order_record_fill_properties():
    o = OrderRecord(order_id="b1", client_order_id="c1", symbol="AAPL",
                    side="buy", order_type="market", time_in_force="gtc",
                    qty=100, filled_qty=75)
    assert o.remaining_qty == 25
    assert o.fill_ratio == 0.75

def test_order_record_to_dict():
    o = OrderRecord(order_id="b1", client_order_id="c1", symbol="MSFT",
                    side="buy", order_type="limit", time_in_force="gtc",
                    qty=50, limit_price=400.0, status=OrderState.SUBMITTED,
                    strategy_id="A")
    d = o.to_dict()
    assert d["symbol"] == "MSFT"
    assert d["status"] == "submitted"
    assert d["limit_price"] == 400.0
    assert d["strategy_id"] == "A"

def test_order_record_from_dict_roundtrip():
    original = OrderRecord(order_id="b1", client_order_id="c1", symbol="GOOG",
                           side="sell", order_type="stop", time_in_force="day",
                           qty=20, stop_price=150.0, status=OrderState.ACKNOWLEDGED,
                           strategy_id="B", stop_loss_price=140.0, take_profit_price=170.0)
    d = original.to_dict()
    restored = OrderRecord.from_dict(d)
    assert restored.symbol == original.symbol
    assert restored.side == original.side
    assert restored.qty == original.qty
    assert restored.status == original.status

def test_order_record_transition_to():
    o = OrderRecord(order_id="b1", client_order_id="c1", symbol="AAPL",
                    side="buy", order_type="market", time_in_force="gtc",
                    qty=10, status=OrderState.CREATED)
    o.transition_to(OrderState.SUBMITTED)
    assert o.status == OrderState.SUBMITTED
    assert o.submitted_at is not None

def test_order_record_transition_rejected():
    o = OrderRecord(order_id="b1", client_order_id="c1", symbol="AAPL",
                    side="buy", order_type="market", time_in_force="gtc",
                    qty=10, status=OrderState.CREATED)
    o.transition_to(OrderState.REJECTED)
    assert o.status == OrderState.REJECTED

# ══════════════════════════════════════════════════════════════════
# OrderStore tests
# ══════════════════════════════════════════════════════════════════

def test_store_put_and_get():
    store = OrderStore()
    o = OrderRecord(order_id="b1", client_order_id="c1", symbol="AAPL",
                    side="buy", order_type="market", time_in_force="gtc",
                    qty=10, status=OrderState.SUBMITTED, strategy_id="A")
    store.put(o)
    assert store.get_by_order_id("b1") is o
    assert store.get_by_client_order_id("c1") is o

def test_store_get_by_symbol():
    store = OrderStore()
    o1 = OrderRecord(order_id="b1", client_order_id="c1", symbol="AAPL",
                     side="buy", order_type="market", time_in_force="gtc",
                     qty=10, status=OrderState.SUBMITTED)
    o2 = OrderRecord(order_id="b2", client_order_id="c2", symbol="TSLA",
                     side="sell", order_type="market", time_in_force="gtc",
                     qty=5, status=OrderState.SUBMITTED)
    store.put(o1)
    store.put(o2)
    aapl = store.get_by_symbol("AAPL")
    assert len(aapl) == 1
    assert aapl[0].symbol == "AAPL"

def test_store_get_by_strategy():
    store = OrderStore()
    o1 = OrderRecord(order_id="b1", client_order_id="c1", symbol="AAPL",
                     side="buy", order_type="market", time_in_force="gtc",
                     qty=10, status=OrderState.SUBMITTED, strategy_id="A")
    o2 = OrderRecord(order_id="b2", client_order_id="c2", symbol="TSLA",
                     side="sell", order_type="market", time_in_force="gtc",
                     qty=5, status=OrderState.SUBMITTED, strategy_id="B")
    store.put(o1)
    store.put(o2)
    assert len(store.get_by_strategy("A")) == 1
    assert len(store.get_by_strategy("B")) == 1

def test_store_get_by_status():
    store = OrderStore()
    o1 = OrderRecord(order_id="b1", client_order_id="c1", symbol="AAPL",
                     side="buy", order_type="market", time_in_force="gtc",
                     qty=10, status=OrderState.SUBMITTED)
    o2 = OrderRecord(order_id="b2", client_order_id="c2", symbol="TSLA",
                     side="sell", order_type="market", time_in_force="gtc",
                     qty=5, status=OrderState.FILLED)
    store.put(o1)
    store.put(o2)
    assert len(store.get_by_status(OrderState.SUBMITTED)) == 1
    assert len(store.get_by_status(OrderState.FILLED)) == 1

def test_store_get_active():
    store = OrderStore()
    o1 = OrderRecord(order_id="b1", client_order_id="c1", symbol="AAPL",
                     side="buy", order_type="market", time_in_force="gtc",
                     qty=10, status=OrderState.SUBMITTED)
    o2 = OrderRecord(order_id="b2", client_order_id="c2", symbol="TSLA",
                     side="sell", order_type="market", time_in_force="gtc",
                     qty=5, status=OrderState.FILLED)
    store.put(o1)
    store.put(o2)
    active = store.get_active()
    assert len(active) == 1
    assert active[0].order_id == "b1"

def test_store_transition():
    store = OrderStore()
    o = OrderRecord(order_id="b1", client_order_id="c1", symbol="AAPL",
                    side="buy", order_type="market", time_in_force="gtc",
                    qty=10, status=OrderState.SUBMITTED)
    store.put(o)
    result = store.transition("b1", OrderState.FILLED)
    assert result is not None
    assert result.status == OrderState.FILLED
    assert store.get_by_order_id("b1").status == OrderState.FILLED

def test_store_transition_invalid():
    store = OrderStore()
    o = OrderRecord(order_id="b1", client_order_id="c1", symbol="AAPL",
                    side="buy", order_type="market", time_in_force="gtc",
                    qty=10, status=OrderState.FILLED)
    store.put(o)
    result = store.transition("b1", OrderState.SUBMITTED)
    assert result is None

def test_store_remove():
    store = OrderStore()
    o = OrderRecord(order_id="b1", client_order_id="c1", symbol="AAPL",
                    side="buy", order_type="market", time_in_force="gtc",
                    qty=10, status=OrderState.SUBMITTED)
    store.put(o)
    removed = store.remove("b1")
    assert removed is o
    assert store.get_by_order_id("b1") is None

def test_store_count():
    store = OrderStore()
    for i in range(5):
        o = OrderRecord(order_id=f"b{i}", client_order_id=f"c{i}", symbol="AAPL",
                        side="buy", order_type="market", time_in_force="gtc",
                        qty=10, status=OrderState.SUBMITTED)
        store.put(o)
    assert store.count() == 5
    assert store.count_active() == 5

def test_store_find():
    store = OrderStore()
    o1 = OrderRecord(order_id="b1", client_order_id="c1", symbol="AAPL",
                     side="buy", order_type="market", time_in_force="gtc",
                     qty=10, status=OrderState.SUBMITTED, strategy_id="A")
    o2 = OrderRecord(order_id="b2", client_order_id="c2", symbol="TSLA",
                     side="sell", order_type="market", time_in_force="gtc",
                     qty=5, status=OrderState.FILLED, strategy_id="A")
    store.put(o1)
    store.put(o2)
    results = store.find(strategy_id="A", status=OrderState.SUBMITTED)
    assert len(results) == 1
    assert results[0].symbol == "AAPL"

def test_store_upsert():
    store = OrderStore()
    o = OrderRecord(order_id="b1", client_order_id="c1", symbol="AAPL",
                    side="buy", order_type="market", time_in_force="gtc",
                    qty=10, status=OrderState.SUBMITTED, strategy_id="A")
    store.put(o)
    o2 = OrderRecord(order_id="b1", client_order_id="c1", symbol="AAPL",
                     side="buy", order_type="market", time_in_force="gtc",
                     qty=20, status=OrderState.PARTIALLY_FILLED, strategy_id="A",
                     filled_qty=10)
    store.put(o2)
    result = store.get_by_order_id("b1")
    assert result is not None
    assert result.qty == 20
    assert result.filled_qty == 10

# ══════════════════════════════════════════════════════════════════
# OrderManager tests (with mocked broker)
# ══════════════════════════════════════════════════════════════════

@pytest.fixture
def store():
    return OrderStore()

@pytest.fixture
def mock_broker():
    broker = MagicMock()
    broker.name = "mock"
    broker.submit_order.return_value = {
        "id": "broker-001",
        "status": "submitted",
        "client_order_id": "",
        "symbol": "AAPL",
    }
    broker.cancel_order.return_value = True
    broker.get_orders.return_value = []
    return broker

@pytest.fixture
def mgr(store, mock_broker, monkeypatch):
    mgr = OrderManager(strategy_id="A", store=store)
    mgr._broker = mock_broker
    return mgr

def test_create_order(mgr, store):
    order = mgr.create_order("AAPL", "buy", 10)
    assert order.symbol == "AAPL"
    assert order.side == "buy"
    assert order.qty == 10
    assert order.status == OrderState.CREATED
    assert order.strategy_id == "A"

def test_submit_order(mgr):
    order = mgr.create_order("AAPL", "buy", 10)
    result = mgr.submit(order)
    assert result["success"] is True
    assert result["order_id"] == "broker-001"
    assert result["order"].status == OrderState.SUBMITTED

def test_submit_order_validation_fails(mgr):
    order = mgr.create_order("", "buy", 10)
    order.order_id = "bad-1"
    result = mgr.submit(order)
    assert result["success"] is False
    assert "VALIDATION_FAILED" in result["reason"]

def test_submit_already_submitted(mgr):
    order = mgr.create_order("AAPL", "buy", 10)
    order.status = OrderState.SUBMITTED
    order.order_id = "already-submitted"
    result = mgr.submit(order)
    assert result["success"] is False
    assert "ORDER_NOT_CREATED" in result["reason"]

def test_cancel_order(mgr, store):
    order = mgr.create_order("AAPL", "buy", 10)
    order.order_id = "b-1"
    order.status = OrderState.SUBMITTED
    store.put(order)
    result = mgr.cancel("b-1")
    assert result["success"] is True

def test_cancel_order_not_found(mgr):
    result = mgr.cancel("nonexistent")
    assert result["success"] is False
    assert "ORDER_NOT_FOUND" in result["reason"]

def test_cancel_order_not_cancelable(mgr, store):
    order = mgr.create_order("AAPL", "buy", 10)
    order.order_id = "b-filled"
    order.status = OrderState.FILLED
    store.put(order)
    result = mgr.cancel("b-filled")
    assert result["success"] is False
    assert "NOT_CANCELABLE" in result["reason"]

def test_apply_fill(mgr, store):
    order = mgr.create_order("AAPL", "buy", 100)
    order.order_id = "b-1"
    order.status = OrderState.SUBMITTED
    store.put(order)
    result = mgr.apply_fill("b-1", 100, 105.50)
    assert result is not None
    assert result.status == OrderState.FILLED
    assert result.filled_qty == 100
    assert result.filled_avg_price == 105.50

def test_apply_partial_fill(mgr, store):
    order = mgr.create_order("AAPL", "buy", 100)
    order.order_id = "b-2"
    order.status = OrderState.SUBMITTED
    store.put(order)
    result = mgr.apply_fill("b-2", 40, 104.0)
    assert result is not None
    assert result.status == OrderState.PARTIALLY_FILLED
    assert result.filled_qty == 40

def test_reject_order(mgr, store):
    order = mgr.create_order("AAPL", "buy", 10)
    order.order_id = "b-reject"
    order.status = OrderState.SUBMITTED
    store.put(order)
    result = mgr.reject("b-reject", "Insufficient funds")
    assert result is not None
    assert result.status == OrderState.REJECTED
    assert "Insufficient funds" in (result.reject_reason or "")

def test_expire_order(mgr, store):
    order = mgr.create_order("AAPL", "buy", 10)
    order.order_id = "b-expire"
    order.status = OrderState.SUBMITTED
    store.put(order)
    result = mgr.expire("b-expire")
    assert result is not None
    assert result.status == OrderState.EXPIRED

def test_expire_already_terminal(mgr, store):
    order = mgr.create_order("AAPL", "buy", 10)
    order.order_id = "b-filled-2"
    order.status = OrderState.FILLED
    store.put(order)
    result = mgr.expire("b-filled-2")
    assert result is not None
    assert result.status == OrderState.FILLED

def test_get_active_orders(mgr, store):
    o1 = mgr.create_order("AAPL", "buy", 10)
    o1.order_id = "b-a1"
    o1.status = OrderState.SUBMITTED
    store.put(o1)
    o2 = mgr.create_order("TSLA", "sell", 5)
    o2.order_id = "b-a2"
    o2.status = OrderState.FILLED
    store.put(o2)
    active = mgr.get_active_orders()
    assert len(active) == 1

def test_get_orders_by_symbol(mgr, store):
    o1 = mgr.create_order("AAPL", "buy", 10)
    o1.order_id = "b-s1"
    o1.status = OrderState.SUBMITTED
    store.put(o1)
    results = mgr.get_orders_by_symbol("AAPL")
    assert len(results) == 1
    assert results[0].symbol == "AAPL"

def test_count_active(mgr, store):
    o1 = mgr.create_order("AAPL", "buy", 10)
    o1.order_id = "b-ca1"
    o1.status = OrderState.SUBMITTED
    store.put(o1)
    assert mgr.count_active() == 1

def test_cancel_all(mgr, store):
    for i in range(3):
        o = mgr.create_order(f"SYM{i}", "buy", 10)
        o.order_id = f"b-call{i}"
        o.status = OrderState.SUBMITTED
        store.put(o)
    count = mgr.cancel_all()
    assert count == 3

def test_cancel_by_symbol(mgr, store):
    o1 = mgr.create_order("AAPL", "buy", 10)
    o1.order_id = "cbs-1"
    o1.status = OrderState.SUBMITTED
    store.put(o1)
    o2 = mgr.create_order("TSLA", "buy", 10)
    o2.order_id = "cbs-2"
    o2.status = OrderState.SUBMITTED
    store.put(o2)
    count = mgr.cancel_by_symbol("AAPL")
    assert count == 1

def test_submit_bracket(mgr):
    result = mgr.submit_bracket(
        symbol="AAPL", side="buy", qty=100,
        limit_price=150.0, stop_loss_price=145.0, take_profit_price=160.0,
    )
    assert result["success"] is True
    assert result["bracket"]["stop_loss"] == 145.0

def test_modify_order(mgr, store):
    order = mgr.create_order("AAPL", "buy", 10)
    order.order_id = "b-mod-1"
    order.status = OrderState.SUBMITTED
    store.put(order)
    result = mgr.modify("b-mod-1", qty=20)
    assert result["success"] is True
    assert result["new_order"].qty == 20

def test_modify_order_not_found(mgr):
    result = mgr.modify("nonexistent", qty=20)
    assert result["success"] is False

def test_reconcile(mgr, store):
    order = mgr.create_order("AAPL", "buy", 10)
    order.order_id = "b-rec-1"
    order.status = OrderState.SUBMITTED
    store.put(order)
    summary = mgr.reconcile()
    assert "strategy" in summary

def test_get_order_manager_singleton():
    mgr1 = get_order_manager("X")
    mgr2 = get_order_manager("X")
    assert mgr1 is mgr2
    mgr3 = get_order_manager("Y")
    assert mgr1 is not mgr3
