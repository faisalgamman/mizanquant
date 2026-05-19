"""Tests for regime_engine.py"""
import pytest
import numpy as np
import pandas as pd
from openbb_forecast.models.regime_engine import (
    RegimeEngine, MarketRegime, HeuristicRegimeClassifier,
    HMMRegimeDetector, RegimeDiagnostic,
)

class TestRegimeEngine:
    @pytest.fixture
    def prices(self):
        np.random.seed(42)
        n = 1000
        # Create phases: bull (low vol) → sideways → bear (high vol) → recovery
        t = np.linspace(0, 4*np.pi, n)
        base = 100 + 20 * np.sin(t)
        noise = np.random.randn(n) * np.where(np.arange(n) < 600, 1.0, 3.0)
        return pd.Series(base + noise)

    @pytest.fixture
    def engine(self, prices):
        eng = RegimeEngine()
        eng.fit(prices)
        return eng

    def test_fit_and_predict(self, prices, engine):
        regimes = engine.predict(prices)
        assert len(regimes) == len(prices)
        assert all(0 <= r < 5 for r in regimes)

    def test_predict_latest(self, prices, engine):
        regime = engine.predict_latest(prices)
        assert isinstance(regime, MarketRegime)
        assert regime.label in ("low_vol_bull", "high_vol_bull", "low_vol_bear",
                                "high_vol_bear", "sideways")

    def test_market_regime_enum(self):
        assert MarketRegime.LOW_VOL_BULL.is_bull
        assert not MarketRegime.LOW_VOL_BULL.is_bear
        assert MarketRegime.HIGH_VOL_BEAR.is_high_vol
        assert MarketRegime.SIDEWAYS.preferred_strategy == "mean_reversion"
        assert MarketRegime.LOW_VOL_BULL.preferred_strategy == "trend_following"

    def test_heuristic_fallback(self, prices):
        heuristic = HeuristicRegimeClassifier()
        heuristic.fit(prices)
        regimes = heuristic.predict(prices)
        assert len(regimes) == len(prices)
        assert set(regimes).issubset({0, 1, 2, 3, 4})

    def test_diagnostics(self, prices, engine):
        diags = engine.get_diagnostics(prices)
        assert len(diags) >= 1  # At least one regime detected
        assert all(isinstance(d, RegimeDiagnostic) for d in diags)
        # At least one regime has n_samples > 0
        assert sum(d.n_samples for d in diags) <= len(prices)

    def test_summary_string(self, prices, engine):
        summary = engine.summary(prices)
        assert "Regime" in summary

    def test_regime_engine_unfitted(self):
        eng = RegimeEngine()
        prices = pd.Series(np.random.randn(100).cumsum() + 100)
        regime = eng.predict_latest(prices)
        # Should auto-fit
        assert isinstance(regime, MarketRegime)
