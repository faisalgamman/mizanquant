"""Tests for look-ahead protection in consensus entry points."""

from __future__ import annotations

import pytest

pd = pytest.importorskip("pandas")


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
