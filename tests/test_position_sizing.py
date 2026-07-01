"""Regime + conviction position-size multipliers."""
from app.services.position_sizing import (
    conviction_size_multiplier, position_multiplier, regime_size_multiplier)


def test_regime_multiplier():
    assert regime_size_multiplier("BULL") == 1.0
    assert regime_size_multiplier("NEUTRAL") == 0.85
    assert regime_size_multiplier("BEAR") == 0.55
    assert regime_size_multiplier("bull") == 1.0          # case-insensitive
    assert regime_size_multiplier("weird") == 0.85        # unknown → neutral


def test_conviction_multiplier():
    assert conviction_size_multiplier(None) == 1.0
    assert conviction_size_multiplier(50) == 1.0          # midpoint
    assert conviction_size_multiplier(0) == 0.7
    assert conviction_size_multiplier(100) == 1.3
    assert conviction_size_multiplier(-50) == 0.7         # clamp
    assert conviction_size_multiplier(200) == 1.3         # clamp


def test_position_multiplier_clamped():
    assert 0.3 <= position_multiplier("BEAR", 0) <= 1.6
    assert position_multiplier("BULL", 100) <= 1.6
    assert position_multiplier("NEUTRAL", 50) == 0.85     # 0.85 × 1.0
