"""Risk manager for automated paper trading.

Enforces:
- PDT rule avoidance (max 3 day trades per 5 rolling business days)
- Position sizing (max % of capital per trade)
- Max open positions
- Daily loss limit (circuit breaker)
- Per-stock exposure limit

Designed for $3,000 capital accounts under PDT restrictions.
"""

import logging
import time
import threading
from datetime import datetime, timedelta
from typing import Optional

from app.config import settings

logger = logging.getLogger("screener")


# ---------------------------------------------------------------------------
# Market Hours Check — US Eastern Time
# ---------------------------------------------------------------------------

def is_market_open() -> bool:
    """Check if US stock market is currently open.

    NYSE/NASDAQ hours: 9:30 AM - 4:00 PM Eastern (Mon-Fri)
    Does NOT account for holidays — Alpaca will reject orders anyway.
    """
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo

    eastern = ZoneInfo("America/New_York")
    now = datetime.now(eastern)

    # Weekend check (0=Mon, 5=Sat, 6=Sun)
    if now.weekday() >= 5:
        return False

    # Market hours: 9:30 AM - 4:00 PM ET
    market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now.replace(hour=16, minute=0, second=0, microsecond=0)

    return market_open <= now <= market_close

# ---------------------------------------------------------------------------
# PDT Tracker — rolling 5 business day window
# ---------------------------------------------------------------------------
_pdt_lock = threading.Lock()
_day_trades: list[datetime] = []  # timestamps of day trades (buy+sell same day)

MAX_DAY_TRADES = 2  # conservative: 2 of 3 allowed (keep 1 as safety buffer)
PDT_WINDOW_DAYS = 5  # rolling business days


def record_day_trade():
    """Record a day trade (bought and sold same calendar day)."""
    with _pdt_lock:
        _day_trades.append(datetime.utcnow())


def get_day_trade_count() -> int:
    """Count day trades in the last 5 business days."""
    with _pdt_lock:
        cutoff = datetime.utcnow() - timedelta(days=7)  # 7 calendar ≈ 5 business
        _day_trades[:] = [dt for dt in _day_trades if dt > cutoff]
        return len(_day_trades)


def can_day_trade() -> bool:
    """Check if we can make another day trade without triggering PDT."""
    return get_day_trade_count() < MAX_DAY_TRADES


# ---------------------------------------------------------------------------
# Position Sizing — conservative for small accounts
# ---------------------------------------------------------------------------

def calculate_position_size(
    price: float,
    stop_loss: float,
    available_cash: float,
    total_equity: float,
) -> dict:
    """Calculate position size based on risk management rules.

    Rules for $3,000 account:
    - Risk max 1.5% of total equity per trade ($45 on $3K)
    - Max position size: 15% of equity ($450 on $3K)
    - Min position: 1 share
    - Whole shares only (no fractional for simplicity)

    Returns:
        dict with qty, position_value, risk_amount, risk_pct
    """
    if price <= 0 or stop_loss <= 0 or stop_loss >= price:
        return {"qty": 0, "reason": "Invalid price/stop_loss"}

    max_risk_pct = settings.TRADE_RISK_PCT / 100  # 1.5% default
    max_position_pct = settings.MAX_POSITION_PCT / 100  # 15% default
    max_risk_dollars = total_equity * max_risk_pct
    max_position_value = total_equity * max_position_pct

    # Risk per share = distance from entry to stop loss
    risk_per_share = price - stop_loss

    if risk_per_share <= 0:
        return {"qty": 0, "reason": "Stop loss >= price"}

    # Shares based on risk budget
    shares_by_risk = int(max_risk_dollars / risk_per_share)

    # Shares based on max position size
    shares_by_position = int(max_position_value / price)

    # Shares based on available cash
    shares_by_cash = int(available_cash / price)

    # Take the minimum (most conservative)
    qty = max(1, min(shares_by_risk, shares_by_position, shares_by_cash))

    # Final check: can we even afford 1 share?
    if price > available_cash:
        return {"qty": 0, "reason": f"Price ${price:.2f} > available cash ${available_cash:.2f}"}

    position_value = qty * price
    risk_amount = qty * risk_per_share
    risk_pct = (risk_amount / total_equity) * 100 if total_equity > 0 else 0

    return {
        "qty": qty,
        "position_value": round(position_value, 2),
        "risk_amount": round(risk_amount, 2),
        "risk_pct": round(risk_pct, 2),
        "risk_per_share": round(risk_per_share, 2),
        "max_risk_budget": round(max_risk_dollars, 2),
    }


# ---------------------------------------------------------------------------
# Trade Eligibility — all pre-trade checks in one place
# ---------------------------------------------------------------------------

def check_trade_eligibility(
    symbol: str,
    side: str,  # "buy" or "sell"
    price: float,
    stop_loss: float,
    account: dict,
    open_positions: list[dict],
) -> dict:
    """Run all pre-trade risk checks. Returns {eligible: bool, reason: str, sizing: dict}.

    Checks:
    1. Trading not blocked
    2. Max open positions not exceeded
    3. Not already holding this symbol
    4. Daily loss limit not breached
    5. Sufficient buying power
    6. Position sizing is valid
    """
    result = {"eligible": False, "reason": "", "sizing": {}}

    # 0. Market hours check
    if not is_market_open():
        result["reason"] = "Market is closed — trades only execute during NYSE hours (9:30 AM - 4:00 PM ET, Mon-Fri)"
        return result

    # 1. Account status
    if account.get("trading_blocked") or account.get("account_blocked"):
        result["reason"] = "Account is blocked"
        return result

    # 2. Max open positions
    max_positions = settings.MAX_OPEN_POSITIONS
    current_positions = len(open_positions)
    if side == "buy" and current_positions >= max_positions:
        result["reason"] = f"Max positions reached ({current_positions}/{max_positions})"
        return result

    # 3. Already holding this symbol (avoid doubling down)
    held_symbols = [p["symbol"] for p in open_positions]
    if side == "buy" and symbol in held_symbols:
        result["reason"] = f"Already holding {symbol}"
        return result

    # 4. Daily loss limit
    equity = account.get("equity", 0)
    last_equity = account.get("last_equity", 0)
    if last_equity > 0:
        daily_pnl_pct = ((equity - last_equity) / last_equity) * 100
        if daily_pnl_pct <= -settings.DAILY_LOSS_LIMIT_PCT:
            result["reason"] = f"Daily loss limit hit ({daily_pnl_pct:.1f}% vs -{settings.DAILY_LOSS_LIMIT_PCT}%)"
            return result

    # 5. Calculate position size
    available_cash = account.get("cash", 0)
    total_equity = account.get("equity", 0)

    sizing = calculate_position_size(price, stop_loss, available_cash, total_equity)
    if sizing.get("qty", 0) == 0:
        result["reason"] = sizing.get("reason", "Position sizing returned 0 shares")
        return result

    # 6. Minimum trade value ($50)
    if sizing["position_value"] < 50:
        result["reason"] = f"Position too small (${sizing['position_value']:.2f} < $50 minimum)"
        return result

    result["eligible"] = True
    result["reason"] = "All checks passed"
    result["sizing"] = sizing
    return result


def get_risk_status(account: dict, positions: list[dict]) -> dict:
    """Get current risk dashboard status."""
    equity = account.get("equity", 0)
    last_equity = account.get("last_equity", 0)
    daily_pnl = equity - last_equity if last_equity > 0 else 0
    daily_pnl_pct = (daily_pnl / last_equity * 100) if last_equity > 0 else 0

    total_exposure = sum(abs(p.get("market_value", 0)) for p in positions)
    exposure_pct = (total_exposure / equity * 100) if equity > 0 else 0

    return {
        "equity": round(equity, 2),
        "cash": round(account.get("cash", 0), 2),
        "daily_pnl": round(daily_pnl, 2),
        "daily_pnl_pct": round(daily_pnl_pct, 2),
        "daily_loss_limit": f"-{settings.DAILY_LOSS_LIMIT_PCT}%",
        "open_positions": len(positions),
        "max_positions": settings.MAX_OPEN_POSITIONS,
        "total_exposure": round(total_exposure, 2),
        "exposure_pct": round(exposure_pct, 2),
        "day_trades_used": get_day_trade_count(),
        "day_trades_max": MAX_DAY_TRADES,
        "pdt_safe": can_day_trade(),
        "trading_blocked": account.get("trading_blocked", False),
        "market_open": is_market_open(),
    }
