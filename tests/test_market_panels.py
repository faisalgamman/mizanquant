"""Tests for bottom panel endpoints: /api/forecast/risers, /api/market/news, /api/market/indicators."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_risers_filters_by_expected_change_pct(monkeypatch):
    """Only positive expected_change_pct returned, sorted desc."""
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
    assert result["count"] >= 1
    symbols = [r["symbol"] for r in result["risers"]]
    assert "A" in symbols
    assert "B" not in symbols
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
        }
    monkeypatch.setattr("app.services.market_data.get_alpaca_snapshots", fake_snaps)
    monkeypatch.setattr("app.services.market_context.get_market_status",
                        lambda *a, **k: {"vix": 18.5})

    from app.workspace_server import market_indicators
    result = await market_indicators()
    assert "indicators" in result
    inds = {i["label"]: i for i in result["indicators"]}
    assert inds["S&P 500"]["change_pct"] == 0.5
    assert inds["Nasdaq"]["change_pct"] == -0.3
    assert inds["VIX"]["price"] == 18.5
    # BTC has no real price without crypto API
    assert inds["Bitcoin"]["symbol"] == "BTC"
