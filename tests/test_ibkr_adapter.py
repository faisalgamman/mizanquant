"""Phase B: Interactive Brokers adapter tests.

These tests exercise the translation layer between Alpaca-style
payloads and ib_insync calls without any real network I/O. They mock
`ib_insync.IB` and the order/contract classes so behaviour is checked
deterministically.

What they pin down:

1. The adapter satisfies the `BrokerClient` Protocol.
2. Connection failures degrade gracefully (None / empty list, no crash).
3. A bracket-order payload (Alpaca shape) is translated into 3 IB
   orders with the correct parent/child wiring and transmit flags.
4. A simple market order returns a single IB order.
5. Position/account/order translations preserve the keys the engine
   expects (`symbol`, `qty`, `avg_entry_price`, `unrealized_pl`,
   `unrealized_plpc`, `status`, `cash`, `equity`, `buying_power`).
6. Per-strategy IBKR_CLIENT_ID env var routes correctly.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

# ib_insync is required by the adapter. On some Python versions
# (notably 3.14) it fails at import time because eventkit calls
# asyncio.get_event_loop() during module load and the implicit-loop
# behaviour was removed. Production runs on Python 3.11 (Dockerfile)
# where this is a non-issue; locally we install an event loop so the
# import succeeds, and skip the suite if even that doesn't help.
try:
    asyncio.set_event_loop(asyncio.new_event_loop())
    import ib_insync  # noqa: F401
except Exception as exc:
    pytest.skip(f"ib_insync unavailable in this environment: {exc}", allow_module_level=True)


@pytest.fixture(autouse=True)
def reset_connections(monkeypatch):
    """Clear the per-strategy connection cache before each test so
    stale mocks don't leak across cases."""
    from app.services.broker import ibkr_adapter

    ibkr_adapter._connections.clear()
    monkeypatch.delenv("IBKR_HOST", raising=False)
    monkeypatch.delenv("IBKR_PORT", raising=False)
    monkeypatch.delenv("IBKR_CLIENT_ID", raising=False)
    yield
    ibkr_adapter._connections.clear()


def _make_fake_ib():
    """Construct a MagicMock that quacks like ib_insync.IB, with a
    successful connect() and empty default state."""
    ib = MagicMock()
    ib.isConnected.return_value = True
    ib.connect.return_value = None
    ib.accountValues.return_value = []
    ib.positions.return_value = []
    ib.trades.return_value = []
    ib.qualifyContracts.return_value = None
    ib.sleep.return_value = None
    return ib


def test_adapter_satisfies_protocol():
    from app.services.broker.base import BrokerClient
    from app.services.broker.ibkr_adapter import IBBroker

    inst = IBBroker()
    assert isinstance(inst, BrokerClient)
    assert inst.name == "ibkr"


def test_connect_failure_returns_none(monkeypatch):
    from app.services.broker import ibkr_adapter

    fake = MagicMock()
    fake.isConnected.return_value = False
    fake.connect.side_effect = ConnectionRefusedError("gateway down")
    monkeypatch.setattr(ibkr_adapter, "_IB", lambda: fake)

    b = ibkr_adapter.IBBroker()
    assert b.get_account() is None
    assert b.get_positions() == []
    assert b.get_orders() == []
    assert b.submit_order({"symbol": "AAPL", "qty": 1, "side": "buy"}) is None
    assert b.cancel_order("123") is False
    assert b.close_position("AAPL") is None


def test_account_translation_pulls_alpaca_keys(monkeypatch):
    from app.services.broker import ibkr_adapter

    fake = _make_fake_ib()
    fake.accountValues.return_value = [
        SimpleNamespace(tag="TotalCashValue", value="123.45"),
        SimpleNamespace(tag="NetLiquidation", value="678.90"),
        SimpleNamespace(tag="BuyingPower", value="1357.80"),
        SimpleNamespace(tag="AccountType", value="INDIVIDUAL"),
        SimpleNamespace(tag="UnrelatedTag", value="ignore me"),
    ]
    monkeypatch.setattr(ibkr_adapter, "_IB", lambda: fake)

    acct = ibkr_adapter.IBBroker().get_account()
    assert acct is not None
    assert acct["cash"] == "123.45"
    assert acct["equity"] == "678.90"
    assert acct["portfolio_value"] == "678.90"
    assert acct["buying_power"] == "1357.80"
    assert acct["status"] == "ACTIVE"


def test_position_translation_computes_unrealized_pl(monkeypatch):
    from app.services.broker import ibkr_adapter

    contract = SimpleNamespace(symbol="AAPL")
    pos = SimpleNamespace(
        contract=contract,
        position=10,
        avgCost=100.0,
        marketPrice=110.0,
        marketValue=1100.0,
    )
    fake = _make_fake_ib()
    fake.positions.return_value = [pos]
    monkeypatch.setattr(ibkr_adapter, "_IB", lambda: fake)

    out = ibkr_adapter.IBBroker().get_positions()
    assert len(out) == 1
    p = out[0]
    assert p["symbol"] == "AAPL"
    assert p["qty"] == "10"
    assert p["avg_entry_price"] == "100.0"
    # 10 * (110 - 100) = 100 unrealized
    assert float(p["unrealized_pl"]) == pytest.approx(100.0)
    assert float(p["unrealized_plpc"]) == pytest.approx(0.10)


def test_zero_position_filtered(monkeypatch):
    from app.services.broker import ibkr_adapter

    contract = SimpleNamespace(symbol="GHOST")
    pos = SimpleNamespace(contract=contract, position=0, avgCost=0.0, marketPrice=0.0, marketValue=0.0)
    fake = _make_fake_ib()
    fake.positions.return_value = [pos]
    monkeypatch.setattr(ibkr_adapter, "_IB", lambda: fake)

    assert ibkr_adapter.IBBroker().get_positions() == []


def test_simple_market_order_translation(monkeypatch):
    from app.services.broker import ibkr_adapter

    fake = _make_fake_ib()
    placed_orders: list = []

    def fake_place(_contract, order):
        placed_orders.append(order)
        return SimpleNamespace(
            contract=_contract,
            order=order,
            orderStatus=SimpleNamespace(status="Submitted", filled=0, avgFillPrice=0),
        )

    fake.placeOrder.side_effect = fake_place
    monkeypatch.setattr(ibkr_adapter, "_IB", lambda: fake)

    payload = {
        "symbol": "AAPL",
        "qty": "5",
        "side": "buy",
        "type": "market",
        "time_in_force": "gtc",
        "client_order_id": "abc-123",
    }
    out = ibkr_adapter.IBBroker().submit_order(payload)
    assert out is not None
    assert len(placed_orders) == 1
    o = placed_orders[0]
    assert o.action == "BUY"
    assert float(o.totalQuantity) == 5.0
    assert o.tif == "GTC"
    assert o.orderRef == "abc-123"


def test_bracket_order_emits_three_orders_with_correct_transmit(monkeypatch):
    from app.services.broker import ibkr_adapter

    fake = _make_fake_ib()
    placed_orders: list = []
    next_order_id = [42]

    def fake_place(_contract, order):
        # Simulate IB assigning an orderId to the parent
        if not getattr(order, "orderId", 0):
            order.orderId = next_order_id[0]
            next_order_id[0] += 1
        placed_orders.append(order)
        return SimpleNamespace(
            contract=_contract,
            order=order,
            orderStatus=SimpleNamespace(status="Submitted", filled=0, avgFillPrice=0),
        )

    fake.placeOrder.side_effect = fake_place
    monkeypatch.setattr(ibkr_adapter, "_IB", lambda: fake)

    payload = {
        "symbol": "MSFT",
        "qty": "3",
        "side": "buy",
        "type": "market",
        "time_in_force": "gtc",
        "order_class": "bracket",
        "take_profit": {"limit_price": "200.00"},
        "stop_loss": {"stop_price": "180.00"},
        "client_order_id": "bracket-tag",
    }
    out = ibkr_adapter.IBBroker().submit_order(payload)
    assert out is not None
    assert len(placed_orders) == 3
    parent, tp, sl = placed_orders
    # Parent: market BUY, transmit False, orderRef carries the client tag
    assert parent.action == "BUY"
    assert parent.transmit is False
    assert parent.orderRef == "bracket-tag"
    # TP: limit SELL, transmit False, parentId set
    assert tp.action == "SELL"
    assert tp.lmtPrice == 200.0
    assert tp.transmit is False
    assert tp.parentId == parent.orderId
    # SL: stop SELL, transmit True (arms the bracket atomically)
    assert sl.action == "SELL"
    assert sl.auxPrice == 180.0
    assert sl.transmit is True
    assert sl.parentId == parent.orderId


def test_bracket_without_prices_raises():
    from app.services.broker import ibkr_adapter

    with pytest.raises(ValueError):
        ibkr_adapter._build_orders({
            "symbol": "AAPL",
            "qty": "1",
            "side": "buy",
            "type": "market",
            "order_class": "bracket",
            "take_profit": {"limit_price": "0"},
            "stop_loss": {"stop_price": "0"},
        })


def test_per_strategy_client_id(monkeypatch):
    from app.services.broker import ibkr_adapter

    monkeypatch.setenv("IBKR_CLIENT_ID", "1")
    monkeypatch.setenv("IBKR_CLIENT_ID_A", "11")
    monkeypatch.setenv("IBKR_CLIENT_ID_B", "12")
    assert ibkr_adapter._client_id_for(None) == 1
    assert ibkr_adapter._client_id_for("A") == 11
    assert ibkr_adapter._client_id_for("B") == 12
    # Strategy without explicit override falls back to the global default
    assert ibkr_adapter._client_id_for("C") == 1


def test_factory_resolves_ibkr_when_env_set(monkeypatch):
    """Phase A factory must route to IBBroker when BROKER_TYPE=ibkr."""
    monkeypatch.setenv("BROKER_TYPE", "ibkr")
    import importlib
    from app.services.broker import factory

    importlib.reload(factory)
    b = factory.get_broker()
    assert b.name == "ibkr"
