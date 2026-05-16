"""Tests for market-data retry, dedupe, and IBKR fetch behavior."""

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


# ---- fetch_ibkr tests ----


def _make_fake_bar(date_str, open_=100.0, high=101.0, low=99.0, close=100.5, volume=10000):
    """Build a SimpleNamespace mimicking ib_insync BarData."""
    import pandas as pd
    from types import SimpleNamespace
    return SimpleNamespace(
        date=pd.Timestamp(date_str, tz="UTC"),
        open=open_, high=high, low=low, close=close, volume=volume,
    )


def test_fetch_ibkr_skip_when_disabled(monkeypatch):
    """Returns None when IBKR_DATA_ENABLED is not 'true'."""
    monkeypatch.delenv("IBKR_DATA_ENABLED", raising=False)
    from app.services import market_data as md
    md.clear_market_data_cache()
    assert md.fetch_ibkr("AAPL") is None
    monkeypatch.setenv("IBKR_DATA_ENABLED", "false")
    assert md.fetch_ibkr("AAPL") is None
    monkeypatch.setenv("IBKR_DATA_ENABLED", "0")
    assert md.fetch_ibkr("AAPL") is None


def test_fetch_ibkr_breaker_open(monkeypatch):
    """Returns None when the IBKR circuit breaker is open."""
    monkeypatch.setenv("IBKR_DATA_ENABLED", "true")
    from app.services import market_data as md
    md._ibkr_breaker.failures = md._ibkr_breaker.failure_threshold
    md._ibkr_breaker.state = "open"
    md.clear_market_data_cache()
    try:
        assert md.fetch_ibkr("AAPL") is None
    finally:
        md._ibkr_breaker.failures = 0
        md._ibkr_breaker.state = "closed"


def test_fetch_ibkr_connect_failure(monkeypatch):
    """Returns None when _connect returns None (gateway unreachable)."""
    monkeypatch.setenv("IBKR_DATA_ENABLED", "true")
    import pandas as pd
    from types import SimpleNamespace
    calls = {}
    fake_ib = SimpleNamespace(reqMarketDataType=lambda *a, **kw: None)

    def fake_connect(strategy_id=None):
        calls["connect"] = True
        return None

    def fake_stock(symbol):
        calls["stock_sym"] = symbol
        return SimpleNamespace(symbol=symbol, currency="USD", exchange="SMART")

    def fake_call_ib(ib, method, *args, timeout=15, **kwargs):
        calls[f"call_{method}"] = (args, kwargs)
        if method == "reqMarketDataType":
            return None
        if method == "reqHistoricalData":
            bar = _make_fake_bar("2024-01-01")
            return [bar]
        return None

    def fake_load():
        calls["load"] = True

    monkeypatch.setattr("app.services.broker.ibkr_adapter._connect", fake_connect)
    monkeypatch.setattr("app.services.broker.ibkr_adapter._stock", fake_stock)
    monkeypatch.setattr("app.services.broker.ibkr_adapter._call_ib", fake_call_ib)
    monkeypatch.setattr("app.services.broker.ibkr_adapter._load_ib_insync", fake_load)

    # Mock socket pre-check to pass (gateway reachable)
    _fake_sock = SimpleNamespace(close=lambda: None)
    monkeypatch.setattr("socket.create_connection", lambda *a, **kw: _fake_sock)

    from app.services import market_data as md
    md.clear_market_data_cache()
    md._ibkr_breaker.failures = 0
    md._ibkr_breaker.state = "closed"

    result = md.fetch_ibkr("AAPL")
    assert result is None
    assert calls.get("connect") is True


def test_fetch_ibkr_empty_bars(monkeypatch):
    """Returns None when reqHistoricalData returns empty list."""
    monkeypatch.setenv("IBKR_DATA_ENABLED", "true")
    import pandas as pd
    from types import SimpleNamespace
    calls = {}
    fake_ib = SimpleNamespace(reqMarketDataType=lambda *a, **kw: None)

    def fake_connect(strategy_id=None):
        calls["connect"] = True
        return fake_ib

    def fake_stock(symbol):
        calls["stock_sym"] = symbol
        return SimpleNamespace(symbol=symbol, currency="USD", exchange="SMART")

    def fake_call_ib(ib, method, *args, timeout=15, **kwargs):
        calls[f"call_{method}"] = (args, kwargs)
        return []

    def fake_load():
        calls["load"] = True

    monkeypatch.setattr("app.services.broker.ibkr_adapter._connect", fake_connect)
    monkeypatch.setattr("app.services.broker.ibkr_adapter._stock", fake_stock)
    monkeypatch.setattr("app.services.broker.ibkr_adapter._call_ib", fake_call_ib)
    monkeypatch.setattr("app.services.broker.ibkr_adapter._load_ib_insync", fake_load)

    # Mock socket pre-check to pass (gateway reachable)
    _fake_sock = SimpleNamespace(close=lambda: None)
    monkeypatch.setattr("socket.create_connection", lambda *a, **kw: _fake_sock)

    from app.services import market_data as md
    md.clear_market_data_cache()
    md._ibkr_breaker.failures = 0
    md._ibkr_breaker.state = "closed"

    assert md.fetch_ibkr("AAPL") is None


def test_fetch_ibkr_success(monkeypatch):
    """Returns DataFrame on successful ib_insync historical data."""
    monkeypatch.setenv("IBKR_DATA_ENABLED", "true")
    import pandas as pd
    from types import SimpleNamespace
    calls = {}
    fake_ib = SimpleNamespace(reqMarketDataType=lambda *a, **kw: None)

    # Generate 250 fake bars (1 year of trading days)
    fake_bars = []
    for i in range(250):
        d = pd.Timestamp("2024-01-01", tz="UTC") + pd.Timedelta(days=i)
        fake_bars.append(_make_fake_bar(
            str(d),
            open_=100.0 + i * 0.1,
            high=101.0 + i * 0.1,
            low=99.0 + i * 0.1,
            close=100.5 + i * 0.1,
            volume=10000 + i * 10,
        ))

    def fake_connect(strategy_id=None):
        calls["connect"] = True
        return fake_ib

    def fake_stock(symbol):
        calls["stock_sym"] = symbol
        return SimpleNamespace(symbol=symbol, currency="USD", exchange="SMART")

    def fake_call_ib(ib, method, *args, timeout=15, **kwargs):
        calls[f"call_{method}"] = (args, kwargs)
        if method == "reqMarketDataType":
            return None
        if method == "reqHistoricalData":
            return fake_bars
        return None

    def fake_load():
        calls["load"] = True

    monkeypatch.setattr("app.services.broker.ibkr_adapter._connect", fake_connect)
    monkeypatch.setattr("app.services.broker.ibkr_adapter._stock", fake_stock)
    monkeypatch.setattr("app.services.broker.ibkr_adapter._call_ib", fake_call_ib)
    monkeypatch.setattr("app.services.broker.ibkr_adapter._load_ib_insync", fake_load)

    # Mock socket pre-check to pass (gateway reachable)
    _fake_sock = SimpleNamespace(close=lambda: None)
    monkeypatch.setattr("socket.create_connection", lambda *a, **kw: _fake_sock)

    from app.services import market_data as md
    md.clear_market_data_cache()
    md._ibkr_breaker.failures = 0
    md._ibkr_breaker.state = "closed"

    df = md.fetch_ibkr("AAPL")
    assert df is not None
    assert len(df) == 250
    assert list(df.columns) == ["date", "open", "high", "low", "close", "volume"]
    assert df["date"].is_monotonic_increasing
    assert df.iloc[0]["close"] == 100.5
    assert df.iloc[-1]["close"] == 100.5 + 249 * 0.1
    assert calls.get("load") is True
    assert calls.get("stock_sym") == "AAPL"


def test_fetch_ibkr_bad_symbol(monkeypatch):
    """Returns None for invalid symbols."""
    monkeypatch.setenv("IBKR_DATA_ENABLED", "true")
    from app.services import market_data as md
    md.clear_market_data_cache()
    assert md.fetch_ibkr("") is None
    assert md.fetch_ibkr("LIY") is None


def test_fetch_priority_ibkr_integration(monkeypatch):
    """fetch() tries IBKR first when IBKR_DATA_ENABLED=true, even without real connection."""
    monkeypatch.setenv("IBKR_DATA_ENABLED", "true")
    import pandas as pd
    from types import SimpleNamespace

    # Mock ib_insync to return data
    import datetime
    base = datetime.date(2024, 1, 1)
    fake_bars = [_make_fake_bar(str(base + datetime.timedelta(days=i))) for i in range(249)]
    fake_ib = SimpleNamespace(reqMarketDataType=lambda *a, **kw: None)

    def fake_connect(strategy_id=None):
        return fake_ib

    def fake_stock(symbol):
        return SimpleNamespace(symbol=symbol, currency="USD", exchange="SMART")

    def fake_call_ib(ib, method, *args, timeout=15, **kwargs):
        if method == "reqMarketDataType":
            return None
        if method == "reqHistoricalData":
            return fake_bars
        return None

    def fake_load():
        pass

    monkeypatch.setattr("app.services.broker.ibkr_adapter._connect", fake_connect)
    monkeypatch.setattr("app.services.broker.ibkr_adapter._stock", fake_stock)
    monkeypatch.setattr("app.services.broker.ibkr_adapter._call_ib", fake_call_ib)
    monkeypatch.setattr("app.services.broker.ibkr_adapter._load_ib_insync", fake_load)

    # Mock socket pre-check to pass (gateway reachable)
    _fake_sock = SimpleNamespace(close=lambda: None)
    monkeypatch.setattr("socket.create_connection", lambda *a, **kw: _fake_sock)

    # Need Alpaca settings to be present (but won't be called)
    from app.config import settings
    monkeypatch.setattr(settings, "ALPACA_API_KEY", "key")
    monkeypatch.setattr(settings, "ALPACA_SECRET_KEY", "secret")

    import app.services.market_data as md
    monkeypatch.setattr(md, "_alpaca_rate_limit", lambda: None)
    monkeypatch.setattr(md.time, "sleep", lambda *a: None)
    md.clear_market_data_cache()
    md._ibkr_breaker.failures = 0
    md._ibkr_breaker.state = "closed"

    # Mock httpx too so alpaca doesn't interfere
    fake_httpx = SimpleNamespace(Client=lambda timeout=30: SimpleNamespace(
        __enter__=lambda s: SimpleNamespace(get=lambda *a, **kw: SimpleNamespace(
            status_code=200, json=lambda: {"bars": {}, "next_page_token": None},
            raise_for_status=lambda: None,
        )),
        __exit__=lambda *a: False,
    ))
    monkeypatch.setitem(__import__("sys").modules, "httpx", fake_httpx)

    df = md.fetch("AAPL")
    assert df is not None
    assert len(df) == 249


def test_fetch_ibkr_socket_timeout(monkeypatch):
    """Returns None and records breaker failure when socket pre-check fails."""
    monkeypatch.setenv("IBKR_DATA_ENABLED", "true")
    from types import SimpleNamespace

    def fake_socket_timeout(*a, **kw):
        raise OSError("Connection refused")

    monkeypatch.setattr("socket.create_connection", fake_socket_timeout)

    from app.services import market_data as md
    md.clear_market_data_cache()
    md._ibkr_breaker.failures = 0
    md._ibkr_breaker.state = "closed"

    result = md.fetch_ibkr("AAPL")
    assert result is None
    assert md._ibkr_breaker.failures == 1

    # Cleanup
    md._ibkr_breaker.failures = 0
    md._ibkr_breaker.state = "closed"
