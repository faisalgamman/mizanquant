"""Screener, USX, BCF, Halal, Backtest, Monte Carlo, Health, Widgets, Intraday endpoints"""
import os
from fastapi import APIRouter

router = APIRouter(tags=["Screener"])


@router.get("/screener")
async def screener():
    from halal_screener import _serve_or_compute, _cache_key, run_screener
    key = _cache_key("screener")
    return _serve_or_compute(key, run_screener, msg="Computing halal screener...")


@router.get("/buys")
async def buys():
    from halal_screener import _get_cached, _bg_compute, run_screener
    key = "screener"
    cached, status = _get_cached(key)
    if cached and isinstance(cached, list):
        return [r for r in cached if r.get("swing_score", 0) >= 55]
    if status != "running":
        from threading import Thread
        Thread(target=_bg_compute, args=(key, run_screener), daemon=True).start()
    return [{"Status": "Computing buy signals...", "Info": "Refresh in 1-2 minutes."}]


@router.get("/watchlist")
async def watchlist():
    from halal_screener import _get_cached, _bg_compute, run_screener
    key = "screener"
    cached, status = _get_cached(key)
    if cached and isinstance(cached, list):
        return [r for r in cached if 35 <= r.get("swing_score", 0) < 55]
    if status != "running":
        from threading import Thread
        Thread(target=_bg_compute, args=(key, run_screener), daemon=True).start()
    return [{"Status": "Computing watchlist...", "Info": "Refresh in 1-2 minutes."}]


@router.get("/bcf")
async def bcf_screener(portfolio: float = 100000):
    from halal_screener import _serve_or_compute, _cache_key, validate_range, run_bcf_screener
    validate_range(portfolio, "portfolio", 1000, 10_000_000)
    key = _cache_key("bcf", portfolio=portfolio)
    return _serve_or_compute(key, run_bcf_screener, args=(portfolio,), msg="Computing BCF screener...")



@router.get("/monte_carlo")
async def monte_carlo(symbol: str = "AAPL", days: int = 30, simulations: int = 1000):
    from halal_screener import _serve_or_compute, _cache_key, validate_symbol, validate_range, run_monte_carlo
    s = validate_symbol(symbol)
    validate_range(days, "days", 1, 365)
    validate_range(simulations, "simulations", 100, 10000)
    key = _cache_key("monte_carlo", symbol=s, days=days, sims=simulations)
    return _serve_or_compute(key, run_monte_carlo, args=(s, days, simulations), msg=f"Running Monte Carlo for {s}...")


@router.get("/widgets.json")
async def widgets():
    return {
        "ready_to_trade": {
            "name": "Ready to Trade",
            "description": "Stocks that passed BOTH screener AND AI consensus \u2014 ready for execution",
            "category": "Trading", "type": "table",
            "endpoint": "/ready",
            "gridData": {"w": 20, "h": 12},
            "params": [
                {"paramName": "min_swing", "value": "55", "label": "Min Swing Score", "type": "text", "show": True},
                {"paramName": "max_stocks", "value": "25", "label": "Max Stocks", "type": "text", "show": True},
            ],
        },
        "portfolio_summary": {
            "name": "Portfolio & Positions",
            "description": "Equity, cash, P&L, open positions",
            "category": "Portfolio", "type": "table",
            "endpoint": "/portfolio/summary",
            "gridData": {"w": 10, "h": 5},
        },
        "ai_agent": {
            "name": "Claude AI Agent",
            "description": "Ask anything: halal check, analysis, trade plan",
            "category": "AI", "type": "table",
            "endpoint": "/agent/analyze",
            "gridData": {"w": 10, "h": 9},
            "params": [{"paramName": "symbol", "value": "AAPL", "label": "Symbol", "type": "text", "show": True}],
        },
        "ai_consensus": {
            "name": "AI Deep Analysis",
            "description": "Full 14-tool consensus for a single stock",
            "category": "Research", "type": "table",
            "endpoint": "/consensus",
            "gridData": {"w": 10, "h": 9},
            "params": [
                {"paramName": "symbol", "value": "AAPL", "label": "Symbol", "type": "text", "show": True},
                {"paramName": "horizon", "value": "5", "label": "Forecast Days", "type": "text", "show": True},
                {"paramName": "episodes", "value": "10", "label": "RL Episodes", "type": "text", "show": True},
            ],
        },
        "halal_check": {
            "name": "Halal Check",
            "description": "AAOIFI Sharia compliance verification",
            "category": "Research", "type": "table",
            "endpoint": "/halal_status",
            "gridData": {"w": 10, "h": 5},
            "params": [{"paramName": "symbol", "value": "AAPL", "label": "Symbol", "type": "text", "show": True}],
        },
    }


@router.get("/halal_status")
async def halal_status(symbol: str = "AAPL"):
    import asyncio
    from halal_screener import validate_symbol, settings, NON_FATAL_ANALYSIS_ERROR, get_halal_status, _json_safe, logger
    s = validate_symbol(symbol)
    if not settings.FMP_API_KEY:
        return [{"Error": "FMP_API_KEY not configured. Get a free key at https://site.financialmodelingprep.com/developer/docs"}]
    try:
        result = await asyncio.to_thread(get_halal_status, s)
    except NON_FATAL_ANALYSIS_ERROR as e:
        logger.error(f"Halal screening crashed for {s}: {e}")
        result = None
    if result is None:
        return [{"Error": f"Could not retrieve fundamental data for {s}"}]
    status = "HALAL" if result.get("is_halal") else "HARAM"
    return [{
        "Symbol": s, "Status": status,
        "Company": result.get("company_name", s), "Sector": result.get("sector", ""),
        "Debt / MCap %": _json_safe(result.get("debt_ratio", 0)),
        "Debt Pass": "YES" if result.get("debt_pass") else "NO",
        "Interest / Rev %": _json_safe(result.get("interest_ratio", 0)),
        "Interest Pass": "YES" if result.get("interest_pass") else "NO",
        "Haram Sector": "YES" if result.get("haram_revenue") else "NO",
        "Sector Pass": "YES" if result.get("haram_pass") else "NO",
        "Liquidity / MCap %": _json_safe(result.get("liquidity_ratio", 0)),
        "Liquidity Pass": "YES" if result.get("liquidity_pass") else "NO",
        "Screens Passed": f"{result.get('screens_passed', 0)}/4",
        "Threshold": "Debt<33%, Interest<5%, No Haram Sector, Liquidity<33%",
    }]


@router.get("/halal_verify/{symbol}")
async def halal_verify(symbol: str):
    from halal_screener import validate_symbol, verify_halal
    s = validate_symbol(symbol)
    is_halal, reason = verify_halal(s)
    return [{"Symbol": s, "Allowed": is_halal,
             "Status": "HALAL - OK TO TRADE" if is_halal else "BLOCKED",
             "Reason": reason, "Gate": "verify_halal()"}]


@router.get("/halal_blocked")
async def halal_blocked():
    from halal_screener import _VERIFIED_HARAM, _HARAM_EXCLUDE
    blocked = []
    for sym in sorted(_VERIFIED_HARAM):
        blocked.append({"Symbol": sym, "Reason": "Verified haram (AAOIFI)"})
    for sym in sorted(_HARAM_EXCLUDE):
        blocked.append({"Symbol": sym, "Reason": "Sector exclusion (banks/insurance/alcohol/etc)"})
    return blocked if blocked else [{"Message": "No stocks blocked yet (gate runs on first access)"}]


@router.get("/screening_report")
async def screening_report():
    from halal_screener import get_screening_report
    results = get_screening_report()
    if not results:
        return [{"Message": "No screening results yet. Use /screen_stocks to start screening, or /halal_status/{symbol} for a single stock."}]
    return results


@router.get("/screen_stocks")
async def screen_stocks_endpoint(max_stocks: int = 80):
    from halal_screener import _serve_or_compute, _cache_key, settings, validate_range, _universe_symbols, batch_screen
    if not settings.FMP_API_KEY:
        return [{"Error": "FMP_API_KEY not configured. Get a free key at https://site.financialmodelingprep.com/developer/docs"}]
    validate_range(max_stocks, "max_stocks", 1, 200)
    all_symbols = list(set(_universe_symbols()))
    key = _cache_key("screening_batch", max=max_stocks)
    def _run_batch():
        return [batch_screen(all_symbols, max_per_run=max_stocks)]
    return _serve_or_compute(key, _run_batch, msg=f"Screening up to {max_stocks} stocks (3 API calls each)... Refresh in a few minutes.")


@router.get("/usx")
async def usx(min_score: int = 7):
    from halal_screener import _serve_or_compute, _cache_key, validate_range, run_usx_screener
    validate_range(min_score, "min_score", 1, 10)
    key = _cache_key("usx", min_score=min_score)
    return _serve_or_compute(key, run_usx_screener, args=(min_score,), msg="Computing USX screener...")


@router.get("/ping")
async def ping():
    """Unauthenticated lightweight health check for load balancers."""
    from halal_screener import _uptime_seconds
    return {
        "status": "ok",
        "uptime_seconds": round(_uptime_seconds(), 1),
    }


@router.get("/health")
async def health():
    import logging
    from halal_screener import (settings, _universe_symbols, _has_alpaca_broker_config,
        _primary_broker_strategy_id, fetch_yf, alpaca_get_account, alpaca_get_last_error,
        _uptime_seconds, _check_model_degradation,
        NON_FATAL_ANALYSIS_ERROR, logger)
    checks = {"openbb_forecast": False, "market_data": False, "database": False, "broker": None}
    try:
        from openbb_forecast.simulation.monte_carlo import MonteCarloSimulator
        from openbb_forecast.models.lstm import LSTMForecaster
        import torch
        checks["openbb_forecast"] = True
    except NON_FATAL_ANALYSIS_ERROR as e:
        logger.error(f"openbb_forecast health check failed: {e}")
    try:
        df = fetch_yf("AAPL", period="1y")
        checks["market_data"] = df is not None and len(df) > 0
    except NON_FATAL_ANALYSIS_ERROR as e:
        logger.error(f"market_data health check failed: {e}")
    try:
        from app.db.database import engine
        from sqlalchemy import text
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["database"] = True
    except NON_FATAL_ANALYSIS_ERROR as e:
        logger.error(f"database health check failed: {e}")
    broker_type = os.environ.get("BROKER_TYPE", "alpaca").lower()
    alpaca_configured = _has_alpaca_broker_config()
    broker_connected = False
    broker_error = {}
    if broker_type == "ibkr":
        try:
            import socket
            from app.services.broker.ibkr_config import get_ibkr_config
            _cfg = get_ibkr_config()
            sock = socket.create_connection((_cfg["host"], _cfg["port"]), timeout=5)
            sock.close()
            broker_connected = True
            checks["broker"] = True
        except Exception:
            checks["broker"] = False
    elif alpaca_configured:
        try:
            broker_connected = bool(alpaca_get_account(strategy_id=_primary_broker_strategy_id()))
            checks["broker"] = broker_connected
            if not broker_connected:
                broker_error = alpaca_get_last_error(strategy_id=_primary_broker_strategy_id())
        except NON_FATAL_ANALYSIS_ERROR as e:
            logger.error(f"broker health check failed: {e}")
            checks["broker"] = False
            broker_error = alpaca_get_last_error(strategy_id=_primary_broker_strategy_id())
    fmp_configured = bool(settings.FMP_API_KEY)
    telegram_configured = bool(settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_ID)
    core_ok = all(checks[name] for name in ("openbb_forecast", "market_data", "database"))
    broker_ok = (not alpaca_configured) or broker_connected
    if broker_type == "ibkr":
        broker_ok = broker_connected
    all_ok = core_ok and broker_ok
    if settings.AUTO_TRADE_ENABLED and not broker_connected:
        all_ok = False

    model_degradation = _check_model_degradation()
    degraded_models = [k for k, v in model_degradation.items() if v.get("status") in ("degraded", "below_chance")]
    if degraded_models:
        all_ok = False

    return {
        "status": "ok" if all_ok else "degraded",
        "version": "17.0.0",
        "widgets": 10,
        "stocks": len(_universe_symbols()),
        "data_source": "alpaca+yfinance" if broker_connected else "yfinance",
        "halal_screening": "fmp_live" if fmp_configured else "hardcoded_lists",
        "telegram": "active" if telegram_configured else "not_configured",
        "operator_api": "configured" if settings.API_KEY else "not_configured",
        "broker": "connected" if broker_connected else "configured_unavailable" if alpaca_configured or broker_type == "ibkr" else "not_configured",
        "broker_type": broker_type,
        "broker_reason": broker_error.get("reason") if broker_error else "",
        "broker_status_code": broker_error.get("status_code") if broker_error else None,
        "database": "connected" if checks["database"] else "unavailable",
        "auto_trading": "enabled" if settings.AUTO_TRADE_ENABLED and broker_connected else "blocked_broker_unavailable" if settings.AUTO_TRADE_ENABLED else "disabled",
        "config": "loaded",
        "uptime_seconds": round(_uptime_seconds(), 1),
        "uptime_human": f"{_uptime_seconds() / 3600:.1f}h",
        "model_degradation": model_degradation,
        "dependencies": checks,
    }


@router.get("/api/v1/intraday/{symbol}")
async def intraday_bars(symbol: str, timeframe: str = "15Min", days: int = 5):
    from halal_screener import (validate_symbol, validate_range, fetch_alpaca_intraday,
        ema, rsi, pd, NON_FATAL_ANALYSIS_ERROR, logger)
    s = validate_symbol(symbol)
    valid_tf = {"1Min", "5Min", "15Min", "1Hour"}
    if timeframe not in valid_tf:
        return [{"Error": f"Invalid timeframe. Use: {valid_tf}"}]
    df = fetch_alpaca_intraday(s, timeframe=timeframe, days_back=days)
    if df is None or df.empty:
        return [{"Error": f"No intraday data for {s}"}]
    close = df["close"]
    df["ema9"] = ema(close, 9)
    df["rsi14"] = rsi(close, 14)
    _vwap_window = 20
    hlc3 = (df["high"] + df["low"] + df["close"]) / 3
    df["vwap"] = (hlc3 * df["volume"]).rolling(_vwap_window).sum() / df["volume"].rolling(_vwap_window).sum()
    bars = []
    for _, row in df.tail(200).iterrows():
        bars.append({
            "date": row["date"].isoformat(),
            "open": round(float(row["open"]), 2),
            "high": round(float(row["high"]), 2),
            "low": round(float(row["low"]), 2),
            "close": round(float(row["close"]), 2),
            "volume": int(row["volume"]),
            "ema9": round(float(row["ema9"]), 2) if pd.notna(row["ema9"]) else None,
            "rsi14": round(float(row["rsi14"]), 1) if pd.notna(row["rsi14"]) else None,
            "vwap": round(float(row["vwap"]), 2) if pd.notna(row["vwap"]) else None,
        })
    return bars
