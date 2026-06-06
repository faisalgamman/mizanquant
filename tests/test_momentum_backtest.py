"""Tests for the cross-sectional momentum backtest core (pure, offline).

Pins: (1) the ranker actually selects past-winners so a winner-heavy panel beats
a mild SPY; (2) NO look-ahead — a name with strong PAST momentum that then DECLINES
is still selected and the portfolio eats the future loss (the code cannot peek at
the future); (3) graceful error on thin data + the DSR/permutation fields exist.
"""
from __future__ import annotations

import numpy as np
import pytest

pd = pytest.importorskip("pandas")

from app.services.momentum_backtest import cross_sectional_momentum_backtest  # noqa: E402


def _bdays(n):
    return pd.bdate_range("2020-01-01", periods=n)


def test_momentum_selects_winners_and_beats_spy():
    rng = np.random.default_rng(1)
    n = 600
    idx = _bdays(n)
    cols = {}
    # 10 winners drift up, 20 losers flat — momentum should hold the winners.
    for i in range(10):
        cols[f"W{i}"] = 100 * np.cumprod(1 + rng.normal(0.0015, 0.004, n))
    for i in range(20):
        cols[f"L{i}"] = 100 * np.cumprod(1 + rng.normal(0.0000, 0.004, n))
    prices = pd.DataFrame(cols, index=idx)
    spy = pd.Series(100 * np.cumprod(1 + rng.normal(0.0004, 0.003, n)), index=idx)

    out = cross_sectional_momentum_backtest(prices, spy, top_n=15)
    assert "error" not in out
    assert out["momentum_portfolio"]["cagr_pct"] > out["spy_buy_hold"]["cagr_pct"]
    for k in ("dsr", "permutation_p", "verdict", "has_edge", "survivorship_warning"):
        assert k in out
    assert out["survivorship_warning"] is True


def test_no_lookahead_holds_past_winner_that_then_declines():
    n = 600
    idx = _bdays(n)
    t = np.arange(n)
    # "A": strong rise through the 252-day warmup, then a steady decline afterwards.
    a = np.where(t < 260, 100 * (1.004) ** t, 100 * (1.004) ** 259 * (0.997) ** (t - 259))
    cols = {"A": a}
    for i in range(5):
        cols[f"F{i}"] = 100 * np.ones(n) + (i + 1) * 0.001  # flat, negligible momentum
    prices = pd.DataFrame(cols, index=idx)
    spy = pd.Series(100 * np.ones(n), index=idx)

    out = cross_sectional_momentum_backtest(prices, spy, top_n=1)
    assert "error" not in out
    # The ranker picks A on its PAST rise; the held month is in A's FUTURE decline.
    # If the code had looked ahead it would have avoided the loss — it cannot.
    assert out["monthly_returns"][0] < 0


def test_thin_data_errors():
    idx = _bdays(50)
    prices = pd.DataFrame({"A": np.ones(50), "B": np.ones(50)}, index=idx)
    spy = pd.Series(np.ones(50), index=idx)
    assert "error" in cross_sectional_momentum_backtest(prices, spy, top_n=15)
