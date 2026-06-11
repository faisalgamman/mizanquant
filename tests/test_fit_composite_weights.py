"""Tests for fit_composite_weights — adoption gate + synthetic walks."""
import pandas as pd

# Import the pure functions for unit testing
from scripts.fit_composite_weights import evaluate_adoption


def test_adoption_gate_pass_all():
    """All conditions met → PASS."""
    assert evaluate_adoption(0.15, 0.05, 2.0, 1.0, 0.01, 0.8) == "PASS"


def test_adoption_gate_fail_ic():
    """v2 IC not beating baseline → KEEP_V1."""
    assert evaluate_adoption(0.04, 0.05, 2.0, 1.0, 0.01, 0.8) == "KEEP_V1"


def test_adoption_gate_fail_spread():
    """v2 spread not beating baseline → KEEP_V1."""
    assert evaluate_adoption(0.15, 0.05, 0.5, 1.0, 0.01, 0.8) == "KEEP_V1"


def test_adoption_gate_fail_pval():
    """pval too high → KEEP_V1."""
    assert evaluate_adoption(0.15, 0.05, 2.0, 1.0, 0.10, 0.8) == "KEEP_V1"


def test_adoption_gate_fail_dsr():
    """DSR too low → KEEP_V1."""
    assert evaluate_adoption(0.15, 0.05, 2.0, 1.0, 0.01, 0.3) == "KEEP_V1"


def test_adoption_gate_edge_boundary():
    """Exactly-at-boundary: pval=0.05 → KEEP_V1 (must be < 0.05)."""
    assert evaluate_adoption(0.15, 0.05, 2.0, 1.0, 0.05, 0.6) == "KEEP_V1"
    # DSR exactly 0.6 → PASS
    assert evaluate_adoption(0.15, 0.05, 2.0, 1.0, 0.049, 0.6) == "PASS"


# ── INSUFFICIENT_DATA guard test ────────────────────────────────────────────

def test_insufficient_data_single_month(monkeypatch):
    """All rows share one month → verdict must be INSUFFICIENT_DATA, never KEEP_V1."""
    from scripts.fit_composite_weights import fit_composite_weights
    import numpy as np

    # Mock load_matured_signals → single-month signals
    def _mock_load(days=365):
        return [{"symbol": "AAPL", "created_at": "2026-05-01T10:00:00", "price": 150.0, "outcome_return_pct": 2.0},
                {"symbol": "MSFT", "created_at": "2026-05-15T10:00:00", "price": 400.0, "outcome_return_pct": -1.0},
                {"symbol": "GOOG", "created_at": "2026-05-03T10:00:00", "price": 140.0, "outcome_return_pct": 5.0},
                {"symbol": "AMZN", "created_at": "2026-05-10T10:00:00", "price": 220.0, "outcome_return_pct": 3.0},
                ] * 30  # enough rows > 60
    monkeypatch.setattr("scripts.fit_composite_weights.load_matured_signals", _mock_load)
    monkeypatch.setattr("scripts.fit_composite_weights.apply_signal_filter", lambda s, d: (s, None))

    # Mock price data + features: build enough rows to pass len(df) >= 60
    n = 260
    closes = np.linspace(100, 120, n)
    df_sym = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=n, freq="B"),
        "open": closes, "high": closes * 1.01, "low": closes * 0.99,
        "close": closes, "volume": np.full(n, 1_000_000.0),
    })

    def _mock_fetch(sigs):
        return {"AAPL": df_sym, "MSFT": df_sym, "GOOG": df_sym, "AMZN": df_sym}

    def _mock_compute_features(df_sym, spy_df, created):
        return {"macd_hist_rising": 1.0, "rs_spy_20": 0.5, "rsi14": 55.0,
                "trend_above_ema50": 1, "bb_bandwidth_pctile": 0.3, "vol_dryup": 0,
                "prox_52w": 0.9, "ema50_above_200": 1, "mom_3m": 10.0,
                "rs_spy_63": 0.4, "adx14": 25.0, "di_bullish": 1,
                "reward_1m": 5.0, "reward_3m": 15.0, "reward_6m": 30.0,
                "reward_12m": 60.0}

    monkeypatch.setattr("scripts.fit_composite_weights.fetch_prices_for_signals", _mock_fetch)
    monkeypatch.setattr("scripts.fit_composite_weights.compute_features", _mock_compute_features)
    monkeypatch.setattr("scripts.fit_composite_weights.FEATURE_ORDER",
                        ["macd_hist_rising", "rs_spy_20", "rsi14", "trend_above_ema50",
                         "bb_bandwidth_pctile", "vol_dryup", "prox_52w", "ema50_above_200",
                         "mom_3m", "rs_spy_63", "adx14", "di_bullish",
                         "reward_1m", "reward_3m", "reward_6m", "reward_12m"])

    result = fit_composite_weights(days=365)
    assert result["verdict"] == "INSUFFICIENT_DATA", \
        f"Expected INSUFFICIENT_DATA, got {result.get('verdict')}"
    assert result["months"] == 1
