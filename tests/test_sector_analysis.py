"""Unit tests for app.services.sector_analysis sector rotation tracking."""
from __future__ import annotations

import pandas as pd
import pytest

from app.services.sector_analysis import (
    get_sector_performance,
    get_leading_sectors,
    is_sector_leading,
    SECTOR_ETFS,
    _sector_cache,
    _sector_cache_ts,
)


def _mock_hist(ticker: str, perf_20d: float = 5.0, perf_5d: float = 1.0, perf_1d: float = 0.5) -> pd.DataFrame:
    """Create a mock history DataFrame for an ETF."""
    dates = pd.date_range(end="2026-05-11", periods=25, freq="B")
    closes = [100 * (1 + perf_20d / 100) ** (i / 24) for i in range(25)]
    close_series = pd.Series(closes, index=dates, name="Close")
    df = pd.DataFrame({"Close": close_series}, index=dates)
    return df


class TestGetSectorPerformance:
    def test_returns_11_sectors(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.sector_analysis._fetch_etf_data",
            lambda t: _mock_hist(t),
        )
        result = get_sector_performance(force_refresh=True)
        assert len(result) == 11
        assert all(isinstance(r, dict) for r in result)

    def test_each_sector_has_required_keys(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.sector_analysis._fetch_etf_data",
            lambda t: _mock_hist(t),
        )
        result = get_sector_performance(force_refresh=True)
        for r in result:
            assert "ticker" in r
            assert "name" in r
            assert "perf_1d" in r or r.get("perf_20d") is not None
            assert "classification" in r

    def test_performance_values(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.sector_analysis._fetch_etf_data",
            lambda t: _mock_hist(t, perf_20d=10.0),
        )
        result = get_sector_performance(force_refresh=True)
        for r in result:
            if r.get("perf_20d") is not None:
                assert r["perf_20d"] > 0, f"Expected positive perf_20d for {r['ticker']}"

    def test_cache_returns_same_data(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.sector_analysis._fetch_etf_data",
            lambda t: _mock_hist(t),
        )
        # Clear cache first
        monkeypatch.setattr("app.services.sector_analysis._sector_cache", {})
        monkeypatch.setattr("app.services.sector_analysis._sector_cache_ts", 0.0)

        first = get_sector_performance(force_refresh=True)
        # Override fetch to prove cache is used
        monkeypatch.setattr(
            "app.services.sector_analysis._fetch_etf_data",
            lambda t: _mock_hist(t, perf_20d=99.0),  # different value
        )
        second = get_sector_performance(force_refresh=False)  # should use cache
        for r1, r2 in zip(first, second):
            if r1.get("perf_20d") is not None:
                assert r1["perf_20d"] == r2["perf_20d"], "Cache should return original values"

    def test_classification_distribution(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.sector_analysis._fetch_etf_data",
            lambda t: _mock_hist(t, perf_20d=hash(t) % 30),  # pseudo-random
        )
        result = get_sector_performance(force_refresh=True)
        classifications = [r["classification"] for r in result if r.get("available")]
        assert "leading" in classifications
        assert "lagging" in classifications
        assert "neutral" in classifications

    def test_failed_fetch_returns_unavailable(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.sector_analysis._fetch_etf_data",
            lambda t: None,
        )
        result = get_sector_performance(force_refresh=True)
        for r in result:
            assert r["available"] is False
            assert r["classification"] == "unknown"


class TestGetLeadingSectors:
    def test_returns_list_of_tickers(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.sector_analysis._fetch_etf_data",
            lambda t: _mock_hist(t),
        )
        leading = get_leading_sectors()
        assert isinstance(leading, list)
        assert all(t in SECTOR_ETFS for t in leading)


class TestIsSectorLeading:
    def test_unknown_sector_returns_true(self, monkeypatch):
        """Empty/unknown sector should not block (conservative default)."""
        monkeypatch.setattr(
            "app.services.sector_analysis.get_sector_performance",
            lambda **kw: [{"ticker": "XLK", "classification": "leading"}],
        )
        result = is_sector_leading("ZZZZ", sector_map={"ZZZZ": ""})
        assert result is True

    def test_leading_sector_classified_correctly(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.sector_analysis.get_sector_performance",
            lambda **kw: [{"ticker": "XLK", "classification": "leading"}],
        )
        result = is_sector_leading("AAPL", sector_map={"AAPL": "Technology"})
        assert result is True

    def test_lagging_sector_classified_correctly(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.sector_analysis.get_sector_performance",
            lambda **kw: [{"ticker": "XLK", "classification": "lagging"}],
        )
        result = is_sector_leading("AAPL", sector_map={"AAPL": "Technology"})
        assert result is False
