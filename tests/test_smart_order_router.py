"""Tests for smart_order_router.py — 18+ tests."""
from __future__ import annotations

import pytest
from app.services.smart_order_router import (
    SmartOrderRouter,
    RoutePlan,
    SliceSpec,
    twap_slices,
    vwap_slices,
    simple_slices,
    apply_liquidity_gate,
    DEFAULT_VOLUME_PROFILE,
)

# ── twap_slices ─────────────────────────────────────────────────

def test_twap_basic():
    slices = twap_slices(total_qty=100, num_slices=5, interval_seconds=60)
    assert len(slices) == 5
    total = sum(s.target_qty for s in slices)
    assert total == 100

def test_twap_single_slice():
    slices = twap_slices(total_qty=50, num_slices=1)
    assert len(slices) == 1
    assert slices[0].target_qty == 50

def test_twap_interval_set():
    slices = twap_slices(total_qty=200, num_slices=4, interval_seconds=120)
    for s in slices:
        assert s.interval_seconds == 120

def test_twap_zero_qty():
    slices = twap_slices(total_qty=0, num_slices=5)
    assert slices == []

def test_twap_last_slice_gets_remainder():
    slices = twap_slices(total_qty=100, num_slices=7)
    total = sum(s.target_qty for s in slices)
    assert total == 100
    # verify distribution
    for s in slices[:-1]:
        assert s.target_qty == 14  # 100 // 7 = 14
    assert slices[-1].target_qty == 16  # remainder

# ── vwap_slices ─────────────────────────────────────────────────

def test_vwap_basic():
    slices = vwap_slices(total_qty=1000)
    assert len(slices) > 0
    total = sum(s.target_qty for s in slices)
    assert total == 1000

def test_vwap_profile_sum_to_one():
    slices = vwap_slices(total_qty=1000)
    total = sum(s.target_qty for s in slices)
    assert total == 1000

def test_vwap_custom_profile():
    profile = [0.5, 0.5]
    slices = vwap_slices(total_qty=200, profile=profile)
    assert len(slices) == 2
    assert sum(s.target_qty for s in slices) == 200

def test_vwap_zero_qty():
    assert vwap_slices(total_qty=0) == []

# ── simple_slices ───────────────────────────────────────────────

def test_simple_slices_basic():
    slices = simple_slices(total_qty=2500, max_per_slice=1000)
    assert len(slices) == 3
    assert slices[0].target_qty == 1000
    assert slices[1].target_qty == 1000
    assert slices[2].target_qty == 500

def test_simple_slices_exact():
    slices = simple_slices(total_qty=2000, max_per_slice=500)
    assert len(slices) == 4
    assert all(s.target_qty == 500 for s in slices)

def test_simple_slices_small():
    slices = simple_slices(total_qty=100, max_per_slice=1000)
    assert len(slices) == 1
    assert slices[0].target_qty == 100

def test_simple_slices_zero():
    assert simple_slices(total_qty=0, max_per_slice=100) == []

# ── apply_liquidity_gate ────────────────────────────────────────

def test_liquidity_gate_no_split_needed():
    specs = [SliceSpec(slice_index=0, target_qty=100)]
    result = apply_liquidity_gate(specs, adv=10000, max_adv_frac=0.02)
    # 2% of 10000 = 200, qty=100 < 200, no split
    assert len(result) == 1

def test_liquidity_gate_splits_large():
    specs = [SliceSpec(slice_index=0, target_qty=500)]
    result = apply_liquidity_gate(specs, adv=10000, max_adv_frac=0.02)
    # 2% of 10000 = 200, qty=500 > 200 → split
    assert len(result) >= 3

def test_liquidity_gate_zero_adv():
    specs = [SliceSpec(slice_index=0, target_qty=5000)]
    result = apply_liquidity_gate(specs, adv=0)
    assert result == specs  # unchanged

# ── SmartOrderRouter ────────────────────────────────────────────

def test_router_plan_twap():
    sor = SmartOrderRouter(default_num_slices=4, default_interval=60)
    plan = sor.plan_twap("AAPL", "buy", 400, num_slices=4)
    assert plan.strategy == "twap"
    assert plan.symbol == "AAPL"
    assert plan.side == "buy"
    assert plan.num_slices == 4
    assert plan.total_planned_qty == 400

def test_router_plan_vwap():
    sor = SmartOrderRouter()
    plan = sor.plan_vwap("MSFT", "sell", 500)
    assert plan.strategy == "vwap"
    assert plan.total_planned_qty == 500

def test_router_plan_simple():
    sor = SmartOrderRouter(default_max_per_slice=500)
    plan = sor.plan_simple("TSLA", "buy", 1200, max_per_slice=500)
    assert plan.strategy == "simple"
    assert plan.num_slices == 3

def test_should_slice_below_threshold():
    sor = SmartOrderRouter()
    assert not sor.should_slice(500, threshold=1000)

def test_should_slice_above_threshold():
    sor = SmartOrderRouter()
    assert sor.should_slice(2000, threshold=1000)

def test_plan_auto():
    sor = SmartOrderRouter()
    plan = sor.plan("AAPL", "buy", 600, strategy="twap", num_slices=3)
    assert plan.strategy == "twap"

def test_route_plan_validate():
    plan = RoutePlan(symbol="AAPL", side="buy", total_qty=100, slices=[
        SliceSpec(slice_index=0, target_qty=100),
    ])
    assert plan.validate()

def test_route_plan_validate_fails():
    plan = RoutePlan(symbol="AAPL", side="buy", total_qty=100, slices=[
        SliceSpec(slice_index=0, target_qty=50),
    ])
    assert not plan.validate()


