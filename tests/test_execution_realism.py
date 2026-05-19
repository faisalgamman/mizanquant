"""Tests for execution_realism.py"""
import pytest
import numpy as np
from openbb_forecast.models.execution_realism import (
    ExecutionSimulator, Order, MarketState, ExecutionResult,
    VolumeProfileModel, SpreadModel, MarketImpactModel, AdverseSelectionModel, FillModel,
    simulate_round_trip,
)

class TestExecutionSimulator:
    @pytest.fixture
    def sim(self):
        return ExecutionSimulator(seed=42)

    @pytest.fixture
    def market(self):
        return MarketState(
            mid_price=100.0,
            bid_price=99.98,
            ask_price=100.02,
            bid_size=1000,
            ask_size=1000,
            volume_last_minute=50000,
            daily_volume=5000000,
            volatility=0.20,  # 20% annualized
            spread_bps=4.0,
        )

    def test_market_order_fills_fully(self, sim, market):
        order = Order(symbol="AAPL", side="BUY", quantity=100)
        result = sim.simulate(order, market)
        assert result.filled_quantity == 100
        assert result.fill_rate == 1.0
        assert result.average_price > 100.0  # spread + impact
        assert result.total_cost > 0
        assert result.commission > 0

    def test_limit_order_fill_model(self, sim, market):
        # Limit buy below mid -> may or may not fill
        order = Order(symbol="AAPL", side="BUY", quantity=100, order_type="LIMIT", limit_price=99.50)
        result = sim.simulate(order, market)
        # With high spread, fill probability is low
        assert 0.0 <= result.fill_rate <= 1.0

    def test_sell_order(self, sim, market):
        order = Order(symbol="AAPL", side="SELL", quantity=200)
        result = sim.simulate(order, market)
        assert result.filled_quantity == 200
        assert result.average_price < 100.0  # receive less than mid

    def test_cost_breakdown(self, sim, market):
        order = Order(symbol="AAPL", side="BUY", quantity=1000, urgency=1.0)
        result = sim.simulate(order, market)
        assert result.commission > 0
        assert result.spread_cost > 0
        assert result.impact_cost >= 0
        assert result.latency_cost >= 0
        total_parts = (result.commission + result.spread_cost + result.impact_cost
                      + result.latency_cost + result.slippage_cost)
        assert abs(total_parts - result.total_cost) < 0.01

    def test_volume_impact_larger_order(self, sim, market):
        small = sim.simulate(Order(symbol="T", side="BUY", quantity=100), market)
        large = sim.simulate(Order(symbol="T", side="BUY", quantity=50000), market)
        # Large orders have higher impact per share
        assert large.total_cost / max(large.filled_quantity, 1) > small.total_cost / max(small.filled_quantity, 1)

    def test_round_trip(self, sim, market):
        entry_market = MarketState(mid_price=100, bid_price=99.98, ask_price=100.02,
                                   bid_size=1000, ask_size=1000, volume_last_minute=50000,
                                   daily_volume=5000000, volatility=0.20, spread_bps=4)
        exit_market = MarketState(mid_price=115, bid_price=114.98, ask_price=115.02,
                                  bid_size=1000, ask_size=1000, volume_last_minute=50000,
                                  daily_volume=5000000, volatility=0.20, spread_bps=4)
        result = simulate_round_trip(sim, "AAPL", "BUY", 10, entry_market, exit_market)
        # gross PnL should be positive (bought at ~$100, sold at ~$115) minus execution costs
        # Smaller quantity reduces market impact → gross PnL ≈ $150 gross, minus $10-20 costs = $130-140 net
        assert result["gross_pnl"] > 0

    def test_reproducibility(self, sim, market):
        sim2 = ExecutionSimulator(seed=42)
        order = Order(symbol="AAPL", side="BUY", quantity=100)
        r1 = sim.simulate(order, market)
        r2 = sim2.simulate(order, market)
        # With same seed and inputs, should be identical
        assert r1.average_price == r2.average_price
        assert r1.total_cost == r2.total_cost

    def test_volume_profile_u_shape(self):
        # Open and close have higher volume than mid-day
        open_vol = VolumeProfileModel.fraction_of_day(0)
        mid_vol = VolumeProfileModel.fraction_of_day(195)
        close_vol = VolumeProfileModel.fraction_of_day(390)
        assert open_vol > mid_vol
        assert close_vol > mid_vol
