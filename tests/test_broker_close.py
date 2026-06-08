"""Tests for POST /api/v1/broker/close endpoint."""

import pytest
from unittest.mock import MagicMock


@pytest.mark.asyncio
async def test_close_success(monkeypatch):
    """Returns success when broker closes position."""
    def fake_close(sym, strategy_id):
        return {"id": "order-123", "status": "filled"}

    class FakeBroker:
        def close_position(self, sym, strategy_id):
            return fake_close(sym, strategy_id)

    def fake_get_broker(strategy_id=None):
        return FakeBroker()

    monkeypatch.setattr("app.services.broker.factory.get_broker", fake_get_broker)

    from app.api.v1.paper import v1_broker_close, BrokerCloseRequest
    body = BrokerCloseRequest(symbol="AAPL")
    result = await v1_broker_close(body)
    assert result["success"] is True
    assert result["symbol"] == "AAPL"
    assert result["order_id"] == "order-123"


@pytest.mark.asyncio
async def test_close_broker_offline(monkeypatch):
    """Returns broker_offline when get_broker returns None."""
    monkeypatch.setattr("app.services.broker.factory.get_broker",
                        lambda strategy_id=None: None)

    from app.api.v1.paper import v1_broker_close, BrokerCloseRequest
    body = BrokerCloseRequest(symbol="AAPL")
    result = await v1_broker_close(body)
    assert result["success"] is False
    assert result["reason"] == "broker_offline"


@pytest.mark.asyncio
async def test_close_broker_error(monkeypatch):
    """Never 500s on exception."""
    def raising_broker(strategy_id=None):
        raise RuntimeError("connection lost")

    monkeypatch.setattr("app.services.broker.factory.get_broker", raising_broker)

    from app.api.v1.paper import v1_broker_close, BrokerCloseRequest
    body = BrokerCloseRequest(symbol="AAPL")
    result = await v1_broker_close(body)
    assert result["success"] is False
    assert result["reason"] == "broker_error"
