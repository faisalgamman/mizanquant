"""Lock the Chan Ch.2 no-look-ahead invariant of the walk-forward backtest.

A signal computed from ``close[i]`` must be filled at the OPEN of bar ``i+1`` —
never the same bar. This is the single most important correctness property of
the backtest harness, so it is pinned here before any refactor (M-C) touches the
scoring/iteration code.

The test drives ``run_backtest`` with a synthetic price frame and a forced score
on exactly one bar, so the resulting trade's entry can be checked exactly.
"""
from __future__ import annotations

import pytest

pd = pytest.importorskip("pandas")  # skip cleanly if pandas is unavailable

from app.services import backtest_service  # noqa: E402  (after importorskip)
from app.services.execution_costs import apply_costs  # noqa: E402


# Bar with the forced signal (post-warmup so it survives dropna()).
_SIGNAL_IDX = 35
_N = 45


def _synthetic_frame() -> pd.DataFrame:
    dates = pd.date_range("2024-01-01", periods=_N, freq="B")
    closes = [round(100 + 0.1 * i, 2) for i in range(_N)]      # gentle uptrend
    opens = [round(c - 0.05, 2) for c in closes]                # open != prev close
    highs = [round(c + 0.5, 2) for c in closes]
    lows = [round(c - 0.5, 2) for c in closes]
    trig = [1.0 if i == _SIGNAL_IDX else 0.0 for i in range(_N)]
    return pd.DataFrame({
        "date": dates, "open": opens, "high": highs,
        "low": lows, "close": closes, "volume": [1_000_000] * _N,
        "trig": trig,
    })


def _fake_score_series(df):
    # Fire only on the marked bar; mirrors score_series' (df -> int Series) contract.
    return pd.Series([60 if t == 1.0 else 0 for t in df["trig"]], index=df.index)


def test_entry_fills_on_next_bar_open(monkeypatch):
    frame = _synthetic_frame()
    monkeypatch.setattr(backtest_service, "fetch_market_data",
                        lambda *a, **k: frame.copy())
    monkeypatch.setattr(backtest_service, "score_series", _fake_score_series)

    # hold_days small so the time-exit closes the trade within the synthetic
    # window (the entry-timing invariant under test is independent of it).
    out = backtest_service.run_backtest(
        "TEST", "2024-01-01", "2024-03-01",
        portfolio=100_000, risk_pct=1.5, hold_days=3,
    )

    trades = [r for r in out if "Entry Date" in r]
    assert len(trades) == 1, f"expected exactly one trade, got {out}"
    trade = trades[0]

    signal_date = str(frame["date"].iloc[_SIGNAL_IDX])[:10]
    entry_bar_date = str(frame["date"].iloc[_SIGNAL_IDX + 1])[:10]
    expected_entry = round(apply_costs(frame["open"].iloc[_SIGNAL_IDX + 1], "buy"), 2)

    # Filled on bar i+1, never the signal bar i (no same-bar look-ahead).
    assert trade["Entry Date"] == entry_bar_date
    assert trade["Entry Date"] != signal_date
    # Entry price is derived from bar i+1's OPEN, not the signal bar's close.
    assert trade["Entry"] == expected_entry


def test_no_signal_means_no_trades(monkeypatch):
    frame = _synthetic_frame()
    monkeypatch.setattr(backtest_service, "fetch_market_data",
                        lambda *a, **k: frame.copy())
    monkeypatch.setattr(backtest_service, "score_series",
                        lambda df: pd.Series(0, index=df.index))

    out = backtest_service.run_backtest(
        "TEST", "2024-01-01", "2024-03-01",
        portfolio=100_000, risk_pct=1.5, hold_days=20,
    )
    assert not [r for r in out if "Entry Date" in r]
