"""Tests for instant-open — lastgood cache fallback.

Verifies that:
1. When fresh+stale caches are empty, lastgood is served with honest stamps.
2. When no cache exists at all, the legacy "scanning" payload is returned.
3. _run_screener_bg writes both the main key and the lastgood key.
"""

import asyncio
import time

import app.workspace_server as ws


# ── helpers ──────────────────────────────────────────────────────────

def _make_cache_result():
    return {
        "total_scanned": 650, "halal_count": 350,
        "results_count": 50, "qualified_count": 5, "watch_count": 20,
        "min_score": 60, "strong_gate": 75,
        "market_status": "RISK ON", "regime": "NEUTRAL",
        "halt_pipeline": False,
        "results": [
            {"symbol": "AAPL", "composite_score": 82, "is_halal": True,
             "signal_composite": "STRONG BUY"},
        ],
    }


class TestSmartScreenerImplLastgood:
    """Tests for _smart_screener_impl lastgood fallback."""

    def test_serves_lastgood_when_caches_miss(self, monkeypatch):
        """Fresh+stale return None, lastgood exists → source=last_good."""
        calls = {}

        def _fake_cache_get(key, max_age=None):
            calls[key] = calls.get(key, 0) + 1
            if key == "smart_screener":
                return None  # fresh miss
            if key == "smart_screener_lastgood":
                return {**_make_cache_result(), "lastgood_asof": time.time() - 3600}
            return None

        monkeypatch.setattr(ws, "_cache_get", _fake_cache_get)
        monkeypatch.setattr(ws, "_cache_set", lambda k, v: None)

        # Prevent the background thread re-kick
        import threading
        class _FakeThread:
            def start(self): pass
        monkeypatch.setattr(threading, "Thread", lambda *a, **kw: _FakeThread())

        # Prevent market_context call
        monkeypatch.setattr(
            "app.services.market_context.get_market_status",
            lambda: {"status": "RISK ON", "regime": "NEUTRAL",
                     "min_gate": 60, "strong_gate": 75, "halt_pipeline": False},
        )
        monkeypatch.setattr(
            "app.services.watchlist_service.get_watchlist_set",
            lambda: set(),
        )

        result = asyncio.run(ws._smart_screener_impl())

        assert result["source"] == "last_good"
        assert result["refreshing"] is True
        assert result["asof"] is not None
        assert len(result["results"]) >= 1
        assert result["results"][0]["symbol"] == "AAPL"

    def test_scanning_when_no_cache_at_all(self, monkeypatch):
        """No caches exist at all → legacy scanning payload."""
        def _fake_cache_get(key, max_age=None):
            return None  # all miss

        monkeypatch.setattr(ws, "_cache_get", _fake_cache_get)
        monkeypatch.setattr(ws, "_cache_set", lambda k, v: None)

        import threading
        class _FakeThread:
            def start(self): pass
        monkeypatch.setattr(threading, "Thread", lambda *a, **kw: _FakeThread())

        monkeypatch.setattr(
            "app.services.market_context.get_market_status",
            lambda: {"status": "RISK ON", "regime": "NEUTRAL",
                     "min_gate": 60, "strong_gate": 75, "halt_pipeline": False},
        )
        monkeypatch.setattr(
            "app.services.watchlist_service.get_watchlist_set",
            lambda: set(),
        )

        result = asyncio.run(ws._smart_screener_impl())

        assert result["source"] == "scanning"
        assert result["status"] == "scanning"


class TestRunScreenerBgWritesLastgood:
    """Verify _run_screener_bg writes the lastgood key."""

    def test_lastgood_written_alongside_main_key(self, monkeypatch):
        """After a successful scan, both keys are cached."""
        cache_writes = {}

        def _record_cache_set(key, value):
            cache_writes[key] = value

        monkeypatch.setattr(ws, "_cache_set", _record_cache_set)

        # Patch _analyze_smart to return a result
        def _fake_analyze(sym, watchlist_set, spy_df_shared):
            return {"symbol": sym, "is_halal": True, "smart_score": 80}

        monkeypatch.setattr(ws, "_analyze_smart", _fake_analyze)

        # No-op patches
        monkeypatch.setattr(
            "app.services.watchlist_service.get_watchlist_set", lambda: set(),
        )
        monkeypatch.setattr(ws, "_score_forecast_consensus", lambda sym: None)
        monkeypatch.setattr(
            "app.services.market_context.get_market_status",
            lambda: {"status": "RISK ON", "regime": "NEUTRAL",
                     "min_gate": 60, "strong_gate": 75, "halt_pipeline": False},
        )
        monkeypatch.setattr(
            "app.services.market_data.fetch_alpaca_batch",
            lambda symbols, period="1y": {},
        )
        monkeypatch.setattr(
            "app.services.telegram_alert.alert_qualified_signal",
            lambda **kw: None,
        )

        import pandas as pd
        import numpy as np
        _mock_bars = pd.DataFrame({
            "open": np.ones(100) * 100, "high": np.ones(100) * 105,
            "low": np.ones(100) * 95, "close": np.ones(100) * 102,
            "volume": np.ones(100) * 1e6,
        }, index=pd.date_range("2026-01-01", periods=100, freq="B"))

        def _fake_fetch(sym, period="2mo"):
            return (True, _mock_bars.copy())

        monkeypatch.setattr(ws, "_fetch_data", _fake_fetch)

        ws._run_screener_bg(["GOOD"])

        assert "smart_screener" in cache_writes, "Main cache key missing"
        assert "smart_screener_lastgood" in cache_writes, "Lastgood key missing"

        lastgood = cache_writes["smart_screener_lastgood"]
        assert lastgood.get("lastgood_asof") is not None
        assert len(lastgood.get("results", [])) >= 1
