"""Tests for screener crash resilience — stuck-flag bug fix.

Verifies that:
1. One bad symbol can never freeze the scanner (per-symbol try/except).
2. The progress flag ALWAYS leaves "scanning" (try/finally guarantee).
3. Stuck scans (>10 min) trigger a re-kick via _screener_is_stuck.
"""

import time

import app.workspace_server as ws


class TestScreenerIsStuck:
    """Unit tests for the _screener_is_stuck helper."""

    def test_not_stuck_when_done(self):
        """A 'done' status is never stuck."""
        assert not ws._screener_is_stuck({"status": "done", "started": 0})

    def test_not_stuck_when_fresh(self):
        """A fresh 'scanning' run is not stuck."""
        now = time.time()
        assert not ws._screener_is_stuck(
            {"status": "scanning", "started": now - 30}, now=now,
        )

    def test_stuck_when_expired(self):
        """A 'scanning' run older than max_age is stuck."""
        now = time.time()
        assert ws._screener_is_stuck(
            {"status": "scanning", "started": now - 700}, now=now,
        )

    def test_no_started_key(self):
        """Missing 'started' key → stuck (no advance since epoch is far past stall)."""
        now = time.time()
        assert ws._screener_is_stuck(
            {"status": "scanning"}, now=now,
        )

    def test_not_stuck_when_advancing(self):
        """A slow-but-ADVANCING scan (old start, recent batch) is NOT stuck — the
        real ~12-min full scan must be allowed to finish instead of being re-kicked."""
        now = time.time()
        assert not ws._screener_is_stuck(
            {"status": "scanning", "started": now - 900, "last_advance": now - 20}, now=now,
        )

    def test_stuck_when_stalled(self):
        """No batch advanced in > stall seconds → stuck, even if it started recently."""
        now = time.time()
        assert ws._screener_is_stuck(
            {"status": "scanning", "started": now - 600, "last_advance": now - 500}, now=now,
        )


class TestRunScreenerBgCrashProof:
    """Verify the try/finally + per-symbol isolation fix."""

    def test_bad_symbol_does_not_abort_scan(self, monkeypatch):
        """One failing symbol → scan continues, flag leaves 'scanning'."""
        # Patch _analyze_smart: raise for BAD, return dict for GOOD
        def _fake_analyze(sym, watchlist_set, spy_df_shared):
            if sym == "BAD":
                raise RuntimeError("simulated crash")
            return {"symbol": sym, "is_halal": True, "smart_score": 80}

        monkeypatch.setattr(ws, "_analyze_smart", _fake_analyze)

        # Patch watchlist fetcher to no-op
        monkeypatch.setattr(
            "app.services.watchlist_service.get_watchlist_set",
            lambda: set(),
        )

        # Patch _score_forecast_consensus to no-op
        monkeypatch.setattr(ws, "_score_forecast_consensus", lambda sym: None)

        # Patch market_context.get_market_status
        monkeypatch.setattr(
            "app.services.market_context.get_market_status",
            lambda: {"status": "RISK ON", "regime": "NEUTRAL",
                     "min_gate": 60, "strong_gate": 75, "halt_pipeline": False},
        )

        # Patch telegram alert
        monkeypatch.setattr(
            "app.services.telegram_alert.alert_qualified_signal",
            lambda **kw: None,
        )

        # Patch Alpaca batch pre-fetch to return empty (falls through)
        monkeypatch.setattr(
            "app.services.market_data.fetch_alpaca_batch",
            lambda symbols, period="1y": {},
        )

        # Patch _fetch_data to return valid SPY + per-symbol data
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

        # Run the scan
        ws._run_screener_bg(["BAD", "GOOD"])

        # THE TEST: progress flag must NOT be "scanning"
        assert ws._screener_progress["status"] != "scanning", (
            f"Flag stuck on 'scanning': {ws._screener_progress}"
        )
        # It should be "done"
        assert ws._screener_progress["status"] == "done"

    def test_progress_flag_always_reset(self, monkeypatch):
        """Even if EVERY symbol fails, the flag still leaves 'scanning'."""
        def _fake_analyze(sym, *a, **kw):
            raise RuntimeError("all symbols crash")

        monkeypatch.setattr(ws, "_analyze_smart", _fake_analyze)
        monkeypatch.setattr(
            "app.services.watchlist_service.get_watchlist_set",
            lambda: set(),
        )

        # Patch everything that runs after the batch loop so they don't
        # accidentally pass and create a cache result
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
        monkeypatch.setattr(ws, "_cache_set", lambda k, v: None)

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

        # Run — should NOT raise
        ws._run_screener_bg(["BAD1", "BAD2"])

        # Flag must be done
        assert ws._screener_progress["status"] == "done"
