"""Alpaca Trading API client (read-only).

Provides access to account info, positions, and order history
from the Alpaca paper/live trading account. No order execution
(user trades manually through Alpaca dashboard).
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger("screener")


def _get_headers() -> dict:
    """Return Alpaca API authentication headers."""
    return {
        "APCA-API-KEY-ID": settings.ALPACA_API_KEY,
        "APCA-API-SECRET-KEY": settings.ALPACA_SECRET_KEY,
    }


def _get_base_url() -> str:
    """Return the Alpaca trading API base URL."""
    url = settings.ALPACA_BASE_URL
    # Ensure it points to the trading API, not data API
    if "data.alpaca" in url:
        url = url.replace("data.alpaca", "paper-api.alpaca")
    # Remove trailing /v2 if present (we add it per endpoint)
    return url.rstrip("/").removesuffix("/v2")


def _api_get(endpoint: str) -> Optional[dict | list]:
    """Make an authenticated GET request to Alpaca Trading API."""
    if not settings.ALPACA_API_KEY or not settings.ALPACA_SECRET_KEY:
        return None

    base = _get_base_url()
    url = f"{base}/v2/{endpoint}"

    try:
        with httpx.Client(timeout=15) as client:
            resp = client.get(url, headers=_get_headers())
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as e:
        logger.error(f"Alpaca API {e.response.status_code} for {endpoint}: {e.response.text[:200]}")
        return None
    except Exception as e:
        logger.error(f"Alpaca API error for {endpoint}: {e}")
        return None


def get_account() -> Optional[dict]:
    """Get Alpaca account summary (equity, cash, buying power)."""
    data = _api_get("account")
    if not data:
        return None

    return {
        "account_id": data.get("id", ""),
        "status": data.get("status", ""),
        "equity": float(data.get("equity", 0)),
        "cash": float(data.get("cash", 0)),
        "buying_power": float(data.get("buying_power", 0)),
        "portfolio_value": float(data.get("portfolio_value", 0)),
        "last_equity": float(data.get("last_equity", 0)),
        "long_market_value": float(data.get("long_market_value", 0)),
        "short_market_value": float(data.get("short_market_value", 0)),
        "initial_margin": float(data.get("initial_margin", 0)),
        "maintenance_margin": float(data.get("maintenance_margin", 0)),
        "daytrade_count": int(data.get("daytrade_count", 0)),
        "pattern_day_trader": data.get("pattern_day_trader", False),
        "trading_blocked": data.get("trading_blocked", False),
        "account_blocked": data.get("account_blocked", False),
        "currency": data.get("currency", "USD"),
    }


def get_positions() -> list[dict]:
    """Get all open positions with P&L."""
    data = _api_get("positions")
    if not data or not isinstance(data, list):
        return []

    positions = []
    for pos in data:
        positions.append({
            "symbol": pos.get("symbol", ""),
            "qty": float(pos.get("qty", 0)),
            "side": pos.get("side", "long"),
            "market_value": float(pos.get("market_value", 0)),
            "cost_basis": float(pos.get("cost_basis", 0)),
            "avg_entry_price": float(pos.get("avg_entry_price", 0)),
            "current_price": float(pos.get("current_price", 0)),
            "unrealized_pl": float(pos.get("unrealized_pl", 0)),
            "unrealized_plpc": float(pos.get("unrealized_plpc", 0)) * 100,  # Convert to %
            "change_today": float(pos.get("change_today", 0)) * 100,  # Convert to %
        })

    return positions


def get_orders(status: str = "all", limit: int = 50) -> list[dict]:
    """Get recent orders."""
    data = _api_get(f"orders?status={status}&limit={limit}&direction=desc")
    if not data or not isinstance(data, list):
        return []

    orders = []
    for order in data:
        orders.append({
            "symbol": order.get("symbol", ""),
            "side": order.get("side", ""),
            "type": order.get("type", ""),
            "qty": order.get("qty", "0"),
            "filled_qty": order.get("filled_qty", "0"),
            "status": order.get("status", ""),
            "filled_avg_price": order.get("filled_avg_price", ""),
            "submitted_at": order.get("submitted_at", "")[:19] if order.get("submitted_at") else "",
            "filled_at": order.get("filled_at", "")[:19] if order.get("filled_at") else "",
        })

    return orders


def get_portfolio_history(period: str = "1M", timeframe: str = "1D") -> Optional[dict]:
    """Get portfolio equity history for charting.

    Args:
        period: Time period (1D, 1W, 1M, 3M, 1A, all)
        timeframe: Data resolution (1Min, 5Min, 15Min, 1H, 1D)
    """
    data = _api_get(f"account/portfolio/history?period={period}&timeframe={timeframe}")
    if not data:
        return None

    timestamps = data.get("timestamp", [])
    equity_values = data.get("equity", [])
    pl_values = data.get("profit_loss", [])
    pl_pct_values = data.get("profit_loss_pct", [])

    history = []
    for i, ts in enumerate(timestamps):
        dt = datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")
        history.append({
            "Date": dt,
            "Equity": round(equity_values[i], 2) if i < len(equity_values) and equity_values[i] else 0,
            "P&L": round(pl_values[i], 2) if i < len(pl_values) and pl_values[i] else 0,
            "P&L %": round(pl_pct_values[i] * 100, 2) if i < len(pl_pct_values) and pl_pct_values[i] else 0,
        })

    return {
        "base_value": data.get("base_value", 0),
        "history": history,
    }
