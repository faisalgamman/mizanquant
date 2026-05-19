"""Tests for upgraded smart_ensemble.py with governance"""
import pytest
import numpy as np
from unittest.mock import patch, MagicMock
from app.services import smart_ensemble as se
from openbb_forecast.models.regime_engine import MarketRegime

class TestModelGovernance:
    def test_regime_multiplier_trend_in_bull(self):
        gov = se.ModelGovernance()
        mult = gov.regime_multiplier("turtle", MarketRegime.LOW_VOL_BULL)
        assert mult > 1.0  # Trend-following in bull market = bonus

    def test_regime_multiplier_mismatch(self):
        gov = se.ModelGovernance()
        mult = gov.regime_multiplier("turtle", MarketRegime.HIGH_VOL_BEAR)
        assert mult < 1.5  # Trend-following in bear = reduced

    def test_regime_multiplier_mean_reversion_in_high_vol(self):
        gov = se.ModelGovernance()
        mult = gov.regime_multiplier("neuro_evolution_novelty", MarketRegime.HIGH_VOL_BEAR)
        assert mult >= 1.0

    def test_recency_weight(self):
        gov = se.ModelGovernance()
        w_low = gov.recency_weight(0)   # No trades
        w_high = gov.recency_weight(20)  # 20 recent trades
        assert w_low == 1.0
        assert w_high > 0.3

    def test_record_trade(self):
        gov = se.ModelGovernance()
        gov.record_trade("test_model", 0.02, MarketRegime.LOW_VOL_BULL)
        gov.record_trade("test_model", -0.01, MarketRegime.SIDEWAYS)
        diag = gov.model_diagnostics("test_model")
        assert diag["n_trades"] == 2
        assert diag["win_rate"] == 50.0  # One win out of two

    def test_model_diagnostics_empty(self):
        gov = se.ModelGovernance()
        diag = gov.model_diagnostics("nonexistent")
        assert diag["n_trades"] == 0
        assert diag["sharpe"] == 0.0


class TestWeightedConsensus:
    def test_consensus_all_buy(self):
        votes = [
            {"Tool": "Halal Screener", "Vote": "BUY"},
            {"Tool": "USX Pro", "Vote": "BUY"},
            {"Tool": "Backtest 2Y", "Vote": "BUY"},
        ]
        weights = {"turtle": 1.0, "moving_average": 1.0, "evolution_strategy": 1.0}
        result = se.weighted_consensus(votes, model_weights=weights, calibrate=False)
        assert result["verdict"] in ("STRONG BUY", "BUY")
        assert result["confidence"] > 0

    def test_consensus_mixed(self):
        votes = [
            {"Tool": "Halal Screener", "Vote": "BUY"},
            {"Tool": "Bollinger Bands", "Vote": "SELL"},
            {"Tool": "EMA Alignment", "Vote": "HOLD"},
        ]
        weights = {"turtle": 1.0, "moving_average": 1.0}
        result = se.weighted_consensus(votes, model_weights=weights, calibrate=False)
        assert result["verdict"] in ("BUY", "NEUTRAL", "SELL")

    def test_consensus_all_skip(self):
        votes = [
            {"Tool": "X", "Vote": "SKIP"},
            {"Tool": "Y", "Vote": "SKIP"},
        ]
        result = se.weighted_consensus(votes, calibrate=False)
        assert result["verdict"] == "NEUTRAL"
        assert result["confidence"] == 0.0

    def test_converter_tool_to_model(self):
        assert "turtle" in se._TOOL_TO_MODEL.values()
        assert "moving_average" in se._TOOL_TO_MODEL.values()


class TestConfidenceCalibrator:
    def test_calibrator_improves_calibration(self):
        np.random.seed(42)
        n = 200
        confidences = np.random.uniform(0, 1, n)
        predictions = np.full(n, 1.0)
        actuals = np.where(confidences > 0.5, 1.0, -1.0)  # Perfect: high conf → correct

        cal = se.ConfidenceCalibrator()
        cal.fit(predictions, confidences, actuals)

        # High confidence should map to high accuracy
        high_cal = cal.calibrate(0.9)
        low_cal = cal.calibrate(0.1)
        assert high_cal >= low_cal

    def test_calibrator_too_few_samples(self):
        cal = se.ConfidenceCalibrator()
        cal.fit(np.array([1.0]), np.array([0.5]), np.array([1.0]))
        assert not cal._fitted  # Not enough samples for 10 bins
        # calibrate should pass through raw conf
        assert cal.calibrate(0.7) == 0.7
