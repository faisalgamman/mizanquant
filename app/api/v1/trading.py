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
    import os
    from app.services.broker.factory import get_broker, _build

    sid = os.environ.get("DASHBOARD_STRATEGY", "MANUAL")
    broker = get_broker(strategy_id=sid)

    account = None
    positions = []
    served_by = broker.name if broker else "unknown"

    # Run broker calls in thread with 10s timeout to avoid event-loop blockage
    def _fetch_sync():
        acc = broker.get_account(strategy_id=sid) if broker else None
        pos = broker.get_positions(strategy_id=sid) if broker else []
        return acc, pos

    try:
        account, positions = await asyncio.wait_for(
            asyncio.to_thread(_fetch_sync),
            timeout=10.0,
        )
    except (asyncio.TimeoutError, Exception):
        account = None
        positions = []

    # Resilience fallback — if the primary broker (e.g. IBKR with a downed
    # gateway) returns no account, fall back to Alpaca so the dashboard
    # still shows live equity/positions instead of blank "—".
    if account is None and served_by != "alpaca":
        try:
            def _alpaca_fetch():
                alpaca = _build("alpaca")
                fb_account = alpaca.get_account(strategy_id=sid)
                fb_positions = alpaca.get_positions(strategy_id=sid) or []
                return fb_account, fb_positions

            fb_account, fb_positions = await asyncio.wait_for(
                asyncio.to_thread(_alpaca_fetch),
                timeout=8.0,
            )
            if fb_account:
                logger.warning(
                    "trading/summary: %s broker unavailable — served via Alpaca fallback",
                    served_by,
                )
                account = fb_account
                positions = fb_positions
                served_by = "alpaca (fallback)"
        except (asyncio.TimeoutError, Exception) as exc:
            logger.error("trading/summary Alpaca fallback failed: %s", exc)

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
        "broker_type": served_by,
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


# ---------------------------------------------------------------------------
# Phase 2 — Execution intelligence endpoints
# ---------------------------------------------------------------------------

@router.get("/execution/estimate")
async def v1_execution_estimate(
    symbol: str = "AAPL",
    side: str = "buy",
    qty: int = 100,
    price: float = 0.0,
):
    """Pre-trade execution cost estimate via ExecutionSimulator (Almgren-Chriss).

    Returns estimated impact, spread, slippage, and total cost in bps/USD.
    Also indicates whether the trade would be blocked by the liquidity gate
    (MAX_EXECUTION_IMPACT_BPS threshold).
    """
    symbol = validate_symbol(symbol)
    try:
        from app.services.execution_cost_estimator import estimate_execution_cost
        if price <= 0:
            # Fetch latest price from market data when not supplied
            from app.services.market_data import fetch as fetch_market_data
            df = fetch_market_data(symbol, period="5d")
            if df is not None and not df.empty:
                col_map = {c.lower(): c for c in df.columns}
                close_col = col_map.get("close") or col_map.get("adjclose")
                if close_col:
                    price = float(df[close_col].dropna().iloc[-1])
        if price <= 0:
            return {"error": "Cannot determine price — supply price= param"}
        estimate = estimate_execution_cost(symbol, side, qty, price)
        return {
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "reference_price": price,
            **estimate,
        }
    except Exception as exc:
        logger.exception("execution/estimate failed for %s", symbol)
        return {"error": str(exc)}


@router.get("/execution/tca")
async def v1_execution_tca(
    symbol: str | None = None,
    strategy_id: str | None = None,
    limit: int = 100,
):
    """Aggregate Transaction Cost Analysis from live trade history.

    Groups realized slippage vs pre-trade model estimates by symbol
    and/or strategy.  Only trades that have a ``signal_details["tca"]``
    block (i.e. fills processed after Phase 2 deployment) are included.
    """
    try:
        from app.db.database import SessionLocal
        from app.db.models import TradeHistory
        import json as _json

        db = SessionLocal()
        rows: list[dict] = []
        try:
            q = db.query(TradeHistory).filter(TradeHistory.side == "buy")
            if symbol:
                q = q.filter(TradeHistory.symbol == symbol.upper())
            if strategy_id:
                q = q.filter(TradeHistory.strategy_id == strategy_id)
            q = q.order_by(TradeHistory.created_at.desc()).limit(limit)
            trades = q.all()
        finally:
            db.close()

        for t in trades:
            raw = getattr(t, "signal_details", None) or {}
            if isinstance(raw, str):
                try:
                    raw = _json.loads(raw)
                except Exception:
                    raw = {}
            tca = raw.get("tca")
            if not tca:
                continue
            rows.append({
                "symbol": t.symbol,
                "strategy_id": t.strategy_id,
                "order_id": t.order_id,
                "created_at": t.created_at.isoformat() if t.created_at else None,
                "decision_price": tca.get("decision_price"),
                "filled_avg_price": tca.get("filled_avg_price"),
                "realized_slippage_bps": tca.get("realized_slippage_bps"),
                "modeled_bps": tca.get("modeled_bps"),
                "slippage_vs_model_bps": tca.get("slippage_vs_model_bps"),
            })

        if not rows:
            return {"count": 0, "trades": [], "summary": {}}

        # Summary statistics
        realized = [r["realized_slippage_bps"] for r in rows if r["realized_slippage_bps"] is not None]
        vs_model = [r["slippage_vs_model_bps"] for r in rows if r["slippage_vs_model_bps"] is not None]

        def _avg(lst):
            return round(sum(lst) / len(lst), 2) if lst else None

        return {
            "count": len(rows),
            "trades": rows,
            "summary": {
                "avg_realized_slippage_bps": _avg(realized),
                "avg_slippage_vs_model_bps": _avg(vs_model),
                "max_realized_slippage_bps": round(max(realized), 2) if realized else None,
            },
        }
    except Exception as exc:
        logger.exception("execution/tca query failed")
        return {"error": str(exc)}


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
    from app.services.backtest_service import run_backtest

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
    # Persist to backtest_runs (analytics layer) — fire-and-forget, never blocks the response.
    await asyncio.to_thread(_persist_backtest, s, start_date, end_date, risk_pct, hold_days, result)
    return result


def _persist_backtest(symbol, start_date, end_date, risk_pct, hold_days, result):
    """Map a run_backtest() result into the backtest_runs table via record_backtest().

    Reuses app/services/backtest_recorder.py so every API backtest is queryable
    by the analytics layer with a reproducibility seal. Silent on any failure.
    """
    try:
        if not result or not isinstance(result, list):
            return
        s0 = result[0]
        if not isinstance(s0, dict) or "Error" in s0 or "Message" in s0:
            return
        summary = {
            "sharpe":            s0.get("Sharpe Ratio"),
            "sortino":           s0.get("Sortino Ratio"),
            "max_drawdown":      s0.get("Max Drawdown %"),
            "calmar":            s0.get("Calmar Ratio"),
            "annualized_return": s0.get("Return %"),  # total return % over the window
            "win_rate":          s0.get("Win Rate %"),
            "profit_factor":     s0.get("Profit Factor"),
            "total_trades":      s0.get("Total Trades"),
            "test_start":        start_date,
            "test_end":          end_date,
        }
        qc = {
            "deflated_sharpe":    s0.get("Deflated Sharpe"),
            "permutation_pvalue": s0.get("Permutation p-value"),
        }
        from app.services.backtest_recorder import record_backtest
        record_backtest(
            strategy_name="manual_swing",
            symbols=[symbol],
            summary=summary,
            qc=qc,
            profile="api",
            extra={"risk_pct": risk_pct, "hold_days": hold_days,
                   "return_pct_is_total": True, "period": s0.get("Period")},
        )
    except Exception as exc:
        logger.debug("backtest persist failed for %s: %s", symbol, exc)


@router.get("/backtest/history")
async def v1_backtest_history(strategy: str = "", symbol: str = "", limit: int = 50):
    """Recent persisted backtest runs from the backtest_runs table (analytics layer).

    Optional filters: ?strategy=manual_swing  ?symbol=AAPL  ?limit=50
    """
    def _query():
        from app.db.database import SessionLocal
        from app.db.models import BacktestRun
        db = SessionLocal()
        try:
            q = db.query(BacktestRun).order_by(BacktestRun.created_at.desc())
            if strategy:
                q = q.filter(BacktestRun.strategy_name == strategy)
            rows = q.limit(min(max(limit, 1), 200)).all()
            out = []
            for r in rows:
                syms = r.symbols if isinstance(r.symbols, list) else []
                if symbol and symbol.upper() not in [str(x).upper() for x in syms]:
                    continue
                out.append({
                    "id":            r.id,
                    "created_at":    r.created_at.isoformat() if r.created_at else None,
                    "strategy_name": r.strategy_name,
                    "symbols":       syms,
                    "profile":       r.profile,
                    "sharpe":        r.sharpe,
                    "sortino":       r.sortino,
                    "max_drawdown_pct": r.max_drawdown_pct,
                    "calmar":        r.calmar,
                    "return_pct":    r.annualized_return_pct,
                    "win_rate":      r.win_rate,
                    "profit_factor": r.profit_factor,
                    "total_trades":  r.total_trades,
                    "deflated_sharpe":    r.deflated_sharpe,
                    "permutation_pvalue": r.permutation_pvalue,
                    "git_sha":       r.git_sha,
                    "is_dirty":      r.is_dirty,
                })
            return out
        finally:
            db.close()
    try:
        rows = await asyncio.to_thread(_query)
        return {"count": len(rows), "runs": rows}
    except Exception as exc:
        logger.debug("backtest history failed: %s", exc)
        return {"count": 0, "runs": [], "error": str(exc)}


# ---------------------------------------------------------------------------
# Phase 3 — Halal pairs trading endpoints
# ---------------------------------------------------------------------------

@router.get("/pairs/candidates")
async def v1_pairs_candidates(force_refresh: bool = False, max_pairs: int = 20):
    """Currently cointegrated pairs (within-sector) from the scanner.

    Returns p-value, hedge ratio, OU half-life, and live z-score per pair.
    """
    try:
        from app.services.pairs_scanner import find_cointegrated_pairs

        def _scan():
            pairs = find_cointegrated_pairs(max_pairs=max_pairs, force_refresh=force_refresh)
            return [p.as_dict() for p in pairs]

        pairs = await asyncio.to_thread(_scan)
        return {"count": len(pairs), "pairs": pairs}
    except Exception as exc:
        logger.exception("pairs/candidates failed")
        return {"error": str(exc)}


@router.get("/pairs/signals")
async def v1_pairs_signals():
    """Current long-only pairs signals (entry/exit/hold) — computed, not executed.

    Reflects the relative-value rule: at a spread extreme, buy the relatively
    undervalued leg; exit when the spread reverts. No short side is ever
    emitted (halal long-only constraint).
    """
    try:
        from app.services import pairs_strategy as ps

        def _signals():
            sigs = ps.compute_signals()
            return [s.as_dict() for s in sigs]

        signals = await asyncio.to_thread(_signals)
        actionable = [s for s in signals if s["action"] in ("long_y", "long_x", "exit")]
        return {
            "enabled": ps.PAIRS_TRADING_ENABLED,
            "entry_z": ps.PAIRS_ENTRY_Z,
            "exit_z": ps.PAIRS_EXIT_Z,
            "count": len(signals),
            "actionable": len(actionable),
            "signals": signals,
        }
    except Exception as exc:
        logger.exception("pairs/signals failed")
        return {"error": str(exc)}
