"""Unit tests for app.services.scoring weighted score system."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.services.scoring import weighted_score, is_signal_ready


def _make_df(close_prices, vol=None):
    """Create a minimal OHLCV DataFrame."""
    n = len(close_prices)
    data = {
        "open": np.array(close_prices) * 0.99,
        "high": np.array(close_prices) * 1.02,
        "low": np.array(close_prices) * 0.98,
        "close": np.array(close_prices, dtype=float),
        "volume": vol or [1_000_000] * n,
    }
    return pd.DataFrame(data)


def _make_spy_df(close_prices):
    return pd.DataFrame({"close": np.array(close_prices, dtype=float)})


class TestWeightedScore:
    def test_strong_uptrend_scores_high(self, monkeypatch):
        """Steady uptrend should produce high total score."""
        monkeypatch.setattr("app.services.scoring.get_market_context", lambda: {
            "spy_regime": {"regime": "bull"},
            "vix": {"vix": 15},
        })
        prices = list(range(100, 200))  # 100 days of uptrend
        spy_prices = list(range(100, 200))
        df = _make_df(prices)
        spy_df = _make_spy_df(spy_prices)
        result = weighted_score(df, spy_df=spy_df, vix=15)
        assert result["total"] >= 50
        assert result["confidence"] >= 50
        assert "components" in result
        assert "thresholds" in result
        assert result["thresholds"]["signal"] == 65

    def test_downtrend_scores_low(self, monkeypatch):
        """Downtrend should produce low total score."""
        monkeypatch.setattr("app.services.scoring.get_market_context", lambda: {
            "spy_regime": {"regime": "bear"},
            "vix": {"vix": 30},
        })
        prices = list(range(200, 100, -1))  # 100 days of downtrend
        spy_prices = list(range(200, 100, -1))
        df = _make_df(prices)
        spy_df = _make_spy_df(spy_prices)
        result = weighted_score(df, spy_df=spy_df, vix=30)
        assert result["total"] < 50

    def test_insufficient_data_returns_partial(self, monkeypatch):
        """Very short DataFrame should still return a dict with partial scores."""
        monkeypatch.setattr("app.services.scoring.get_market_context", lambda: {
            "spy_regime": {"regime": "neutral"},
            "vix": {"vix": 20},
        })
        df = _make_df([100, 101], vol=[1_000_000, 1_200_000])
        spy_df = _make_spy_df([100, 101])
        result = weighted_score(df, spy_df=spy_df, vix=20)
        # Should still return valid structure even with limited data
        assert isinstance(result, dict)
        assert "total" in result
        assert result["total"] >= 0

    def test_all_components_present(self, monkeypatch):
        """Result must contain all 10 scoring components."""
        monkeypatch.setattr("app.services.scoring.get_market_context", lambda: {
            "spy_regime": {"regime": "bull"},
            "vix": {"vix": 15},
        })
        prices = list(range(100, 200))
        df = _make_df(prices)
        spy_df = _make_spy_df(prices)
        result = weighted_score(df, spy_df=spy_df, vix=15)
        expected_keys = {"trend", "regime", "rsi", "adx", "rs", "volume", "gap", "bb", "vwap", "macd"}
        assert expected_keys == set(result["components"].keys())

    def test_component_sum_matches_total(self, monkeypatch):
        """Sum of component scores should equal total."""
        monkeypatch.setattr("app.services.scoring.get_market_context", lambda: {
            "spy_regime": {"regime": "bull"},
            "vix": {"vix": 15},
        })
        prices = list(range(100, 200))
        df = _make_df(prices)
        spy_df = _make_spy_df(prices)
        result = weighted_score(df, spy_df=spy_df, vix=15)
        assert sum(result["components"].values()) == result["total"]

    def test_max_score_100(self, monkeypatch):
        """When all conditions are perfect, max score is 100."""
        monkeypatch.setattr("app.services.scoring.get_market_context", lambda: {
            "spy_regime": {"regime": "bull"},
            "vix": {"vix": 15},
        })
        # Perfect uptrend with ideal conditions
        n = 100
        prices = np.linspace(100, 200, n)
        # High volume on last day to trigger vol score
        vol = [1_000_000] * (n - 1) + [3_000_000]
        df = _make_df(prices, vol=vol)
        spy_prices = np.linspace(100, 150, n)
        spy_df = _make_spy_df(spy_prices)
        result = weighted_score(df, spy_df=spy_df, vix=15)
        assert result["total"] <= 100

    def test_without_spy_df_still_works(self, monkeypatch):
        """When spy_df is None, should still run (falls back to internal fetch)."""
        monkeypatch.setattr("app.services.scoring.get_market_context", lambda: {
            "spy_regime": {"regime": "bull"},
            "vix": {"vix": 15},
        })
        prices = list(range(100, 200))
        df = _make_df(prices)
        # Don't pass spy_df - it will try to fetch internally
        result = weighted_score(df, spy_df=None, vix=15)
        assert isinstance(result, dict)
        assert "total" in result


class TestIsSignalReady:
    def test_above_threshold(self):
        assert is_signal_ready({"total": 80}) is True

    def test_at_threshold(self):
        assert is_signal_ready({"total": 65}) is True

    def test_below_threshold(self):
        assert is_signal_ready({"total": 50}) is False

    def test_custom_threshold(self):
        assert is_signal_ready({"total": 70}, min_score=75) is False

    def test_missing_total(self):
        assert is_signal_ready({}) is False
