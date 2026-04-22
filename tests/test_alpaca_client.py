"""Unit tests for app.services.alpaca_client order normalization."""
from __future__ import annotations

from app.services import alpaca_client as ac


def test_get_orders_preserves_reconciliation_fields(monkeypatch):
    payload = [{
        "id": "order-123",
        "symbol": "AAPL",
        "side": "buy",
        "type": "market",
        "order_class": "bracket",
        "client_order_id": "coid-123",
        "parent_order_id": "",
        "qty": "7",
        "filled_qty": "7",
        "status": "filled",
        "filled_avg_price": "101.25",
        "submitted_at": "2026-04-17T10:00:00Z",
        "filled_at": "2026-04-17T10:00:05Z",
        "legs": [
            {"id": "leg-stop", "type": "stop", "status": "new", "client_order_id": "coid-stop"},
            {"id": "leg-limit", "type": "limit", "status": "new", "client_order_id": "coid-limit"},
        ],
    }]
    monkeypatch.setattr(ac, "_api_get", lambda *args, **kwargs: payload)

    orders = ac.get_orders(status="all", limit=1, strategy_id="A")

    assert len(orders) == 1
    order = orders[0]
    assert order["id"] == "order-123"
    assert order["client_order_id"] == "coid-123"
    assert order["order_class"] == "bracket"
    assert [leg["type"] for leg in order["legs"]] == ["stop", "limit"]


def test_get_orders_defaults_missing_legs_to_empty_list(monkeypatch):
    payload = [{
        "id": "order-456",
        "symbol": "MSFT",
        "side": "sell",
        "type": "limit",
        "qty": "3",
        "filled_qty": "0",
        "status": "new",
        "filled_avg_price": "",
    }]
    monkeypatch.setattr(ac, "_api_get", lambda *args, **kwargs: payload)

    orders = ac.get_orders()

    assert orders[0]["legs"] == []
    assert orders[0]["client_order_id"] == ""
