"""Portfolio, Strategy, Trading, Signals, Telegram endpoints"""
import os
from fastapi import APIRouter
from halal_screener import OperatorAPIKey

router = APIRouter(tags=["Portfolio"])


def _broker_type() -> str:
    return os.environ.get("BROKER_TYPE", "alpaca").lower()


@router.get("/strategies")
async def list_strategies(x_api_key: OperatorAPIKey = None):
    from halal_screener import _require_api_key
    _require_api_key(x_api_key)
    from app.config import STRATEGY_CONFIGS as CFG
    bt = _broker_type()
    result = []
    for sid, cfg in CFG.items():
        result.append({
            "id": cfg.strategy_id, "name": cfg.name,
            "max_positions": cfg.max_positions, "position_pct": cfg.position_pct,
            "trailing_stop": cfg.trailing_stop_enabled, "trailing_stop_pct": cfg.trailing_stop_pct,
            "static_sl_pct": cfg.static_sl_pct, "min_confidence": cfg.min_confidence,
            "configured": bool(cfg.alpaca_api_key) if bt == "alpaca" else True,
            "broker_type": bt,
        })
    if not result:
        hint = "Set ALPACA_API_KEY_A/B/C env vars." if bt == "alpaca" else "Configure IBKR_HOST/IBKR_PORT in env."
        return [{"message": f"No strategies configured. {hint}"}]
    return result


@router.get("/strategy/{strategy_id}/account")
async def strategy_account(strategy_id: str, x_api_key: OperatorAPIKey = None):
    from halal_screener import _require_api_key
    from app.config import STRATEGY_CONFIGS
    _require_api_key(x_api_key)
    sid = strategy_id.upper()
    if sid not in STRATEGY_CONFIGS:
        return [{"Error": f"Strategy {sid} not configured"}]
    bt = _broker_type()
    if bt == "ibkr":
        return [{"strategy": f"{sid}: {STRATEGY_CONFIGS[sid].name}", "broker_type": "ibkr",
                 "message": "IBKR account details available via IB Gateway desktop only."}]
    from halal_screener import alpaca_get_account
    account = alpaca_get_account(strategy_id=sid)
    if not account:
        return [{"Error": f"Cannot fetch account for strategy {sid}"}]
    account["strategy"] = f"{sid}: {STRATEGY_CONFIGS[sid].name}"
    return [account]


@router.get("/debug/alpaca")
async def debug_alpaca(x_api_key: OperatorAPIKey = None):
    import httpx
    from halal_screener import _require_api_key, settings, STRATEGY_CONFIGS, NON_FATAL_ANALYSIS_ERROR
    _require_api_key(x_api_key)
    from app.config import STRATEGY_CONFIGS as CFG
    results = []
    for sid, cfg in CFG.items():
        key_prefix = cfg.alpaca_api_key[:8] + "..." if cfg.alpaca_api_key else "EMPTY"
        secret_prefix = cfg.alpaca_secret_key[:4] + "..." if cfg.alpaca_secret_key else "EMPTY"
        base_url = settings.ALPACA_BASE_URL
        test_url = f"{base_url.rstrip('/').removesuffix('/v2')}/v2/account"
        try:
            with httpx.Client(timeout=10) as client:
                resp = client.get(test_url, headers={
                    "APCA-API-KEY-ID": cfg.alpaca_api_key,
                    "APCA-API-SECRET-KEY": cfg.alpaca_secret_key,
                })
                status = resp.status_code
                body = resp.text[:200]
        except NON_FATAL_ANALYSIS_ERROR as e:
            status = "ERROR"
            body = str(e)[:200]
        results.append({
            "strategy": f"{sid}: {cfg.name}", "key_prefix": key_prefix,
            "secret_prefix": secret_prefix, "base_url": base_url,
            "test_url": test_url, "http_status": status, "response": body,
        })
    return results


@router.get("/strategy/{strategy_id}/positions")
async def strategy_positions(strategy_id: str, x_api_key: OperatorAPIKey = None):
    from halal_screener import _require_api_key
    from app.config import STRATEGY_CONFIGS
    _require_api_key(x_api_key)
    sid = strategy_id.upper()
    if sid not in STRATEGY_CONFIGS:
        return [{"Error": f"Strategy {sid} not configured"}]
    bt = _broker_type()
    if bt == "ibkr":
        return [{"strategy": f"{sid}: {STRATEGY_CONFIGS[sid].name}", "broker_type": "ibkr",
                 "message": "IBKR positions available via IB Gateway desktop only."}]
    from halal_screener import alpaca_get_positions
    positions = alpaca_get_positions(strategy_id=sid)
    if not positions:
        return [{"message": f"No open positions for strategy {sid}: {STRATEGY_CONFIGS[sid].name}"}]
    for p in positions:
        p["strategy"] = f"{sid}: {STRATEGY_CONFIGS[sid].name}"
    return positions


@router.get("/strategy/{strategy_id}/scan")
async def strategy_scan(strategy_id: str, symbol: str = "AAPL", x_api_key: OperatorAPIKey = None):
    from halal_screener import _require_api_key, run_consensus_momentum, run_consensus_reversion, run_consensus_ml
    _require_api_key(x_api_key)
    sid = strategy_id.upper()
    if sid == "A":
        return run_consensus_momentum(symbol)
    elif sid == "B":
        return run_consensus_reversion(symbol)
    elif sid == "C":
        return run_consensus_ml(symbol)
    return [{"Error": f"Unknown strategy {sid}. Use A, B, or C."}]


@router.get("/strategies/comparison")
async def strategy_comparison(x_api_key: OperatorAPIKey = None):
    from halal_screener import _require_api_key
    from app.config import STRATEGY_CONFIGS
    _require_api_key(x_api_key)
    bt = _broker_type()
    if bt == "ibkr":
        return [{"broker_type": "ibkr", "message": "Strategy comparison not available for IBKR. Use IB Gateway desktop."}]
    from halal_screener import alpaca_get_account, alpaca_get_positions
    result = []
    total_equity = 0
    total_pnl = 0
    for sid in ("A", "B", "C"):
        cfg = STRATEGY_CONFIGS.get(sid)
        if not cfg:
            continue
        account = alpaca_get_account(strategy_id=sid)
        positions = alpaca_get_positions(strategy_id=sid)
        if account:
            equity = account.get("equity", 0)
            last_eq = account.get("last_equity", 0)
            pnl = equity - last_eq if last_eq > 0 else 0
            total_equity += equity
            total_pnl += pnl
            result.append({
                "strategy_id": sid, "name": cfg.name, "equity": round(equity, 2),
                "pnl": round(pnl, 2), "pnl_pct": round(pnl / last_eq * 100, 2) if last_eq > 0 else 0,
                "positions": len(positions), "max_positions": cfg.max_positions,
                "position_symbols": [p["symbol"] for p in positions],
            })
    if result:
        result.append({"total_equity": round(total_equity, 2), "total_pnl": round(total_pnl, 2)})
    return result if result else [{"message": "No strategies configured"}]


@router.get("/portfolio/summary")
async def portfolio_summary(x_api_key: OperatorAPIKey = None):
    from halal_screener import _require_api_key
    _require_api_key(x_api_key)
    bt = _broker_type()
    if bt == "ibkr":
        return [{"broker_type": "ibkr", "message": "Portfolio summary available via IB Gateway desktop only."}]
    from halal_screener import _has_alpaca_broker_config, _primary_broker_strategy_id, alpaca_get_account
    if not _has_alpaca_broker_config():
        return [{"Error": "Alpaca API keys not configured"}]
    sid = _primary_broker_strategy_id()
    account = alpaca_get_account(strategy_id=sid)
    if not account:
        return [{"Error": "Could not connect to Alpaca"}]
    daily_pl = account["equity"] - account["last_equity"]
    daily_pl_pct = (daily_pl / account["last_equity"] * 100) if account["last_equity"] > 0 else 0
    return [{
        "Equity": f"${account['equity']:,.2f}", "Cash": f"${account['cash']:,.2f}",
        "Buying Power": f"${account['buying_power']:,.2f}",
        "Portfolio Value": f"${account['portfolio_value']:,.2f}",
        "Long Mkt Value": f"${account['long_market_value']:,.2f}",
        "Daily P&L": f"${daily_pl:+,.2f}", "Daily P&L %": f"{daily_pl_pct:+.2f}%",
        "Day Trades": account["daytrade_count"], "Status": account["status"],
        "Currency": account["currency"],
    }]


@router.get("/portfolio/positions")
async def portfolio_positions(x_api_key: OperatorAPIKey = None):
    from halal_screener import _require_api_key
    _require_api_key(x_api_key)
    bt = _broker_type()
    if bt == "ibkr":
        return [{"broker_type": "ibkr", "message": "Positions available via IB Gateway desktop only."}]
    from halal_screener import _has_alpaca_broker_config, _primary_broker_strategy_id, alpaca_get_positions
    if not _has_alpaca_broker_config():
        return [{"Error": "Alpaca API keys not configured"}]
    sid = _primary_broker_strategy_id()
    positions = alpaca_get_positions(strategy_id=sid)
    if not positions:
        return [{"Message": "No open positions"}]
    result = []
    for p in positions:
        result.append({
            "Symbol": p["symbol"], "Qty": p["qty"], "Side": p["side"].upper(),
            "Avg Entry": f"${p['avg_entry_price']:.2f}", "Current": f"${p['current_price']:.2f}",
            "Market Value": f"${p['market_value']:,.2f}", "Cost Basis": f"${p['cost_basis']:,.2f}",
            "Unrealized P&L": f"${p['unrealized_pl']:+,.2f}", "P&L %": f"{p['unrealized_plpc']:+.2f}%",
            "Today %": f"{p['change_today']:+.2f}%",
        })
    return result


@router.get("/portfolio/orders")
async def portfolio_orders(status: str = "all", limit: int = 20, x_api_key: OperatorAPIKey = None):
    from halal_screener import _require_api_key
    _require_api_key(x_api_key)
    bt = _broker_type()
    if bt == "ibkr":
        return [{"broker_type": "ibkr", "message": "Orders available via IB Gateway desktop only."}]
    from halal_screener import _has_alpaca_broker_config, _primary_broker_strategy_id, alpaca_get_orders
    if not _has_alpaca_broker_config():
        return [{"Error": "Alpaca API keys not configured"}]
    sid = _primary_broker_strategy_id()
    orders = alpaca_get_orders(status=status, limit=limit, strategy_id=sid)
    if not orders:
        return [{"Message": "No orders found"}]
    return orders


@router.get("/portfolio/history")
async def portfolio_history(period: str = "1M", x_api_key: OperatorAPIKey = None):
    from halal_screener import _require_api_key
    _require_api_key(x_api_key)
    bt = _broker_type()
    if bt == "ibkr":
        return [{"broker_type": "ibkr", "message": "Portfolio history available via IB Gateway desktop only."}]
    from halal_screener import _has_alpaca_broker_config, _primary_broker_strategy_id, alpaca_get_portfolio_history
    if not _has_alpaca_broker_config():
        return [{"Error": "Alpaca API keys not configured"}]
    sid = _primary_broker_strategy_id()
    valid_periods = ["1D", "1W", "1M", "3M", "1A", "all"]
    if period not in valid_periods:
        return [{"Error": f"Invalid period. Use one of: {valid_periods}"}]
    data = alpaca_get_portfolio_history(period=period, strategy_id=sid)
    if not data:
        return [{"Error": "Could not fetch portfolio history"}]
    summary = [{"Period": period, "Base Value": f"${data['base_value']:,.2f}" if data["base_value"] else "N/A",
                "Data Points": len(data["history"])}]
    return summary + data["history"]


@router.get("/signals/accuracy")
async def signals_accuracy(period: int = 30):
    from halal_screener import validate_range, check_signal_outcomes, get_accuracy_report, logger, NON_FATAL_ANALYSIS_ERROR
    import threading
    validate_range(period, "period", 1, 365)
    try:
        threading.Thread(target=check_signal_outcomes, args=(5,), daemon=True).start()
    except NON_FATAL_ANALYSIS_ERROR as e:
        logger.error(f"check_signal_outcomes thread failed: {e}")
    return get_accuracy_report(period_days=period)


@router.get("/signals/history")
async def signals_history(symbol: str = "", limit: int = 50):
    from halal_screener import validate_symbol, validate_range, get_signal_history
    s = validate_symbol(symbol) if symbol else None
    validate_range(limit, "limit", 1, 500)
    return get_signal_history(symbol=s, limit=limit)


@router.get("/signals/check_outcomes")
async def signals_check_outcomes(days: int = 5, x_api_key: OperatorAPIKey = None):
    from halal_screener import _require_api_key, validate_range, check_signal_outcomes
    _require_api_key(x_api_key)
    validate_range(days, "days", 1, 60)
    result = check_signal_outcomes(lookback_days=days)
    return [{"Action": "Outcome check complete", **result}]


@router.get("/telegram/test")
async def telegram_test(x_api_key: OperatorAPIKey = None):
    from halal_screener import _require_api_key, settings, tg_send
    _require_api_key(x_api_key)
    if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHAT_ID:
        return [{"Error": "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID not configured in .env"}]
    ok = tg_send("Test message from Halal Trading Bot\n\nTelegram alerts are working!")
    return [{"Status": "sent" if ok else "failed"}]


@router.get("/telegram/test_chart")
async def telegram_test_chart(symbol: str = "AAPL", x_api_key: OperatorAPIKey = None):
    from halal_screener import _require_api_key, validate_symbol, fetch_market_data, tg_send_photo, generate_signal_chart, NON_FATAL_ANALYSIS_ERROR, logger
    _require_api_key(x_api_key)
    symbol = validate_symbol(symbol)
    result = {"symbol": symbol, "steps": []}
    try:
        df = fetch_market_data(symbol, period="2y")
        if df is None or len(df) < 30:
            result["steps"].append({"fetch_data": "FAILED", "reason": "No data or < 30 bars"})
            return result
        result["steps"].append({"fetch_data": "OK", "bars": len(df)})
    except NON_FATAL_ANALYSIS_ERROR as e:
        result["steps"].append({"fetch_data": "ERROR", "error": str(e)})
        return result
    try:
        price = float(df["close"].iloc[-1])
        chart_bytes = generate_signal_chart(
            df=df, symbol=symbol, verdict="STRONG BUY",
            entry_price=price, stop_loss=round(price * 0.97, 2),
            tp1=round(price * 1.04, 2), tp2=round(price * 1.07, 2),
            tp3=round(price * 1.10, 2), confidence=78.0,
            votes_buy=7, votes_sell=1, votes_hold=1,
        )
        if chart_bytes:
            result["steps"].append({"generate_chart": "OK", "size_kb": round(len(chart_bytes) / 1024, 1)})
        else:
            result["steps"].append({"generate_chart": "FAILED", "reason": "Returned None"})
            return result
    except NON_FATAL_ANALYSIS_ERROR as e:
        import traceback
        result["steps"].append({"generate_chart": "ERROR", "error": str(e), "traceback": traceback.format_exc()[-500:]})
        return result
    try:
        ok = tg_send_photo(chart_bytes, caption=f"Test chart for {symbol} @ ${price:.2f}")
        result["steps"].append({"send_photo": "OK" if ok else "FAILED"})
    except NON_FATAL_ANALYSIS_ERROR as e:
        result["steps"].append({"send_photo": "ERROR", "error": str(e)})
    return result


@router.get("/telegram/daily_summary")
async def telegram_daily_summary(x_api_key: OperatorAPIKey = None):
    from halal_screener import _require_api_key, settings, _get_cached, _cache_key, alert_daily_summary, STRATEGY_CONFIGS
    _require_api_key(x_api_key)
    if not settings.TELEGRAM_BOT_TOKEN:
        return [{"Error": "Telegram not configured"}]
    key = _cache_key("screener")
    cached, _ = _get_cached(key)
    if not cached or not isinstance(cached, list):
        return [{"Error": "Screener data not available yet"}]
    bt = _broker_type()
    portfolio_info = None
    if bt != "ibkr":
        from halal_screener import alpaca_get_account
        _sid = next(iter(STRATEGY_CONFIGS), None)
        portfolio = alpaca_get_account(strategy_id=_sid)
        if portfolio:
            daily_pl = portfolio["equity"] - portfolio["last_equity"]
            portfolio_info = {"equity": portfolio["equity"], "daily_pl": daily_pl}
    ok = alert_daily_summary(cached, portfolio_info)
    return [{"Status": "sent" if ok else "failed"}]


@router.get("/api/v1/trading/status")
async def trading_status(x_api_key: OperatorAPIKey = None):
    from halal_screener import _require_api_key, settings
    _require_api_key(x_api_key)
    bt = _broker_type()
    base = {"auto_trade_enabled": settings.AUTO_TRADE_ENABLED,
            "min_confidence": settings.MIN_TRADE_CONFIDENCE, "trade_risk_pct": settings.TRADE_RISK_PCT,
            "max_position_pct": settings.MAX_POSITION_PCT, "broker_type": bt}
    if bt == "ibkr":
        return {"broker_configured": True, "broker_connected": True,
                "message": "IBKR broker configured. Use IB Gateway for account details.",
                **base}
    from halal_screener import _primary_broker_strategy_id, _has_alpaca_broker_config, alpaca_get_account, alpaca_get_last_error, alpaca_get_positions, get_risk_status
    sid = _primary_broker_strategy_id()
    broker_configured = _has_alpaca_broker_config()
    if not broker_configured:
        return {"error": "Alpaca API keys not configured", "broker_configured": False,
                "broker_connected": False, **base}
    account = alpaca_get_account(strategy_id=sid)
    if not account:
        broker_error = alpaca_get_last_error(strategy_id=sid)
        return {"error": "Cannot connect to Alpaca", "broker_configured": True,
                "broker_connected": False, "broker_reason": broker_error.get("reason") or "unknown",
                "broker_status_code": broker_error.get("status_code"), **base,
                "strategy_id": sid or "default"}
    positions = alpaca_get_positions(strategy_id=sid)
    risk = get_risk_status(account, positions)
    risk["broker_configured"] = True
    risk["broker_connected"] = True
    risk["strategy_id"] = sid or "default"
    risk["broker_type"] = bt
    risk["auto_trade_enabled"] = settings.AUTO_TRADE_ENABLED
    risk["min_confidence"] = settings.MIN_TRADE_CONFIDENCE
    risk["trade_risk_pct"] = settings.TRADE_RISK_PCT
    risk["max_position_pct"] = settings.MAX_POSITION_PCT
    return risk


@router.get("/api/v1/trading/history")
async def trading_history(limit: int = 50, x_api_key: OperatorAPIKey = None):
    from halal_screener import _require_api_key, get_trade_history
    _require_api_key(x_api_key)
    return get_trade_history(limit=limit)


@router.get("/api/v1/trading/performance")
async def trading_performance(x_api_key: OperatorAPIKey = None):
    from halal_screener import _require_api_key, get_performance_report
    _require_api_key(x_api_key)
    return get_performance_report()


@router.post("/api/v1/trading/enable")
async def trading_enable(x_api_key: OperatorAPIKey = None):
    from halal_screener import _require_api_key, settings, tg_send
    _require_api_key(x_api_key)
    settings.AUTO_TRADE_ENABLED = True
    tg_send("AUTO-TRADING ENABLED\n\nPaper account auto-execution is now active.\nOnly STRONG BUY/SELL signals will be executed.")
    return {"auto_trade_enabled": True, "message": "Paper trading auto-execution enabled"}


@router.post("/api/v1/trading/disable")
async def trading_disable(x_api_key: OperatorAPIKey = None):
    from halal_screener import _require_api_key, settings, tg_send
    _require_api_key(x_api_key)
    settings.AUTO_TRADE_ENABLED = False
    tg_send("AUTO-TRADING DISABLED\n\nAuto-execution has been stopped.\nAll existing positions remain open.")
    return {"auto_trade_enabled": False, "message": "Auto-trading disabled"}


@router.get("/api/ibkr/ping")
async def ibkr_ping(x_api_key: OperatorAPIKey = None):
    import socket
    from halal_screener import _require_api_key, settings
    _require_api_key(x_api_key)
    host = settings.IBKR_HOST
    port = settings.IBKR_PORT
    client_id = settings.IBKR_CLIENT_ID
    try:
        sock = socket.create_connection((host, port), timeout=5)
        sock.close()
        return {
            "status": "ok",
            "host": host,
            "port": port,
            "client_id": client_id,
            "message": "TWS/IB Gateway is reachable",
        }
    except socket.timeout:
        return {
            "status": "error",
            "host": host,
            "port": port,
            "client_id": client_id,
            "message": "Connection timed out. Check that TWS/IB Gateway is running and the port is correct.",
        }
    except ConnectionRefusedError:
        return {
            "status": "error",
            "host": host,
            "port": port,
            "client_id": client_id,
            "message": "Connection refused. TWS/IB Gateway is not running on this host:port.",
        }
    except Exception as exc:
        return {
            "status": "error",
            "host": host,
            "port": port,
            "client_id": client_id,
            "message": str(exc),
        }


@router.get("/api/v1/portfolio/stop-status")
async def portfolio_stop_status(x_api_key: OperatorAPIKey = None):
    from halal_screener import _require_api_key
    from app.services.portfolio_stop import get_status, check_drawdown
    _require_api_key(x_api_key)
    check_drawdown()
    return get_status()


@router.get("/api/v1/portfolio/purification")
async def portfolio_purification(x_api_key: OperatorAPIKey = None):
    from halal_screener import _require_api_key
    from app.services.purification_calculator import calculate_purification
    _require_api_key(x_api_key)
    bt = _broker_type()
    if bt == "ibkr":
        return calculate_purification(positions=[], strategy_id=None)
    from halal_screener import _primary_broker_strategy_id, _has_alpaca_broker_config, alpaca_get_positions
    sid = _primary_broker_strategy_id() if _has_alpaca_broker_config() else None
    positions = alpaca_get_positions(strategy_id=sid) if sid else []
    return calculate_purification(positions=positions, strategy_id=sid)
