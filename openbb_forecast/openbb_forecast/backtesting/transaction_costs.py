"""Transaction cost model — the original codebase had zero cost modeling.

Original code: `starting_money -= self.trend[t]` (exact price, no costs)
Reality: every trade incurs commission, slippage, and spread costs.
Even small costs (10-20 bps round-trip) can eliminate thin alpha.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TransactionCostModel:
    """Models realistic trading costs.

    Three cost components:
        commission_bps: Broker commission in basis points (e.g., 10 = 0.10%).
        slippage_bps: Market impact / slippage in basis points (e.g., 5 = 0.05%).
        spread_bps: Half bid-ask spread in basis points (e.g., 5 = 0.05%).

    Total cost per trade = price * (commission + slippage + spread) / 10000.

    Typical values for US equities:
        - Discount broker: commission=0-5 bps
        - Slippage (small orders): 2-10 bps
        - Spread (liquid stocks): 1-5 bps
        - Total round-trip: 10-40 bps
    """

    commission_bps: float = 10.0
    slippage_bps: float = 5.0
    spread_bps: float = 5.0

    @property
    def total_bps(self) -> float:
        return self.commission_bps + self.slippage_bps + self.spread_bps

    def buy_cost(self, price: float, quantity: int = 1) -> tuple[float, float, float]:
        """Calculate effective buy price and cost breakdown.

        Returns:
            (effective_price, commission_amount, slippage_amount)
            where effective_price > market_price (you pay more).
        """
        cost_frac = self.total_bps / 10_000
        effective_price = price * (1 + cost_frac)
        commission = price * quantity * self.commission_bps / 10_000
        slippage = price * quantity * (self.slippage_bps + self.spread_bps) / 10_000
        return effective_price, commission, slippage

    def sell_cost(self, price: float, quantity: int = 1) -> tuple[float, float, float]:
        """Calculate effective sell price and cost breakdown.

        Returns:
            (effective_price, commission_amount, slippage_amount)
            where effective_price < market_price (you receive less).
        """
        cost_frac = self.total_bps / 10_000
        effective_price = price * (1 - cost_frac)
        commission = price * quantity * self.commission_bps / 10_000
        slippage = price * quantity * (self.slippage_bps + self.spread_bps) / 10_000
        return effective_price, commission, slippage

    def round_trip_cost(self, price: float, quantity: int = 1) -> float:
        """Total cost for a buy + sell round trip."""
        return price * quantity * 2 * self.total_bps / 10_000
