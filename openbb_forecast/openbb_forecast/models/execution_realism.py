"""Realistic execution simulation for backtesting.

Goes beyond fixed-bps slippage to model:
  - Volume-dependent market impact (Almgren-Chriss model)
  - Queue position effects (random delay)
  - Latency penalties (distance to exchange)
  - Spread crossing costs
  - Dark pool / lit market routing
  - Partial fills and fill probability
  - Adverse selection (price moves against you on large orders)

Usage:
    sim = ExecutionSimulator(volume_profile, spread_model)
    result = sim.simulate(order, market_state, latency_ms=15)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal

import numpy as np

logger = logging.getLogger("execution")


# ------------------------------------------------------------------
# Domain objects
# ------------------------------------------------------------------

@dataclass
class Order:
    """A single order for simulation."""
    symbol: str
    side: Literal["BUY", "SELL"]
    quantity: int  # number of shares
    order_type: Literal["MARKET", "LIMIT", "TWAP", "VWAP"] = "MARKET"
    limit_price: float | None = None  # for LIMIT orders
    urgency: float = 1.0  # 0.1=pure TWAP, 1.0=aggressive market
    id: str = ""


@dataclass
class MarketState:
    """Snapshot of market conditions at order time."""
    mid_price: float
    bid_price: float
    ask_price: float
    bid_size: int
    ask_size: int
    volume_last_minute: int  # recent trading volume (liquidity proxy)
    daily_volume: int
    volatility: float  # realized vol (annualized)
    spread_bps: float  # current spread in basis points
    timestamp: str = ""


@dataclass
class ExecutionResult:
    """Result of simulating an order execution."""
    order: Order
    filled_quantity: int
    average_price: float
    total_cost: float  # includes commission, spread, slippage, impact
    commission: float
    slippage_cost: float  # price slippage vs mid at decision time
    spread_cost: float  # half-spread crossed
    impact_cost: float  # market impact (Almgren-Chriss)
    latency_cost: float  # adverse selection during latency
    fill_rate: float  # filled / ordered
    execution_time_ms: float  # simulated time to fill
    details: dict = field(default_factory=dict)


# ------------------------------------------------------------------
# Models
# ------------------------------------------------------------------

class VolumeProfileModel:
    """Models intraday volume distribution (U-shape pattern)."""

    @staticmethod
    def fraction_of_day(minute_of_day: int) -> float:
        """Return expected volume fraction at a given minute (0-390 for US equities)."""
        # U-shape: high at open (0), low mid-day, high at close (390)
        x = minute_of_day / 390.0
        # Parabolic U-shape with minimum at mid-day
        u_shape = 1.0 + 2.0 * ((x - 0.5) ** 2)
        return u_shape / 1.5  # Normalize so mean ≈ 1.0

    @staticmethod
    def expected_volume(minute_of_day: int, daily_volume: int, window_minutes: int = 1) -> int:
        """Expected volume in the next N minutes."""
        fraction = VolumeProfileModel.fraction_of_day(minute_of_day)
        return int(daily_volume * fraction * window_minutes / 390.0)


class SpreadModel:
    """Models bid-ask spread dynamics."""

    def __init__(self, mean_spread_bps: float = 5.0, vol_spread_ratio: float = 0.3):
        self.mean_spread = mean_spread_bps
        self.vol_spread_ratio = vol_spread_ratio

    def current_spread(self, volatility: float, rng: np.random.Generator | None = None) -> float:
        """Estimate current spread based on volatility.

        Wider spreads during high volatility.
        """
        rng = rng or np.random.default_rng()
        base_spread = self.mean_spread
        vol_component = volatility * self.vol_spread_ratio * 10000  # convert to bps
        noise = rng.normal(0, 1.0)
        return max(1.0, base_spread + vol_component + noise)


class MarketImpactModel:
    """Almgren-Chriss temporary and permanent market impact.

    Reference: Almgren & Chriss (2000), "Optimal Execution of Portfolio Transactions"
    """

    def __init__(
        self,
        eta: float = 0.1,     # temporary impact coefficient (bps per % of ADV)
        gamma: float = 0.05,  # permanent impact coefficient
        daily_volatility: float = 0.01,  # 1% daily vol
    ):
        self.eta = eta
        self.gamma = gamma
        self.sigma = daily_volatility

    def temporary_impact(
        self,
        shares: int,
        daily_volume: int,
        urgency: float = 1.0,
    ) -> float:
        """Estimate temporary (liquidity-driven) price impact in basis points.

        Impact = eta * sigma * (Q / ADV * T)^beta * sign(Q)
        where Q = order size, ADV = average daily volume, T = time fraction.
        """
        if daily_volume <= 0:
            return 0.0

        participation_rate = abs(shares) / daily_volume
        # Higher urgency = higher participation rate
        effective_rate = participation_rate * (1.0 + urgency * 0.5)

        # Almgren-Chriss square-root impact model
        impact = self.eta * self.sigma * np.sqrt(effective_rate)

        # Convert to bps and apply sign
        impact_bps = impact * 10000.0 * np.sign(shares)
        return float(impact_bps)

    def permanent_impact(self, shares: int, daily_volume: int) -> float:
        """Permanent information-driven price impact in basis points."""
        if daily_volume <= 0:
            return 0.0

        signed_fraction = shares / daily_volume
        impact = self.gamma * self.sigma * signed_fraction
        return float(impact * 10000)


class AdverseSelectionModel:
    """Models adverse price moves during order latency."""

    def __init__(self, base_adverse_move_bps: float = 0.5):
        self.base_move = base_adverse_move_bps

    def expected_adverse_move(
        self,
        order_size: int,
        daily_volume: int,
        latency_ms: float,
        volatility: float,
        rng: np.random.Generator | None = None,
    ) -> float:
        """Expected adverse price move (bps) during latency.

        Larger orders signal more information → worse selection.
        Higher volatility → larger moves during any delay.

        Returns positive number (cost in bps).
        """
        rng = rng or np.random.default_rng()

        if daily_volume <= 0:
            return 0.0

        size_signal = min(1.0, abs(order_size) / max(daily_volume * 0.01, 1))
        latency_seconds = latency_ms / 1000.0
        vol_per_second = volatility / np.sqrt(252 * 390 * 60)  # annualized → per-second

        # Base move + size penalty + volatility scaling
        expected_move = (
            self.base_move
            + size_signal * 2.0
            + vol_per_second * latency_seconds * 10000 * 3.0  # 3-sigma for worst case
        )

        # Randomize
        noise = max(0, rng.normal(0, expected_move * 0.5))
        return float(expected_move + noise)


class FillModel:
    """Models fill probability for limit orders."""

    @staticmethod
    def fill_probability(
        limit_price: float,
        mid_price: float,
        side: str,
        volatility: float,
        time_horizon_minutes: float,
        rng: np.random.Generator | None = None,
    ) -> float:
        """Probability that a limit order fills within time_horizon_minutes.

        Closer to mid → higher fill probability.
        Higher volatility → higher fill (price moves through limit).
        """
        rng = rng or np.random.default_rng()

        if side == "BUY":
            # Limit buy: we buy at or below limit_price
            # Price needs to drop below our limit
            distance_bps = (mid_price - limit_price) / mid_price * 10000
        else:
            # Limit sell: we sell at or above limit_price
            distance_bps = (limit_price - mid_price) / mid_price * 10000

        if distance_bps <= 0:
            return 1.0  # Marketable limit order

        # Model: probability decreases exponentially with distance
        vol_over_horizon = volatility * np.sqrt(time_horizon_minutes / (252 * 390))
        z_score = distance_bps / 10000 / max(vol_over_horizon, 1e-8)

        # Normal CDF approximation
        prob = 1.0 / (1.0 + np.exp(-1.7 * z_score))

        return max(0.0, min(1.0, prob))


# ------------------------------------------------------------------
# Simulator
# ------------------------------------------------------------------

class ExecutionSimulator:
    """Realistic execution simulator for backtesting.

    Simulates orders through realistic market microstructure models
    including volume-dependent impact, adverse selection, and fill modeling.

    Args:
        commission_per_share: Commission per share in dollars.
        min_commission: Minimum commission per order.
        volume_model: Intraday volume profile model.
        spread_model: Bid-ask spread model.
        impact_model: Market impact model.
        adverse_model: Adverse selection model.
        seed: Random seed for reproducibility.
    """

    def __init__(
        self,
        commission_per_share: float = 0.005,
        min_commission: float = 1.0,
        volume_model: VolumeProfileModel | None = None,
        spread_model: SpreadModel | None = None,
        impact_model: MarketImpactModel | None = None,
        adverse_model: AdverseSelectionModel | None = None,
        fill_model: FillModel | None = None,
        latency_ms: float = 15.0,
        seed: int | None = None,
    ):
        self.commission_per_share = commission_per_share
        self.min_commission = min_commission
        self.latency_ms = latency_ms
        self.volume_model = volume_model or VolumeProfileModel()
        self.spread_model = spread_model or SpreadModel()
        self.impact_model = impact_model or MarketImpactModel()
        self.adverse_model = adverse_model or AdverseSelectionModel()
        self.fill_model = fill_model or FillModel()
        self._rng = np.random.default_rng(seed)

    def simulate(
        self,
        order: Order,
        market: MarketState,
        latency_ms: float | None = None,
    ) -> ExecutionResult:
        """Simulate execution of an order against current market state.

        Args:
            order: The order to simulate.
            market: Current market state.
            latency_ms: Override latency (uses instance default if None).

        Returns:
            ExecutionResult with fill details and cost breakdown.
        """
        lat = latency_ms if latency_ms is not None else self.latency_ms

        # 1. Commission
        commission = max(self.min_commission, order.quantity * self.commission_per_share)

        # 2. Spread cost (crossing half-spread)
        half_spread_bps = market.spread_bps / 2.0
        spread_cost = order.quantity * market.mid_price * half_spread_bps / 10000.0

        # 3. Market impact
        impact_bps = self.impact_model.temporary_impact(
            order.quantity, market.daily_volume, urgency=order.urgency
        )
        impact_cost = abs(order.quantity) * market.mid_price * abs(impact_bps) / 10000.0

        # 4. Adverse selection during latency
        adverse_bps = self.adverse_model.expected_adverse_move(
            order.quantity, market.daily_volume, lat, market.volatility, self._rng
        )
        latency_cost = order.quantity * market.mid_price * adverse_bps / 10000.0

        # 5. Slippage (random component based on volatility)
        vol_scaled_slippage = market.volatility * self._rng.normal(0, 0.5)
        slippage_bps = max(0, abs(vol_scaled_slippage) * 10000)
        slippage_cost = order.quantity * market.mid_price * slippage_bps / 10000.0

        # 6. Fill determination
        if order.order_type == "LIMIT" and order.limit_price is not None:
            fill_prob = self.fill_model.fill_probability(
                order.limit_price, market.mid_price, order.side,
                market.volatility, time_horizon_minutes=5.0, rng=self._rng,
            )
            filled_qty = int(order.quantity * fill_prob)
        else:
            # Market orders always fill (at cost of impact)
            filled_qty = order.quantity

        if filled_qty <= 0:
            return ExecutionResult(
                order=order,
                filled_quantity=0,
                average_price=0.0,
                total_cost=0.0,
                commission=0.0,
                slippage_cost=0.0,
                spread_cost=0.0,
                impact_cost=0.0,
                latency_cost=0.0,
                fill_rate=0.0,
                execution_time_ms=lat,
                details={"reason": "unfilled_limit_order"},
            )

        # 7. Average execution price
        direction = 1 if order.side == "BUY" else -1
        total_bps_cost = (half_spread_bps + abs(impact_bps) + adverse_bps + slippage_bps) * direction
        average_price = market.mid_price * (1.0 + total_bps_cost / 10000.0)

        # 8. Total cost
        total_cost = commission + spread_cost + impact_cost + latency_cost + slippage_cost

        # 9. Simulated execution time
        # Larger orders take longer
        exec_time_multiplier = 1.0 + (abs(order.quantity) / max(market.daily_volume * 0.01, 1)) * 10.0
        execution_time = lat * exec_time_multiplier * (1.0 + self._rng.uniform(0, 1.0))

        return ExecutionResult(
            order=order,
            filled_quantity=filled_qty,
            average_price=float(average_price),
            total_cost=float(total_cost),
            commission=float(commission),
            slippage_cost=float(slippage_cost),
            spread_cost=float(spread_cost),
            impact_cost=float(impact_cost),
            latency_cost=float(latency_cost),
            fill_rate=filled_qty / order.quantity if order.quantity > 0 else 0.0,
            execution_time_ms=execution_time,
            details={
                "half_spread_bps": half_spread_bps,
                "impact_bps": abs(impact_bps),
                "adverse_bps": adverse_bps,
                "slippage_bps": slippage_bps,
                "total_bps": total_bps_cost * direction,
                "latency_ms": lat,
            },
        )


def simulate_round_trip(
    simulator: ExecutionSimulator,
    symbol: str,
    entry_side: Literal["BUY", "SELL"],
    quantity: int,
    entry_market: MarketState,
    exit_market: MarketState,
    entry_price_override: float | None = None,
    exit_price_override: float | None = None,
    latency_ms: float | None = None,
) -> dict:
    """Simulate a complete round-trip trade with entry and exit.

    Returns a dict with full trade economics.
    """
    entry_order = Order(symbol=symbol, side=entry_side, quantity=quantity)
    exit_side = "SELL" if entry_side == "BUY" else "BUY"
    exit_order = Order(symbol=symbol, side=exit_side, quantity=quantity)

    entry_result = simulator.simulate(entry_order, entry_market, latency_ms)
    exit_result = simulator.simulate(exit_order, exit_market, latency_ms)

    # Gross PnL — theoretical mid-to-mid move before execution costs
    entry_mid = entry_market.mid_price
    exit_mid = exit_market.mid_price

    if entry_side == "BUY":
        gross_pnl = (exit_mid - entry_mid) * entry_result.filled_quantity
    else:
        gross_pnl = (entry_mid - exit_mid) * entry_result.filled_quantity

    total_cost = entry_result.total_cost + exit_result.total_cost
    net_pnl = gross_pnl - total_cost

    # Realized fill prices (including execution costs)
    entry_price = entry_price_override or entry_result.average_price
    exit_price = exit_price_override or exit_result.average_price

    return {
        "symbol": symbol,
        "side": entry_side,
        "quantity_ordered": quantity,
        "quantity_filled": min(entry_result.filled_quantity, exit_result.filled_quantity),
        "entry_price": entry_price,
        "exit_price": exit_price,
        "gross_pnl": float(gross_pnl),
        "entry_cost": float(entry_result.total_cost),
        "exit_cost": float(exit_result.total_cost),
        "total_cost": float(total_cost),
        "net_pnl": float(net_pnl),
        "net_return_bps": float(net_pnl / (entry_price * quantity) * 10000) if entry_price > 0 and quantity > 0 else 0.0,
        "entry_details": entry_result.details,
        "exit_details": exit_result.details,
    }
