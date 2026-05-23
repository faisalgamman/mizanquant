"""Pre-trade execution cost estimation (Phase 2A).

Wraps ExecutionSimulator (Almgren-Chriss + spread + adverse selection)
to produce a cost estimate *before* any order is submitted.

The estimate is stored in signal_details["execution_estimate"] and used
to gate illiquid symbols before they reach the broker.

Environment variables
---------------------
MAX_EXECUTION_IMPACT_BPS   Maximum tolerated impact cost in bps (default 50).
                           Trades whose estimated impact exceeds this are
                           blocked and a reason is returned.
"""

from __future__ import annotations

import logging
import math
import os

logger = logging.getLogger("screener")

# ------------------------------------------------------------------
# Gate threshold (env-configurable)
# ------------------------------------------------------------------

MAX_EXECUTION_IMPACT_BPS: float = float(
    os.environ.get("MAX_EXECUTION_IMPACT_BPS", "50.0")
)


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

def _get_adv_and_vol(symbol: str) -> tuple[int, float]:
    """Return (avg_daily_volume_shares, annualized_volatility) for *symbol*.

    Uses the last 20 trading days from market_data.fetch().
    Falls back to conservative defaults (500_000 shares, 30% vol) on error
    so that cost estimation degrades gracefully rather than crashing.
    """
    DEFAULT_ADV = 500_000
    DEFAULT_VOL = 0.30

    try:
        from app.services.market_data import fetch as fetch_market_data

        df = fetch_market_data(symbol, period="30d")
        if df is None or df.empty:
            return DEFAULT_ADV, DEFAULT_VOL

        # Column name normalisation (Alpaca/yfinance differ)
        col_map = {c.lower(): c for c in df.columns}
        vol_col = col_map.get("volume") or col_map.get("vol")
        close_col = col_map.get("close") or col_map.get("adjclose")

        adv = DEFAULT_ADV
        ann_vol = DEFAULT_VOL

        if vol_col:
            recent = df[vol_col].dropna().tail(20)
            if len(recent) > 0:
                adv = int(recent.mean())

        if close_col:
            closes = df[close_col].dropna().tail(21)
            if len(closes) >= 2:
                daily_returns = closes.pct_change().dropna()
                if len(daily_returns) > 0:
                    ann_vol = float(daily_returns.std() * math.sqrt(252))

        return max(1, adv), max(0.01, ann_vol)

    except Exception as exc:
        logger.debug("execution_cost_estimator: ADV/vol lookup failed for %s: %s", symbol, exc)
        return DEFAULT_ADV, DEFAULT_VOL


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

def build_market_state(symbol: str, price: float):
    """Construct a MarketState for *symbol* at *price*.

    Fetches 20-day average volume and realized volatility from market_data.
    The spread is modelled with SpreadModel (mean 5 bps + vol component).

    Returns
    -------
    MarketState  from openbb_forecast.models.execution_realism
    """
    from openbb_forecast.openbb_forecast.models.execution_realism import (
        MarketState,
        SpreadModel,
    )

    adv, ann_vol = _get_adv_and_vol(symbol)

    spread_model = SpreadModel(mean_spread_bps=5.0, vol_spread_ratio=0.3)
    import numpy as np
    spread_bps = spread_model.current_spread(ann_vol, rng=np.random.default_rng(42))
    half_spread = price * spread_bps / 20_000.0   # half-spread in $

    return MarketState(
        mid_price=price,
        bid_price=price - half_spread,
        ask_price=price + half_spread,
        bid_size=min(adv // 400, 10_000),
        ask_size=min(adv // 400, 10_000),
        volume_last_minute=max(1, adv // 390),
        daily_volume=adv,
        volatility=ann_vol,
        spread_bps=spread_bps,
        timestamp="",
    )


def estimate_execution_cost(
    symbol: str,
    side: str,
    qty: int,
    price: float,
    *,
    market_state=None,
) -> dict:
    """Estimate pre-trade execution cost.

    Parameters
    ----------
    symbol : str
        Ticker symbol (e.g. "AAPL").
    side : str
        "buy" or "sell" (case-insensitive).
    qty : int
        Number of shares.
    price : float
        Decision / reference price (mid price at signal time).
    market_state : MarketState, optional
        Supply a pre-built MarketState to avoid a second network call
        (useful in tests or when the caller already fetched market data).

    Returns
    -------
    dict  with keys:
        est_cost_usd      – total estimated cost in dollars
        est_cost_bps      – total cost in basis points of notional
        impact_bps        – Almgren-Chriss market impact bps
        spread_bps        – current spread bps (snapshot)
        slippage_bps      – random volatility-driven slippage bps
        latency_bps       – adverse-selection cost during latency bps
        fill_rate         – expected fill rate (1.0 for market orders)
        blocked           – True if impact_bps > MAX_EXECUTION_IMPACT_BPS
        block_reason      – human-readable reason when blocked, else ""
    """
    from openbb_forecast.openbb_forecast.models.execution_realism import (
        ExecutionSimulator,
        Order,
    )

    if qty <= 0 or price <= 0:
        return {
            "est_cost_usd": 0.0,
            "est_cost_bps": 0.0,
            "impact_bps": 0.0,
            "spread_bps": 0.0,
            "slippage_bps": 0.0,
            "latency_bps": 0.0,
            "fill_rate": 1.0,
            "blocked": False,
            "block_reason": "",
        }

    if market_state is None:
        market_state = build_market_state(symbol, price)

    order = Order(
        symbol=symbol.upper(),
        side=side.upper(),
        quantity=qty,
        order_type="MARKET",
        urgency=1.0,
    )

    sim = ExecutionSimulator(seed=42)
    result = sim.simulate(order, market_state)

    notional = qty * price
    est_cost_bps = (result.total_cost / notional * 10_000.0) if notional > 0 else 0.0
    impact_bps = result.details.get("impact_bps", 0.0)
    slippage_bps = result.details.get("slippage_bps", 0.0)
    latency_bps = result.details.get("adverse_bps", 0.0)

    blocked = impact_bps > MAX_EXECUTION_IMPACT_BPS
    block_reason = (
        f"Execution impact {impact_bps:.1f} bps exceeds MAX_EXECUTION_IMPACT_BPS "
        f"{MAX_EXECUTION_IMPACT_BPS:.1f} bps — illiquid symbol rejected"
        if blocked
        else ""
    )

    return {
        "est_cost_usd": round(result.total_cost, 4),
        "est_cost_bps": round(est_cost_bps, 2),
        "impact_bps": round(impact_bps, 2),
        "spread_bps": round(market_state.spread_bps, 2),
        "slippage_bps": round(slippage_bps, 2),
        "latency_bps": round(latency_bps, 2),
        "fill_rate": round(result.fill_rate, 4),
        "blocked": blocked,
        "block_reason": block_reason,
    }
