"""Shared guard dataclasses and helper types."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal, Optional

from app.services.regime import RegimeSnapshot


@dataclass(frozen=True)
class GuardContext:
    strategy_id: Optional[str]
    symbol: str
    side: Literal["buy", "sell"]
    price: float
    stop_loss: float
    qty: int
    account: dict
    positions: list[dict]
    regime: RegimeSnapshot

    @property
    def notional(self) -> float:
        return float(self.qty or 0) * float(self.price or 0)


@dataclass(frozen=True)
class GuardResult:
    name: str
    passed: bool
    blocking: bool
    reason: str
    code: str


Guard = Callable[[GuardContext], GuardResult]
