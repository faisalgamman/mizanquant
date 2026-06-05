"""Smoke tests for new admin/operator endpoints."""

from __future__ import annotations

import pytest

pd = pytest.importorskip("pandas")
pytest.importorskip("numpy")
pytest.importorskip("fastapi")

from fastapi.testclient import TestClient


class _FakeBroker:
    name = "alpaca"
    def __init__(self, account=None, positions=None):
        self._a = account; self._p = positions or []
    def get_account(self, strategy_id=None): return self._a
    def get_positions(self, strategy_id=None): return self._p

import halal_screener as hs


def test_admin_regime_endpoint_forces_state(monkeypatch):
    client = TestClient(hs.app)
    monkeypatch.setattr(hs.settings, "API_KEY", "secret")
    monkeypatch.setattr(hs, "tg_send", lambda *_args, **_kwargs: True)

    response = client.post("/admin/regime?state=BEAR", headers={"X-API-Key": "secret"})

    assert response.status_code == 200
    assert response.json()["state"] == "BEAR"
    assert response.json()["forced"] is True


def test_ops_body_requires_api_key(monkeypatch):
    client = TestClient(hs.app)
    monkeypatch.setattr(hs.settings, "API_KEY", "secret")

    response = client.get("/ops/body")

    assert response.status_code == 401


def test_operator_routes_fail_closed_without_api_key_configured(monkeypatch):
    client = TestClient(hs.app)
    monkeypatch.setattr(hs.settings, "API_KEY", "")

    trading = client.get("/api/v1/trading/history")
    portfolio = client.get("/portfolio/orders")

    assert trading.status_code == 503
    assert portfolio.status_code == 503


def test_trading_history_requires_key_and_openapi_declares_security(monkeypatch):
    client = TestClient(hs.app)
    monkeypatch.setattr(hs.settings, "API_KEY", "secret")
    monkeypatch.setattr(hs, "get_trade_history", lambda limit=50: [{"symbol": "AAPL"}])

    unauthorized = client.get("/api/v1/trading/history")
    authorized = client.get("/api/v1/trading/history", headers={"X-API-Key": "secret"})
    schema = client.get("/openapi.json").json()

    assert unauthorized.status_code == 401
    assert authorized.status_code == 200
    assert schema["paths"]["/api/v1/trading/history"]["get"]["security"]
    assert schema["paths"]["/portfolio/orders"]["get"]["security"]
    assert schema["paths"]["/admin/config"]["get"]["security"]
    assert schema["paths"]["/refresh_ready"]["get"]["security"]
    assert schema["paths"]["/telegram/test"]["get"]["security"]
    assert schema["paths"]["/api/v1/post_market_scan"]["get"]["security"]


def test_health_reports_broker_unavailable_when_auto_trade_enabled(monkeypatch):
    client = TestClient(hs.app)
    monkeypatch.setattr(hs.settings, "AUTO_TRADE_ENABLED", True)
    monkeypatch.setattr(hs.settings, "API_KEY", "")
    monkeypatch.setattr(hs.settings, "ALPACA_API_KEY_A", "key")
    monkeypatch.setattr(hs.settings, "ALPACA_SECRET_KEY_A", "secret")
    monkeypatch.setattr(hs, "alpaca_get_account", lambda strategy_id=None: None)
    monkeypatch.setattr(hs, "alpaca_get_last_error", lambda strategy_id=None: {"reason": "unauthorized", "status_code": 401})
    monkeypatch.setattr(hs, "fetch_yf", lambda *_args, **_kwargs: pd.DataFrame({"close": [1.0]}))

    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["broker"] == "configured_unavailable"
    assert body["broker_reason"] == "unauthorized"
    assert body["broker_status_code"] == 401
    assert body["operator_api"] == "not_configured"
    assert body["auto_trading"] == "blocked_broker_unavailable"
    assert body["dependencies"]["broker"] is False


def test_refresh_and_telegram_operator_routes_require_api_key(monkeypatch):
    client = TestClient(hs.app)
    monkeypatch.setattr(hs.settings, "API_KEY", "secret")

    refresh = client.get("/refresh_ready")
    telegram = client.get("/telegram/test")
    scan = client.get("/api/v1/post_market_scan")

    assert refresh.status_code == 401
    assert telegram.status_code == 401
    assert scan.status_code == 401


def test_trading_status_returns_broker_diagnostics(monkeypatch):
    client = TestClient(hs.app)
    monkeypatch.setattr(hs.settings, "API_KEY", "secret")
    monkeypatch.setattr(hs.settings, "ALPACA_API_KEY_A", "key")
    monkeypatch.setattr(hs.settings, "ALPACA_SECRET_KEY_A", "secret")
    monkeypatch.setattr("app.services.broker.factory.get_broker",
                        lambda strategy_id=None: _FakeBroker(account=None))
    monkeypatch.setattr("app.services.alpaca_client.get_last_error",
                        lambda strategy_id=None: {"reason": "unauthorized", "status_code": 401})

    response = client.get("/api/v1/trading/status", headers={"X-API-Key": "secret"})

    assert response.status_code == 200
    body = response.json()
    assert body["broker_connected"] is False
    assert body["broker_reason"] == "unauthorized"
    assert body["broker_status_code"] == 401


def test_agent_health_no_longer_exposes_key_prefix(monkeypatch):
    client = TestClient(hs.app)
    monkeypatch.setattr(hs.settings, "ANTHROPIC_API_KEY", "sk-ant-api03-secret-value")

    response = client.get("/agent/health")

    assert response.status_code == 200
    body = response.json()
    assert body["api_key_configured"] is True
    assert "api_key_prefix" not in body
