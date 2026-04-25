"""Tests for calibration acceptance gating."""

from __future__ import annotations

from scripts import calibrate_thresholds as ct


def test_aggregate_profile_keeps_legacy_threshold_when_improvement_too_small(monkeypatch):
    monkeypatch.setattr(ct, "SYMBOLS", ("AAPL",))
    monkeypatch.setattr(ct, "_load_prices", lambda symbol: [1.0] * 300)

    def fake_simulate(prices, profile, threshold):
        sharpe_map = {45: 1.00, 50: 1.03}
        return {
            "sharpe": sharpe_map.get(threshold, 0.5),
            "hit_rate": 55.0,
            "expectancy": 0.2,
            "profit_factor": 1.4,
            "max_dd": 10.0,
            "trade_count": 80,
            "trades_per_year": 80.0,
        }

    monkeypatch.setattr(ct, "_simulate_profile", fake_simulate)
    monkeypatch.setattr(ct, "THRESHOLDS", [45, 50])

    result = ct._aggregate_profile("momentum")

    assert result["accepted"] is False
    assert result["selected"] == 45


def test_aggregate_profile_accepts_new_threshold_after_five_percent_sharpe_gain(monkeypatch):
    monkeypatch.setattr(ct, "SYMBOLS", ("AAPL",))
    monkeypatch.setattr(ct, "_load_prices", lambda symbol: [1.0] * 300)

    def fake_simulate(prices, profile, threshold):
        sharpe_map = {40: 1.00, 55: 1.10}
        return {
            "sharpe": sharpe_map.get(threshold, 0.5),
            "hit_rate": 55.0,
            "expectancy": 0.2,
            "profit_factor": 1.4,
            "max_dd": 10.0,
            "trade_count": 80,
            "trades_per_year": 80.0,
        }

    monkeypatch.setattr(ct, "_simulate_profile", fake_simulate)
    monkeypatch.setattr(ct, "THRESHOLDS", [40, 55])

    result = ct._aggregate_profile("reversion")

    assert result["accepted"] is True
    assert result["selected"] == 55
