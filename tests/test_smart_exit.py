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

from app.services.smart_exit import (
    compute_exit_indicators, live_exit_decision, partial_tp_hit, post_entry_bars, simulate_smart_exit)


def _bars(rows: list[dict]) -> pd.DataFrame:
    idx = pd.date_range("2024-01-02", periods=len(rows), freq="B")
    return pd.DataFrame(rows, index=idx)


# ── 1. catastrophe stop ───────────────────────────────────────────────────────

def test_catastrophe_stop_fires():
    # No ATR column → the base 15% stop is capped by the −10% safety net.
    post = _bars([
        {"close": 98, "high": 99, "low": 97},
        {"close": 90, "high": 92, "low": 88},   # low 88 ≤ 90 (10% safety net)
        {"close": 84, "high": 86, "low": 83},
    ])
    res = simulate_smart_exit(post, 100.0, stop_pct=15, hold_days=20)
    assert res is not None
    ret, price, reason = res
    assert reason == "stop"
    assert price == 90.0 and ret == -10.0


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


# ── 7. post-entry slicing (the market_data.fetch 'date'-column shape) ──────────

def _fetch_shaped(n: int = 30):
    """Mimic market_data.fetch: RangeIndex + tz-aware 'date' COLUMN (NOT index)."""
    dates = pd.date_range("2024-05-01", periods=n, freq="B", tz="UTC")
    return pd.DataFrame({
        "date": dates,
        "close": [100 + i for i in range(n)],
        "high": [101 + i for i in range(n)],
        "low": [99 + i for i in range(n)],
    })  # default RangeIndex


def test_post_entry_bars_slices_on_date_column():
    df = _fetch_shaped(30)
    cutoff = df["date"].iloc[20]            # entered at bar 20
    post = post_entry_bars(df, cutoff, tail=5)
    assert len(post) == 9                   # bars 21..29 are strictly after
    assert (pd.to_datetime(post["date"], utc=True) > cutoff).all()


def test_post_entry_bars_naive_timestamp_ok():
    # A tz-naive DB created_at must not raise against the tz-aware 'date' column.
    df = _fetch_shaped(30)
    naive = df["date"].iloc[25].tz_localize(None).to_pydatetime()
    post = post_entry_bars(df, naive, tail=5)
    assert len(post) == 4                    # bars 26..29


def test_post_entry_bars_entry_after_all_is_empty():
    df = _fetch_shaped(30)
    future = df["date"].iloc[-1] + pd.Timedelta(days=10)
    assert len(post_entry_bars(df, future, tail=5)) == 0   # not matured, NOT a tail fallback


def test_post_entry_bars_none_falls_back_to_tail():
    df = _fetch_shaped(30)
    assert len(post_entry_bars(df, None, tail=5)) == 5


# ── 8. live (forward) exit decision — current-bar, peak-since-entry ────────────

def test_live_winner_at_peak_is_held():
    # THE case: a position up +30% sitting AT its peak must HOLD — never flattened
    # at a stale earlier trigger (this is what simulate_smart_exit would mis-do).
    post = _bars([
        {"close": 110, "high": 111, "low": 109},
        {"close": 122, "high": 123, "low": 121},
        {"close": 130, "high": 131, "low": 129},   # latest = at the peak
    ])
    assert live_exit_decision(post, 100.0, stop_pct=15, hold_days=None) is None


def test_live_trailing_locks_current_price_off_peak():
    # Peak 131, now 120 (>5% off peak) → trailing at the CURRENT price, not a stale level.
    post = _bars([
        {"close": 110, "high": 111, "low": 109},
        {"close": 130, "high": 131, "low": 129},   # peak
        {"close": 120, "high": 121, "low": 119},   # gave back from peak
    ])
    out = live_exit_decision(post, 100.0, stop_pct=15, hold_days=None)
    assert out is not None
    reason, price, ret = out
    assert reason == "trailing" and price == 120.0 and ret == 20.0


def test_live_catastrophe_stop_current_bar():
    post = _bars([{"close": 95, "high": 96, "low": 94}, {"close": 86, "high": 88, "low": 84}])
    out = live_exit_decision(post, 100.0, stop_pct=15, hold_days=None)
    assert out is not None and out[0] == "stop" and out[1] == 90.0   # 10% safety net


def test_live_weakening_on_latest_bar():
    post = _bars([
        {"close": 103, "high": 104, "low": 102, "_ema10": 102, "_rsi": 58, "_macd_hist": 0.3},
        {"close": 104, "high": 105, "low": 103, "_ema10": 106, "_rsi": 46, "_macd_hist": -0.2},
    ])
    out = live_exit_decision(post, 100.0, stop_pct=15, hold_days=None)
    assert out is not None and out[0] == "weakening" and out[1] == 104.0


def test_live_time_backstop_optional():
    flat = _bars([{"close": 101, "high": 102, "low": 100} for _ in range(3)])
    assert live_exit_decision(flat, 100.0, stop_pct=15, hold_days=None) is None   # disabled
    out = live_exit_decision(flat, 100.0, stop_pct=15, hold_days=3)               # enabled
    assert out is not None and out[0] == "time"


# ── 9. partial take-profit trigger ────────────────────────────────────────────

def test_partial_tp_fires_at_target():
    post = _bars([{"close": 103, "high": 104, "low": 102},
                  {"close": 107, "high": 108, "low": 106}])   # +7% latest
    hit = partial_tp_hit(post, 100.0, tp1_pct=6)
    assert hit is not None and hit[0] == 107.0 and hit[1] == 7.0


def test_partial_tp_holds_below_target():
    post = _bars([{"close": 104, "high": 105, "low": 103}])   # +4% < 6%
    assert partial_tp_hit(post, 100.0, tp1_pct=6) is None


def test_partial_tp_guards_bad_input():
    assert partial_tp_hit(None, 100.0) is None
    assert partial_tp_hit(_bars([{"close": 110, "high": 111, "low": 109}]), 0.0) is None


# ── 10. loss discipline: ATR stop + safety net + breakdown + break-even ───────

def test_effective_stop_is_atr_scaled_and_clamped():
    from app.services.smart_exit import _effective_stop_pct
    assert _effective_stop_pct(_bars([{"close": 100, "high": 100, "low": 100, "_atr_pct": 1.0}]), 15) == 5.0   # 2.5·1 → min 5
    assert _effective_stop_pct(_bars([{"close": 100, "high": 100, "low": 100, "_atr_pct": 3.0}]), 15) == 7.5   # 2.5·3 in range
    assert _effective_stop_pct(_bars([{"close": 100, "high": 100, "low": 100, "_atr_pct": 6.0}]), 15) == 10.0  # 2.5·6 → max 10 (safety net)


def test_safety_net_caps_stop_without_atr():
    from app.services.smart_exit import _effective_stop_pct
    assert _effective_stop_pct(_bars([{"close": 100, "high": 100, "low": 100}]), 15) == 10.0   # no ATR → capped at 10


def test_technical_breakdown_cuts_a_loser():
    # Down only −4% but the thesis broke (close < EMA20 AND MACD < 0) → cut now,
    # don't wait for the price stop.
    post = _bars([{"close": 96, "high": 97, "low": 95, "_ema20": 100, "_macd_hist": -0.3}])
    out = live_exit_decision(post, 100.0, stop_pct=15, hold_days=None)
    assert out is not None and out[0] == "breakdown" and out[1] == 96.0 and out[2] == -4.0


def test_breakeven_stop_after_partial():
    # After a partial (breakeven=True), a pullback to entry exits flat, not at a loss.
    post = _bars([{"close": 100, "high": 101, "low": 99}])
    out = live_exit_decision(post, 100.0, stop_pct=15, hold_days=None, breakeven=True)
    assert out is not None and out[0] == "breakeven" and out[2] == 0.0
    # without breakeven the 10% stop is far below → hold
    assert live_exit_decision(post, 100.0, stop_pct=15, hold_days=None, breakeven=False) is None
