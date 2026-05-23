"""Tests for the unified execution_costs module (Phase 0).

Verifies that:
1. apply_costs worsens the price in the correct direction.
2. Round-trip cost equals 2 × one-way cost.
3. round_trip_cost_pct() returns the expected fraction.
4. The default constant matches the 20 bps standard across all engines.
"""
from __future__ import annotations

import os
import pytest

from app.services.execution_costs import (
    BACKTEST_COST_BPS,
    apply_costs,
    round_trip_cost_pct,
)


def test_buy_side_worsens_price_up():
    price = 100.0
    result = apply_costs(price, "buy")
    assert result > price, "Buy fill must be higher than raw price"


def test_sell_side_worsens_price_down():
    price = 100.0
    result = apply_costs(price, "sell")
    assert result < price, "Sell fill must be lower than raw price"


def test_round_trip_is_double_one_way():
    """round_trip_cost_pct() must equal buy-cost + sell-cost."""
    price = 200.0
    buy_adj  = apply_costs(price, "buy")
    sell_adj = apply_costs(price, "sell")
    rt_manual = (buy_adj - price) / price + (price - sell_adj) / price
    rt_fn = round_trip_cost_pct()
    assert abs(rt_manual - rt_fn) < 1e-10, (
        f"round_trip_cost_pct()={rt_fn} does not match manual {rt_manual}"
    )


def test_default_is_20bps_per_side():
    """Default BACKTEST_COST_BPS must be 20 (institutional standard)."""
    assert BACKTEST_COST_BPS == 20.0 or float(
        os.environ.get("BACKTEST_COST_BPS", "20.0")
    ) == BACKTEST_COST_BPS


def test_40bps_round_trip():
    """20 bps/side → 0.40 % round-trip cost."""
    rt = round_trip_cost_pct(bps=20.0)
    assert abs(rt - 0.0040) < 1e-10, f"Expected 0.0040, got {rt}"


def test_custom_bps_override():
    """Caller can override bps per-call."""
    price = 100.0
    adj_10 = apply_costs(price, "buy", bps=10.0)
    adj_20 = apply_costs(price, "buy", bps=20.0)
    assert adj_10 < adj_20, "Lower bps → smaller cost adjustment"


def test_cost_is_symmetric():
    """For a given price, buy penalty == sell penalty."""
    price = 150.0
    buy_penalty  = apply_costs(price, "buy")  - price
    sell_penalty = price - apply_costs(price, "sell")
    assert abs(buy_penalty - sell_penalty) < 1e-10
