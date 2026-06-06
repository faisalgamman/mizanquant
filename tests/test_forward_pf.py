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


def test_stop_takes_precedence_over_a_winning_time_exit():
    # BUY @100: day 2 low pierces 85, even though day 20 close would be +20%.
    closes = [99, 80] + [100 + i for i in range(1, 19)]   # 20 bars, day20 close=118
    lows = [98, 84] + [c - 1 for c in closes[2:]]          # day2 low 84 <= 85
    highs = [c + 1 for c in closes]
    ret, exit_price = _simulate_fixed_exit(_bars(lows, highs, closes), 100.0, 20, 15.0, is_sell=False)
    assert exit_price == 85.0                                # stop wins over the later time exit
    assert ret == pytest.approx(-15.0)


def test_sell_time_exit_is_direction_normalised():
    # SELL @100: price drifts DOWN to 90 by day 20 -> short profits +10%.
    closes = [100 - i * 0.5 for i in range(1, 21)]          # 99.5 .. 90.0
    highs = [c + 0.5 for c in closes]                        # never >= 115 (stop)
    lows = [c - 0.5 for c in closes]
    ret, exit_price = _simulate_fixed_exit(_bars(lows, highs, closes), 100.0, 20, 15.0, is_sell=True)
    assert exit_price == pytest.approx(closes[19])           # day-20 close
    assert ret == pytest.approx(10.0)                        # short gains as price falls


def test_sell_catastrophe_stop_when_price_rises():
    # SELL @100: day 2 high pierces 115 -> short stopped out for -15%.
    closes = [101, 116, 110, 108, 105]
    highs = [103, 117, 112, 109, 106]                        # day2 high 117 >= 115
    lows = [100, 109, 108, 106, 103]
    ret, exit_price = _simulate_fixed_exit(_bars(lows, highs, closes), 100.0, 20, 15.0, is_sell=True)
    assert exit_price == pytest.approx(115.0)
    assert ret == pytest.approx(-15.0)


def test_stop_triggers_on_exact_touch():
    # Boundary: low == stop level counts as a hit (<=), not a miss.
    closes = [100, 100, 100]
    lows = [99, 90.0, 99]                                    # exactly 90 == 100*(1-0.10)
    highs = [101, 101, 101]
    ret, exit_price = _simulate_fixed_exit(_bars(lows, highs, closes), 100.0, 20, 10.0, is_sell=False)
    assert exit_price == pytest.approx(90.0)
    assert ret == pytest.approx(-10.0)


@pytest.mark.parametrize("bad", [None, pd.DataFrame()])
def test_guard_clauses_return_none(bad):
    assert _simulate_fixed_exit(bad, 100.0, 20, 15.0, is_sell=False) is None


def test_nonpositive_entry_returns_none():
    closes, lows, highs = [100, 101], [99, 100], [101, 102]
    assert _simulate_fixed_exit(_bars(lows, highs, closes), 0.0, 20, 15.0, is_sell=False) is None
