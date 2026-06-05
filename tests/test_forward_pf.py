"""D1 — forward-outcome simulation must mirror the live Option-A exit.

The forward PF that gates the real-money decision is only trustworthy if a
signal's outcome is measured at its ACTUAL exit (fixed 15% catastrophe stop or
the 20-day time exit), not a stale current-price snapshot.
"""
from __future__ import annotations

import pytest

pd = pytest.importorskip("pandas")

from app.services.signal_tracker import _simulate_fixed_exit


def _bars(lows, highs, closes):
    return pd.DataFrame({"low": lows, "high": highs, "close": closes})


def test_time_exit_when_stop_not_hit():
    # BUY @100, never drops to 85; 20 bars; close on day 20 = 110 -> +10%.
    closes = [100 + i for i in range(1, 21)]      # 101..120
    lows = [c - 1 for c in closes]                 # never <= 85
    highs = [c + 1 for c in closes]
    ret, exit_price = _simulate_fixed_exit(_bars(lows, highs, closes), 100.0, 20, 15.0, is_sell=False)
    assert exit_price == closes[19]                # day-20 close (time exit)
    assert ret == pytest.approx(20.0)              # 120/100 - 1


def test_catastrophe_stop_hit_first():
    # BUY @100; day 3 low pierces 85 -> exit at the stop, -15%.
    closes = [99, 98, 80, 90, 95]
    lows = [98, 96, 84, 88, 93]                    # day3 low 84 <= 85
    highs = [101, 100, 99, 96, 97]
    ret, exit_price = _simulate_fixed_exit(_bars(lows, highs, closes), 100.0, 20, 15.0, is_sell=False)
    assert exit_price == 85.0                      # fixed stop level
    assert ret == pytest.approx(-15.0)


def test_not_matured_returns_none():
    # Only 5 bars, no stop hit, hold window is 20 -> not matured yet.
    closes = [101, 102, 103, 104, 105]
    lows = [100, 101, 102, 103, 104]
    highs = [102, 103, 104, 105, 106]
    assert _simulate_fixed_exit(_bars(lows, highs, closes), 100.0, 20, 15.0, is_sell=False) is None
