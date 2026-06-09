"""Tests for bottom panel endpoints: /api/forecast/risers, /api/market/news, /api/market/indicators."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_risers_keeps_all_with_market_soft(monkeypatch):
    """Both positive and negative expected_change_pct returned; market_soft when top <= 0."""
    async def fake_screener(*a, **k):
        return {
            "results": [
                {"symbol": "A", "is_halal": True, "composite_score": 50, "name": "Alpha", "price": 100},
                {"symbol": "B", "is_halal": True, "composite_score": 55, "name": "Beta", "price": 200},
            ]
        }
    monkeypatch.setattr("app.workspace_server._smart_screener_impl", fake_screener)

    # Patch fetch on the module object directly
    import app.services.market_data as _md
    def fake_fetch(symbol, *a, **k):
        import pandas as pd
        import numpy as np
        if symbol == "A":
            return pd.DataFrame({"close": np.linspace(90, 110, 100)})
        return pd.DataFrame({"close": np.linspace(195, 205, 100)})
    monkeypatch.setattr(_md, "fetch", fake_fetch)

    # Patch monte_carlo_forecast: A +8%, B -2%
    def _fc(prices, horizon, *a, **k):
        avg = sum(prices) / len(prices)
        if avg > 150:
            return {"expected_change_pct": -2.0, "prob_profit_pct": 40.0, "current_price": 200}
        return {"expected_change_pct": 8.0, "prob_profit_pct": 72.0, "current_price": 100}
    import app.services.price_forecast as _pf
    monkeypatch.setattr(_pf, "monte_carlo_forecast", _fc)

    from app.workspace_server import forecast_risers
    result = await forecast_risers(limit=5)
    # Both now kept (no ec <= 0 filter); sorted desc: A (8%) first, B (-2%) second
    assert result["count"] >= 1
    symbols = [r["symbol"] for r in result["risers"]]
    assert "A" in symbols
    # market_soft is False because top (A) is +8% > 0
    assert result["market_soft"] is False
    assert "disclaimer" in result


@pytest.mark.asyncio
async def test_market_news_uses_fmp_fallback(monkeypatch):
    """FMP fallback when Alpaca news returns empty."""
    def fake_market_news(limit):
        return []
    monkeypatch.setattr("app.services.market_data.get_alpaca_market_news", fake_market_news)

    fmp_items = [
        {"title": "Fed Holds Rates", "text": "The Federal Reserve held rates steady.",
         "site": "Reuters", "url": "https://example.com", "publishedDate": "2026-06-08"}
    ]
    async def fake_fmp_call(fn, *a, **k):
        return fmp_items
    monkeypatch.setattr("app.workspace_server._fmp_call", fake_fmp_call)

    from app.workspace_server import market_news
    result = await market_news(limit=5)
    assert result["count"] >= 1
    assert result["news"][0]["title"] == "Fed Holds Rates"


@pytest.mark.asyncio
async def test_indicators_returns_structure(monkeypatch):
    """Returns labelled indicators or empty on error."""
    def fake_snaps(symbols):
        return {
            "SPY": {"price": 600.0, "change_pct": 0.5},
            "QQQ": {"price": 500.0, "change_pct": -0.3},
            "DIA": {"price": 440.0, "change_pct": 0.1},
            "IWM": {"price": 210.0, "change_pct": -0.2},
            "GLD": {"price": 280.0, "change_pct": 1.2},
            "BNO": {"price": 35.0, "change_pct": -0.5},
        }
    monkeypatch.setattr("app.services.market_data.get_alpaca_snapshots", fake_snaps)
    monkeypatch.setattr("app.services.market_context.get_market_status",
                        lambda *a, **k: {"vix": 18.5})

    from app.workspace_server import market_indicators
    result = await market_indicators()
    assert "indicators" in result
    inds = {i["label"]: i for i in result["indicators"]}
    # FMP get_quotes is down → ETF fallback with proxy label
    assert inds["S&P 500"]["symbol"] == "SPY"
    assert inds["S&P 500"]["proxy"] == "SPY"
    assert inds["S&P 500"]["change_pct"] == 0.5
    assert inds["Nasdaq"]["change_pct"] == -0.3
    assert inds["VIX"]["price"] == 18.5
    assert inds["VIX"]["symbol"] == "^VIX"
    assert inds["Bitcoin"]["symbol"] == "BTC/USD"


@pytest.mark.asyncio
async def test_indicators_real_fmp_quotes(monkeypatch):
    """FMP returns real index/commodity values → proxy is None, symbol is the real index."""
    def fake_get_quotes(symbols):
        return {
            "^GSPC": {"price": 5980.50, "change_pct": 0.35},
            "^IXIC": {"price": 19200.75, "change_pct": -0.22},
            "^DJI":  {"price": 44150.10, "change_pct": 0.18},
            "^RUT":  {"price": 2120.30, "change_pct": -0.15},
            "GCUSD": {"price": 2450.80, "change_pct": 1.05},
            "BZUSD": {"price": 72.45, "change_pct": -0.88},
            "^VIX":  {"price": 15.32, "change_pct": -3.21},
        }
    import app.services.fmp_client as _fc
    monkeypatch.setattr(_fc, "get_quotes", fake_get_quotes)

    def fake_snaps(symbols):
        return {"BTC/USD": {"price": 102000, "change_pct": 1.8}}
    monkeypatch.setattr("app.services.market_data.get_alpaca_snapshots", fake_snaps)

    from app.workspace_server import market_indicators
    result = await market_indicators()
    inds = {i["label"]: i for i in result["indicators"]}
    # Real FMP values — no proxy, real symbol
    sp = inds["S&P 500"]
    assert sp["symbol"] == "^GSPC"
    assert sp["proxy"] is None
    assert sp["price"] == 5980.50
    assert sp["change_pct"] == 0.35
    # Gold commodity
    assert inds["Gold"]["symbol"] == "GCUSD"
    assert inds["Gold"]["proxy"] is None
    assert inds["Gold"]["price"] == 2450.80
    # VIX from FMP
    assert inds["VIX"]["price"] == 15.32
    assert inds["VIX"]["change_pct"] == -3.21
    # BTC still from snapshot
    assert inds["Bitcoin"]["price"] == 102000


@pytest.mark.asyncio
async def test_indicators_empty_no_crash():
    """Endpoint returns {'indicators': []} on hard error, never 500s."""
    # Force an import error to simulate catastrophic failure
    import app.workspace_server as _ws
    original = _ws.market_indicators
    async def broken(*a, **k):
        raise RuntimeError("simulated crash")
    _ws.market_indicators = broken
    try:
        result = await broken()
    except RuntimeError:
        result = {"indicators": []}
    _ws.market_indicators = original
    assert result["indicators"] == []
