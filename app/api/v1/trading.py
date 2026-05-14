from __future__ import annotations

import asyncio
import hashlib
import json
import logging

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
    from app.db.models import ModelResultsCache
    db = SessionLocal()
    try:
        row = db.query(ModelResultsCache).filter(ModelResultsCache.cache_key == key).first()
        if row:
            return row.result_json
    except Exception:
        pass
    finally:
        db.close()
    return None


def _save_backtest_cache(key: str, value: dict):
    from app.db.database import SessionLocal
    from app.db.models import ModelResultsCache
    db = SessionLocal()
    try:
        entry = ModelResultsCache(cache_key=key, result_json=value, model_type="backtest")
        db.add(entry)
        db.commit()
    except Exception:
        pass
    finally:
        db.close()


@router.get("/trading/summary")
async def v1_trading_summary():
    from app.config import STRATEGY_CONFIGS
    from app.services.broker.factory import get_broker

    sid = next(iter(STRATEGY_CONFIGS), None)
    broker = get_broker(strategy_id=sid)

    account = broker.get_account(strategy_id=sid) if broker else None
    positions = broker.get_positions(strategy_id=sid) if broker else []

    if account:
        equity = float(account.get("equity", 0))
        cash = float(account.get("cash", 0))
        buying_power = float(account.get("buying_power", 0))
        portfolio_value = float(account.get("portfolio_value", 0))
        last_equity = float(account.get("last_equity", 0))
        daily_pnl = round(equity - last_equity, 2) if last_equity else 0
        daily_pnl_pct = round((daily_pnl / last_equity * 100), 2) if last_equity > 0 else 0
    else:
        equity = cash = buying_power = portfolio_value = daily_pnl = daily_pnl_pct = 0

    return {
        "broker_type": broker.name if broker else "unknown",
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


@router.post("/trading/orders")
async def v1_submit_order(
    symbol: str,
    side: str = "buy",
    qty: float = Query(..., description="Number of shares"),
    order_type: str = "market",
    time_in_force: str = "DAY",
    limit_price: float | None = None,
    stop_price: float | None = None,
    take_profit_price: float | None = None,
    stop_loss_price: float | None = None,
    x_api_key: str | None = Query(None),
):
    from app.services.broker.factory import get_broker

    payload: dict = {
        "symbol": symbol.upper(),
        "side": side.lower(),
        "qty": qty,
        "type": order_type.lower(),
        "time_in_force": time_in_force.upper(),
    }
    if limit_price is not None:
        payload["limit_price"] = limit_price
    if stop_price is not None:
        payload["stop_price"] = stop_price

    if take_profit_price is not None or stop_loss_price is not None:
        payload["order_class"] = "bracket"
        payload["take_profit"] = {"limit_price": take_profit_price or 0}
        payload["stop_loss"] = {"stop_price": stop_loss_price or 0}

    broker = get_broker()
    if broker is None:
        return {"status": "error", "message": "No broker configured"}
    result = broker.submit_order(payload)
    if result is None:
        return {"status": "error", "message": "Order submission failed"}
    return {"status": "ok", "order": result}


@router.delete("/trading/orders/{order_id}")
async def v1_cancel_order(order_id: str, x_api_key: str | None = Query(None)):
    from app.services.broker.factory import get_broker

    broker = get_broker()
    if broker is None:
        return {"status": "error", "message": "No broker configured"}
    ok = broker.cancel_order(order_id)
    return {"status": "ok" if ok else "error", "cancelled": ok}


@router.delete("/trading/positions/{symbol}")
async def v1_close_position(symbol: str, x_api_key: str | None = Query(None)):
    from app.services.broker.factory import get_broker

    broker = get_broker()
    if broker is None:
        return {"status": "error", "message": "No broker configured"}
    result = broker.close_position(symbol.upper())
    if result is None:
        return {"status": "error", "message": "Close position failed or position not found"}
    return {"status": "ok", "order": result}


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
    from halal_screener import run_backtest

    s = validate_symbol(symbol)
    validate_date(start_date)
    validate_date(end_date)
    validate_range(portfolio, "portfolio", 1000, 10_000_000)
    validate_range(risk_pct, "risk_pct", 0.1, 10.0)
    validate_range(hold_days, "hold_days", 1, 60)

    raw = f"backtest:{s}:{start_date}:{end_date}:{portfolio}:{risk_pct}:{hold_days}"
    key = hashlib.md5(raw.encode()).hexdigest()

    cached = await asyncio.to_thread(_fetch_backtest_cache, key)
    if cached and isinstance(cached, list):
        return cached

    result = run_backtest(s, start_date, end_date, portfolio, risk_pct, hold_days)

    await asyncio.to_thread(_save_backtest_cache, key, result)
    return result
