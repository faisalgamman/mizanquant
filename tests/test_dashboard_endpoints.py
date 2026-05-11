"""Smoke tests for dashboard API endpoints."""
from __future__ import annotations

import pytest

pytest.importorskip("pandas")
pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

import halal_screener as hs


def test_system_status(monkeypatch):
    client = TestClient(hs.app)
    monkeypatch.setattr(hs.settings, "API_KEY", "")
    monkeypatch.setattr(hs, "_universe_symbols", lambda: ["AAPL", "MSFT"])

    resp = client.get("/api/system/status")
    assert resp.status_code == 200
    body = resp.json()
    assert "status" in body
    assert "broker" in body
    assert "regime" in body


def test_symbols_universe(monkeypatch):
    client = TestClient(hs.app)
    monkeypatch.setattr(hs, "_universe_symbols", lambda: ["AAPL", "MSFT", "GOOGL"])

    resp = client.get("/api/symbols/universe")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 3
    assert body["symbols"] == ["AAPL", "GOOGL", "MSFT"]


def test_symbols_search(monkeypatch):
    client = TestClient(hs.app)
    monkeypatch.setattr(hs, "_universe_symbols", lambda: ["AAPL", "MSFT", "GOOGL", "AMD"])

    resp = client.get("/api/symbols/search?q=AM")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert "AMD" in body["symbols"]


def test_symbols_search_empty(monkeypatch):
    client = TestClient(hs.app)
    monkeypatch.setattr(hs, "_universe_symbols", lambda: ["AAPL", "MSFT"])

    resp = client.get("/api/symbols/search?q=ZZZ")
    assert resp.status_code == 200
    assert resp.json()["count"] == 0


def test_trading_summary_no_account(monkeypatch):
    client = TestClient(hs.app)
    monkeypatch.setattr(hs, "alpaca_get_account", lambda strategy_id=None: None)
    monkeypatch.setattr(hs, "alpaca_get_positions", lambda strategy_id=None: [])

    resp = client.get("/api/trading/summary")
    assert resp.status_code == 200
    body = resp.json()
    assert body["equity"] == 0
    assert body["open_positions"] == 0


def test_trading_summary_with_account(monkeypatch):
    import app.config
    monkeypatch.setattr(app.config, "STRATEGY_CONFIGS", {"A": lambda: None})
    client = TestClient(hs.app)
    mock_account = {
        "equity": "100000.00",
        "cash": "50000.00",
        "buying_power": "200000.00",
        "portfolio_value": "100000.00",
        "last_equity": "99000.00",
    }
    mock_positions = [
        {
            "symbol": "AAPL",
            "qty": "10",
            "avg_entry_price": "150.00",
            "current_price": "155.00",
            "market_value": "1550.00",
            "unrealized_pl": "50.00",
            "unrealized_plpc": "0.0333",
        }
    ]
    monkeypatch.setattr(hs, "alpaca_get_account", lambda strategy_id=None: mock_account)
    monkeypatch.setattr(hs, "alpaca_get_positions", lambda strategy_id=None: mock_positions)

    resp = client.get("/api/trading/summary")
    assert resp.status_code == 200
    body = resp.json()
    assert body["equity"] == 100000.00
    assert body["daily_pnl"] == 1000.00
    assert body["open_positions"] == 1
    assert body["positions"][0]["symbol"] == "AAPL"


def test_guards_recent_empty(monkeypatch):
    client = TestClient(hs.app)

    class MockDB:
        def query(self, *args): return self
        def order_by(self, *args): return self
        def limit(self, *args): return self
        def all(self): return []

    class MockSession:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def query(self, *a): return MockDB()
        def order_by(self, *a): return self
        def limit(self, *a): return self
        def all(self): return []
        def close(self): pass

    monkeypatch.setattr("app.db.database.SessionLocal", lambda: MockSession())

    resp = client.get("/api/guards/recent?limit=5")
    assert resp.status_code == 200
    assert resp.json() == []


def test_halal_check(monkeypatch):
    client = TestClient(hs.app)
    monkeypatch.setattr(hs, "validate_symbol", lambda s: s.upper())
    monkeypatch.setattr(hs, "verify_halal", lambda s: (True, "Verified halal"))

    resp = client.get("/api/halal/check?symbol=AAPL")
    assert resp.status_code == 200
    body = resp.json()
    assert body["symbol"] == "AAPL"
    assert body["halal"] is True
    assert body["details"]["reason"] == "Verified halal"


def test_halal_universe(monkeypatch):
    client = TestClient(hs.app)
    monkeypatch.setattr(hs, "_universe_symbols", lambda: ["AAPL", "MSFT"])

    resp = client.get("/api/halal/universe")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 2
    assert body["total"] == 2


def test_scheduler_status(monkeypatch):
    client = TestClient(hs.app)
    monkeypatch.setattr("app.services.scheduler_metrics.scheduler_metrics.health", lambda: {"status": "running", "runs_today": 5})
    monkeypatch.setattr("app.services.scheduler._scheduler_running", True)

    resp = client.get("/api/scheduler/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["running"] is True


def test_trading_controls(monkeypatch):
    client = TestClient(hs.app)
    monkeypatch.setattr(hs.settings, "AUTO_TRADE_ENABLED", True)
    monkeypatch.setattr(hs.settings, "MIN_TRADE_CONFIDENCE", 60.0)
    monkeypatch.setattr(hs.settings, "TRADE_RISK_PCT", 2.0)
    monkeypatch.setattr(hs.settings, "MAX_POSITION_PCT", 25.0)
    monkeypatch.setattr(hs.settings, "MAX_OPEN_POSITIONS", 5)
    monkeypatch.setattr(hs.settings, "DAILY_LOSS_LIMIT_PCT", 3.0)
    monkeypatch.setattr(hs.settings, "RISK_CAPITAL", 100000.0)
    monkeypatch.setattr(hs.app_cfg, "killed", False)

    resp = client.get("/api/trading/controls")
    assert resp.status_code == 200
    body = resp.json()
    assert body["auto_trade_enabled"] is True
    assert body["kill_switch"] is False
    assert body["min_confidence"] == 60.0


def test_kill_switch_requires_api_key(monkeypatch):
    client = TestClient(hs.app)
    monkeypatch.setattr(hs.settings, "API_KEY", "secret")

    resp = client.post("/api/trading/controls/kill-switch?killed=true")
    assert resp.status_code == 401


def test_kill_switch_toggle(monkeypatch):
    client = TestClient(hs.app)
    monkeypatch.setattr(hs.settings, "API_KEY", "secret")
    monkeypatch.setattr(hs, "_require_api_key", lambda k: None)
    monkeypatch.setattr(hs.app_cfg, "killed", False)
    monkeypatch.setattr(hs, "tg_send", lambda *a, **kw: True)

    resp = client.post("/api/trading/controls/kill-switch?killed=true&x_api_key=secret")
    assert resp.status_code == 200
    assert resp.json()["killed"] is True


def test_auto_trade_toggle(monkeypatch):
    client = TestClient(hs.app)
    monkeypatch.setattr(hs.settings, "API_KEY", "secret")
    monkeypatch.setattr(hs.settings, "AUTO_TRADE_ENABLED", False)
    monkeypatch.setattr(hs, "_require_api_key", lambda k: None)
    monkeypatch.setattr(hs, "tg_send", lambda *a, **kw: True)

    resp = client.post("/api/trading/controls/auto-trade?enabled=true&x_api_key=secret")
    assert resp.status_code == 200
    assert resp.json()["auto_trade_enabled"] is True


def test_trading_history(monkeypatch):
    client = TestClient(hs.app)
    monkeypatch.setattr(hs, "get_trade_history", lambda limit=20: [{"symbol": "AAPL", "action": "BUY"}])

    resp = client.get("/api/trading/history/recent?limit=5")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    assert body[0]["symbol"] == "AAPL"


def test_dashboard_page_served(monkeypatch):
    import os
    client = TestClient(hs.app)
    _dash = os.path.join(os.path.dirname(hs.__file__), "app", "static", "dashboard.html")
    monkeypatch.setattr(os.path, "isfile", lambda p: p == _dash)

    resp = client.get("/dashboard")
    assert resp.status_code == 200


# ── New API endpoint tests: market context, sectors, scoring, trade plan, block status ──


def test_market_context_endpoint(monkeypatch):
    client = TestClient(hs.app)
    resp = client.get("/api/market/context")
    assert resp.status_code == 200
    body = resp.json()
    for key in ("vix", "spy_regime", "breadth", "credit", "liquidity"):
        assert key in body, f"Missing key: {key}"


def test_market_context_endpoint_force_refresh(monkeypatch):
    client = TestClient(hs.app)
    resp = client.get("/api/market/context?force_refresh=true")
    assert resp.status_code == 200
    body = resp.json()
    assert "cached_at" in body


def test_sectors_endpoint(monkeypatch):
    client = TestClient(hs.app)
    resp = client.get("/api/sectors/performance")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    assert len(body) == 11  # 11 sector ETFs
    for sector in body:
        assert "ticker" in sector
        assert "name" in sector
        assert "classification" in sector


def test_scoring_endpoint(monkeypatch):
    client = TestClient(hs.app)
    monkeypatch.setattr("app.services.market_data.fetch", lambda symbol, **kw: None)
    resp = client.get("/api/scoring/weighted?symbol=INVALID")
    assert resp.status_code == 200
    body = resp.json()
    assert "error" in body


def test_trade_plan_endpoint(monkeypatch):
    client = TestClient(hs.app)
    monkeypatch.setattr("app.services.market_data.fetch", lambda symbol, **kw: None)
    resp = client.get("/api/trade/plan?symbol=INVALID")
    assert resp.status_code == 200
    body = resp.json()
    assert "error" in body


def test_block_status_endpoint(monkeypatch):
    client = TestClient(hs.app)
    resp = client.get("/api/block/status")
    assert resp.status_code == 200
    body = resp.json()
    assert "block_level" in body
    assert body["block_level"] in ("NORMAL", "CREDIT", "VIX", "BEAR")


def test_pipeline_run_endpoint(monkeypatch):
    def mock_run(**kw):
        from app.services.pipeline_orchestrator import PipelineReport
        r = PipelineReport()
        r.date_utc = "2026-05-11"
        r.signals_passed = 0
        r.signals_executed = 0
        r.elapsed_s = 0.5
        return r
    monkeypatch.setattr("app.services.pipeline_orchestrator.run_pipeline", mock_run)
    client = TestClient(hs.app)
    resp = client.get("/api/pipeline/run?dry_run=true")
    assert resp.status_code == 200
    body = resp.json()
    assert "signals_passed" in body
    assert "elapsed_s" in body
