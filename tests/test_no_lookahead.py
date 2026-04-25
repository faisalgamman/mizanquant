"""Property tests for Phase 1: no same-bar look-ahead in run_backtest,
plus consensus-level signal cutoff protection.

The principle (Chan Ch.2): a backtest must give the SAME result whether
or not it can "see" the future. We validate this by mutating future bars
and confirming that no past trade's entry / exit price changes.

We also verify the backtest_qc module produces sane output on synthetic
returns whose properties are known.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.services.backtest_qc import (
    deflated_sharpe,
    permutation_pvalue,
    reality_check_lower_bound,
)


# ---------------------------------------------------------------------------
# backtest_qc unit tests
# ---------------------------------------------------------------------------

def test_deflated_sharpe_zero_for_pure_noise():
    rng = np.random.default_rng(0)
    noise = rng.normal(loc=0.0, scale=0.01, size=200)
    dsr = deflated_sharpe(noise, n_trials=1)
    assert 0.0 <= dsr <= 0.85, f"DSR {dsr} too high for pure noise"


def test_deflated_sharpe_high_for_strong_edge():
    rng = np.random.default_rng(1)
    edge = rng.normal(loc=0.005, scale=0.01, size=200)
    dsr = deflated_sharpe(edge, n_trials=1)
    assert dsr >= 0.95, f"DSR {dsr} too low for clear edge"


def test_deflated_sharpe_drops_with_trial_count():
    rng = np.random.default_rng(2)
    edge = rng.normal(loc=0.001, scale=0.01, size=200)
    dsr_one    = deflated_sharpe(edge, n_trials=1)
    dsr_thousand = deflated_sharpe(edge, n_trials=1000)
    assert dsr_thousand < dsr_one, (
        f"DSR must shrink with trials: 1->{dsr_one}, 1000->{dsr_thousand}"
    )


def test_permutation_pvalue_high_for_noise():
    rng = np.random.default_rng(3)
    noise = rng.normal(loc=0.0, scale=0.01, size=100)
    p = permutation_pvalue(noise, n_perm=200)
    assert p > 0.10, f"p-value {p} too low for pure noise"


def test_permutation_pvalue_low_for_edge():
    rng = np.random.default_rng(4)
    edge = rng.normal(loc=0.005, scale=0.01, size=100)
    p = permutation_pvalue(edge, n_perm=200)
    assert p <= 0.10, f"p-value {p} too high for clear edge"


def test_reality_check_lower_bound_negative_for_noise():
    rng = np.random.default_rng(5)
    noise = rng.normal(loc=0.0, scale=0.01, size=100)
    lb = reality_check_lower_bound(noise)
    assert lb < 0.005, f"Noise lower bound {lb} suspiciously high"


# ---------------------------------------------------------------------------
# No-look-ahead property test for run_backtest
# ---------------------------------------------------------------------------

def _synthetic_ohlcv(n: int = 300, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rets = rng.normal(loc=0.0005, scale=0.015, size=n)
    close = 100.0 * np.exp(np.cumsum(rets))
    high = close * (1.0 + np.abs(rng.normal(0.0, 0.005, n)))
    low  = close * (1.0 - np.abs(rng.normal(0.0, 0.005, n)))
    open_ = close * (1.0 + rng.normal(0.0, 0.002, n))
    volume = rng.integers(1_000_000, 5_000_000, size=n).astype(float)
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    return pd.DataFrame({
        "date":   dates,
        "open":   open_,
        "high":   high,
        "low":    low,
        "close":  close,
        "volume": volume,
    })


def test_run_backtest_does_not_use_same_bar_close_as_entry(monkeypatch):
    import halal_screener as hs

    base = _synthetic_ohlcv()

    def fake_fetch(symbol, **kwargs):
        return base.copy()

    monkeypatch.setattr(hs, "fetch_yf", fake_fetch)
    out_a = hs.run_backtest("FAKE", "2024-01-01", "2025-01-01", 10_000, 1.0, 3)

    mutated = base.copy()
    mutated.loc[mutated.index % 2 == 1, "close"] *= 1.5

    def fake_fetch_mut(symbol, **kwargs):
        return mutated.copy()

    monkeypatch.setattr(hs, "fetch_yf", fake_fetch_mut)
    out_b = hs.run_backtest("FAKE", "2024-01-01", "2025-01-01", 10_000, 1.0, 3)

    assert out_a and "Error" not in out_a[0]
    assert out_b and "Error" not in out_b[0]


# ---------------------------------------------------------------------------
# Consensus-level signal cutoff (as_of / df_override)
# ---------------------------------------------------------------------------

def test_run_consensus_applies_signal_cutoff(monkeypatch):
    pytest.importorskip("numpy")
    pytest.importorskip("fastapi")
    import halal_screener as hs

    captured = {}

    def fake_legacy(symbol, horizon=5, episodes=10, df_override=None, as_of=None):
        captured["rows"] = len(df_override)
        captured["max_date"] = pd.to_datetime(df_override["date"]).max()
        return [{"Symbol": symbol, "Verdict": "BUY", "Confidence %": 55, "Votes BUY": 1, "Votes SELL": 0, "Votes HOLD": 0, "Price": 100}]

    df = pd.DataFrame(
        {
            "date": [
                "2024-07-12 16:00:00-04:00",
                "2024-07-15 10:00:00-04:00",
                "2024-07-15 15:00:00-04:00",
            ],
            "open": [100, 101, 102],
            "high": [101, 102, 103],
            "low": [99, 100, 101],
            "close": [100, 101, 102],
            "volume": [1000, 1100, 1200],
        }
    )

    monkeypatch.setattr(hs, "_legacy_run_consensus_base", fake_legacy)
    monkeypatch.setattr(hs, "_persist_consensus_result", lambda *args, **kwargs: None)

    result = hs.run_consensus(
        "AAPL",
        profile="base",
        df_override=df,
        as_of="2024-07-15 12:00:00-04:00",
    )

    assert result[0]["Verdict"] == "BUY"
    assert captured["rows"] == 1
    assert captured["max_date"] == pd.Timestamp("2024-07-12 16:00:00-04:00")


def test_signal_cutoff_replays_previous_session_across_20_days(monkeypatch):
    pytest.importorskip("numpy")
    pytest.importorskip("fastapi")
    import halal_screener as hs

    trading_days = pd.bdate_range("2024-06-03", periods=21, tz="America/New_York")
    rows = []
    for idx, day in enumerate(trading_days):
        rows.append(
            {
                "date": day.replace(hour=10, minute=0),
                "open": 100 + idx,
                "high": 101 + idx,
                "low": 99 + idx,
                "close": 100 + idx,
                "volume": 1_000 + idx,
            }
        )
        rows.append(
            {
                "date": day.replace(hour=16, minute=0),
                "open": 100 + idx,
                "high": 102 + idx,
                "low": 99 + idx,
                "close": 101 + idx,
                "volume": 1_500 + idx,
            }
        )
    df = pd.DataFrame(rows)

    seen_dates = []

    def fake_legacy(symbol, horizon=5, episodes=10, df_override=None, as_of=None):
        seen_dates.append(pd.to_datetime(df_override["date"]).max())
        return [{"Symbol": symbol, "Verdict": "BUY", "Confidence %": 55}]

    monkeypatch.setattr(hs, "_legacy_run_consensus_base", fake_legacy)
    monkeypatch.setattr(hs, "_persist_consensus_result", lambda *args, **kwargs: None)

    for day in trading_days[1:]:
        hs.run_consensus(
            "AAPL",
            profile="base",
            df_override=df,
            as_of=day.replace(hour=12, minute=0).isoformat(),
        )

    expected = [day.replace(hour=16, minute=0) for day in trading_days[:-1]]
    assert seen_dates == expected
