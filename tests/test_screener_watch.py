"""Tests for /api/screener/watch — WATCH list endpoint."""

import pytest
from unittest.mock import AsyncMock, patch

@pytest.mark.asyncio
async def test_watch_filters_correctly(monkeypatch):
    """Only WATCH-band (38-54) symbols returned; STRONG BUY/AVOID excluded."""
    async def fake_screener(*a, **k):
        return {
            "results": [
                {"symbol": "AMD", "composite_score": 46, "signal_composite": "WATCH",
                 "score_tech": 22, "score_fund": 14, "rs_vs_spy": -1.4,
                 "hard_gates_failed": ["VIX HIGH"], "is_halal": True, "name": "AMD Inc"},
                {"symbol": "X", "composite_score": 80, "signal_composite": "STRONG BUY"},
                {"symbol": "Y", "composite_score": 20, "signal_composite": "AVOID"},
            ]
        }
    monkeypatch.setattr(
        "app.workspace_server._smart_screener_impl",
        fake_screener,
    )
    monkeypatch.setattr(
        "app.services.market_context.get_market_status",
        lambda *a, **k: {"status": "RISK-OFF", "regime": "BEAR", "min_gate": 60},
    )
    from app.workspace_server import screener_watch
    result = await screener_watch(limit=10)
    assert result["count"] == 1
    assert result["watch"][0]["symbol"] == "AMD"
    assert "الدرجة 46/100" in result["watch"][0]["watch_reason"]
    assert result["market_block"]


@pytest.mark.asyncio
async def test_watch_empty_on_error(monkeypatch):
    """Never 500 — returns empty list on exception."""
    async def failing_screener(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr("app.workspace_server._smart_screener_impl", failing_screener)
    monkeypatch.setattr(
        "app.services.market_context.get_market_status",
        lambda *a, **k: {"status": "NORMAL"},
    )
    from app.workspace_server import screener_watch
    result = await screener_watch(limit=10)
    assert result["count"] == 0
    assert result["watch"] == []


@pytest.mark.asyncio
async def test_watch_excludes_buy(monkeypatch):
    """Only scores 38-54 when signal_composite missing."""
    async def fake_screener(*a, **k):
        return {
            "results": [
                {"symbol": "A", "composite_score": 50, "signal_composite": ""},
                {"symbol": "B", "composite_score": 60, "signal_composite": ""},
                {"symbol": "C", "composite_score": 37, "signal_composite": ""},
            ]
        }
    monkeypatch.setattr("app.workspace_server._smart_screener_impl", fake_screener)
    monkeypatch.setattr(
        "app.services.market_context.get_market_status",
        lambda *a, **k: {},
    )
    from app.workspace_server import screener_watch
    result = await screener_watch(limit=10)
    assert result["count"] == 1
    assert result["watch"][0]["symbol"] == "A"
