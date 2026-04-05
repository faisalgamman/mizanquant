"""Alpaca Trading API client (read-only).

Provides access to account info, positions, and order history
from the Alpaca paper/live trading account.

Supports multi-account via optional strategy_id parameter:
  - strategy_id=None  → uses default ALPACA_API_KEY (legacy)
  - strategy_id="A"   → uses ALPACA_API_KEY_A (Momentum Alpha)
  - strategy_id="B"   → uses ALPACA_API_KEY_B (Mean Reversion)
  - strategy_id="C"   → uses ALPACA_API_KEY_C (AI Ensemble)
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger("screener")


def _get_headers(strategy_id: str = None) -> dict:
    """Return Alpaca API authentication headers.

    If strategy_id is provided, use that strategy's credentials.
    Otherwise fall back to the default (legacy) credentials.
    """
    if strategy_id:
        from app.config import STRATEGY_CONFIGS
        cfg = STRATEGY_CONFIGS.get(strategy_id)
        if cfg and cfg.alpaca_api_key:
            return {
                "APCA-API-KEY-ID": cfg.alpaca_api_key,
                "APCA-API-SECRET-KEY": cfg.alpaca_secret_key,
            }
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


def _has_credentials(strategy_id: str = None) -> bool:
    """Check if credentials are configured for the given account."""
    if strategy_id:
        from app.config import STRATEGY_CONFIGS
        cfg = STRATEGY_CONFIGS.get(strategy_id)
        return bool(cfg and cfg.alpaca_api_key and cfg.alpaca_secret_key)
    return bool(settings.ALPACA_API_KEY and settings.ALPACA_SECRET_KEY)


def _api_get(endpoint: str, strategy_id: str = None) -> Optional[dict | list]:
    """Make an authenticated GET request to Alpaca Trading API."""
    if not _has_credentials(strategy_id):
        return None

    base = _get_base_url()
    url = f"{base}/v2/{endpoint}"

    try:
        with httpx.Client(timeout=15) as client:
            resp = client.get(url, headers=_get_headers(strategy_id))
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as e:
        sid = f" [Strategy {strategy_id}]" if strategy_id else ""
        logger.error(f"Alpaca API{sid} {e.response.status_code} for {endpoint}: {e.response.text[:200]}")
        return None
    except Exception as e:
        sid = f" [Strategy {strategy_id}]" if strategy_id else ""
        logger.error(f"Alpaca API{sid} error for {endpoint}: {e}")
        return None


def get_account(strategy_id: str = None) -> Optional[dict]:
    """Get Alpaca account summary (equity, cash, buying power)."""
    data = _api_get("account", strategy_id=strategy_id)
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


def get_positions(strategy_id: str = None) -> list[dict]:
    """Get all open positions with P&L."""
    data = _api_get("positions", strategy_id=strategy_id)
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


def get_orders(status: str = "all", limit: int = 50, strategy_id: str = None) -> list[dict]:
    """Get recent orders."""
    data = _api_get(f"orders?status={status}&limit={limit}&direction=desc", strategy_id=strategy_id)
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


def get_portfolio_history(period: str = "1M", timeframe: str = "1D", strategy_id: str = None) -> Optional[dict]:
    """Get portfolio equity history for charting.

    Args:
        period: Time period (1D, 1W, 1M, 3M, 1A, all)
        timeframe: Data resolution (1Min, 5Min, 15Min, 1H, 1D)
        strategy_id: Optional strategy account to query
    """
    data = _api_get(f"account/portfolio/history?period={period}&timeframe={timeframe}", strategy_id=strategy_id)
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
