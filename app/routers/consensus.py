"""Consensus, Pipeline, Ready-to-Trade endpoints"""
from __future__ import annotations
from fastapi import APIRouter, Body

from app.core.security import OperatorAPIKey

router = APIRouter(tags=["Consensus"])


@router.get("/consensus")
async def consensus(symbol: str = "AAPL", horizon: int = 5, episodes: int = 10):
    from halal_screener import (_serve_or_compute, _cache_key, validate_symbol,
        validate_range, run_consensus)
    s = validate_symbol(symbol)
    validate_range(horizon, "horizon", 1, 30)
    validate_range(episodes, "episodes", 1, 50)
    key = _cache_key("consensus", symbol=s, horizon=horizon, episodes=episodes)
    return _serve_or_compute(key, run_consensus, args=(s, horizon, episodes), msg=f"Computing AI consensus for {s}...")


@router.get("/consensus_momentum")
async def consensus_momentum(symbol: str = "AAPL", horizon: int = 5):
    from halal_screener import (_serve_or_compute, _cache_key, validate_symbol,
        validate_range, run_consensus_momentum)
    s = validate_symbol(symbol)
    validate_range(horizon, "horizon", 1, 30)
    key = _cache_key("consensus_momentum", symbol=s, horizon=horizon)
    return _serve_or_compute(key, run_consensus_momentum, args=(s, horizon), msg=f"Computing momentum consensus for {s}...")


@router.get("/consensus_reversion")
async def consensus_reversion(symbol: str = "AAPL", horizon: int = 3):
    from halal_screener import (_serve_or_compute, _cache_key, validate_symbol,
        validate_range, run_consensus_reversion)
    s = validate_symbol(symbol)
    validate_range(horizon, "horizon", 1, 30)
    key = _cache_key("consensus_reversion", symbol=s, horizon=horizon)
    return _serve_or_compute(key, run_consensus_reversion, args=(s, horizon), msg=f"Computing reversion consensus for {s}...")


@router.get("/consensus_ml")
async def consensus_ml(symbol: str = "AAPL", horizon: int = 7, episodes: int = 5):
    from halal_screener import (_serve_or_compute, _cache_key, validate_symbol,
        validate_range, run_consensus_ml)
    s = validate_symbol(symbol)
    validate_range(horizon, "horizon", 1, 30)
    validate_range(episodes, "episodes", 1, 50)
    key = _cache_key("consensus_ml", symbol=s, horizon=horizon, episodes=episodes)
    return _serve_or_compute(key, run_consensus_ml, args=(s, horizon, episodes), msg=f"Computing ML consensus for {s}...")


@router.get("/batch_consensus")
async def batch_consensus(min_swing_score: int = 55, horizon: int = 5, episodes: int = 5, max_stocks: int = 10):
    from halal_screener import (_serve_or_compute, _cache_key, run_batch_consensus)
    key = _cache_key("batch_consensus", min=min_swing_score, h=horizon, ep=episodes, max=max_stocks)
    return _serve_or_compute(key, run_batch_consensus, args=(min_swing_score, horizon, episodes, max_stocks), msg="Computing batch consensus...")


@router.get("/pipeline")
async def pipeline(min_confidence: int = 40, max_final: int = 15, horizon: int = 5, episodes: int = 5):
    from halal_screener import (_serve_or_compute, _cache_key, run_pipeline)
    key = _cache_key("pipeline", conf=min_confidence, max=max_final, h=horizon, ep=episodes)
    return _serve_or_compute(key, run_pipeline, args=(min_confidence, max_final, horizon, episodes), msg="Computing full pipeline...")


@router.get("/ready")
async def ready_to_trade(min_swing: int = 55, max_stocks: int = 25):
    from halal_screener import (_serve_or_compute, _cache_key, run_ready_to_trade)
    key = _cache_key("ready_to_trade", min=min_swing, max=max_stocks)
    return _serve_or_compute(key, run_ready_to_trade, args=(min_swing, max_stocks),
                            msg="Analyzing top stocks through full AI pipeline... Pre-market scan runs at 9:00 AM ET.")


@router.get("/refresh_ready")
async def refresh_ready(min_swing: int = 55, max_stocks: int = 25, x_api_key: OperatorAPIKey = None):
    from halal_screener import _require_api_key, _cache_key, _cache_lock, _bg_cache, _cache_status, _bg_compute, run_ready_to_trade
    _require_api_key(x_api_key)
    key = _cache_key("ready_to_trade", min=min_swing, max=max_stocks)
    with _cache_lock:
        _bg_cache.pop(key, None)
        _cache_status[key] = "idle"
    from threading import Thread
    Thread(target=_bg_compute, args=(key, run_ready_to_trade, (min_swing, max_stocks)), daemon=True).start()
    return [{"Status": "Ready-to-Trade refresh started."}]


@router.get("/weekly_picks")
async def weekly_picks(account: float = 10000.0, top: int = 15,
                       min_confidence: float = 45.0, funnel: str = "pipeline",
                       format: str = "json"):
    """Advisory weekly swing-picks report (read-only, no orders).

    Runs the halal funnel (cached/background) and enriches each candidate with the
    validated Option-A plan (fixed 15% catastrophe stop + ~20 trading-day time
    exit) and a cap-aware position size, plus the live validation status
    (forward-PF + paper-trade graduation). `format=text` returns the rendered
    table once computed. First call may return a "computing" status (1-3 min).
    """
    from halal_screener import _serve_or_compute, _cache_key, validate_range
    from app.services.weekly_report import build_weekly_report, format_report
    validate_range(account, "account", 100, 100_000_000)
    validate_range(top, "top", 1, 50)
    validate_range(min_confidence, "min_confidence", 0, 100)
    funnel = funnel if funnel in ("pipeline", "ready") else "pipeline"
    key = _cache_key("weekly_picks", acct=account, top=top, conf=min_confidence, funnel=funnel)
    result = _serve_or_compute(
        key, build_weekly_report, args=(account,),
        kwargs={"top": top, "min_confidence": min_confidence, "funnel": funnel},
        msg="Building weekly swing-picks report (full funnel, 1-3 min)...",
    )
    if format == "text" and isinstance(result, dict) and "picks" in result:
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse(format_report(result))
    return result


@router.get("/paper_validation/record")
async def paper_validation_record(account: float = 10000.0, top: int = 15,
                                  min_confidence: float = 45.0, funnel: str = "pipeline",
                                  scanner: str = "weekly", x_api_key: OperatorAPIKey = None):
    """Record picks into a ledger (background).

    scanner=weekly  → record this week's swing picks (Option-A ledger PV).
    scanner=monthly → rebalance the monthly composite ledger (PVM) to the top-N.
    """
    from halal_screener import _require_api_key, validate_range
    _require_api_key(x_api_key)
    validate_range(account, "account", 100, 100_000_000)
    from threading import Thread
    if str(scanner).lower().startswith("month"):
        from app.services.paper_validation import rebalance_monthly
        validate_range(top, "top", 1, 50)
        Thread(target=rebalance_monthly, kwargs={"top_n": top, "account": account},
               daemon=True).start()
        return {"Status": "Monthly rebalance started. Poll /paper_validation/status?scanner=monthly."}
    from app.services.paper_validation import record_weekly_picks
    funnel = funnel if funnel in ("pipeline", "ready") else "pipeline"
    Thread(target=record_weekly_picks,
           kwargs={"account": account, "top": top, "min_confidence": min_confidence, "funnel": funnel},
           daemon=True).start()
    return {"Status": "Paper-validation recording started (1-3 min). Poll /paper_validation/status."}


@router.get("/paper_validation/mature")
async def paper_validation_mature(x_api_key: OperatorAPIKey = None):
    """Close any open WEEKLY paper trade whose Option-A exit has fired."""
    from halal_screener import _require_api_key
    from app.services.paper_validation import mature_open_paper_trades
    _require_api_key(x_api_key)
    return mature_open_paper_trades()


@router.get("/paper_validation/rebalance")
async def paper_validation_rebalance(top_n: int = 15, account: float = 10000.0,
                                     x_api_key: OperatorAPIKey = None):
    """Rebalance the MONTHLY composite ledger (PVM) to the current top-N picks."""
    from halal_screener import _require_api_key, validate_range
    from app.services.paper_validation import rebalance_monthly
    _require_api_key(x_api_key)
    validate_range(account, "account", 100, 100_000_000)
    validate_range(top_n, "top_n", 1, 50)
    return rebalance_monthly(top_n=top_n, account=account)


@router.get("/paper_validation/status")
async def paper_validation_status(scanner: str = "weekly"):
    """Open/closed ledger counts + graduation for a scanner (weekly PV | monthly PVM | core PVC)."""
    from app.services.paper_validation import paper_ledger_status, _strategy_for
    return paper_ledger_status(strategy_id=_strategy_for(scanner))


@router.get("/api/core-ledger")
async def core_ledger_ep():
    """The paper Core Portfolio ledger (PVC): open positions with unrealized P/L at latest scan
    prices, realized stats, blended return since inception, and rebalance cadence. This is the
    paper mirror the user compares their REAL book against to measure execution slippage."""
    from app.services.paper_validation import core_ledger_summary
    return core_ledger_summary()


@router.post("/api/core-ledger/rebalance")
async def core_ledger_rebalance_ep():
    """Open / refresh the paper Core basket now (equal-weight halal universe, force past the
    quarterly cadence). Runs in the background; poll GET /api/core-ledger. Simulated — the paper
    ledger only; NEVER places a real order."""
    from threading import Thread
    from app.services.paper_validation import rebalance_core, _days_since_last_core
    Thread(target=rebalance_core, kwargs={"force": True}, daemon=True,
           name="core-rebalance").start()
    first = _days_since_last_core() is None
    return {"status": "started",
            "message": ("يفتح سلّة النواة الورقيّة الأولى — دقيقة تقريباً." if first
                        else "يعيد توازن دفتر النواة الورقيّ — دقيقة تقريباً.")}


@router.get("/api/satellite-ledger")
async def satellite_ledger_ep():
    """The momentum SATELLITE paper ledger (PVSA) — the FORWARD out-of-sample record of the only
    strategy that beat the core after costs in the walk-forward (monthly 12-1 momentum, top-K).
    Shadow measurement; the alpha is unproven forward (survivorship risk). Never trades."""
    from app.services.paper_validation import satellite_ledger_summary
    return satellite_ledger_summary()


@router.post("/api/satellite-ledger/rebalance")
async def satellite_ledger_rebalance_ep():
    """Open / refresh the momentum satellite paper basket now (top-K by 12-1 momentum, force past
    the monthly cadence). Background; poll GET /api/satellite-ledger. SHADOW — paper only; NEVER a
    real order. Starting the forward track record does NOT deploy it live."""
    from threading import Thread
    from app.services.paper_validation import rebalance_satellite, _days_since_last, PV_SATELLITE
    Thread(target=rebalance_satellite, kwargs={"force": True}, daemon=True,
           name="satellite-rebalance").start()
    first = _days_since_last(PV_SATELLITE) is None
    return {"status": "started",
            "message": ("يفتح سلّة القمر (الزخم) الورقيّة الأولى — دقيقة تقريباً." if first
                        else "يعيد توازن دفتر القمر الورقيّ — دقيقة تقريباً.")}


@router.get("/api/explorer-ledger")
async def explorer_ledger_ep():
    """The EXPLORER paper ledger (PVEX) — forward OOS record of the Winner-Autopsy 'rocket signature'
    (high volatility + far below 52w high). A lottery basket; the beaten-down leg is survivorship-
    inflated in-sample. Shadow measurement; never trades."""
    from app.services.paper_validation import explorer_ledger_summary
    return explorer_ledger_summary()


@router.post("/api/explorer-ledger/rebalance")
async def explorer_ledger_rebalance_ep():
    """Open / refresh the explorer paper basket now (top-K by rocket signature, force past the monthly
    cadence). Background; poll GET /api/explorer-ledger. SHADOW — paper only; NEVER a real order."""
    from threading import Thread
    from app.services.paper_validation import rebalance_explorer, _days_since_last, PV_EXPLORER
    Thread(target=rebalance_explorer, kwargs={"force": True}, daemon=True,
           name="explorer-rebalance").start()
    first = _days_since_last(PV_EXPLORER) is None
    return {"status": "started",
            "message": ("يفتح سلّة المستكشف الورقيّة الأولى — دقيقة تقريباً." if first
                        else "يعيد توازن دفتر المستكشف الورقيّ — دقيقة تقريباً.")}


@router.get("/api/ledger-nav")
async def ledger_nav_ep():
    """Daily NAV race series — paper core (PVC) vs momentum satellite (PVSA) — for the forward
    out-of-sample curve. Read-only measurement; never trades."""
    from app.services.ledger_nav import nav_history
    return nav_history()


@router.post("/api/ledger-nav/record")
async def ledger_nav_record_ep():
    """Record today's NAV row now (background) — normally the scheduler does this ~daily at close.
    Poll GET /api/ledger-nav. Measurement only."""
    from threading import Thread
    from app.services.ledger_nav import record_nav
    Thread(target=record_nav, daemon=True, name="ledger-nav-record").start()
    return {"status": "started", "message": "يسجّل قيمة اليوم للدفترين — لحظات."}


@router.get("/api/signal-calibration")
async def signal_calibration(scanner: str = "weekly", view: str = "calibration"):
    """READ-ONLY measurement: does a higher scanner score actually yield a higher forward
    return? Reads the paper ledger (no trade-path impact).

    view=calibration (default) → per-score-band win-rate/avg-return + score↔return rank
    correlation + monotonicity. view=attribution → per-component rank correlation (monthly).
    """
    from app.services.signal_calibration import calibration_report, component_attribution
    if view == "attribution":
        return component_attribution(scanner)
    return calibration_report(scanner)


@router.get("/api/selection-quality")
async def selection_quality(force: bool = False):
    """Honest at-a-glance scorecard: does the SELECTION beat just buying SPY? Bundles the
    alpha-vs-SPY t-stat with the score→return calibration into a plain-language grade per
    scanner (weekly + monthly). READ-ONLY measurement from the paper ledgers; cached ~30 min."""
    from app.services.selection_quality import selection_quality_summary
    return selection_quality_summary(force=force)


@router.get("/api/factor-lab")
async def factor_lab(force: bool = False):
    """Offline, look-ahead-safe ESTIMATE (available now, no waiting): the with-trend gate
    A/B replay over history + the cross-sectional Information Coefficient of RS and 12-1
    momentum. Price-only (PIT-clean); the live ledger still confirms. Cached 6h."""
    from app.services.factor_lab import factor_lab_report
    return factor_lab_report(force=force)


@router.get("/api/weekly-shadow-ab")
async def weekly_shadow_ab_ep(horizon_days: int = 10):
    """Forward PAIRED A/B of the with-trend gate: fixed-horizon return of gate-PASS (PV)
    vs gate-REJECTED (shadow PVSH) picks — the live confirmation of the history replay."""
    from app.services.paper_validation import weekly_shadow_ab
    return weekly_shadow_ab(horizon_days=horizon_days)


# ── self-calibrating gate: OOS recommendation + live confirmation + approval ──

@router.get("/api/gate-calibration")
async def gate_calibration_ep():
    """② OOS-validated threshold-grid recommendation for the weekly entry gate. Cache-only
    on the request path (heavy compute runs in the factor-lab background warm)."""
    from app.services.factor_lab import gate_calibration_cached, factor_lab_cached
    cached = gate_calibration_cached()
    if cached is not None:
        return cached
    factor_lab_cached(warm=True)   # kick the single-flight warm (computes gate_calibration too)
    return {"status": "computing", "recommendation": None}


@router.get("/api/gate-forward")
async def gate_forward_ep(min_rs: float | None = None, require_ema20: bool = True):
    """③ Score a candidate MIN_RS on the accumulated forward cross-section (PV+PVSH)."""
    from app.services.paper_validation import weekly_gate_forward_eval
    return weekly_gate_forward_eval(min_rs=min_rs, require_ema20=require_ema20)


@router.get("/api/gate-config")
async def gate_config_ep():
    """Current live gate threshold + provenance/audit history."""
    from app.services.gate_config import gate_config_state
    return gate_config_state()


@router.post("/api/gate-config/apply")
async def gate_config_apply_ep(min_rs: float, test_t: float | None = None,
                               train_t: float | None = None):
    """④ Approve a new MIN_RS threshold (PAPER ledger only; never a real order). Persisted,
    audited, reversible. This is the user's explicit approval action from the dashboard."""
    from app.services.gate_config import set_min_rs
    return set_min_rs(min_rs, evidence={"test_t": test_t, "train_t": train_t, "via": "dashboard"})


@router.post("/api/gate-config/reset")
async def gate_config_reset_ep():
    """Revert the gate threshold to the env/default (delete the approved override)."""
    from app.services.gate_config import reset_min_rs
    return reset_min_rs()


@router.get("/api/factor-weights")
async def factor_weights_ep():
    """Current composite-score factor weights + defaults/bounds/provenance (for the sliders)."""
    from app.services.factor_weights import factor_weights_state
    return factor_weights_state()


@router.post("/api/factor-weights/apply")
async def factor_weights_apply_ep(payload: dict = Body(...)):
    """Approve new composite factor weights (PAPER scoring only; never a real order).
    Persisted, audited, reversible. The user's explicit action from the settings sliders.
    Body: {"weights": {"COMPOSITE_MOM121_WEIGHT": 12, ...}}."""
    from app.services.factor_weights import set_weights
    weights = (payload or {}).get("weights") if isinstance(payload, dict) else None
    if not isinstance(weights, dict):
        weights = payload if isinstance(payload, dict) else {}
    return set_weights(weights)


@router.post("/api/factor-weights/reset")
async def factor_weights_reset_ep():
    """Revert every factor weight to its env/coded default (delete the override)."""
    from app.services.factor_weights import reset_weights
    return reset_weights()


# ── written circuit-breaker card (Core Portfolio discipline contract) ─────────

@router.get("/api/circuit-breaker")
async def circuit_breaker_ep():
    """The user's written circuit-breaker card for the Core Portfolio (thresholds + provenance +
    whether it has been approved). Records a discipline protocol only — changes NO trading."""
    from app.services.circuit_breaker import circuit_breaker_state
    return circuit_breaker_state()


@router.post("/api/circuit-breaker/approve")
async def circuit_breaker_approve_ep(payload: dict = Body(...)):
    """Approve/update the written circuit-breaker card (the user's own protocol before the first
    real order). Persisted, audited, reversible. Never places or blocks an order.
    Body: {"max_cumulative_loss_pct": 20, "max_deviation_pct": 10, "capital_experimental": 1000}."""
    from app.services.circuit_breaker import approve_card
    vals = (payload or {}).get("values") if isinstance(payload, dict) else None
    if not isinstance(vals, dict):
        vals = payload if isinstance(payload, dict) else {}
    return approve_card(vals)


@router.post("/api/circuit-breaker/reset")
async def circuit_breaker_reset_ep():
    """Revoke the approved card (delete it, audited) — reverts to 'not yet approved'."""
    from app.services.circuit_breaker import reset_card
    return reset_card()


# ── quant-fund overlays: alpha capture, meta-model, HMM regime ────────────────

@router.get("/api/alpha-capture")
async def alpha_capture_ep(horizon_days: int = 10, sector_neutral: bool = False):
    """① Per-factor IC across the daily whole-universe capture panel + row/label counts."""
    from app.services.alpha_capture import snapshot_attribution, capture_status
    return {"status": capture_status(),
            "attribution": snapshot_attribution(horizon_days=horizon_days, sector_neutral=sector_neutral)}


@router.post("/api/alpha-capture/backfill")
async def alpha_capture_backfill_ep(period: str | None = None, cap: int | None = None,
                                    warmup: int | None = None):
    """① Kick the look-ahead-safe HISTORICAL backfill of the capture base in the background —
    fills IC/attribution/meta with hundreds of dates today. Single-flight. Optional query params
    (or env BACKFILL_PERIOD/CAP/WARMUP) size a BIG run, e.g. ?period=3y&cap=350&warmup=252 to
    multiply the panel (the statistical-power lever). Heavy but off the request path."""
    from app.services.alpha_capture import run_backfill_bg
    return run_backfill_bg(period=period, cap=cap, warmup=warmup)


@router.get("/api/alpha-capture/backfill/status")
async def alpha_capture_backfill_status_ep():
    from app.services.alpha_capture import backfill_status, capture_status
    return {**backfill_status(), "capture": capture_status()}


@router.get("/api/meta-model")
async def meta_model_ep():
    """② Meta-labeling model status (in-sample AUC, base rate, top features)."""
    from app.services.meta_label import meta_model_status
    return meta_model_status()


@router.get("/api/concentration")
async def concentration_ep():
    """② Effective Number of Bets over the open weekly paper book (Meucci PCA-entropy)."""
    from app.services.concentration import open_positions_enb
    return open_positions_enb()


@router.get("/api/regime-ic")
async def regime_ic_ep(horizon_days: int = 10):
    """① × ④ Per-factor Information Coefficient measured WITHIN each market regime."""
    from app.services.alpha_capture import regime_conditional_ic
    return regime_conditional_ic(horizon_days=horizon_days)


_SPARK_CACHE = {"at": 0.0, "data": None}


@router.get("/api/market/spark")
def market_spark_ep():
    """Recent close series (last ~24) for the strip's index/commodity ETF proxies — for the
    market-strip sparklines. Cached 15 min. Sync (runs in FastAPI's threadpool)."""
    import time
    now = time.time()
    if _SPARK_CACHE["data"] is not None and (now - _SPARK_CACHE["at"]) < 900:
        return _SPARK_CACHE["data"]
    from app.services.market_data import fetch
    out = {}
    for sym in ("SPY", "QQQ", "DIA", "IWM", "GLD", "BNO"):
        try:
            df = fetch(sym, period="1mo")
            if df is not None and len(df) > 3:
                out[sym] = [round(float(x), 2) for x in df["close"].astype(float).values[-24:]]
        except Exception:
            pass
    res = {"spark": out}
    _SPARK_CACHE.update(at=now, data=res)
    return res


@router.get("/api/risk/var")
async def risk_var_ep(equity: float | None = None):
    """Parametric 1-day VaR (95/99%) for the book — equity × SPY vol × z."""
    from app.services.risk_metrics import portfolio_var
    return portfolio_var(equity=equity)


@router.get("/api/alpha-curve")
async def alpha_curve_ep(days: int = 365):
    """Real cumulative selection-alpha curve from the closed paper ledger."""
    from app.services.risk_metrics import cumulative_alpha_series
    return cumulative_alpha_series(days=days)


@router.get("/api/factor-ic-multi")
async def factor_ic_multi_ep():
    """Per-factor IC at 5/10/20-day horizons + direction + verdict (for the factor table)."""
    from app.services.alpha_capture import snapshot_attribution_multi, capture_status
    return {"status": capture_status(), "attribution": snapshot_attribution_multi()}


@router.get("/api/candidate-composites")
async def candidate_composites_ep():
    """Shadow factor race — forward IC of candidate technical composites vs plain momentum,
    measured on the snapshot panel. Research/measurement only; never touches live scoring."""
    from app.services.alpha_capture import candidate_composites_ic
    return candidate_composites_ic()


@router.get("/api/candidate-validation")
async def candidate_validation_ep(recent_n: int = 60):
    """Graduation gate — each shadow candidate's recent-window vs full-panel top-bucket t, with a
    'ready/watching/weak' verdict. Powers the auto-PROPOSE decision inbox. Never auto-applies."""
    from app.services.alpha_capture import candidate_forward_validation
    return candidate_forward_validation(recent_n=recent_n)


@router.get("/api/walk-forward-sim")
async def walk_forward_sim_ep(top_k: int = 5, hold: str = "5", cost_bps: float = 15.0):
    """⏱️ Time-machine backtest of the shadow composites on the 4-year panel (equity/CAGR/maxDD/
    2022 slice vs the equal-weight halal universe). Research/measurement only; never trades."""
    from app.services.alpha_capture import walk_forward_sim
    return walk_forward_sim(top_k=top_k, hold=hold, cost_bps=cost_bps)


@router.get("/api/core-overlay-sim")
async def core_overlay_sim_ep(hold: str = "5", target_vol: float = 0.15, cost_bps: float = 15.0):
    """⏱️ Core+Overlay A/B — own the halal universe (core) vs core×HMM-regime-dial vs core×vol-target
    vs both, on the 4-year panel (CAGR/maxDD/CAGR-DD ratio/2022). Research only; never trades."""
    from app.services.alpha_capture import core_overlay_sim
    return core_overlay_sim(hold=hold, target_vol=target_vol, cost_bps=cost_bps)


@router.get("/api/gate-ema20-ab")
async def gate_ema20_ab_ep(horizon_days: int = 20, min_rs: float | None = None):
    """Shadow A/B of the weekly gate's 'above EMA20' requirement (with-trend vs counter-trend
    forward returns on the capture panel) — tests whether requiring above-EMA20 hurts."""
    from app.services.alpha_capture import gate_ema20_ab
    return gate_ema20_ab(horizon_days=horizon_days, min_rs=min_rs)


@router.get("/api/regime-hmm")
async def regime_hmm_ep():
    """④ HMM regime probabilities on SPY (calm_bull / choppy / crisis) + book multiplier."""
    from app.services.regime_hmm import regime_probabilities
    from app.services.position_sizing import book_multiplier
    try:
        from app.services.market_data import fetch
        df = fetch("SPY", period="1y")
        closes = df["close"].astype(float).values if df is not None and len(df) > 60 else None
    except Exception:
        closes = None
    return {"regime": regime_probabilities(closes) if closes is not None else None,
            "book_multiplier": book_multiplier()}


@router.get("/refresh_consensus")
async def refresh_consensus(symbol: str = "AAPL", x_api_key: OperatorAPIKey = None):
    from halal_screener import (_require_api_key, validate_symbol, _cache_key, _cache_lock,
        _bg_cache, _cache_status, _bg_compute, run_consensus)
    _require_api_key(x_api_key)
    s = validate_symbol(symbol)
    key = _cache_key("consensus", symbol=s, horizon=5, episodes=10)
    with _cache_lock:
        _bg_cache.pop(key, None)
        _cache_status[key] = "idle"
    from threading import Thread
    Thread(target=_bg_compute, args=(key, run_consensus, (s, 5, 10)), daemon=True).start()
    return {"Status": f"Consensus refresh started for {s}."}


@router.get("/refresh_batch")
async def refresh_batch(x_api_key: OperatorAPIKey = None):
    from halal_screener import _require_api_key, _cache_key, _cache_lock, _bg_cache, _cache_status, _bg_compute, run_batch_consensus
    _require_api_key(x_api_key)
    key = _cache_key("batch_consensus", min=55, h=5, ep=5, max=10)
    with _cache_lock:
        _bg_cache.pop(key, None)
        _cache_status[key] = "idle"
    from threading import Thread
    Thread(target=_bg_compute, args=(key, run_batch_consensus, (55, 5, 5, 10)), daemon=True).start()
    return [{"Status": "Batch consensus refresh started"}]


@router.get("/refresh_pipeline")
async def refresh_pipeline(x_api_key: OperatorAPIKey = None):
    from halal_screener import _require_api_key, _cache_key, _cache_lock, _bg_cache, _cache_status, _bg_compute, run_pipeline
    _require_api_key(x_api_key)
    key = _cache_key("pipeline", conf=40, max=15, h=5, ep=5)
    with _cache_lock:
        _bg_cache.pop(key, None)
        _cache_status[key] = "idle"
    from threading import Thread
    Thread(target=_bg_compute, args=(key, run_pipeline, (40, 15, 5, 5)), daemon=True).start()
    return [{"Status": "Pipeline refresh started"}]
