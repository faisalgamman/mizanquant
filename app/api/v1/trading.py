from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os

from fastapi import APIRouter, Query

from app.config import settings
from app.core.config import app_cfg
from app.api.deps import require_api_key
from app.utils.validation import validate_symbol, validate_date, validate_range
from app.services.redis_client import get_redis

logger = logging.getLogger("screener")

router = APIRouter(tags=["v1-trading"])


def _fetch_backtest_cache(key: str):
    from app.db.database import SessionLocal
    from app.db.models import CacheEntry
    db = SessionLocal()
    try:
        row = db.query(CacheEntry).filter(CacheEntry.cache_key == key).first()
        if row:
            return json.loads(row.value)
    except Exception:
        pass
    finally:
        db.close()
    return None


def _save_backtest_cache(key: str, value: dict):
    from app.db.database import SessionLocal
    from app.db.models import CacheEntry
    import json
    db = SessionLocal()
    try:
        entry = CacheEntry(cache_key=key, value=json.dumps(value))
        db.add(entry)
        db.commit()
    except Exception:
        pass
    finally:
        db.close()


@router.get("/trading/summary")
async def v1_trading_summary():
    from app.config import STRATEGY_CONFIGS

    broker_type = os.environ.get("BROKER_TYPE", "alpaca").lower()
    sid = next(iter(STRATEGY_CONFIGS), None)

    if broker_type == "ibkr":
        return {
            "broker_type": "ibkr",
            "equity": 0, "cash": 0, "buying_power": 0, "portfolio_value": 0,
            "daily_pnl": 0, "daily_pnl_pct": 0,
            "open_positions": 0,
            "auto_trade_enabled": settings.AUTO_TRADE_ENABLED,
            "positions": [],
            "message": "IBKR account details not yet available via REST API. Use IB Gateway desktop to view positions.",
        }

    from app.services.alpaca_client import get_account, get_positions

    account = get_account(strategy_id=sid) if sid else None
    positions = get_positions(strategy_id=sid) if sid else []

    if account:
        equity = float(account.get("equity", 0))
        cash = float(account.get("cash", 0))
        buying_power = float(account.get("buying_power", 0))
        portfolio_value = float(account.get("portfolio_value", 0))
        last_equity = float(account.get("last_equity", 0))
        daily_pnl = round(equity - last_equity, 2)
        daily_pnl_pct = round((daily_pnl / last_equity * 100), 2) if last_equity > 0 else 0
    else:
        equity = cash = buying_power = portfolio_value = daily_pnl = daily_pnl_pct = 0

    return {
        "broker_type": "alpaca",
        "equity": equity,
        "cash": cash,
        "buying_power": buying_power,
        "portfolio_value": portfolio_value,
        "daily_pnl": daily_pnl,
        "daily_pnl_pct": daily_pnl_pct,
        "open_positions": len(positions),
        "auto_trade_enabled": settings.AUTO_TRADE_ENABLED,
        "positions": [
            {
                "symbol": p["symbol"], "qty": float(p.get("qty", 0)),
                "avg_entry": float(p.get("avg_entry_price", 0)),
                "current_price": float(p.get("current_price", 0)),
                "market_value": float(p.get("market_value", 0)),
                "unrealized_pl": float(p.get("unrealized_pl", 0)),
                "unrealized_plpc": float(p.get("unrealized_plpc", 0)),
            }
            for p in (positions or [])
        ],
    }


@router.get("/trading/controls")
async def v1_trading_controls():
    return {
        "auto_trade_enabled": settings.AUTO_TRADE_ENABLED,
        "kill_switch": app_cfg.killed,
        "min_confidence": settings.MIN_TRADE_CONFIDENCE,
        "trade_risk_pct": settings.TRADE_RISK_PCT,
        "max_position_pct": settings.MAX_POSITION_PCT,
        "max_open_positions": settings.MAX_OPEN_POSITIONS,
        "daily_loss_limit_pct": settings.DAILY_LOSS_LIMIT_PCT,
        "risk_capital": settings.RISK_CAPITAL,
        "live_whitelist": settings.live_whitelist,
        "live_whitelist_raw": settings.LIVE_WHITELIST,
    }


@router.post("/trading/controls/kill-switch")
async def v1_kill_switch(killed: bool = True, x_api_key: str = Query(None)):
    require_api_key(x_api_key)
    app_cfg.killed = killed
    from app.services.notify import send_message as tg_send
    tg_send(f"🔴 KILL SWITCH {'ENABLED' if killed else 'DISABLED'} from Dashboard")
    return {"killed": app_cfg.killed}


@router.post("/trading/controls/auto-trade")
async def v1_auto_trade(enabled: bool = True, x_api_key: str = Query(None)):
    require_api_key(x_api_key)
    settings.AUTO_TRADE_ENABLED = enabled
    from app.services.notify import send_message as tg_send
    tg_send(f"{'🟢' if enabled else '🔴'} AUTO-TRADING {'ENABLED' if enabled else 'DISABLED'} from Dashboard")
    return {"auto_trade_enabled": settings.AUTO_TRADE_ENABLED}


@router.get("/trading/history/recent")
async def v1_trading_history(limit: int = 20):
    from app.services.trade_history import get_trade_history
    return get_trade_history(limit=limit)


@router.get("/halal/check")
async def v1_halal_check(symbol: str = "AAPL"):
    from app.services.halal_screening import verify_halal

    symbol = validate_symbol(symbol)
    is_halal, reason = verify_halal(symbol)
    return {"symbol": symbol, "halal": is_halal, "details": {"reason": reason}}


@router.get("/halal/universe")
async def v1_halal_universe():
    redis = await get_redis()
    cache_key = "halal:universe"

    if redis is not None:
        try:
            cached = await redis.get(cache_key)
            if cached is not None:
                return json.loads(cached)
        except Exception as exc:
            logger.debug("Redis read error for %s: %s", cache_key, exc)

    from app.db.database import SessionLocal as SyncSessionLocal
    from app.services.universe import get_universe_symbols

    def _fetch():
        db = SyncSessionLocal()
        try:
            symbols = sorted(get_universe_symbols(db))
            return symbols
        finally:
            db.close()

    symbols = await asyncio.to_thread(_fetch)
    data = {"count": len(symbols), "symbols": symbols[:100], "total": len(symbols)}

    if redis is not None:
        try:
            await redis.setex(cache_key, 86400, json.dumps(data, default=str))
        except Exception as exc:
            logger.debug("Redis write error for %s: %s", cache_key, exc)

    return data


@router.get("/backtest")
async def v1_backtest(
    symbol: str = "AAPL",
    start_date: str = "2022-01-01",
    end_date: str = "2024-12-31",
    portfolio: float = 100000,
    risk_pct: float = 1.0,
    hold_days: int = 3,
):
    from app.services.backtest_qc import run_backtest

    s = validate_symbol(symbol)
    validate_date(start_date)
    validate_date(end_date)
    validate_range(portfolio, "portfolio", 1000, 10_000_000)
    validate_range(risk_pct, "risk_pct", 0.1, 10.0)
    validate_range(hold_days, "hold_days", 1, 60)

    raw = f"backtest:{s}:{start_date}:{end_date}:{portfolio}:{risk_pct}:{hold_days}"
    key = hashlib.md5(raw.encode()).hexdigest()

    cached = await asyncio.to_thread(_fetch_backtest_cache, key)
    if cached:
        return cached

    result = run_backtest(s, start_date, end_date, portfolio, risk_pct, hold_days)
    result_dict = result if isinstance(result, dict) else {"result": str(result)}

    await asyncio.to_thread(_save_backtest_cache, key, result_dict)
    return result_dict
