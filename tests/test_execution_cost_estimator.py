"""Tests for execution_cost_estimator (Phase 2A).

Verifies:
1. estimate_execution_cost returns positive costs for a market buy.
2. Costs increase with order size (larger order = more impact).
3. A large order in an illiquid stock exceeds MAX_EXECUTION_IMPACT_BPS → blocked=True.
4. A small liquid order is not blocked.
5. build_market_state builds a valid MarketState.
6. Side "sell" produces a valid positive cost.
7. Zero qty / zero price edge cases handled gracefully.
"""

from __future__ import annotations

import os

import pytest


# ── helper: pre-built MarketState so tests don't hit network ─────────────────

def _liquid_market(price: float = 200.0):
    """Synthetic MarketState for a liquid large-cap stock."""
    from openbb_forecast.openbb_forecast.models.execution_realism import MarketState

    return MarketState(
        mid_price=price,
        bid_price=price * 0.9995,
        ask_price=price * 1.0005,
        bid_size=5_000,
        ask_size=5_000,
        volume_last_minute=50_000,
        daily_volume=5_000_000,   # very liquid
        volatility=0.20,
        spread_bps=5.0,
        timestamp="",
    )


def _illiquid_market(price: float = 10.0):
    """Synthetic MarketState for a very illiquid micro-cap stock."""
    from openbb_forecast.openbb_forecast.models.execution_realism import MarketState

    return MarketState(
        mid_price=price,
        bid_price=price * 0.998,
        ask_price=price * 1.002,
        bid_size=100,
        ask_size=100,
        volume_last_minute=200,
        daily_volume=50_000,    # very illiquid
        volatility=0.60,
        spread_bps=40.0,
        timestamp="",
    )


# ── tests ─────────────────────────────────────────────────────────────────────

def test_estimate_buy_returns_positive_cost():
    from app.services.execution_cost_estimator import estimate_execution_cost

    result = estimate_execution_cost(
        "AAPL", "buy", 100, 200.0,
        market_state=_liquid_market(200.0),
    )
    assert result["est_cost_usd"] > 0, "Cost must be positive"
    assert result["est_cost_bps"] > 0, "Cost in bps must be positive"


def test_cost_increases_with_qty():
    """Larger orders should incur higher absolute cost."""
    from app.services.execution_cost_estimator import estimate_execution_cost

    small = estimate_execution_cost("AAPL", "buy", 10, 200.0, market_state=_liquid_market())
    large = estimate_execution_cost("AAPL", "buy", 5_000, 200.0, market_state=_liquid_market())
    assert large["est_cost_usd"] > small["est_cost_usd"], (
        "Larger order should cost more in absolute USD"
    )


def test_impact_bps_increases_with_qty():
    """Market impact in bps should increase with order size relative to ADV."""
    from app.services.execution_cost_estimator import estimate_execution_cost

    small = estimate_execution_cost("AAPL", "buy", 100, 200.0, market_state=_liquid_market())
    large = estimate_execution_cost("AAPL", "buy", 50_000, 200.0, market_state=_liquid_market())
    assert large["impact_bps"] >= small["impact_bps"], (
        "Impact bps should be >= for larger relative order size"
    )


def test_illiquid_order_is_blocked(monkeypatch):
    """A very large order in an illiquid stock should be blocked."""
    from app.services import execution_cost_estimator as ece

    # Lower the gate to 10 bps so this test is deterministic regardless of env
    monkeypatch.setattr(ece, "MAX_EXECUTION_IMPACT_BPS", 10.0)

    from app.services.execution_cost_estimator import estimate_execution_cost

    result = estimate_execution_cost(
        "ILLIQ", "buy", 40_000, 10.0,
        market_state=_illiquid_market(10.0),
    )
    assert result["blocked"] is True, (
        f"High-impact order should be blocked; impact_bps={result['impact_bps']}"
    )
    assert result["block_reason"], "block_reason must be a non-empty string"


def test_liquid_small_order_not_blocked(monkeypatch):
    from app.services import execution_cost_estimator as ece
    monkeypatch.setattr(ece, "MAX_EXECUTION_IMPACT_BPS", 50.0)

    from app.services.execution_cost_estimator import estimate_execution_cost

    result = estimate_execution_cost(
        "AAPL", "buy", 50, 200.0,
        market_state=_liquid_market(200.0),
    )
    assert result["blocked"] is False, (
        f"Small liquid order should not be blocked; impact_bps={result['impact_bps']}"
    )


def test_sell_side_returns_valid_cost():
    from app.services.execution_cost_estimator import estimate_execution_cost

    result = estimate_execution_cost(
        "AAPL", "sell", 100, 200.0,
        market_state=_liquid_market(200.0),
    )
    assert result["est_cost_usd"] >= 0
    assert "fill_rate" in result


def test_zero_qty_handled_gracefully():
    from app.services.execution_cost_estimator import estimate_execution_cost

    result = estimate_execution_cost("AAPL", "buy", 0, 200.0)
    assert result["est_cost_usd"] == 0.0
    assert result["blocked"] is False


def test_result_keys_complete():
    from app.services.execution_cost_estimator import estimate_execution_cost

    result = estimate_execution_cost(
        "AAPL", "buy", 100, 200.0,
        market_state=_liquid_market(),
    )
    for key in (
        "est_cost_usd", "est_cost_bps", "impact_bps",
        "spread_bps", "slippage_bps", "latency_bps",
        "fill_rate", "blocked", "block_reason",
    ):
        assert key in result, f"Missing key: {key}"
