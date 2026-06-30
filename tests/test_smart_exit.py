"""Tests for the smart exit manager (lock winners, cut weakening ones).

Covers each trigger in isolation:
1. catastrophe stop fires on an intrabar break below entry·(1−stop).
2. trailing stop locks a runner that gives back from its peak — and the locked
   return is positive (a trailing exit never books a loss).
3. technical-weakening exits a profitable position whose chart rolls over.
4. time exit is the backstop when nothing else fires.
5. not-matured → None (fewer than hold_days bars, no trigger).
6. compute_exit_indicators attaches the indicator columns.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.services.smart_exit import compute_exit_indicators, simulate_smart_exit


def _bars(rows: list[dict]) -> pd.DataFrame:
    idx = pd.date_range("2024-01-02", periods=len(rows), freq="B")
    return pd.DataFrame(rows, index=idx)


# ── 1. catastrophe stop ───────────────────────────────────────────────────────

def test_catastrophe_stop_fires():
    post = _bars([
        {"close": 98, "high": 99, "low": 97},
        {"close": 90, "high": 92, "low": 88},
        {"close": 84, "high": 86, "low": 83},   # low 83 ≤ 85 stop
    ])
    res = simulate_smart_exit(post, 100.0, stop_pct=15, hold_days=20)
    assert res is not None
    ret, price, reason = res
    assert reason == "stop"
    assert price == 85.0 and ret == -15.0


# ── 2. trailing stop locks a runner ───────────────────────────────────────────

def test_trailing_stop_locks_gain():
    # Runs to +20% (arms past +8%), then a close 5%+ off the peak → exit in profit.
    post = _bars([
        {"close": 102, "high": 103, "low": 101},
        {"close": 108, "high": 109, "low": 107},
        {"close": 115, "high": 116, "low": 114},
        {"close": 120, "high": 121, "low": 119},   # peak 121
        {"close": 113, "high": 114, "low": 112},   # 113 ≤ 121·0.95 → trail
    ])
    res = simulate_smart_exit(post, 100.0, stop_pct=15, hold_days=20)
    assert res is not None
    ret, price, reason = res
    assert reason == "trailing"
    assert price == 113.0 and ret == 13.0
    assert ret > 0, "a trailing exit must never book a loss"


# ── 3. technical weakening ────────────────────────────────────────────────────

def test_technical_weakening_exit():
    # In profit (+4%) but close rolls below EMA10 with RSI<50 → protect the gain.
    post = _bars([
        {"close": 102, "high": 102.5, "low": 101, "_ema10": 101, "_rsi": 60, "_macd_hist": 0.5},
        {"close": 104, "high": 104.5, "low": 103, "_ema10": 106, "_rsi": 45, "_macd_hist": -0.2},
    ])
    res = simulate_smart_exit(post, 100.0, stop_pct=15, hold_days=20)
    assert res is not None
    ret, price, reason = res
    assert reason == "weakening"
    assert price == 104.0 and ret == 4.0


def test_weakening_does_not_fire_below_min_profit():
    # Same rollover but NOT in profit → weakening must not fire (stop owns losers).
    post = _bars([
        {"close": 101, "high": 101.5, "low": 100, "_ema10": 103, "_rsi": 40, "_macd_hist": -0.3},
        {"close": 100, "high": 101, "low": 99, "_ema10": 103, "_rsi": 40, "_macd_hist": -0.3},
    ])
    res = simulate_smart_exit(post, 100.0, stop_pct=15, hold_days=2)
    assert res is not None
    assert res[2] == "time", f"expected time backstop, got {res[2]}"


# ── 4 & 5. time backstop / not matured ────────────────────────────────────────

def test_time_exit_backstop():
    post = _bars([
        {"close": 100, "high": 101, "low": 99},
        {"close": 101, "high": 102, "low": 100},
        {"close": 100, "high": 101, "low": 99},
    ])
    res = simulate_smart_exit(post, 100.0, stop_pct=15, hold_days=3)
    assert res is not None and res[2] == "time"
    assert res[1] == 100.0


def test_not_matured_returns_none():
    post = _bars([
        {"close": 100, "high": 101, "low": 99},
        {"close": 101, "high": 102, "low": 100},
    ])
    # hold_days far beyond the 2 bars available, nothing triggers → still open
    assert simulate_smart_exit(post, 100.0, stop_pct=15, hold_days=20) is None


# ── 6. indicator attachment ───────────────────────────────────────────────────

def test_compute_exit_indicators_attaches_columns():
    df = _bars([{"close": 100 + i * 0.5, "high": 101 + i * 0.5, "low": 99 + i * 0.5}
                for i in range(40)])
    out = compute_exit_indicators(df)
    for col in ("_ema10", "_rsi", "_macd_hist"):
        assert col in out.columns
    assert np.isfinite(out["_ema10"].iloc[-1])
