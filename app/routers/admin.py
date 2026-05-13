"""Admin, Agent, Ops, Optimize, Post-market scan endpoints"""
import os
from fastapi import APIRouter
from halal_screener import OperatorAPIKey

router = APIRouter(tags=["Admin"])


@router.get("/admin/config")
async def admin_config(x_api_key: OperatorAPIKey = None):
    from halal_screener import _require_api_key, settings, app_cfg
    _require_api_key(x_api_key)
    return {"settings": settings.redacted(), "app_cfg": app_cfg.to_redacted_dict()}


@router.get("/admin/guards")
async def admin_guards(limit: int = 50, x_api_key: OperatorAPIKey = None):
    from halal_screener import _require_api_key, NON_FATAL_ANALYSIS_ERROR, logger
    _require_api_key(x_api_key)
    try:
        from app.db.database import SessionLocal
        from app.db.models import GuardLog
        db = SessionLocal()
        try:
            rows = db.query(GuardLog).order_by(GuardLog.ts.desc()).limit(min(max(limit, 1), 200)).all()
            return [{"ts": row.ts.isoformat() if row.ts else "", "strategy_id": row.strategy_id,
                     "symbol": row.symbol, "guard_name": row.guard_name, "passed": row.passed,
                     "code": row.code, "reason": row.reason, "regime": row.regime} for row in rows]
        finally:
            db.close()
    except NON_FATAL_ANALYSIS_ERROR as e:
        logger.error(f"/admin/guards failed: {e}", exc_info=True)
        return [{"error": str(e)}]


@router.post("/admin/killswitch")
async def admin_killswitch(killed: bool = True, x_api_key: OperatorAPIKey = None):
    from halal_screener import _require_api_key, app_cfg, tg_send
    _require_api_key(x_api_key)
    app_cfg.killed = killed
    tg_send(f"KILL SWITCH {'ENABLED' if killed else 'DISABLED'}\n\nAll new trades will {'fail closed' if killed else 'resume normal guard evaluation'}.")
    return {"killed": app_cfg.killed}


@router.post("/admin/scan_signals")
async def admin_scan_signals(strategy: str = "ABC", min_confidence: float = 70.0, account_usd: float = 5000.0, dry_run: bool = False, symbols: str = "", x_api_key: OperatorAPIKey = None):
    from halal_screener import _require_api_key, scan_and_notify_strong_buys
    _require_api_key(x_api_key)
    sids = tuple(c for c in strategy.upper() if c in "ABC")
    if not sids:
        sids = ("A", "B", "C")
    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()] or None
    if dry_run or (syms is not None and len(syms) <= 20):
        return scan_and_notify_strong_buys(strategy_ids=sids, symbols=syms, min_confidence=min_confidence, account_usd=account_usd, dry_run=dry_run)
    import threading
    def _run_in_bg():
        try:
            scan_and_notify_strong_buys(strategy_ids=sids, symbols=syms, min_confidence=min_confidence, account_usd=account_usd, dry_run=False)
        except Exception as e:
            import logging
            logging.getLogger("screener").exception(f"background scan_signals failed: {e}")
    threading.Thread(target=_run_in_bg, daemon=True, name="scan_signals").start()
    return {"queued": True, "scope": "full universe (~357 halal stocks)" if not syms else f"{len(syms)} symbols",
            "strategies": list(sids), "min_confidence": min_confidence,
            "note": "Signals will arrive on Telegram as they are found (~10-15 min)."}


@router.get("/admin/alpaca_check")
async def admin_alpaca_check(x_api_key: OperatorAPIKey = None):
    from halal_screener import _require_api_key, settings
    from app.config import STRATEGY_CONFIGS
    _require_api_key(x_api_key)
    bt = os.environ.get("BROKER_TYPE", "alpaca").lower()
    if bt == "ibkr":
        return {"broker_type": "ibkr", "message": "IBKR configured. Use IB Gateway desktop for account check.",
                "ibkr_host": settings.IBKR_HOST, "ibkr_port": settings.IBKR_PORT}
    from halal_screener import alpaca_get_account, alpaca_get_last_error
    results = {}
    if settings.ALPACA_API_KEY:
        acct = alpaca_get_account(strategy_id=None)
        err = alpaca_get_last_error(strategy_id=None)
        results["DEFAULT"] = {"configured": True, "account_status": acct.get("status") if acct else None,
                              "equity": acct.get("equity") if acct else None, "last_error": err}
    else:
        results["DEFAULT"] = {"configured": False}
    for sid in ("A", "B", "C"):
        cfg = STRATEGY_CONFIGS.get(sid)
        if not cfg or not cfg.alpaca_api_key:
            results[sid] = {"configured": False}
            continue
        acct = alpaca_get_account(strategy_id=sid)
        err = alpaca_get_last_error(strategy_id=sid)
        results[sid] = {"configured": True, "name": cfg.name, "account_status": acct.get("status") if acct else None,
                        "equity": acct.get("equity") if acct else None, "last_error": err}
    return {"broker_type": "alpaca", "results": results, "base_url": settings.ALPACA_BASE_URL}


@router.post("/admin/rearm_orphans")
async def admin_rearm_orphans(strategy_id: str | None = None, x_api_key: OperatorAPIKey = None):
    from halal_screener import _require_api_key
    from app.config import STRATEGY_CONFIGS
    from app.services.trading_engine import rearm_all_orphans
    _require_api_key(x_api_key)
    if strategy_id:
        results = rearm_all_orphans(strategy_id=strategy_id)
        return {"strategy": strategy_id, "actions": results}
    out = {}
    for sid in list(STRATEGY_CONFIGS.keys()):
        out[sid] = rearm_all_orphans(strategy_id=sid)
    return {"actions_by_strategy": out}


@router.post("/admin/regime")
async def admin_regime(state: str | None = None, x_api_key: OperatorAPIKey = None):
    from halal_screener import _require_api_key, tg_send
    from fastapi import HTTPException
    from app.services.regime import force_regime, refresh_regime
    _require_api_key(x_api_key)
    normalized = state.upper() if state else None
    if normalized not in (None, "BULL", "NEUTRAL", "BEAR"):
        raise HTTPException(status_code=400, detail="state must be one of BULL, NEUTRAL, BEAR, or omitted to clear")
    snapshot = force_regime(normalized)
    if normalized is None:
        snapshot = refresh_regime()
        tg_send("REGIME OVERRIDE CLEARED\n\nLive regime computation has resumed.")
    else:
        tg_send(f"REGIME OVERRIDE\n\nForced state: {normalized}")
    return {"state": snapshot.state, "changed": snapshot.changed,
            "computed_at": snapshot.computed_at.isoformat() if snapshot.computed_at else "", "forced": normalized is not None}


@router.get("/api/v1/post_market_scan")
async def post_market_scan(top_n: int = 5, min_score: int = 55, x_api_key: OperatorAPIKey = None):
    from halal_screener import _require_api_key, settings, _run_post_market_scan_bg
    import threading
    _require_api_key(x_api_key)
    if not settings.TELEGRAM_BOT_TOKEN:
        return [{"Error": "Telegram not configured"}]
    thread = threading.Thread(target=_run_post_market_scan_bg, args=(top_n, min_score), daemon=True)
    thread.start()
    return [{"Status": "Post-market scan started", "top_n": top_n, "min_score": min_score,
             "Message": "Results will be sent to Telegram. Check your bot."}]


@router.get("/api/v1/optimize")
async def optimize_parameters(n_stocks: int = 10, x_api_key: OperatorAPIKey = None):
    from halal_screener import _require_api_key, settings, tg_send, NON_FATAL_ANALYSIS_ERROR, logger
    import threading
    _require_api_key(x_api_key)
    def _run_optimizer():
        try:
            from app.services.optimizer import optimize_params
            result = optimize_params(n_samples=n_stocks)
            if "error" not in result:
                msg = (f"OPTIMIZER RESULTS\n\nStocks tested: {len(result['stocks_tested'])}\n\n"
                       f"CURRENT vs OPTIMAL:\nWin Rate: {result['baseline_performance']['win_rate']}% -> {result['optimal_performance']['win_rate']}%\n"
                       f"Profit Factor: {result['baseline_performance']['profit_factor']} -> {result['optimal_performance']['profit_factor']}\n"
                       f"Sharpe: {result['baseline_performance']['sharpe']} -> {result['optimal_performance']['sharpe']}\n\n"
                       f"Best Params:\nStrong BUY votes: {result['optimal_params']['strong_buy_votes']}\n"
                       f"Min confidence: {result['optimal_params']['min_confidence']}%\n"
                       f"SL mult: {result['optimal_params']['sl_atr_mult']}x ATR\n"
                       f"TP1 mult: {result['optimal_params']['tp1_atr_mult']}x ATR\n"
                       f"BB squeeze: {result['optimal_params']['bb_squeeze_width']}%\n"
                       f"Momentum ROC: {result['optimal_params']['momentum_roc_threshold']}%")
                tg_send(msg)
        except NON_FATAL_ANALYSIS_ERROR as e:
            logger.error(f"Optimizer error: {e}")
    threading.Thread(target=_run_optimizer, daemon=True).start()
    return [{"Status": "Optimizer started", "Message": "Results will be sent to Telegram"}]


@router.post("/agent/chat")
async def agent_chat(payload: dict):
    from halal_screener import settings, NON_FATAL_ANALYSIS_ERROR, logger
    if not settings.ANTHROPIC_API_KEY:
        return {"error": "ANTHROPIC_API_KEY not configured. Add it to Railway environment variables."}
    message = payload.get("message", "").strip()
    if not message:
        return {"error": "Message is required"}
    conversation_id = payload.get("conversation_id")
    try:
        from app.services.claude_agent import get_agent
        agent = get_agent()
        result = await agent.chat(message, conversation_id=conversation_id)
        return result
    except NON_FATAL_ANALYSIS_ERROR as e:
        logger.error(f"Agent chat error: {e}")
        return {"error": f"Agent error: {str(e)}"}


@router.get("/agent/analyze")
async def agent_analyze(symbol: str = "AAPL"):
    from halal_screener import settings, NON_FATAL_ANALYSIS_ERROR, logger, validate_symbol
    if not settings.ANTHROPIC_API_KEY:
        return [{"Error": "ANTHROPIC_API_KEY not configured"}]
    s = validate_symbol(symbol)
    try:
        from app.services.claude_agent import get_agent
        agent = get_agent()
        prompt = (f"Analyze {s} comprehensively: check halal status, run technical analysis "
                  f"and consensus. Give me a clear verdict with entry/SL/TP levels. Be concise.")
        result = await agent.chat(prompt)
        return [{"Symbol": s, "AI Analysis": result.get("response", "No response"),
                 "Tools Used": ", ".join(result.get("tools_used", [])), "Model": result.get("model", "")}]
    except NON_FATAL_ANALYSIS_ERROR as e:
        logger.error(f"Agent analyze error: {e}")
        return [{"Error": f"Agent error: {str(e)}"}]


@router.get("/agent/health")
async def agent_health():
    from halal_screener import settings
    from app.services.claude_tools import TOOL_SCHEMAS
    configured = bool(settings.ANTHROPIC_API_KEY)
    return {"status": "ready" if configured else "not_configured", "model": settings.CLAUDE_MODEL,
            "api_key_configured": configured, "tools_count": len(TOOL_SCHEMAS),
            "tools": [t["name"] for t in TOOL_SCHEMAS]}


from pydantic import BaseModel


class CopilotMessage(BaseModel):
    role: str
    content: str


class CopilotQueryRequest(BaseModel):
    messages: list[CopilotMessage]
    context: str | None = None


@router.post("/v1/query")
async def copilot_query(request: CopilotQueryRequest):
    from halal_screener import settings, NON_FATAL_ANALYSIS_ERROR, logger
    from fastapi.responses import StreamingResponse
    import anthropic, json

    if not settings.ANTHROPIC_API_KEY:
        return {"error": "ANTHROPIC_API_KEY not configured"}

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

    def generate():
        messages = [{"role": m.role, "content": m.content} for m in request.messages]
        with client.messages.stream(
            model=settings.CLAUDE_MODEL or "claude-sonnet-4-20250514",
            max_tokens=2048,
            system="أنت مساعد تداول متخصص في الأسهم الحلال والسوق الأمريكي وفق AAOIFI Standard 21.",
            messages=messages,
        ) as stream:
            for text in stream.text_stream:
                yield f"data: {json.dumps({'delta': text})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.get("/ops/body")
async def ops_dashboard_body(x_api_key: OperatorAPIKey = None):
    from halal_screener import _require_api_key, _render_ops_fragment, NON_FATAL_ANALYSIS_ERROR, logger, escape
    from fastapi.responses import HTMLResponse
    _require_api_key(x_api_key)
    try:
        return HTMLResponse(_render_ops_fragment(x_api_key))
    except NON_FATAL_ANALYSIS_ERROR as e:
        logger.error(f"/ops/body failed: {e}", exc_info=True)
        return HTMLResponse(f"<div><h2>Ops fragment error</h2><pre>{escape(str(e))}</pre></div>", status_code=500)


@router.get("/ops")
async def ops_dashboard(x_api_key: OperatorAPIKey = None):
    from halal_screener import _require_api_key, _render_ops_fragment, escape, json
    from fastapi.responses import HTMLResponse
    _require_api_key(x_api_key)
    headers_json = escape(json.dumps({"X-API-Key": x_api_key}) if x_api_key else "{}", quote=True)
    body = _render_ops_fragment(x_api_key)
    return HTMLResponse(
        f"""<html><head><title>Ops Dashboard</title><script src="https://unpkg.com/htmx.org@1.9.12"></script>
<style>body{{font-family:Georgia,serif;background:linear-gradient(180deg,#f7f2ea,#fcfbf8);margin:0;padding:24px;color:#1f2933;}}
table th,td{{padding:8px 10px;border-bottom:1px solid #eee;vertical-align:top;}}</style></head>
<body><div id="ops-shell" hx-get="/ops/body" hx-trigger="load, every 10s" hx-swap="innerHTML" hx-headers='{headers_json}'>{body}</div></body></html>"""
    )


@router.get("/admin/scheduler/health")
async def admin_scheduler_health(x_api_key: OperatorAPIKey = None):
    from halal_screener import _require_api_key
    _require_api_key(x_api_key)
    from app.services.scheduler_metrics import scheduler_metrics
    return scheduler_metrics.health()


@router.get("/admin/scheduler/metrics")
async def admin_scheduler_metrics(x_api_key: OperatorAPIKey = None):
    from halal_screener import _require_api_key
    _require_api_key(x_api_key)
    from app.services.scheduler_metrics import scheduler_metrics
    return scheduler_metrics.snapshot()


@router.get("/admin/scheduler/status")
async def admin_scheduler_status(x_api_key: OperatorAPIKey = None):
    from halal_screener import _require_api_key, NON_FATAL_ANALYSIS_ERROR, logger
    _require_api_key(x_api_key)
    from app.services.scheduler import _scheduler_running
    from app.services.scheduler_metrics import scheduler_metrics
    health = scheduler_metrics.health()
    health["running"] = _scheduler_running
    health["worker_mode"] = os.environ.get("WORKER_SERVICE", "").lower() == "true"
    return health


@router.get("/admin/metrics")
async def admin_metrics(x_api_key: OperatorAPIKey = None):
    """Return runtime instrumentation snapshot (counters, timings, cache hit rates)."""
    from halal_screener import _require_api_key
    from app.services.metrics import metrics
    _require_api_key(x_api_key)
    return metrics.snapshot()


@router.get("/admin/cache-stats")
async def admin_cache_stats(x_api_key: OperatorAPIKey = None):
    """Return cache hit/miss statistics per layer."""
    from halal_screener import _require_api_key
    from app.services.metrics import metrics
    _require_api_key(x_api_key)
    return metrics.cache_stats()
