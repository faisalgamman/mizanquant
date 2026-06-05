"""Tests for USX Pro V4 filter — cache, source parity, and UnboundLocalError regression.

Covers: F-1 (global cache crash), F-2 (source parity), T1 + T6.
"""
from __future__ import annotations

import time

import numpy as np
import pandas as pd

import app.services.usx_pro_filter as u


# ── helpers ──────────────────────────────────────────────────────────────


def _fake_df(n_rows: int = 260) -> pd.DataFrame:
    """Synthetic daily OHLCV with a mild uptrend so gates pass."""
    rng = np.random.default_rng(42)
    close = 100 + np.cumsum(rng.normal(0.05, 0.80, n_rows))
    high = close + rng.uniform(0.1, 1.0, n_rows)
    low = close - rng.uniform(0.1, 1.0, n_rows)
    open_ = close - rng.uniform(-0.3, 0.3, n_rows)
    volume = rng.integers(1_000_000, 10_000_000, n_rows).astype(float)
    return pd.DataFrame({
        "open": open_, "high": high, "low": low,
        "close": close, "volume": volume,
    })


def _fake_regime() -> u.RegimeReport:
    return u.RegimeReport(
        spy_bull=True, vix_rank=0.2, vix_ok=True,
        credit_ok=True, breadth_ok=True, overall_ok=True, reason="mock",
    )


# ── T1: UnboundLocalError regression (F-1) ──────────────────────────────


def test_filter_universe_cache_hit_no_unboundlocal(monkeypatch):
    """Two back-to-back filter_universe calls within TTL must not raise
    UnboundLocalError.  Second call must reuse the cached batch."""
    # Reset cache state
    u._ohclv_batch_cache = {"data": {}, "ts": 0.0}

    captured_symbols: list = []

    def _mock_fetch_yf(symbol: str, period: str = "2y"):
        captured_symbols.append(symbol)
        return _fake_df()

    monkeypatch.setattr(u, "_fetch_daily", _mock_fetch_yf)
    monkeypatch.setattr(u, "check_market_regime", lambda use_cache=True: _fake_regime())
    # Also patch halal_screener.fetch_yf used by batch path
    monkeypatch.setattr("halal_screener.fetch_yf", _mock_fetch_yf)

    symbols = ["AAPL", "MSFT", "NVDA"]

    # First call — should build cache
    out1, _ = u.filter_universe(symbols)
    assert isinstance(out1, list)

    first_fetch_count = len(captured_symbols)
    captured_symbols.clear()

    # Second call within TTL — must reuse cache, NOT refetch
    out2, _ = u.filter_universe(symbols)
    assert isinstance(out2, list)

    second_fetch_count = len(captured_symbols)
    assert second_fetch_count == 0, (
        f"Second call fetched {second_fetch_count} symbols — cache NOT reused"
    )


# ── T1b: cache expiry ───────────────────────────────────────────────────


def test_filter_universe_cache_expiry(monkeypatch):
    """After TTL expires, a fresh batch must be fetched."""
    u._ohclv_batch_cache = {"data": {}, "ts": 0.0}
    u._OHLCV_CACHE_TTL_S = 0  # expire immediately

    captured: list = []

    def _mock_fetch_yf(symbol: str, period: str = "2y"):
        captured.append(symbol)
        return _fake_df()

    monkeypatch.setattr(u, "_fetch_daily", _mock_fetch_yf)
    monkeypatch.setattr(u, "check_market_regime", lambda use_cache=True: _fake_regime())
    monkeypatch.setattr("halal_screener.fetch_yf", _mock_fetch_yf)

    u.filter_universe(["AAPL"])
    first_count = len(captured)
    captured.clear()

    u.filter_universe(["AAPL"])
    second_count = len(captured)
    assert second_count > 0, "Expired cache should trigger re-fetch"


# ── T6: source parity — yfinance batch vs per-symbol ────────────────────


def test_score_symbol_source_parity(monkeypatch):
    """score_symbol with batch-injected df must match per-symbol fetch path.
    Both use the SAME underlying data (same random seed), so score/verdict
    must be identical."""
    rng = np.random.default_rng(12345)
    n = 260
    close = 100 + np.cumsum(rng.normal(0.06, 0.75, n))
    # ensure uptrend for the last 60 bars so d_bull gate passes
    close[-60:] += np.linspace(0, 8, 60)
    df = pd.DataFrame({
        "open":  close + rng.uniform(-0.3, 0.3, n),
        "high":  close + rng.uniform(0.2, 1.2, n),
        "low":   close - rng.uniform(0.2, 1.2, n),
        "close": close,
        "volume": rng.integers(2_000_000, 12_000_000, n).astype(float),
    })

    # Monkey-patch earnings check to always return None (no blackout)
    monkeypatch.setattr(u, "_next_earnings_days", lambda s: None)
    monkeypatch.setattr(u, "check_market_regime", lambda use_cache=True: _fake_regime())

    # Score with df injected (batch path)
    sc_batch = u.score_symbol("TEST", df=df)

    # Score without df (per-symbol path — will call _fetch_daily)
    monkeypatch.setattr(u, "_fetch_daily", lambda s, period="2y": df.copy())
    sc_per_sym = u.score_symbol("TEST")

    # Core assertions: verdict and score must match
    assert sc_batch.passes == sc_per_sym.passes, (
        f"Verdict mismatch: batch={sc_batch.passes} per_sym={sc_per_sym.passes}"
    )
    assert abs(sc_batch.score - sc_per_sym.score) <= 0.01, (
        f"Score mismatch: batch={sc_batch.score} per_sym={sc_per_sym.score}"
    )


# ── T1c: global declaration presence ────────────────────────────────────


def test_global_ohclv_batch_cache_declared():
    """F-1 regression guard: filter_universe MUST contain 'global _ohclv_batch_cache'."""
    import inspect
    src = inspect.getsource(u.filter_universe)
    assert "global _ohclv_batch_cache" in src, (
        "global _ohclv_batch_cache declaration MISSING — UnboundLocalError will crash every scan"
    )
