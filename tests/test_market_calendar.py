"""Tests for the scheduler's market-closed calendar (weekends + NYSE holidays).

Drives the off-session full precompute and the holiday-skip on session scans.
"""

from datetime import datetime

from app.services import scheduler as S


def test_weekend_is_closed():
    sat = datetime(2026, 6, 20)
    assert not S._is_weekday(sat)            # genuinely a weekend day
    assert S._is_market_closed(sat)


def test_nyse_holiday_on_a_weekday_is_closed():
    xmas = datetime(2026, 12, 25)            # Christmas, a Friday in 2026
    assert S._is_weekday(xmas)               # it IS a weekday...
    assert S._is_market_holiday(xmas)        # ...but a NYSE holiday
    assert S._is_market_closed(xmas)


def test_mlk_monday_is_closed():
    mlk = datetime(2026, 1, 19)              # MLK Day (Monday)
    assert S._is_weekday(mlk) and S._is_market_holiday(mlk)
    assert S._is_market_closed(mlk)


def test_normal_weekday_is_open():
    d = datetime(2026, 6, 23)                # a Tuesday, not a holiday
    assert S._is_weekday(d) and not S._is_market_holiday(d)
    assert not S._is_market_closed(d)
