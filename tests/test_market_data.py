"""Tests for market-data retry and dedupe behavior."""

from __future__ import annotations

from types import SimpleNamespace
import pytest


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400 and self.status_code != 429:
            raise RuntimeError(f"http {self.status_code}")


class _FakeClient:
    def __init__(self, responses, calls):
        self._responses = responses
        self._calls = calls

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get(self, url, headers=None, params=None):
        self._calls.append(params.get("page_token"))
        return self._responses.pop(0)


def test_pagination_resumes_on_429(monkeypatch):
    pytest.importorskip("pandas")
    pytest.importorskip("numpy")
    from app.config import settings
    from app.services import market_data as md

    monkeypatch.setattr(settings, "ALPACA_API_KEY", "key")
    monkeypatch.setattr(settings, "ALPACA_SECRET_KEY", "secret")
    monkeypatch.setattr(md, "_alpaca_rate_limit", lambda: None)
    monkeypatch.setattr(md.time, "sleep", lambda *_args, **_kwargs: None)
    md.clear_market_data_cache()

    calls = []
    responses = [
        _FakeResponse(
            200,
            {
                "bars": {"AAPL": [{"t": "2024-01-01T15:30:00Z", "o": 1, "h": 1, "l": 1, "c": 1, "v": 10}]},
                "next_page_token": "p2",
            },
        ),
        _FakeResponse(429, {}),
        _FakeResponse(
            200,
            {
                "bars": {
                    "AAPL": [
                        {"t": "2024-01-01T15:30:00Z", "o": 1, "h": 1, "l": 1, "c": 1, "v": 10},
                        {"t": "2024-01-01T15:45:00Z", "o": 2, "h": 2, "l": 2, "c": 2, "v": 20},
                    ]
                },
                "next_page_token": "p3",
            },
        ),
        _FakeResponse(
            200,
            {
                "bars": {"AAPL": [{"t": "2024-01-01T16:00:00Z", "o": 3, "h": 3, "l": 3, "c": 3, "v": 30}]},
                "next_page_token": None,
            },
        ),
    ]
    fake_httpx = SimpleNamespace(Client=lambda timeout=30: _FakeClient(responses, calls))
    monkeypatch.setitem(__import__("sys").modules, "httpx", fake_httpx)

    df = md.fetch_alpaca_intraday("AAPL", timeframe="15Min", days_back=1)

    assert list(calls) == [None, "p2", "p2", "p3"]
    assert list(df["date"].astype(str)) == [
        "2024-01-01 15:30:00+00:00",
        "2024-01-01 15:45:00+00:00",
        "2024-01-01 16:00:00+00:00",
    ]


def test_fetch_yf_configures_local_cache(monkeypatch):
    pytest.importorskip("pandas")
    from app.services import market_data as md

    calls = {}

    class DummyTicker:
        def __init__(self, symbol):
            self.symbol = symbol

        def history(self, period=None, start=None, end=None, auto_adjust=True):
            import pandas as pd

            return pd.DataFrame(
                {
                    "Open": [1.0] * 50,
                    "High": [1.0] * 50,
                    "Low": [1.0] * 50,
                    "Close": [1.0] * 50,
                    "Volume": [100] * 50,
                },
                index=pd.date_range("2024-01-01", periods=50, tz="UTC"),
            )

    fake_yf = SimpleNamespace(
        set_tz_cache_location=lambda path: calls.setdefault("cache_path", path),
        Ticker=DummyTicker,
    )
    monkeypatch.setitem(__import__("sys").modules, "yfinance", fake_yf)
    monkeypatch.setattr(md, "_YF_CACHE_CONFIGURED", False)

    df = md.fetch_yf("AAPL", start="2024-01-01", end="2024-03-31")

    assert calls["cache_path"].endswith("data\\yfinance_cache") or calls["cache_path"].endswith("data/yfinance_cache")
    assert len(df) == 50
