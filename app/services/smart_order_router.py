"""Smart Order Router — split large orders intelligently.

Provides:
  - TWAP (Time-Weighted Average Price): split order over N slices at fixed intervals
  - VWAP (Volume-Weighted Average Price): distribute based on volume profile
  - Simple slicing: split into equal-sized tranches
  - Liquidity gates: cap individual child orders to max_frac of ADV

Used by OrderManager when qty > configured threshold.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional, Sequence

logger = logging.getLogger("screener")


# ---------------------------------------------------------------------------
# Slice plan
# ---------------------------------------------------------------------------

@dataclass
class SliceSpec:
    """A single slice in a routing plan."""
    slice_index: int
    target_qty: int
    target_price: Optional[float] = None
    interval_seconds: float = 300.0   # seconds between this slice and the next
    order_type: str = "limit"
    time_in_force: str = "day"


@dataclass
class RoutePlan:
    """A complete plan of order slices."""
    symbol: str
    side: str
    total_qty: int
    slices: list[SliceSpec] = field(default_factory=list)
    strategy: str = "twap"  # twap, vwap, simple
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def num_slices(self) -> int:
        return len(self.slices)

    @property
    def total_planned_qty(self) -> int:
        return sum(s.target_qty for s in self.slices)

    def validate(self) -> bool:
        return self.total_planned_qty == self.total_qty and len(self.slices) > 0


# ---------------------------------------------------------------------------
# Volume profile — default approximate distribution
# ---------------------------------------------------------------------------

# Default US equity intraday volume profile (30-minute buckets, normalized)
# Based on typical U-shape: heavier at open and close
DEFAULT_VOLUME_PROFILE: list[float] = [
    0.12,   # 09:30-10:00
    0.09,   # 10:00-10:30
    0.07,   # 10:30-11:00
    0.06,   # 11:00-11:30
    0.05,   # 11:30-12:00
    0.04,   # 12:00-12:30
    0.04,   # 12:30-13:00
    0.04,   # 13:00-13:30
    0.05,   # 13:30-14:00
    0.06,   # 14:00-14:30
    0.07,   # 14:30-15:00
    0.09,   # 15:00-15:30
    0.22,   # 15:30-16:00
]


def _normalize_profile(profile: Sequence[float]) -> list[float]:
    """Ensure profile sums to 1.0."""
    total = sum(profile)
    if total <= 0:
        return [1.0 / len(profile)] * len(profile)
    return [v / total for v in profile]


# ---------------------------------------------------------------------------
# Slice generators
# ---------------------------------------------------------------------------

def twap_slices(
    total_qty: int,
    num_slices: int,
    interval_seconds: float = 300.0,
    order_type: str = "limit",
    time_in_force: str = "day",
    min_slice_qty: int = 1,
) -> list[SliceSpec]:
    """Generate TWAP slices: equal quantity per slice.

    Args:
        total_qty: Total quantity to execute
        num_slices: Number of slices
        interval_seconds: Time between slices
        min_slice_qty: Minimum qty per slice (remaining goes to last slice)
    """
    if total_qty <= 0 or num_slices <= 0:
        return []

    # Equal distribution with remainder to last slice
    base = max(min_slice_qty, total_qty // num_slices)
    slices: list[SliceSpec] = []
    allocated = 0

    for i in range(num_slices):
        if i == num_slices - 1:
            qty = total_qty - allocated
        else:
            qty = base
            allocated += base
        if qty <= 0:
            break
        slices.append(SliceSpec(
            slice_index=i,
            target_qty=qty,
            interval_seconds=interval_seconds,
            order_type=order_type,
            time_in_force=time_in_force,
        ))

    return slices


def vwap_slices(
    total_qty: int,
    profile: Optional[Sequence[float]] = None,
    order_type: str = "limit",
    time_in_force: str = "day",
    min_slice_qty: int = 1,
) -> list[SliceSpec]:
    """Generate VWAP slices: distributed by volume profile.

    Args:
        total_qty: Total quantity
        profile: Volume profile per bucket (normalized to 1.0)
                 Defaults to standard US equity intraday profile.
    """
    if total_qty <= 0:
        return []

    probs = _normalize_profile(profile or DEFAULT_VOLUME_PROFILE)
    slices: list[SliceSpec] = []
    allocated = 0

    for i, prob in enumerate(probs):
        if i == len(probs) - 1:
            qty = total_qty - allocated
        else:
            qty = max(0, round(total_qty * prob))
            allocated += qty
        if qty <= 0:
            continue
        slices.append(SliceSpec(
            slice_index=i,
            target_qty=qty,
            interval_seconds=1800,  # 30 minutes per bucket
            order_type=order_type,
            time_in_force=time_in_force,
        ))

    # Adjust final allocation to match total exactly
    if slices:
        actual_total = sum(s.target_qty for s in slices)
        diff = total_qty - actual_total
        if diff != 0:
            slices[-1].target_qty += diff

    return slices


def simple_slices(
    total_qty: int,
    max_per_slice: int,
    order_type: str = "market",
    time_in_force: str = "day",
) -> list[SliceSpec]:
    """Split into slices less than or equal to max_per_slice.

    No timing — all slices submitted at once.
    """
    if total_qty <= 0:
        return []

    slices: list[SliceSpec] = []
    remaining = total_qty
    idx = 0

    while remaining > 0:
        qty = min(max_per_slice, remaining)
        slices.append(SliceSpec(
            slice_index=idx,
            target_qty=qty,
            interval_seconds=0,  # immediate
            order_type=order_type,
            time_in_force=time_in_force,
        ))
        remaining -= qty
        idx += 1

    return slices


# ---------------------------------------------------------------------------
# Liquidity gate
# ---------------------------------------------------------------------------

def apply_liquidity_gate(
    slices: list[SliceSpec],
    adv: int,              # Average daily volume (shares)
    max_adv_frac: float = 0.02,   # Max 2% of ADV per slice
) -> list[SliceSpec]:
    """Cap each slice to a maximum fraction of ADV.

    If a slice exceeds max_adv_frac * adv, it's split further.
    """
    if adv <= 0 or max_adv_frac <= 0:
        return slices

    max_per = max(1, int(adv * max_adv_frac))
    result: list[SliceSpec] = []

    for spec in slices:
        if spec.target_qty <= max_per:
            result.append(spec)
        else:
            # Split this slice into sub-slices
            sub = simple_slices(spec.target_qty, max_per,
                               order_type=spec.order_type,
                               time_in_force=spec.time_in_force)
            for s in sub:
                s.interval_seconds = spec.interval_seconds
            result.extend(sub)

    return result


# ---------------------------------------------------------------------------
# Smart Order Router
# ---------------------------------------------------------------------------

class SmartOrderRouter:
    """Routes large orders intelligently by slicing them.

    Usage:
        sor = SmartOrderRouter()
        plan = sor.plan_twap(symbol="AAPL", side="buy", total_qty=5000, num_slices=10)
        for slice in plan.slices:
            ...submit slice...
    """

    def __init__(
        self,
        default_max_per_slice: int = 1000,
        default_interval: float = 300.0,
        default_num_slices: int = 5,
        adv_lookup: Optional[Callable[[str], int]] = None,
    ) -> None:
        self.default_max_per_slice = default_max_per_slice
        self.default_interval = default_interval
        self.default_num_slices = default_num_slices
        self._adv_lookup = adv_lookup

    def plan_twap(
        self,
        symbol: str,
        side: str,
        total_qty: int,
        num_slices: Optional[int] = None,
        interval_seconds: Optional[float] = None,
        order_type: str = "limit",
        time_in_force: str = "day",
    ) -> RoutePlan:
        """Plan a TWAP execution."""
        n = num_slices or self.default_num_slices
        interval = interval_seconds or self.default_interval

        specs = twap_slices(
            total_qty=total_qty,
            num_slices=n,
            interval_seconds=interval,
            order_type=order_type,
            time_in_force=time_in_force,
        )

        # Apply liquidity gate if ADV is known
        adv = self._get_adv(symbol)
        if adv:
            specs = apply_liquidity_gate(specs, adv)

        return RoutePlan(
            symbol=symbol.upper(),
            side=side.lower(),
            total_qty=total_qty,
            slices=specs,
            strategy="twap",
        )

    def plan_vwap(
        self,
        symbol: str,
        side: str,
        total_qty: int,
        profile: Optional[Sequence[float]] = None,
        order_type: str = "limit",
        time_in_force: str = "day",
    ) -> RoutePlan:
        """Plan a VWAP execution."""
        specs = vwap_slices(
            total_qty=total_qty,
            profile=profile,
            order_type=order_type,
            time_in_force=time_in_force,
        )

        adv = self._get_adv(symbol)
        if adv:
            specs = apply_liquidity_gate(specs, adv)

        return RoutePlan(
            symbol=symbol.upper(),
            side=side.lower(),
            total_qty=total_qty,
            slices=specs,
            strategy="vwap",
        )

    def plan_simple(
        self,
        symbol: str,
        side: str,
        total_qty: int,
        max_per_slice: Optional[int] = None,
        order_type: str = "market",
        time_in_force: str = "day",
    ) -> RoutePlan:
        """Split into equal-sized chunks."""
        max_per = max_per_slice or self.default_max_per_slice
        specs = simple_slices(
            total_qty=total_qty,
            max_per_slice=max_per,
            order_type=order_type,
            time_in_force=time_in_force,
        )

        adv = self._get_adv(symbol)
        if adv:
            specs = apply_liquidity_gate(specs, adv)

        return RoutePlan(
            symbol=symbol.upper(),
            side=side.lower(),
            total_qty=total_qty,
            slices=specs,
            strategy="simple",
        )

    def _get_adv(self, symbol: str) -> int:
        """Get ADV for a symbol. Returns 0 if unknown."""
        if self._adv_lookup:
            try:
                return self._adv_lookup(symbol.upper())
            except Exception as e:
                logger.debug("SOR ADV lookup failed for %s: %s", symbol, e)
        return 0

    # ------------------------------------------------------------------
    # Auto-slice decision
    # ------------------------------------------------------------------

    def should_slice(self, total_qty: int, threshold: int = 1000) -> bool:
        """Return True if the order should be sliced."""
        return total_qty > threshold

    def plan(
        self,
        symbol: str,
        side: str,
        total_qty: int,
        strategy: str = "twap",
        **kwargs,
    ) -> RoutePlan:
        """Auto-plan based on strategy string."""
        strategy = strategy.lower().strip()
        if strategy == "vwap":
            return self.plan_vwap(symbol, side, total_qty, **kwargs)
        elif strategy == "simple":
            return self.plan_simple(symbol, side, total_qty, **kwargs)
        else:
            return self.plan_twap(symbol, side, total_qty, **kwargs)


__all__ = [
    "SmartOrderRouter",
    "RoutePlan",
    "SliceSpec",
    "twap_slices",
    "vwap_slices",
    "simple_slices",
    "apply_liquidity_gate",
]
