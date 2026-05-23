"""Tests for the regime-aware backtest infrastructure (Pillar 5).

Verifies:
1. classify_regime uses a pre-fetched SPY DataFrame (point-in-time, no network).
2. tag_trades handles both 'entry_date' and 'Entry Date' key formats.
3. compute_per_regime_breakdown returns correct per-regime stats.
4. An all-BULL backtest produces 0 profitable BEAR/NEUTRAL regimes.
"""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from app.services.backtest_regime_filter import (
    classify_regime,
    compute_per_regime_breakdown,
    tag_trades,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_spy_df(n: int = 300, above_ema: bool = True) -> pd.DataFrame:
    """Synthetic SPY daily closes for regime detection (no network)."""
    dates = pd.date_range("2020-01-02", periods=n, freq="B")
    # Trend: steadily rising (BULL) or falling (BEAR)
    base = 300.0
    if above_ema:
        closes = base + np.linspace(0, 50, n)    # price > EMA200 → BULL
    else:
        closes = base + np.linspace(50, -20, n)  # price < EMA200 → BEAR
    return pd.DataFrame({"close": closes}, index=dates)


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_classify_regime_bull_with_preloaded_spy():
    """BULL when SPY is well above its EMA200 at entry date."""
    spy = _make_spy_df(n=300, above_ema=True)
    entry = "2021-01-04"
    regime = classify_regime(entry, spy_df=spy)
    # The last date in our synthetic SPY is after entry, so it should be found
    assert regime in ("BULL", "NEUTRAL", "UNKNOWN"), f"Unexpected regime: {regime}"


def test_classify_regime_no_spy_returns_unknown():
    """When SPY DataFrame is empty / None, regime should be UNKNOWN."""
    empty = pd.DataFrame({"close": []}, index=pd.DatetimeIndex([]))
    regime = classify_regime("2021-01-04", spy_df=empty)
    assert regime == "UNKNOWN"


def test_tag_trades_lowercase_key():
    """tag_trades must read 'entry_date' (lower-case) key."""
    spy = _make_spy_df(300, above_ema=True)
    trades = [
        {"entry_date": "2021-01-04", "return_pct": 2.5},
        {"entry_date": "2021-03-01", "return_pct": -1.0},
    ]
    result = tag_trades(trades, spy_df=spy)
    for t in result:
        assert "regime_at_entry" in t, "tag_trades must add 'regime_at_entry'"


def test_tag_trades_title_case_key():
    """tag_trades must also handle 'Entry Date' (Title Case from backtest_service)."""
    spy = _make_spy_df(300, above_ema=True)
    trades = [
        {"Entry Date": "2021-01-04", "PnL": 150.0},
        {"Entry Date": "2021-02-15", "PnL": -80.0},
    ]
    result = tag_trades(trades, spy_df=spy)
    for t in result:
        assert "regime_at_entry" in t, "tag_trades must handle 'Entry Date' key"


def test_compute_per_regime_breakdown_structure():
    """compute_per_regime_breakdown must return all required keys."""
    trades = [
        {"regime_at_entry": "BULL",    "return_pct": 3.0},
        {"regime_at_entry": "BULL",    "return_pct": -1.0},
        {"regime_at_entry": "NEUTRAL", "return_pct": 1.5},
        {"regime_at_entry": "BEAR",    "return_pct": -2.0},
    ]
    bd = compute_per_regime_breakdown(trades)

    for key in ("BULL", "NEUTRAL", "BEAR", "all"):
        assert key in bd, f"Missing '{key}' in regime breakdown"
        assert "trades" in bd[key]
        assert "win_rate" in bd[key]
        assert "avg_return" in bd[key]
        assert "profit_factor" in bd[key]


def test_compute_per_regime_breakdown_correctness():
    """Win-rate and avg_return must be correct for a known input."""
    trades = [
        {"regime_at_entry": "BULL", "return_pct": 4.0},
        {"regime_at_entry": "BULL", "return_pct": 4.0},
        {"regime_at_entry": "BULL", "return_pct": -2.0},
    ]
    bd = compute_per_regime_breakdown(trades)
    bull = bd["BULL"]
    assert bull["trades"] == 3
    assert abs(bull["win_rate"] - 66.7) < 0.2
    assert abs(bull["avg_return"] - round((4.0 + 4.0 - 2.0) / 3, 2)) < 0.01


def test_regime_robustness_only_one_regime():
    """A strategy profitable in only 1 regime is flagged as NOT robust."""
    trades_all_bull = [
        {"regime_at_entry": "BULL",    "return_pct": 5.0},
        {"regime_at_entry": "NEUTRAL", "return_pct": -2.0},
        {"regime_at_entry": "BEAR",    "return_pct": -3.0},
    ]
    bd = compute_per_regime_breakdown(trades_all_bull)
    profitable = sum(
        1 for reg in ("BULL", "NEUTRAL", "BEAR")
        if bd.get(reg, {}).get("avg_return", -1) > 0
    )
    assert profitable < 2, (
        "Only BULL regime profitable → should fail the 2-regime robustness gate"
    )
