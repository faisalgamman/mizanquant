import asyncio, time, logging, os, uuid, threading, gc, json, pandas as pd, numpy as np
# NOTE: This module is a router/function provider for workspace_server,
# which is the CANONICAL entry point (see railway.json → Dockerfile).
# halal_screener is NOT independently deployable.

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import FastAPI, HTTPException, Header, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.security import APIKeyHeader
from html import escape

# --- New modular imports (Phase 1 restructuring) ---
from app.config import settings
from app.core.config import app_cfg
from app.services.market_data import fetch as fetch_market_data, fetch_alpaca_intraday
from app.services.technical import (
    ema, rsi, macd, atr, calc_metrics,
    safe_scale, walk_forward_split, prepare_sequences, score_series,
)
from app.exceptions import DataFetchError, ModelTrainingError
from app.db.database import init_db
from app.background.cache_manager import record_signal, db_cache_get, db_cache_set
from app.services.halal_screening import (
    get_halal_status, get_screening_report, batch_screen, screen_symbol,
)
from app.services.alpaca_client import (
    get_account as alpaca_get_account,
    get_last_error as alpaca_get_last_error,
    get_positions as alpaca_get_positions,
    get_orders as alpaca_get_orders,
    get_portfolio_history as alpaca_get_portfolio_history,
)
from app.services.telegram_alert import (
    alert_strong_signal, alert_consensus, alert_daily_summary,
    alert_system_health, alert_signal_with_chart,
    send_message as tg_send, send_photo as tg_send_photo,
)
from app.services.signal_tracker import (
    check_signal_outcomes, get_accuracy_report, get_signal_history,
)
from app.services.trading_engine import (
    on_signal as auto_trade_signal,
    execute_buy, execute_sell,
    get_performance_report,
)
from app.services.trade_history import get_trade_history
from app.services.risk_manager import get_risk_status
from app.services.smart_ensemble import weighted_consensus
from openbb_forecast.data.time_guard import signal_cutoff

# Limit PyTorch/OpenMP threads to prevent memory bloat on small containers
os.environ.setdefault("OMP_NUM_THREADS", "2")
os.environ.setdefault("MKL_NUM_THREADS", "2")
# PyTorch removed for memory efficiency on Railway free tier.
# ML consensus replaced with lightweight technical analysis tools.
# Re-add when upgrading to Railway plan with >= 1GB RAM.

# Semaphore: only one model trains at a time to prevent OOM
_model_semaphore = threading.Semaphore(1)

logging.basicConfig(level=getattr(logging, settings.LOG_LEVEL, logging.INFO))
logger = logging.getLogger("screener")

NON_FATAL_ANALYSIS_ERROR = (
    ValueError,
    KeyError,
    TypeError,
    RuntimeError,
    ZeroDivisionError,
    IndexError,
    FileNotFoundError,
    OSError,
    DataFetchError,
    ModelTrainingError,
    HTTPException,
)
NON_FATAL_PERSISTENCE_ERROR = Exception
ATR_TARGETS = app_cfg.thresholds.atr_targets


@asynccontextmanager
async def _app_lifespan(_app: FastAPI):
    await _startup_bootstrap()
    try:
        # Send startup notification
        try:
            model_status = _check_model_degradation()
            degraded = [k for k, v in model_status.items() if v.get("status") in ("degraded", "below_chance")]
            scheduler_mode = "worker" if os.environ.get("WORKER_SERVICE", "").lower() == "true" else "in-process"
            msg = f"System booted. Models tracked: {len(model_status)}"
            if degraded:
                msg += f", {len(degraded)} degraded ({', '.join(degraded)})"
            tg_send(f"Startup OK — {msg}\nScheduler: {scheduler_mode}")
            logger.info(f"{msg} | scheduler={scheduler_mode}")
        except NON_FATAL_ANALYSIS_ERROR:
            logger.exception("Startup notification failed")
        yield
    finally:
        try:
            from app.services.scheduler import stop_scheduler
            stop_scheduler()
        except NON_FATAL_ANALYSIS_ERROR:
            logger.exception("Scheduler shutdown failed")
        try:
            from app.services.fill_watcher import stop_fill_watcher
            stop_fill_watcher()
        except NON_FATAL_ANALYSIS_ERROR:
            logger.exception("Fill watcher shutdown failed")


# ═══════════════════════════════════════════════════════════════════
# APP CONSTRUCTION: halal_screener is a LIBRARY, not a deployable app.
# The canonical entry point is app/workspace_server.py (railway.json).
# `app` exists ONLY for backward compat with tests (TestClient(hs.app)).
# See F-3 resolution in CODE_REVIEW_DEEPSEEK_2026-06-05.md.
# ═══════════════════════════════════════════════════════════════════

def _build_app(standalone: bool = False) -> FastAPI:
    """Build the FastAPI app instance. Called once at module load for
    backward test compatibility. NOT intended for standalone deployment.

    When standalone=False (imported by tests/workspace_server), lifespan
    is a no-op — no scheduler start/stop. When standalone=True (run via
    `python halal_screener.py`), the real lifespan with scheduler cleanup
    is used.
    """
    async def _noop_lifespan(_app: FastAPI):
        yield  # nothing — scheduler is workspace_server's responsibility

    _lifespan = _app_lifespan if standalone else _noop_lifespan
    return FastAPI(lifespan=_lifespan)

app = _build_app(standalone=False)  # no-op lifespan — workspace_server owns scheduler
from app.core.security import operator_api_key_header, OperatorAPIKey  # noqa: F401


@app.get("/")
async def root():
    return {
        "service": "MizanQuant Halal Screener",
        "version": "17.0.0",
        "docs": "/docs",
        "health": "/health",
        "status": "live",
    }

from app.services import keep_alive
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
    max_age=600,
)

# --- 4.1: Timeout wrapper ---
async def with_timeout(coro, seconds=120):
    try:
        return await asyncio.wait_for(coro, timeout=seconds)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail=f"Request timed out after {seconds}s")

# --- 4.2 + 4.3: Input validation helpers ---
# Extracted to app.api.request_validators (M-E). Re-exported here so the public
# contract `from halal_screener import validate_symbol/validate_date/validate_range`
# (and `hs.validate_*`) is preserved unchanged.
VALID_SYMBOLS = set()  # populated after HALAL_STOCKS is defined

from app.api.request_validators import (  # noqa: E402
    validate_symbol, validate_date, validate_range,
)


def _primary_broker_strategy_id() -> str | None:
    from app.config import STRATEGY_CONFIGS

    return next(iter(STRATEGY_CONFIGS), None)


def _has_alpaca_broker_config() -> bool:
    return bool(
        (settings.ALPACA_API_KEY and settings.ALPACA_SECRET_KEY)
        or (settings.ALPACA_API_KEY_A and settings.ALPACA_SECRET_KEY_A)
        or (settings.ALPACA_API_KEY_B and settings.ALPACA_SECRET_KEY_B)
        or (settings.ALPACA_API_KEY_C and settings.ALPACA_SECRET_KEY_C)
    )

# --- 4.5: Simple rate limiter ---
_rate_limits = defaultdict(list)
_rate_lock = threading.Lock()

def check_rate_limit(endpoint: str, max_concurrent: int = 2):
    """Soft rate limiter — returns True if OK, False if over limit.
    Never raises HTTPException so OpenBB Pro widgets don't break."""
    with _rate_lock:
        _rate_limits[endpoint] = [t for t in _rate_limits[endpoint] if time.time() - t < 60]
        if len(_rate_limits[endpoint]) >= max_concurrent:
            return False  # caller decides what to do
        _rate_limits[endpoint].append(time.time())
        return True

# Sector-level haram exclusions — extracted to app.data.halal_exclusions (M-E)
# and re-exported under the original name. Each surviving stock still goes through
# AAOIFI screening for final halal verification in verify_halal.
from app.data.halal_exclusions import (  # noqa: E402
    HARAM_EXCLUDE as _HARAM_EXCLUDE,
    SP500_DELISTED_HALAL as _SP500_DELISTED_HALAL,
)

# ── HALAL_STOCKS: now imported from universe module (single source of truth) ──
from app.services.universe import (
    HALAL_STOCKS_FALLBACK as HALAL_STOCKS,
    HALAL_STOCKS_BACKTEST_FALLBACK as HALAL_STOCKS_BACKTEST,
)

try:
    from app.services.universe import get_universe_symbols as _get_universe
    _db_symbols = None
    def _universe_symbols():
        global _db_symbols
        if _db_symbols is None:
            try:
                from app.db.database import SessionLocal
                db = SessionLocal()
                try:
                    _db_symbols = _get_universe(db)
                finally:
                    db.close()
            except Exception:
                _db_symbols = []
        return _db_symbols or HALAL_STOCKS
except ImportError:
    def _universe_symbols():
        return HALAL_STOCKS

VALID_SYMBOLS.update(HALAL_STOCKS)
VALID_SYMBOLS.update(_SP500_DELISTED_HALAL)

_SIMPLE_CACHE_TTL = settings.SIMPLE_CACHE_TTL
_cache = {}
_cache_ts = {}

def cache_get(k):
    try:
        from app.services.metrics import metrics
    except Exception:
        metrics = None
    if time.time() - _cache_ts.get(k, 0) < _SIMPLE_CACHE_TTL:
        if metrics:
            metrics.cache_hit("simple_cache")
        return _cache.get(k)
    if metrics:
        metrics.cache_miss("simple_cache")
    return None

def cache_set(k, v):
    _cache[k] = v
    _cache_ts[k] = time.time()

# Model cache: stores trained models with TTL (capped at 30 min)
MODEL_CACHE_TTL = min(settings.MODEL_CACHE_TTL, 1800)
MODEL_CACHE_MAX = 20
_model_cache = {}
_model_cache_ts = {}

def model_cache_get(key):
    try:
        from app.services.metrics import metrics
    except Exception:
        metrics = None
    if time.time() - _model_cache_ts.get(key, 0) < MODEL_CACHE_TTL:
        if metrics:
            metrics.cache_hit("model_cache")
        return _model_cache.get(key)
    if metrics:
        metrics.cache_miss("model_cache")
    # Expired — remove it
    _model_cache.pop(key, None)
    _model_cache_ts.pop(key, None)
    return None

def model_cache_set(key, model_data):
    # Evict oldest if at capacity
    if len(_model_cache) >= MODEL_CACHE_MAX:
        oldest_key = min(_model_cache_ts, key=_model_cache_ts.get)
        _model_cache.pop(oldest_key, None)
        _model_cache_ts.pop(oldest_key, None)
        gc.collect()
    _model_cache[key] = model_data
    _model_cache_ts[key] = time.time()

def _flush_all_caches():
    """Emergency memory cleanup — flush all caches."""
    _model_cache.clear()
    _model_cache_ts.clear()
    _cache.clear()
    _cache_ts.clear()
    gc.collect()
    logger.info("All caches flushed for memory relief")

def fetch_yf(symbol, period="2y", start=None, end=None):
    """Fetch market data via Alpaca (primary) or yfinance (fallback)."""
    return fetch_market_data(symbol, period=period, start=start, end=end)


# ---------------------------------------------------------------------------
# Halal verification gate — blocks unverified / haram stocks from trading
# ---------------------------------------------------------------------------
# Runtime cache of halal verification results.
_VERIFIED_HARAM = set()  # symbols confirmed haram via FMP
_VERIFIED_HALAL = set()  # symbols confirmed halal via FMP or curated list
_CURATED_SET = None      # lazy-built set for O(1) lookups
_RATIO_EXCLUDED = None    # names the monthly financial re-screen flagged (debt/liquidity > 33%)

def _get_curated_set():
    global _CURATED_SET
    if _CURATED_SET is None:
        _CURATED_SET = set(HALAL_STOCKS)
    return _CURATED_SET


def _get_ratio_excluded() -> set:
    """Names pruned by the AAOIFI financial re-screen (data/halal_excluded_by_ratio.json).

    Curation only proves the SECTOR screen; a curated name can still breach the
    debt/liquidity ratio (e.g. CLX ~39% > 33%). These are blocked here so the
    curated fast-path can never re-pass a financially non-compliant name.
    """
    global _RATIO_EXCLUDED
    if _RATIO_EXCLUDED is None:
        _RATIO_EXCLUDED = set()
        try:
            import json
            from pathlib import Path
            p = Path(__file__).resolve().parent / "data" / "halal_excluded_by_ratio.json"
            if p.exists():
                data = json.loads(p.read_text())
                _RATIO_EXCLUDED = {e["symbol"].upper() for e in data.get("excluded", []) if e.get("symbol")}
        except Exception as e:
            logger.debug("ratio-excluded load failed: %s", e)
    return _RATIO_EXCLUDED

def verify_halal(symbol: str) -> tuple[bool, str]:
    """Check if a symbol is verified halal. Returns (is_halal, reason).

    Priority:
    1. If in _HARAM_EXCLUDE → blocked (sector-level exclusion)
    2. If already verified this session → use cached result
    3. If in curated HALAL_STOCKS list → allowed (already sector-filtered)
    4. If FMP data available → run live AAOIFI screening
    5. If not in curated list AND FMP unavailable → blocked
    """
    sym = symbol.upper().strip()

    # 1. Sector-level exclusion (instant reject)
    if sym in _HARAM_EXCLUDE:
        return False, "Excluded sector (banks/insurance/alcohol/gambling/weapons/utilities/REITs)"

    # 1b. AAOIFI financial-ratio exclusion (from the monthly re-screen). Curation
    # only proves the SECTOR screen, so a curated name that breached the debt or
    # liquidity ratio (e.g. CLX ~39% > 33%) must still be blocked here.
    if sym in _get_ratio_excluded():
        return False, "AAOIFI financial ratio breach (debt/liquidity > 33%)"

    # 2. Session cache — fast path
    if sym in _VERIFIED_HALAL:
        return True, "Verified halal"
    if sym in _VERIFIED_HARAM:
        return False, "Verified haram (AAOIFI screening failed)"

    # 3. Curated list — stocks already passed sector exclusion
    # This is the fast path that avoids FMP API calls for known stocks
    curated = _get_curated_set()
    if sym in curated:
        _VERIFIED_HALAL.add(sym)
        return True, "Halal (curated S&P 500 list, sector-verified)"

    # 4. Not in curated list — must verify via FMP AAOIFI screening
    try:
        result = get_halal_status(sym)
        if result is not None:
            if result.get("is_halal"):
                _VERIFIED_HALAL.add(sym)
                return True, "Verified halal (AAOIFI)"
            else:
                _VERIFIED_HARAM.add(sym)
                reasons = []
                if not result.get("debt_pass", True):
                    reasons.append(f"debt {result.get('debt_ratio', '?')}% > 33%")
                if not result.get("interest_pass", True):
                    reasons.append(f"interest {result.get('interest_ratio', '?')}% > 5%")
                if not result.get("haram_pass", True):
                    reasons.append("haram sector/industry")
                if not result.get("liquidity_pass", True):
                    reasons.append(f"liquidity {result.get('liquidity_ratio', '?')}% > 33%")
                return False, f"Haram: {', '.join(reasons)}" if reasons else "Verified haram"
        else:
            # FMP unavailable AND not in curated list → block
            logger.warning(f"Halal gate: {sym} BLOCKED — not in curated list, no FMP data")
            return False, "Cannot verify — not in curated list and no financial data"
    except NON_FATAL_ANALYSIS_ERROR as e:
        logger.error(f"Halal verification error for {sym}: {e}")
        return False, f"Verification error: {e}"

# Technical analysis functions (ema, rsi, macd, atr, calc_metrics, safe_scale,
# walk_forward_split, get_score, prepare_sequences) imported from app.services.technical

def analyze(symbol, df):
    try:
        close = df["close"]
        price = float(close.iloc[-1])
        ema21 = float(ema(close, 21).iloc[-1])
        sma50 = float(close.tail(50).mean())
        sma200 = float(close.tail(200).mean())
        rsi_s = rsi(close)
        rsi_val = float(rsi_s.iloc[-1])
        rsi_prv = float(rsi_s.iloc[-2])
        _, _, hist_s = macd(close)
        hist_now = float(hist_s.iloc[-1])
        hist_prev = float(hist_s.iloc[-2])
        atr_val = float(atr(df).iloc[-1])
        avg_vol = float(df["volume"].iloc[-20:].mean())
        vol_rat = float(df["volume"].iloc[-1] / avg_vol) if avg_vol else 0
        # Penny / illiquidity floor — exclude low-priced or thin names so a
        # $1.53 pump can never become a STRONG BUY swing signal.
        _min_price = float(getattr(settings, "MIN_PRICE", 5.0))
        _min_adv_m = float(getattr(settings, "MIN_ADV_DOLLAR_M", 5.0))
        if price < _min_price:
            return None
        if (avg_vol * price / 1_000_000) < _min_adv_m:
            return None
        support = float(df["low"].iloc[-10:].min())
        score = 0
        signals = []
        if price > ema21: score += 25; signals.append("Trend OK")
        if 40 <= rsi_val <= 60 and rsi_val > rsi_prv: score += 20; signals.append(f"RSI {rsi_val:.1f}")
        elif 35 <= rsi_val <= 65 and rsi_val > rsi_prv: score += 10; signals.append(f"RSI {rsi_val:.1f}")
        if hist_now > 0 and hist_prev <= 0: score += 20; signals.append("MACD crossover")
        elif hist_now > 0 and hist_now > hist_prev: score += 10; signals.append("MACD expanding")
        if vol_rat >= 1.5: score += 15; signals.append(f"Vol {vol_rat:.1f}x")
        elif vol_rat >= 1.2: score += 8; signals.append(f"Vol {vol_rat:.1f}x")
        atr_pct = atr_val / price * 100
        if 1.0 <= atr_pct <= 4.0: score += 10; signals.append(f"ATR {atr_pct:.1f}%")
        dist = (price - support) / support * 100
        if dist <= 2.0: score += 10; signals.append(f"Sup {dist:.1f}%")
        if score >= 75: sig = "STRONG BUY"
        elif score >= 55: sig = "BUY"
        elif score >= 35: sig = "WATCH"
        else: sig = "NO TRADE"
        pos = int((settings.RISK_CAPITAL * (settings.RISK_PCT / 100)) / atr_val) if atr_val > 0 else 0
        chg1w = float(((price - close.iloc[-5]) / close.iloc[-5]) * 100) if len(close) >= 5 else 0
        chg1m = float(((price - close.iloc[-21]) / close.iloc[-21]) * 100) if len(close) >= 21 else 0
        chg3m = float(((price - close.iloc[-63]) / close.iloc[-63]) * 100) if len(close) >= 63 else 0
        return {"symbol": symbol, "price": round(price, 2), "ema21": round(ema21, 2),
                "sma50": round(sma50, 2), "sma200": round(sma200, 2), "rsi": round(rsi_val, 1),
                "rsi_trend": "Rising" if rsi_val > rsi_prv else "Falling",
                "atr_pct": round(atr_pct, 2), "volume_ratio": round(vol_rat, 2),
                "chg_1w": round(chg1w, 2), "chg_1m": round(chg1m, 2), "chg_3m": round(chg3m, 2),
                "swing_score": round(score, 1), "swing_signal": sig, "signals": ", ".join(signals),
                "stop_loss": round(price - atr_val, 2), "take_profit": round(price + (2 * atr_val), 2),
                "position_size": pos, "halal": "Yes"}
    except NON_FATAL_ANALYSIS_ERROR as e:
        logger.error(f"{symbol}: {e}")
        return None

def _analyze_one(symbol):
    # Halal gate — skip stocks that fail AAOIFI verification
    is_halal, reason = verify_halal(symbol)
    if not is_halal:
        return None
    df = fetch_yf(symbol)
    if df is None: return None
    return analyze(symbol, df)

def run_screener():
    from app.services.metrics import metrics as _m
    cached = cache_get("all")
    if cached: return cached
    _m.incr("run_screener_calls")
    results = []
    symbols = _universe_symbols()
    with ThreadPoolExecutor(max_workers=settings.SCREENER_WORKERS) as pool:
        futures = {pool.submit(_analyze_one, s): s for s in symbols}
        for f in as_completed(futures):
            r = f.result()
            if r: results.append(r)
    results.sort(key=lambda x: x["swing_score"], reverse=True)

    # Shadow conditioning: attach rotation_quadrant / sector_rank /
    # context_adjusted_score from the MarketContextBundle (additive — does NOT
    # change swing_score ordering unless CONTEXT_CONDITIONING_LIVE is enabled).
    try:
        from app.services.market_context_bundle import apply_context_shadow, get_symbol_sectors
        from app.config import settings as _cfg
        syms = [r["symbol"] for r in results if isinstance(r, dict) and r.get("symbol")]
        sym_sectors = get_symbol_sectors(syms)
        apply_context_shadow(results, symbol_sectors=sym_sectors)
        # Wire the sector name onto each row so /buys (and the Overview scanner
        # table) can display it — display-only; does NOT affect swing_score.
        for r in results:
            if isinstance(r, dict) and r.get("symbol") and not r.get("sector"):
                r["sector"] = sym_sectors.get(str(r["symbol"]).upper(), "")
        if getattr(_cfg, "CONTEXT_CONDITIONING_LIVE", False):
            results.sort(key=lambda x: x.get("context_adjusted_score", x["swing_score"]), reverse=True)
    except Exception as _ctx_exc:
        logger.debug("screener context shadow skipped: %s", _ctx_exc)

    cache_set("all", results)
    return results

# Round-trip transaction cost — centralised in execution_costs.py (Phase 0).
# Import here so any code in this module that references the names still works.
from app.services.execution_costs import apply_costs as _apply_costs, BACKTEST_COST_BPS


def run_backtest(symbol, start_date, end_date, portfolio, risk_pct, hold_days):
    """Walk-forward backtest with NO same-bar look-ahead and transaction costs.

    Key invariants (Chan Ch.2):
      - Signals computed using close[i] are acted on at OPEN of bar i+1.
      - All fills pay BACKTEST_COST_BPS in slippage/commission per leg.
      - Stop-loss / take-profit checked using next bar's high/low after entry.
    """
    try:
        df = fetch_yf(symbol, start=start_date, end=end_date)
        if df is None: return [{"Error": f"No data for {symbol}"}]
        df["ema21"] = ema(df["close"], 21)
        df["rsi"] = rsi(df["close"])
        df["rsi_prev"] = df["rsi"].shift(1)
        _, _, h = macd(df["close"])
        df["hist"] = h
        df["hist_prev"] = h.shift(1)
        df["atr_v"] = atr(df)
        df["vol_ratio"] = df["volume"] / df["volume"].rolling(20).mean()
        df["support"] = df["low"].rolling(10).min()
        df = df.dropna().reset_index(drop=True)
        df["score"] = score_series(df)
        trades = []
        port = portfolio
        in_trade = False
        entry_idx = entry_price = sl = tp = pos_size = 0
        trade_returns = []
        n = len(df)
        # Stop one bar early: signal at i-1 needs bar i to fill.
        for i in range(n):
            row = df.iloc[i]
            if in_trade:
                days = i - entry_idx
                if float(row["low"]) <= sl:
                    exit_price = _apply_costs(sl, "sell")
                    pnl = (exit_price - entry_price) * pos_size
                    port += pnl
                    ret = (exit_price - entry_price) / entry_price
                    trade_returns.append(ret)
                    trades.append({"Entry Date": str(df.iloc[entry_idx]["date"])[:10], "Exit Date": str(row["date"])[:10], "Entry": round(entry_price, 2), "Exit": round(exit_price, 2), "Result": "Stop Loss", "PnL": round(pnl, 2), "Portfolio": round(port, 2), "Days": days, "Score": int(df.iloc[entry_idx]["score"])})
                    in_trade = False
                elif float(row["high"]) >= tp:
                    exit_price = _apply_costs(tp, "sell")
                    pnl = (exit_price - entry_price) * pos_size
                    port += pnl
                    ret = (exit_price - entry_price) / entry_price
                    trade_returns.append(ret)
                    trades.append({"Entry Date": str(df.iloc[entry_idx]["date"])[:10], "Exit Date": str(row["date"])[:10], "Entry": round(entry_price, 2), "Exit": round(exit_price, 2), "Result": "Take Profit", "PnL": round(pnl, 2), "Portfolio": round(port, 2), "Days": days, "Score": int(df.iloc[entry_idx]["score"])})
                    in_trade = False
                elif days >= hold_days:
                    exit_price = _apply_costs(float(row["close"]), "sell")
                    pnl = (exit_price - entry_price) * pos_size
                    port += pnl
                    ret = (exit_price - entry_price) / entry_price
                    trade_returns.append(ret)
                    trades.append({"Entry Date": str(df.iloc[entry_idx]["date"])[:10], "Exit Date": str(row["date"])[:10], "Entry": round(entry_price, 2), "Exit": round(exit_price, 2), "Result": "Time Stop", "PnL": round(pnl, 2), "Portfolio": round(port, 2), "Days": days, "Score": int(df.iloc[entry_idx]["score"])})
                    in_trade = False
            # Signal evaluated at bar i; entry happens at OPEN of bar i+1.
            # This eliminates the same-bar look-ahead (Chan Ch.2).
            if not in_trade and i + 1 < n and float(df.iloc[i]["score"]) >= 55:
                next_open_raw = float(df.iloc[i + 1].get("open", df.iloc[i + 1]["close"]))
                entry_price = _apply_costs(next_open_raw, "buy")
                atr_v = float(df.iloc[i]["atr_v"])
                sl = entry_price - atr_v
                tp = entry_price + (2 * atr_v)
                risk = entry_price - sl
                pos_size = int((port * (risk_pct / 100)) / risk) if risk > 0 else 0
                if pos_size > 0:
                    in_trade = True
                    entry_idx = i + 1  # entry bar is the NEXT bar
        if not trades: return [{"Symbol": symbol, "Message": "No trades found"}]
        # Classify every trade by PnL, not by exit reason
        for t in trades:
            if t["PnL"] > 0:
                t["Classification"] = "WIN"
            elif t["PnL"] < 0:
                t["Classification"] = "LOSS"
            else:
                t["Classification"] = "BREAKEVEN"
        wins = [t for t in trades if t["Classification"] == "WIN"]
        losses = [t for t in trades if t["Classification"] == "LOSS"]
        ann = max(252 // max(hold_days, 1), 12)
        metrics = calc_metrics(trade_returns, annualization=ann)
        # Chan Ch.2 / Bailey & Lopez de Prado: Deflated Sharpe penalises trial count.
        from app.services.backtest_qc import deflated_sharpe, permutation_pvalue
        dsr = deflated_sharpe(trade_returns, n_trials=1, annualization=ann)
        pval = permutation_pvalue(trade_returns, n_perm=200)
        total_closed = len(trades)
        summary = [{"Symbol": symbol, "Period": f"{start_date} to {end_date}",
                    "Total Trades": total_closed, "Winners": len(wins), "Losers": len(losses),
                    "Win Rate %": round(len(wins) / total_closed * 100, 1) if total_closed else 0,
                    "Total PnL": round(sum(t["PnL"] for t in trades), 2),
                    "Final Portfolio": round(port, 2),
                    "Return %": round((port - portfolio) / portfolio * 100, 2),
                    "Avg Win": round(sum(t["PnL"] for t in wins) / len(wins), 2) if wins else 0,
                    "Avg Loss": round(sum(t["PnL"] for t in losses) / len(losses), 2) if losses else 0,
                    "Deflated Sharpe": round(dsr, 3),
                    "Permutation p-value": round(pval, 3),
                    **metrics}]
        return summary + trades
    except NON_FATAL_ANALYSIS_ERROR as e:
        return [{"Error": str(e)}]

def run_monte_carlo(symbol, days, simulations, df=None):
    try:
        from openbb_forecast.simulation.monte_carlo import MonteCarloSimulator
        if df is None:
            df = fetch_yf(symbol)
        if df is None: return [{"Error": f"No data for {symbol}"}]
        prices = np.array(df["close"].values, dtype=np.float64).flatten()
        prices = prices[~np.isnan(prices)]
        mc = MonteCarloSimulator(seed=42)
        result = mc.simulate(prices, n_simulations=simulations, forecast_days=days)
        s = result["summary"]
        summary = [{"Symbol": symbol.upper(), "Current Price": round(float(s["initial_price"]), 2),
                    "Expected Price": round(float(s["expected_terminal_price"]), 2),
                    "Prob Profit %": round(float(s["prob_profit"]) * 100, 1),
                    "Annual Vol %": round(float(s["annualized_volatility"]) * 100, 1),
                    "Annual Drift %": round(float(s["annualized_drift"]) * 100, 1),
                    "VaR 95%": round(float(s["terminal_var_95"]) * 100, 1),
                    "CVaR 95%": round(float(s["terminal_cvar_95"]) * 100, 1),
                    "Simulations": simulations, "Forecast Days": days}]
        day_stats = []
        for d in result["day_stats"][::5]:
            day_stats.append({"Day": d["day"], "Mean Price": round(d["mean_price"], 2),
                              "Median": round(d["median_price"], 2),
                              "P5 Bear": round(d["percentile_5"], 2),
                              "P25": round(d["percentile_25"], 2),
                              "P75": round(d["percentile_75"], 2),
                              "P95 Bull": round(d["percentile_95"], 2),
                              "VaR%": round(d["var_95"] * 100, 2)})
        return summary + day_stats
    except NON_FATAL_ANALYSIS_ERROR as e:
        return [{"Error": str(e)}]

def _run_with_memory_guard(func, *args, **kwargs):
    """Run a model function with semaphore to prevent OOM and gc after."""
    _model_semaphore.acquire()
    try:
        return func(*args, **kwargs)
    finally:
        gc.collect()
        _model_semaphore.release()


try:
    from openbb_forecast.models.factory import create_model as _create_forecast_model, get_model_class, get_model_suffix, MODEL_NAMES as ALL_MODEL_NAMES
    from openbb_forecast.agents.factory import create_agent as _create_rl_agent, AGENT_NAMES as ALL_AGENT_NAMES
    _HAS_OPENBB = True
except Exception:
    logger.warning("openbb_forecast imports failed (torch may be unavailable); forecast/RL endpoints disabled")
    _HAS_OPENBB = False
    _create_forecast_model = None
    _create_rl_agent = None
    ALL_MODEL_NAMES = []
    ALL_AGENT_NAMES = []
    get_model_class = None
    get_model_suffix = None

_PERSISTED_MODELS = {}
_PERSISTED_MODEL_ATTEMPTS = set()


def _build_model_from_state(model_name, state_dict, meta):
    """Reconstruct a proper PyTorch model from persisted state dict."""
    try:
        import torch.nn as nn
        import numpy as np
        
        actual_state = state_dict.get('state_dict', state_dict)
        config = state_dict.get('config', meta)
        input_size = state_dict.get('input_size', meta.get('input_size', 14))
        output_size = state_dict.get('output_size', meta.get('output_size', 1))
        device = torch.device('cpu')
        
        if model_name == 'transformer':
            d_model = config.get('d_model', 64)
            n_heads = config.get('n_heads', 4)
            num_layers = config.get('num_layers', 2)
            dim_feedforward = config.get('dim_feedforward', 128)
            dropout = config.get('dropout', 0.2)
            
            class PositionalEncoding(nn.Module):
                def __init__(self, d_model, max_len=5000, dropout=0.1):
                    super().__init__()
                    self.dropout = nn.Dropout(p=dropout)
                    pe = torch.zeros(max_len, d_model)
                    position = torch.arange(0, max_len, dtype=torch.float).unsqueeze(1)
                    div_term = torch.exp(torch.arange(0, d_model, 2).float() * (-np.log(10000.0) / d_model))
                    pe[:, 0::2] = torch.sin(position * div_term)
                    pe[:, 1::2] = torch.cos(position * div_term)
                    pe = pe.unsqueeze(0)
                    self.register_buffer('pe', pe)
                def forward(self, x):
                    return self.dropout(x + self.pe[:, :x.size(1), :])
            
            class TransformerModel(nn.Module):
                def __init__(self):
                    super().__init__()
                    self.input_projection = nn.Linear(input_size, d_model)
                    self.pos_encoder = PositionalEncoding(d_model, dropout=dropout)
                    encoder_layer = nn.TransformerEncoderLayer(
                        d_model=d_model, nhead=n_heads, dim_feedforward=dim_feedforward,
                        dropout=dropout, batch_first=True)
                    self.transformer_encoder = nn.TransformerEncoder(encoder_layer, num_layers=num_layers)
                    self.output_projection = nn.Linear(d_model, output_size)
                def forward(self, x):
                    x = self.input_projection(x)
                    x = self.pos_encoder(x)
                    x = self.transformer_encoder(x)
                    x = x[:, -1, :]
                    return self.output_projection(x)
            
            model = TransformerModel().to(device)
            model.load_state_dict(actual_state, strict=False)
            model.eval()
            
            class Wrapper:
                def __init__(self, m, dev):
                    self._model = m
                    self._device = dev
                def predict(self, X):
                    import numpy as np
                    if isinstance(X, np.ndarray):
                        X = torch.from_numpy(X.astype(np.float32))
                    X = X.to(self._device)
                    with torch.no_grad():
                        preds = self._model(X)
                    return preds.cpu().numpy()
            
            return Wrapper(model, device)
        
        elif model_name == 'lstm':
            hidden_size = config.get('hidden_size', 64)
            num_layers = config.get('num_layers', 2)
            dropout = config.get('dropout', 0.3)
            
            class LSTMModel(nn.Module):
                def __init__(self):
                    super().__init__()
                    self.lstm = nn.LSTM(input_size=input_size, hidden_size=hidden_size,
                                        num_layers=num_layers, dropout=dropout, batch_first=True)
                    self.fc = nn.Linear(hidden_size, output_size)
                def forward(self, x):
                    out, _ = self.lstm(x)
                    return self.fc(out[:, -1, :])
            
            model = LSTMModel().to(device)
            model.load_state_dict(actual_state, strict=False)
            model.eval()
            
            class Wrapper:
                def __init__(self, m, dev):
                    self._model = m
                    self._device = dev
                def predict(self, X):
                    import numpy as np
                    if isinstance(X, np.ndarray):
                        X = torch.from_numpy(X.astype(np.float32))
                    X = X.to(self._device)
                    with torch.no_grad():
                        preds = self._model(X)
                    return preds.cpu().numpy()
            
            return Wrapper(model, device)
        
        # Generic: try loading via model class if available
        try:
            from openbb_forecast.models.factory import get_model_class
            model_cls = get_model_class(model_name)
            model = model_cls.__new__(model_cls)
            model.load_state_dict(actual_state, strict=False)
            model.eval()
            class Wrapper:
                def __init__(self, m, dev):
                    self._model = m
                    self._device = dev
                def predict(self, X):
                    import numpy as np
                    if isinstance(X, np.ndarray):
                        X = torch.from_numpy(X.astype(np.float32))
                    X = X.to(self._device)
                    with torch.no_grad():
                        preds = self._model(X)
                    return preds.cpu().numpy()
            return Wrapper(model, device)
        except Exception:
            pass
        
        logger.warning(f"_build_model_from_state: unknown model '{model_name}'")
        return None
        
    except Exception as e:
        logger.warning(f"_build_model_from_state failed for {model_name}: {e}")
        return None


_MODEL_LOAD_ERRORS = {}  # {model_name: error_count} for backoff
_MAX_MODEL_LOAD_ERRORS = 3  # retry up to 3 times before giving up

def _load_persisted_model(model_name):
    """Load a persisted model. Retries up to _MAX_MODEL_LOAD_ERRORS times on failure."""
    # If already successfully loaded, return cached model
    if model_name in _PERSISTED_MODELS:
        model = _PERSISTED_MODELS[model_name]
        if model is not None:
            return model
    
    # Check backoff: don't retry infinitely
    errors = _MODEL_LOAD_ERRORS.get(model_name, 0)
    if errors >= _MAX_MODEL_LOAD_ERRORS:
        logger.warning(f"Model '{model_name}' failed {errors} times — giving up until restart")
        return None
    
    _MODEL_LOAD_ERRORS[model_name] = errors + 1
    attempt = errors + 1
    
    # First try OpenBB if available
    if _HAS_OPENBB and app_cfg.thresholds.ml_tools_enabled.get(model_name, True):
        try:
            from openbb_forecast.models.persistence import resolve_latest
            suffix = get_model_suffix(model_name)
            model_cls = get_model_class(model_name)
            latest = resolve_latest(model_name, suffix)
            _PERSISTED_MODELS[model_name] = model_cls.load(latest)
            _MODEL_LOAD_ERRORS[model_name] = 0  # reset on success
            logger.info(f"Loaded persisted {model_name} model from {latest}")
            return _PERSISTED_MODELS[model_name]
        except NON_FATAL_ANALYSIS_ERROR as e:
            logger.warning(f"OpenBB {model_name} model failed (attempt {attempt}/{_MAX_MODEL_LOAD_ERRORS}): {e}")
            if attempt >= _MAX_MODEL_LOAD_ERRORS:
                logger.warning(f"OpenBB failed {_MAX_MODEL_LOAD_ERRORS} times for {model_name} — trying PyTorch fallback...")
    
    # Fallback: try to load .pt models directly with PyTorch
    try:
        import torch
        import os
        
        model_dir = os.path.join(os.path.dirname(__file__), "models", model_name)
        latest_file = os.path.join(model_dir, "latest.txt")
        
        if os.path.exists(latest_file):
            with open(latest_file, 'r') as f:
                version = f.read().strip()
            
            suffix = ".pt" if model_name != "ensemble" else ".pkl"
            model_path = os.path.join(model_dir, f"{version}{suffix}")
            meta_path = os.path.join(model_dir, f"{version}.meta.json")
            
            if os.path.exists(model_path) and os.path.exists(meta_path):
                device = torch.device('cpu')
                import json
                with open(meta_path, 'r') as f:
                    meta = json.load(f)
                
                # For ensemble, load pickle; for others, load state dict
                if suffix == ".pkl":
                    import pickle
                    with open(model_path, 'rb') as f:
                        model = pickle.load(f)
                    if hasattr(model, 'predict'):
                        _PERSISTED_MODELS[model_name] = model
                        _MODEL_LOAD_ERRORS[model_name] = 0
                        logger.info(f"Loaded persisted {model_name} model from {model_path}")
                        return model
                else:
                    state_dict = torch.load(model_path, map_location=device, weights_only=True)
                    model = _build_model_from_state(model_name, state_dict, meta)
                    if model is not None:
                        _PERSISTED_MODELS[model_name] = model
                        _MODEL_LOAD_ERRORS[model_name] = 0
                        logger.info(f"Loaded persisted {model_name} model using PyTorch fallback from {model_path}")
                        return model
                
    except Exception as e:
        logger.warning(f"PyTorch fallback for {model_name} failed (attempt {attempt}/{_MAX_MODEL_LOAD_ERRORS}): {e}")
    
    # All loading methods failed for this attempt
    if attempt >= _MAX_MODEL_LOAD_ERRORS:
        logger.warning(f"All loading methods failed for {model_name} model after {_MAX_MODEL_LOAD_ERRORS} attempts")
        _PERSISTED_MODELS[model_name] = None
    return None


def _predict_persisted_model(model_name, X_test):
    model = _load_persisted_model(model_name)
    if model is None:
        return None
    try:
        return model.predict(X_test[-1:])
    except NON_FATAL_ANALYSIS_ERROR as e:
        logger.warning(f"Persisted {model_name} prediction failed: {e}")
        return None


def _clear_model_cache(model_name=None):
    """Clear cached model(s) to force reload on next call."""
    if model_name:
        _PERSISTED_MODELS.pop(model_name, None)
        _MODEL_LOAD_ERRORS.pop(model_name, None)
    else:
        _PERSISTED_MODELS.clear()
        _MODEL_LOAD_ERRORS.clear()
    logger.info(f"Model cache cleared for: {model_name or 'ALL'}")


# _preload_persisted_models()  # lazy-load on first use to save RAM (~500 MB on Railway)


# ── Multi-feature sequence builder (mirrors train_models.py) ──────────────
_FEATURE_COLS = [
    "returns", "volatility_5", "volatility_10", "volatility_20",
    "momentum_5", "momentum_10", "momentum_20",
    "rsi_14", "macd", "macd_signal", "volume_ratio",
    "high_low_range", "open_close_range",
]


def _build_feature_sequences(df, seq_len, horizon):
    """Build (N, seq_len, 14) sequences matching training pipeline.

    Uses create_features() → 14 features → SafeScaler → create_sequences.
    Returns X_train, y_train, X_test, y_test, mean_p, std_p (for inverse transform).
    """
    from openbb_forecast.data.preprocessing import create_features, SafeScaler, create_sequences

    feat_df = create_features(df)
    close_col = feat_df["close"].values.reshape(-1, 1)
    feat_cols = [c for c in _FEATURE_COLS if c in feat_df.columns]
    features = feat_df[feat_cols].values
    feature_matrix = np.concatenate([close_col, features], axis=1)

    valid = ~np.isnan(feature_matrix).any(axis=1)
    feature_matrix = feature_matrix[valid]
    close_prices = close_col[valid].ravel()

    split = int(len(feature_matrix) * 0.8)
    train_fm = feature_matrix[:split]
    train_close = close_prices[:split]

    feature_scaler = SafeScaler(method="standard").fit(train_fm)
    price_scaler = SafeScaler(method="standard").fit(train_close.reshape(-1, 1))

    scaled = feature_scaler.transform(feature_matrix)
    X, y_full = create_sequences(scaled, sequence_length=seq_len, forecast_horizon=horizon)
    y = y_full[:, :, 0]  # target = close price (column 0)

    split_seq = int(len(X) * 0.8)
    X_train, X_test = X[:split_seq], X[split_seq:]
    y_train, y_test = y[:split_seq], y[split_seq:]

    # Price scaling params for inverse transform
    mean_p = float(price_scaler._mean[0])
    std_p = float(price_scaler._std[0])

    return X_train, y_train, X_test, y_test, mean_p, std_p


def run_lstm(symbol, horizon, df=None):
    return _run_with_memory_guard(_run_lstm_inner, symbol, horizon, df=df)

def _run_lstm_inner(symbol, horizon, df=None):
    try:
        if df is None:
            df = fetch_yf(symbol)
        if df is None: return [{"Error": f"No data for {symbol}"}]
        SEQ_LEN = 30
        X_train, y_train, X_test, y_test, mean_p, std_p = _build_feature_sequences(df, SEQ_LEN, horizon)

        preds = _predict_persisted_model("lstm", X_test)
        if preds is None:
            return [{"Error": "Persisted LSTM model unavailable"}]

        model_label = "LSTM Persisted"
        fold_scores = []
        pred_prices = preds[0] * std_p + mean_p
        last_price = float(df["close"].iloc[-1])
        avg_mse = round(float(np.mean(fold_scores)), 4) if fold_scores else 0.0
        summary = [{"Symbol": symbol.upper(), "Current Price": round(last_price, 2),
                    "Model": model_label, "Folds": len(fold_scores),
                    "Avg Fold MSE": avg_mse,
                    "Forecast Horizon": horizon, "Training Samples": len(X_train)}]
        forecasts = []
        for i, p in enumerate(pred_prices):
            change = ((float(p) - last_price) / last_price) * 100
            forecasts.append({"Day": i+1, "Predicted Price": round(float(p), 2),
                              "Change %": round(change, 2),
                              "Signal": "BUY" if change > 1 else "SELL" if change < -1 else "HOLD"})
        return summary + forecasts
    except NON_FATAL_ANALYSIS_ERROR as e:
        return [{"Error": str(e)}]

def run_transformer(symbol, horizon, df=None):
    return _run_with_memory_guard(_run_transformer_inner, symbol, horizon, df=df)

def _run_transformer_inner(symbol, horizon, df=None):
    try:
        if df is None:
            df = fetch_yf(symbol)
        if df is None: return [{"Error": f"No data for {symbol}"}]
        SEQ_LEN = 30
        X_train, y_train, X_test, y_test, mean_p, std_p = _build_feature_sequences(df, SEQ_LEN, horizon)

        preds = _predict_persisted_model("transformer", X_test)
        if preds is None:
            return [{"Error": "Persisted Transformer model unavailable"}]

        model_label = "Transformer Persisted"
        fold_scores = []
        pred_prices = preds[0] * std_p + mean_p
        last_price = float(df["close"].iloc[-1])
        avg_mse = round(float(np.mean(fold_scores)), 4) if fold_scores else 0.0
        summary = [{"Symbol": symbol.upper(), "Current Price": round(last_price, 2),
                    "Model": model_label, "Folds": len(fold_scores),
                    "Avg Fold MSE": avg_mse,
                    "Forecast Horizon": horizon}]
        forecasts = []
        for i, p in enumerate(pred_prices):
            change = ((float(p) - last_price) / last_price) * 100
            forecasts.append({"Day": i+1, "Predicted Price": round(float(p), 2),
                              "Change %": round(change, 2),
                              "Signal": "BUY" if change > 1 else "SELL" if change < -1 else "HOLD"})
        return summary + forecasts
    except NON_FATAL_ANALYSIS_ERROR as e:
        return [{"Error": str(e)}]

def run_ensemble(symbol, horizon, df=None):
    return _run_with_memory_guard(_run_ensemble_inner, symbol, horizon, df=df)

def _run_ensemble_inner(symbol, horizon, df=None):
    try:
        if df is None:
            df = fetch_yf(symbol)
        if df is None: return [{"Error": f"No data for {symbol}"}]
        SEQ_LEN = 30
        X_train, y_train, X_test, y_test, mean_p, std_p = _build_feature_sequences(df, SEQ_LEN, horizon)

        preds = _predict_persisted_model("ensemble", X_test)
        if preds is None:
            return [{"Error": "Persisted Ensemble model unavailable"}]

        model_label = "Stacking Ensemble Persisted"
        pred_prices = preds[0] * std_p + mean_p
        last_price = float(df["close"].iloc[-1])
        summary = [{"Symbol": symbol.upper(), "Current Price": round(last_price, 2),
                    "Model": model_label, "Forecast Horizon": horizon}]
        forecasts = []
        for i, p in enumerate(pred_prices):
            change = ((float(p) - last_price) / last_price) * 100
            forecasts.append({"Day": i+1, "Predicted Price": round(float(p), 2),
                              "Change %": round(change, 2),
                              "Signal": "BUY" if change > 1 else "SELL" if change < -1 else "HOLD"})
        return summary + forecasts
    except NON_FATAL_ANALYSIS_ERROR as e:
        return [{"Error": str(e)}]

def run_dqn(symbol, episodes, df=None):
    if df is None:
        pretrained_key = f"pretrained_dqn|symbol={symbol.upper()}"
        cached = db_cache_get(pretrained_key)
        if cached is not None:
            return cached
    return _run_with_memory_guard(_run_dqn_inner, symbol, episodes, df=df)

def _run_dqn_inner(symbol, episodes, df=None):
    try:
        from openbb_forecast.agents.double_dqn import DoubleDQNAgent
        if df is None:
            df = fetch_yf(symbol)
        if df is None: return [{"Error": f"No data for {symbol}"}]
        prices = np.array(df["close"].values, dtype=np.float64).flatten()
        prices = prices[~np.isnan(prices)]

        agent = DoubleDQNAgent(
            state_size=30, action_size=3,
            early_stop_patience=10,
            checkpoint_dir="model_checkpoints",
            checkpoint_name=f"dqn_{symbol.upper()}",
        )
        # Walk-forward evaluation: auto-split 70/30, train on first, test on second
        result = agent.walk_forward_evaluate(
            prices=prices,
            train_ratio=0.7,
            window_size=30,
            episodes=episodes,
            initial_capital=10_000.0,
            commission_bps=10.0,
            slippage_bps=5.0,
            max_position=5,
            stop_loss_pct=0.02,
            max_drawdown_pct=0.10,
        )
        backtest = result.get("backtest_summary", {})
        rewards = (result.get("training_rewards", []) or [])
        if isinstance(rewards, dict):
            rewards = rewards.get("episode_rewards", [])
        train_rewards = rewards

        # Get final signal from test evaluation
        test_results = result.get("test_results", {})
        actions_log = test_results.get("actions_log", [["HOLD"]])
        last_action = actions_log[-1] if actions_log else ["HOLD"]
        signal = last_action[0] if isinstance(last_action, (list, tuple)) else str(last_action)

        last_price = float(prices[-1])
        metrics = {
            "Sharpe Ratio": round(float(backtest.get("sharpe_ratio", 0)), 2),
            "Max Drawdown %": round(float(backtest.get("max_drawdown", 0)), 2),
            "Win Rate %": round(float(backtest.get("win_rate", 0)), 1),
            "Total Return %": round(float(backtest.get("total_return_pct", 0)), 2),
            "Best Reward": round(float(max(train_rewards)) if train_rewards else 0, 2),
            "Avg Reward": round(float(np.mean(train_rewards)) if train_rewards else 0, 2),
            "Early Stopped": bool(result.get("test_results", {}).get("early_stopped", False)),
        }
        summary = [{"Symbol": symbol.upper(), "Current Price": round(last_price, 2),
                    "Model": "Double DQN (Walk-Forward)", "Episodes": episodes,
                    "Signal": signal, **metrics}]
        episode_results = [{"Episode": i+1, "Reward": round(float(r), 2),
                            "Status": "Good" if r > 0 else "Bad"}
                           for i, r in enumerate(train_rewards)]
        # Also include backtest details
        if backtest:
            for key in ["total_trades", "benchmark_return_pct", "alpha_vs_benchmark"]:
                val = backtest.get(key, "N/A")
                if isinstance(val, float):
                    val = round(val, 2)
                summary[0][key] = val
        return summary + episode_results
    except NON_FATAL_ANALYSIS_ERROR as e:
        return [{"Error": str(e)}]

def run_policy_gradient(symbol, episodes, df=None):
    if df is None:
        pretrained_key = f"pretrained_policy_gradient|symbol={symbol.upper()}"
        cached = db_cache_get(pretrained_key)
        if cached is not None:
            return cached
    return _run_with_memory_guard(_run_policy_gradient_inner, symbol, episodes, df=df)

def _run_policy_gradient_inner(symbol, episodes, df=None):
    try:
        from openbb_forecast.agents.policy_gradient import PolicyGradientAgent
        if df is None:
            df = fetch_yf(symbol)
        if df is None: return [{"Error": f"No data for {symbol}"}]
        prices = np.array(df["close"].values, dtype=np.float64).flatten()
        prices = prices[~np.isnan(prices)]

        agent = PolicyGradientAgent(
            state_size=30, action_size=3,
            early_stop_patience=10,
            checkpoint_dir="model_checkpoints",
            checkpoint_name=f"pg_{symbol.upper()}",
        )
        # Walk-forward evaluation
        result = agent.walk_forward_evaluate(
            prices=prices,
            train_ratio=0.7,
            window_size=30,
            episodes=episodes,
            initial_capital=10_000.0,
            commission_bps=10.0,
            slippage_bps=5.0,
            max_position=5,
            stop_loss_pct=0.02,
            max_drawdown_pct=0.10,
        )
        backtest = result.get("backtest_summary", {})
        rewards = (result.get("training_rewards", []) or [])
        if isinstance(rewards, dict):
            rewards = rewards.get("episode_rewards", [])
        train_rewards = rewards

        test_results = result.get("test_results", {})
        actions_log = test_results.get("actions_log", [["HOLD"]])
        last_action = actions_log[-1] if actions_log else ["HOLD"]
        signal = last_action[0] if isinstance(last_action, (list, tuple)) else str(last_action)

        last_price = float(prices[-1])
        metrics = {
            "Sharpe Ratio": round(float(backtest.get("sharpe_ratio", 0)), 2),
            "Max Drawdown %": round(float(backtest.get("max_drawdown", 0)), 2),
            "Win Rate %": round(float(backtest.get("win_rate", 0)), 1),
            "Total Return %": round(float(backtest.get("total_return_pct", 0)), 2),
            "Best Reward": round(float(max(train_rewards)) if train_rewards else 0, 2),
            "Avg Reward": round(float(np.mean(train_rewards)) if train_rewards else 0, 2),
        }
        summary = [{"Symbol": symbol.upper(), "Current Price": round(last_price, 2),
                    "Model": "Policy Gradient (REINFORCE, Walk-Forward)", "Episodes": episodes,
                    "Signal": signal, **metrics}]
        episode_results = [{"Episode": i+1, "Reward": round(float(r), 2),
                            "Status": "Good" if r > 0 else "Bad"}
                           for i, r in enumerate(train_rewards)]
        if backtest:
            for key in ["total_trades", "benchmark_return_pct", "alpha_vs_benchmark"]:
                val = backtest.get(key, "N/A")
                if isinstance(val, float):
                    val = round(val, 2)
                summary[0][key] = val
        return summary + episode_results
    except NON_FATAL_ANALYSIS_ERROR as e:
        return [{"Error": str(e)}]


def score_usx_single(symbol, df):
    """Score a single stock using the 10-point USX system. Returns dict or None."""
    try:
        close = df["close"]
        high  = df["high"]
        low   = df["low"]
        vol   = df["volume"]

        ema9  = float(ema(close, 9).iloc[-2])
        ema21_v = float(ema(close, 21).iloc[-2])
        daily_ema21 = float(ema(close, 21).iloc[-1])
        daily_bullish = float(close.iloc[-1]) > daily_ema21
        rsi_s   = rsi(close)
        rsi_val = float(rsi_s.iloc[-2])
        _, _, hist_s = macd(close)
        hist_now  = float(hist_s.iloc[-2])
        hist_prev = float(hist_s.iloc[-3])
        macd_rising = hist_now > 0 and hist_now > hist_prev
        atr_val = float(atr(df).iloc[-1])

        high_s  = df["high"]
        low_s   = df["low"]
        tr      = pd.concat([high_s - low_s,
                             (high_s - close.shift()).abs(),
                             (low_s  - close.shift()).abs()], axis=1).max(axis=1)
        atr14   = tr.ewm(alpha=1/14, min_periods=14).mean()
        dm_plus  = (high_s.diff()).clip(lower=0)
        dm_minus = (-low_s.diff()).clip(lower=0)
        di_plus  = (dm_plus.ewm(alpha=1/14, min_periods=14).mean() / atr14 * 100).iloc[-2]
        di_minus = (dm_minus.ewm(alpha=1/14, min_periods=14).mean() / atr14 * 100).iloc[-2]
        adx_approx = abs(di_plus - di_minus) / (di_plus + di_minus + 1e-9) * 100
        adx_ok     = adx_approx >= 20 and di_plus > di_minus

        sma20    = close.rolling(20).mean()
        std20    = close.rolling(20).std()
        bb_upper = sma20 + 2 * std20
        bb_lower = sma20 - 2 * std20
        bb_width = ((bb_upper - bb_lower) / sma20 * 100)
        bb_width_sma = bb_width.rolling(20).mean()
        bb_squeeze = float(bb_width.iloc[-2]) < float(bb_width_sma.iloc[-2])

        vol_ma    = vol.rolling(20).mean()
        vol_ratio = float(vol.iloc[-2] / vol_ma.iloc[-2]) if float(vol_ma.iloc[-2]) > 0 else 1.0
        vol_ok    = vol_ratio >= 1.0
        gap_pct = float(((close.iloc[-1] - close.iloc[-2]) / close.iloc[-2]) * 100)
        gap_ok  = 0 <= gap_pct <= 1.5

        hlc3   = (df["high"] + df["low"] + df["close"]) / 3
        # Rolling 20-day VWAP instead of all-time cumulative
        _vwap_window = 20
        vwap   = (hlc3 * vol).rolling(_vwap_window).sum() / vol.rolling(_vwap_window).sum()
        above_vwap = float(close.iloc[-2]) > float(vwap.iloc[-2])
        roc5    = float((close.iloc[-1] - close.iloc[-6]) / close.iloc[-6] * 100) if len(close) >= 6 else 0
        rs_ok   = roc5 > 0

        s1  = 1 if above_vwap else 0
        s2  = 1 if daily_bullish else 0
        s3  = 1 if ema9 > ema21_v else 0
        s4  = 1 if macd_rising else 0
        s5  = 1 if 40 <= rsi_val <= 65 else 0
        s6  = 1 if adx_ok else 0
        s7  = 1 if bb_squeeze else 0
        s8  = 1 if rs_ok else 0
        s9  = 1 if vol_ok else 0
        s10 = 1 if gap_ok else 0
        score = s1+s2+s3+s4+s5+s6+s7+s8+s9+s10

        if score >= 9:   signal = "STRONG BUY"
        elif score >= 7: signal = "BUY"
        elif score >= 6: signal = "NEUTRAL"
        else:            signal = "NO TRADE"

        price = float(close.iloc[-1])
        levels = ATR_TARGETS["base"]
        sl_mult = levels["sl"]
        tp1_mult = levels["tp1"]
        tp2_mult = levels["tp2"]
        tp3_mult = levels["tp3"]
        sl  = round(price - sl_mult  * atr_val, 2)
        tp1 = round(price + tp1_mult * atr_val, 2)
        tp2 = round(price + tp2_mult * atr_val, 2)
        tp3 = round(price + tp3_mult * atr_val, 2)
        be  = round(price + 1.0 * atr_val, 2)
        risk_per_share = price - sl
        risk_capital   = settings.RISK_CAPITAL * (settings.RISK_PCT / 100)
        qty = int(risk_capital / risk_per_share) if risk_per_share > 0 else 0

        breakdown = []
        if s1: breakdown.append("VWAP")
        if s2: breakdown.append("Daily")
        if s3: breakdown.append("EMA")
        if s4: breakdown.append("MACD")
        if s5: breakdown.append(f"RSI {rsi_val:.0f}")
        if s6: breakdown.append("ADX")
        if s7: breakdown.append("Squeeze")
        if s8: breakdown.append("RS+")
        if s9: breakdown.append(f"Vol {vol_ratio:.1f}x")
        if s10: breakdown.append("Gap OK")

        return {
            "Symbol": symbol, "Price": round(price, 2), "Score": f"{score}/10",
            "Signal": signal, "RSI": round(rsi_val, 1), "ADX": round(adx_approx, 1),
            "BB Squeeze": "Yes" if bb_squeeze else "No", "Vol Ratio": round(vol_ratio, 2),
            "Gap %": round(gap_pct, 2), "Stop Loss": sl, "TP1 (50%)": tp1,
            "TP2 (30%)": tp2, "TP3 (20%)": tp3, "Breakeven": be, "Qty": qty,
            "RR1": round(tp1_mult/sl_mult, 2), "RR2": round(tp2_mult/sl_mult, 2),
            "RR3": round(tp3_mult/sl_mult, 2), "Confluence": ", ".join(breakdown),
            "_score_int": score,
        }
    except NON_FATAL_ANALYSIS_ERROR as e:
        logger.error(f"USX {symbol}: {e}")
        return None

def _usx_one(symbol):
    df = fetch_yf(symbol)
    if df is None: return None
    return score_usx_single(symbol, df)

def run_usx_screener(min_score=7, direction="Long Only"):
    try:
        results = []
        with ThreadPoolExecutor(max_workers=settings.SCREENER_WORKERS) as pool:
            futures = {pool.submit(_usx_one, s): s for s in _universe_symbols()}
            for f in as_completed(futures):
                r = f.result()
                if r and r["_score_int"] >= min_score:
                    row = {k: v for k, v in r.items() if k != "_score_int"}
                    results.append(row)
        results.sort(key=lambda x: int(x["Score"].split("/")[0]), reverse=True)
        return results if results else [{"Message": "No stocks meet USX Pro criteria"}]
    except NON_FATAL_ANALYSIS_ERROR as e:
        return [{"Error": str(e)}]


# ---------------------------------------------------------------------------
# GICS sector mapping for BCF portfolio diversification (max 2 per sector)
# ---------------------------------------------------------------------------
_GICS_SECTOR = {
    # Technology
    **{s: "Technology" for s in [
        "AAPL","MSFT","NVDA","AVGO","ORCL","CRM","AMD","ADBE","CSCO","ACN",
        "IBM","INTC","INTU","NOW","QCOM","TXN","AMAT","ADI","LRCX","SNPS",
        "CDNS","KLAC","MCHP","MPWR","FTNT","PANW","CRWD","DDOG","NXPI",
        "KEYS","FFIV","EPAM","CTSH","IT","GDDY","GEN","SMCI","HPQ","HPE",
        "DELL","NTAP","WDC","STX","JKHY","TYL","CSGP","CIEN","COHR","LITE",
        "CDW","FDS","BR","FIS","FISV","GPN","PYPL","FI","COIN","PLTR",
        "APP","HOOD","FICO","SWKS","ON","TER","ZBRA","AKAM","SNDK",
    ]},
    # Healthcare
    **{s: "Healthcare" for s in [
        "LLY","UNH","JNJ","ABBV","MRK","TMO","ABT","DHR","BMY","AMGN",
        "PFE","GILD","VRTX","REGN","ISRG","MDT","BSX","SYK","BDX","EW",
        "IDXX","IQV","ZBH","BAX","HOLX","PODD","DXCM","A","LH","DGX",
        "CRL","RVTY","TECH","MRNA","BIIB","INCY","GEHC","SOLV","RMD",
        "STE","COO","HSIC","HCA","DVA","WAT","MCK","CAH","CVS",
    ]},
    # Consumer Discretionary
    **{s: "Consumer Disc" for s in [
        "AMZN","TSLA","HD","MCD","NKE","LOW","SBUX","TJX","BKNG","CMG",
        "ORLY","AZO","ROST","MAR","HLT","GM","F","APTV","DHI","LEN",
        "PHM","NVR","DECK","LULU","BBY","DPZ","YUM","DRI","POOL","GPC",
        "ULTA","TPR","RL","DG","DLTR","CVNA","DASH","UBER","ABNB",
        "EXPE","EBAY","ETSY","WSM","TSCO","LII","SWK","GNRC","LUV",
        "DAL","UAL",
    ]},
    # Consumer Staples
    **{s: "Consumer Staples" for s in [
        "PG","KO","PEP","COST","WMT","MDLZ","CL","KMB","GIS","HSY",
        "KHC","KDP","MKC","HRL","SJM","CPB","CAG","TSN","CHD","CLX",
        "MNST","SYY","KR","ADM","BG","KVUE",
    ]},
    # Energy
    **{s: "Energy" for s in [
        "XOM","CVX","COP","EOG","SLB","MPC","PSX","VLO","OXY","HAL",
        "DVN","FANG","BKR","CTRA","EQT","APA","OKE","WMB","KMI","TRGP",
    ]},
    # Industrials
    **{s: "Industrials" for s in [
        "CAT","GE","HON","UNP","UPS","DE","ETN","ITW","EMR","CSX",
        "NSC","WM","RSG","FAST","CMI","IR","ROK","DOV","PH","OTIS",
        "CARR","FTV","GWW","SNA","JBHT","CHRW","EXPD","ODFL","WAB",
        "TT","AME","IEX","AXON","BLDR","PWR","EME","FIX","SAIC","LDOS",
        "TDG","TDY","HWM","TXT","J","JCI","ALLE","AOS","XYL","AWK",
        "ROL","CTAS","PAYX","VRSK","IP","AVY","PKG","MLM","VMC","CRH",
        "SW","TRMB","FDX",
    ]},
    # Communication Services
    **{s: "Communication" for s in [
        "GOOGL","GOOG","META","CMCSA","CHTR","T","TMUS","VZ","EA","TTWO",
        "OMC","IPG","NWSA","NWS","TKO",
    ]},
    # Materials
    **{s: "Materials" for s in [
        "LIN","APD","ECL","SHW","FCX","NUE","STLD","NEM","DOW","DD",
        "PPG","ALB","CF","MOS","AMCR","BALL","LYB","IFF","FMC","CTVA",
        "AVY",
    ]},
}


def _get_news_sentiment_compound(symbol):
    """Lightweight news sentiment using NLTK VADER on recent headlines.

    Returns compound score in [-1, +1].  Falls back to 0.0 (neutral) on any
    failure so the gate never blocks due to data-source issues.
    """
    try:
        import nltk
        from nltk.sentiment.vader import SentimentIntensityAnalyzer
        # Ensure vader lexicon is available
        try:
            nltk.data.find("sentiment/vader_lexicon.zip")
        except LookupError:
            nltk.download("vader_lexicon", quiet=True)

        import yfinance as yf
        ticker = yf.Ticker(symbol)
        news = ticker.news
        if not news:
            return 0.0

        sia = SentimentIntensityAnalyzer()
        scores = []
        for article in news[:10]:  # latest 10 headlines
            title = article.get("title", "")
            if title:
                scores.append(sia.polarity_scores(title)["compound"])
        return float(np.mean(scores)) if scores else 0.0
    except NON_FATAL_ANALYSIS_ERROR as e:
        logger.debug(f"Sentiment fetch failed for {symbol}: {e}")
        return 0.0  # neutral fallback — don't block trade on data failure


def run_bcf_screener(portfolio_value=100000, rf_rate=0.043):
    """Balanced Confluence Framework — 5-gate AND-logic screener.

    Gates:
      1. Technical Consensus: swing score >= 60 AND USX score >= 7
      2. Probabilistic Confirmation: Monte Carlo prob_profit >= 55%
      3. Sentiment Filter (veto): news compound >= -0.10
      4. Regime Filter: SPY above 50-day SMA
      5. Historical Validation: 2-year backtest win rate >= 45%

    Position sizing: 1.25% risk, 1.5 ATR stop, 20% max position.
    Exit rules: TP1 +1.75 ATR (40%), TP2 +2.75 ATR (35%), TP3 +4.0 ATR (25%),
                SL -1.5 ATR (100%), Time Stop Day 12 (100%).
    Portfolio: max 5 positions, max 2 per GICS sector.
    """
    try:
        # --- Gate 4: Regime Filter — SPY above 50-day SMA ---
        spy_df = fetch_yf("SPY", period="1y")
        if spy_df is None or len(spy_df) < 50:
            return [{"Message": "Cannot fetch SPY data for regime filter"}]

        spy_close = spy_df["close"]
        spy_price = float(spy_close.iloc[-1])
        spy_sma50 = float(spy_close.tail(50).mean())

        if spy_price < spy_sma50:
            return [{
                "Message": "BCF BLOCKED — Bear regime detected",
                "SPY": round(spy_price, 2),
                "SPY SMA50": round(spy_sma50, 2),
                "Reason": "SPY below 50-day SMA. No long entries allowed.",
            }]

        # --- Pre-compute swing screener data (Gate 1a) ---
        screener_data = cache_get("all")
        if not screener_data:
            screener_data = run_screener()

        # Index by symbol for fast lookup
        swing_by_sym = {r["symbol"]: r for r in screener_data if isinstance(r, dict) and "symbol" in r}

        results = []
        sector_count = defaultdict(int)
        max_positions = 5
        max_per_sector = 2

        for symbol in _universe_symbols():
            if len(results) >= max_positions:
                break

            # --- Gate 1a: Swing score >= 60 ---
            sw = swing_by_sym.get(symbol)
            if not sw or sw.get("swing_score", 0) < 60:
                continue

            # --- Gate 1b: USX Pro score >= 7 ---
            try:
                df = fetch_yf(symbol)
                if df is None or len(df) < 50:
                    continue
                usx = score_usx_single(symbol, df)
                if not usx or usx.get("_score_int", 0) < 7:
                    continue
            except NON_FATAL_ANALYSIS_ERROR as e:
                logger.debug(f"BCF Gate 1b failed for {symbol}: {e}")
                continue

            # --- Sector cap check (max 2 per GICS sector) ---
            sector = _GICS_SECTOR.get(symbol, "Other")
            if sector_count[sector] >= max_per_sector:
                continue

            # --- Gate 2: Monte Carlo prob_profit >= 55% ---
            try:
                mc_result = run_monte_carlo(symbol, days=30, simulations=200, df=df)
                if not mc_result or isinstance(mc_result[0], dict) and "Error" in mc_result[0]:
                    continue
                prob_profit = mc_result[0].get("Prob Profit %", 0)
                if prob_profit < 55:
                    continue
            except NON_FATAL_ANALYSIS_ERROR as e:
                logger.debug(f"BCF Gate 2 (Monte Carlo) failed for {symbol}: {e}")
                continue

            # --- Gate 3: Sentiment >= -0.10 ---
            sentiment_score = _get_news_sentiment_compound(symbol)
            if sentiment_score < -0.10:
                continue

            # --- Gate 5: 2-year backtest win rate >= 45% ---
            try:
                now_utc = _utc_now()
                end_dt = now_utc.strftime("%Y-%m-%d")
                start_dt = (now_utc - timedelta(days=730)).strftime("%Y-%m-%d")
                bt = run_backtest(symbol, start_dt, end_dt, portfolio_value, 1.25, 12)
                if not bt or isinstance(bt[0], dict) and "Error" in bt[0]:
                    continue
                win_rate = bt[0].get("Win Rate %", 0)
                if win_rate < 45:
                    continue
            except NON_FATAL_ANALYSIS_ERROR as e:
                logger.debug(f"BCF Gate 5 (Backtest) failed for {symbol}: {e}")
                continue

            # --- All 5 gates passed — compute position sizing ---
            price = float(df["close"].iloc[-1])
            atr_val = float(atr(df).iloc[-1])

            stop_distance = 1.5 * atr_val
            risk_per_trade = portfolio_value * 0.0125  # 1.25%
            qty = int(risk_per_trade / stop_distance) if stop_distance > 0 else 0
            max_position = portfolio_value * 0.20
            if qty * price > max_position:
                qty = int(max_position / price)
            if qty <= 0:
                continue

            sl = round(price - 1.5 * atr_val, 2)
            tp1 = round(price + 1.75 * atr_val, 2)
            tp2 = round(price + 2.75 * atr_val, 2)
            tp3 = round(price + 4.0 * atr_val, 2)

            results.append({
                "Symbol": symbol,
                "Price": round(price, 2),
                "Swing Score": sw.get("swing_score", 0),
                "USX Score": usx.get("Score", "?"),
                "MC Prob %": round(prob_profit, 1),
                "Sentiment": round(sentiment_score, 2),
                "BT Win %": round(win_rate, 1),
                "Sector": sector,
                "ATR": round(atr_val, 2),
                "Qty": qty,
                "Risk $": round(qty * stop_distance, 2),
                "Position $": round(qty * price, 2),
                "Stop Loss": sl,
                "TP1 (40%)": tp1,
                "TP2 (35%)": tp2,
                "TP3 (25%)": tp3,
                "Time Stop": "Day 12",
                "RR1": round(1.75 / 1.5, 2),
                "RR2": round(2.75 / 1.5, 2),
                "RR3": round(4.0 / 1.5, 2),
            })
            sector_count[sector] += 1

        if not results:
            return [{"Message": "No stocks passed all 5 BCF gates",
                     "SPY": round(spy_price, 2),
                     "SPY SMA50": round(spy_sma50, 2),
                     "Regime": "Bull (SPY > SMA50)"}]

        return results

    except NON_FATAL_ANALYSIS_ERROR as e:
        logger.error(f"BCF screener error: {e}")
        return [{"Error": str(e)}]


def _send_consensus_breakdown(symbol, verdict, confidence, price,
                              votes_buy, votes_sell, votes_hold,
                              sl, tp1, tp2, tp3, details):
    """Send full 14-tool consensus breakdown to Telegram."""
    from app.services.telegram_alert import send_message as tg_send, _is_duplicate_signal

    # Skip if already sent this symbol recently
    dedup_key = f"breakdown_{symbol}"
    if _is_duplicate_signal(symbol, f"breakdown_{verdict}"):
        return

    # Verdict icon
    icons = {
        "STRONG BUY": "[STRONG BUY]", "BUY": "[BUY]",
        "WEAK BUY": "[WEAK BUY]", "STRONG SELL": "[STRONG SELL]",
        "SELL": "[SELL]", "WEAK SELL": "[WEAK SELL]",
    }
    icon = icons.get(verdict, "[--]")

    # Header
    lines = [
        f"{icon} AI CONSENSUS: {symbol}",
        f"Verdict: {verdict} | Confidence: {confidence:.0f}%",
        f"Price: ${price:.2f}",
        f"Votes: BUY {votes_buy} | SELL {votes_sell} | HOLD {votes_hold}",
        f"",
        f"--- 14-TOOL BREAKDOWN ---",
    ]

    # Tool details
    for d in details:
        tool = d.get("Tool", "?")
        signal = d.get("Signal", "?")
        vote = d.get("Vote", "?")
        # Vote emoji
        if vote == "BUY":
            v_icon = "+"
        elif vote == "SELL":
            v_icon = "-"
        elif vote == "-":
            v_icon = "X"
        else:
            v_icon = "="
        lines.append(f"  [{v_icon}] {tool}: {signal}")

    # Levels
    lines.append(f"")
    lines.append(f"Entry: ${price:.2f}")
    lines.append(f"SL: ${sl:.2f} | TP1: ${tp1:.2f}")
    lines.append(f"TP2: ${tp2:.2f} | TP3: ${tp3:.2f}")

    lines.append(f"")
    lines.append(f"{_utc_now().strftime('%Y-%m-%d %H:%M')} UTC")

    tg_send("\n".join(lines))


def _vote_signal(sig_str):
    """Helper: convert signal string to (vote_str, buy_delta, sell_delta, hold_delta)."""
    if "STRONG BUY" in sig_str:
        return "BUY", 2, 0, 0
    elif "BUY" in sig_str:
        return "BUY", 1, 0, 0
    elif "SELL" in sig_str:
        return "SELL", 0, 1, 0
    return "HOLD", 0, 0, 1

def _impl_run_consensus_base(symbol, horizon=5, episodes=10, df_override=None, as_of=None):
    try:
        # --- Halal verification gate (MUST pass before any analysis) ---
        is_halal, halal_reason = verify_halal(symbol)
        if not is_halal:
            logger.info(f"Consensus blocked for {symbol}: {halal_reason}")
            return [{"Symbol": symbol.upper(), "Verdict": "BLOCKED",
                     "Error": f"Not halal-verified: {halal_reason}",
                     "Action": "DO NOT TRADE"}]

        votes_buy  = 0
        votes_sell = 0
        votes_hold = 0
        details    = []

        df = _load_consensus_df(symbol, df_override=df_override, as_of=as_of)
        if df is None:
            return [{"Error": f"No data for {symbol}"}]
        price = float(df["close"].iloc[-1])
        atr_val = float(atr(df).iloc[-1])

        # --- 1. Halal Screener ---
        try:
            r = analyze(symbol, df)
            if r:
                sig = r["swing_signal"]
                vote, b, s, h = _vote_signal(sig)
                votes_buy += b; votes_sell += s; votes_hold += h
                details.append({"Tool": "Halal Screener", "Signal": sig, "Vote": vote, "Score": r["swing_score"]})
        except NON_FATAL_ANALYSIS_ERROR as e:
            logger.debug(f"Halal Screener tool error: {e}")
            details.append({"Tool": "Halal Screener", "Signal": "ERROR", "Vote": "-", "Score": 0})

        # --- 2. USX Pro (single symbol - no full scan) ---
        try:
            usx_r = score_usx_single(symbol, df)
            if usx_r and usx_r["_score_int"] >= 7:
                vote, b, s, h = _vote_signal(usx_r["Signal"])
                votes_buy += b; votes_sell += s; votes_hold += h
                details.append({"Tool": "USX Pro", "Signal": usx_r["Signal"], "Vote": vote, "Score": usx_r["Score"]})
            else:
                votes_hold += 1
                score_str = usx_r["Score"] if usx_r else "N/A"
                details.append({"Tool": "USX Pro", "Signal": f"Below Min ({score_str})", "Vote": "HOLD", "Score": 0})
        except NON_FATAL_ANALYSIS_ERROR as e:
            logger.debug(f"USX Pro tool error: {e}")
            details.append({"Tool": "USX Pro", "Signal": "ERROR", "Vote": "-", "Score": 0})

        # --- 3. Backtest ---
        try:
            today = _utc_now().date()
            end_date = today.strftime("%Y-%m-%d")
            start_date = (today - timedelta(days=365 * 2)).strftime("%Y-%m-%d")
            bt = run_backtest(symbol, start_date, end_date, settings.RISK_CAPITAL, 1.0, 3)
            if bt and len(bt) > 0 and "Return %" in bt[0]:
                ret = float(bt[0]["Return %"])
                win_rate_v = float(bt[0].get("Win Rate %", 0))
                sharpe = float(bt[0].get("Sharpe Ratio", 0))
                # Chan Ch.2 gating: a positive return without a positive
                # Deflated Sharpe AND a permutation p-value <= 0.10 is
                # statistically indistinguishable from luck.
                dsr  = float(bt[0].get("Deflated Sharpe", 0))
                pval = float(bt[0].get("Permutation p-value", 1.0))
                stat_ok = (dsr >= 0.60) and (pval <= 0.10)
                sig = f"Return {ret:.1f}% WR {win_rate_v:.0f}% DSR {dsr:.2f} p={pval:.2f}"
                if ret > 5 and win_rate_v > 50 and sharpe > 0.5 and stat_ok:
                    votes_buy += 1; vote = "BUY"
                elif ret < -5 or (win_rate_v < 40 and sharpe < 0):
                    votes_sell += 1; vote = "SELL"
                else:
                    votes_hold += 1; vote = "HOLD"
                details.append({"Tool": "Backtest 2Y", "Signal": sig, "Vote": vote, "Score": round(ret, 1)})
            else:
                votes_hold += 1
                details.append({"Tool": "Backtest 2Y", "Signal": "No trades", "Vote": "HOLD", "Score": 0})
        except NON_FATAL_ANALYSIS_ERROR as e:
            logger.debug(f"Backtest 2Y tool error: {e}")
            details.append({"Tool": "Backtest 2Y", "Signal": "ERROR", "Vote": "-", "Score": 0})

        # --- 4. Monte Carlo (reuse df) ---
        try:
            from openbb_forecast.simulation.monte_carlo import MonteCarloSimulator
            prices_arr = np.array(df["close"].values, dtype=np.float64).flatten()
            prices_arr = prices_arr[~np.isnan(prices_arr)]
            mc = MonteCarloSimulator(seed=42)
            result = mc.simulate(prices_arr, n_simulations=500, forecast_days=horizon)
            prob = float(result["summary"]["prob_profit"])
            exp_price = float(result["summary"]["expected_terminal_price"])
            chg = (exp_price - price) / price * 100
            if prob >= 0.60: votes_buy += 1; vote = "BUY"
            elif prob <= 0.45: votes_sell += 1; vote = "SELL"
            else: votes_hold += 1; vote = "HOLD"
            details.append({"Tool": "Monte Carlo", "Signal": f"Prob {prob*100:.1f}% Exp {chg:+.1f}%", "Vote": vote, "Score": round(prob*100, 1)})
        except NON_FATAL_ANALYSIS_ERROR as e:
            logger.debug(f"Monte Carlo tool error: {e}")
            details.append({"Tool": "Monte Carlo", "Signal": "ERROR", "Vote": "-", "Score": 0})

        # --- 5. Bollinger Band Squeeze + Breakout ---
        try:
            close = df["close"]
            sma20 = close.rolling(20).mean()
            std20 = close.rolling(20).std()
            upper_bb = sma20 + 2 * std20
            lower_bb = sma20 - 2 * std20
            bb_width = ((upper_bb - lower_bb) / sma20 * 100).iloc[-1]
            bb_width_prev = ((upper_bb - lower_bb) / sma20 * 100).iloc[-5]
            price_vs_upper = (price - float(upper_bb.iloc[-1])) / float(upper_bb.iloc[-1]) * 100
            # Squeeze (narrow bands) + breakout above = BUY
            if bb_width < 4 and price > float(upper_bb.iloc[-1]):
                votes_buy += 1; vote = "BUY"; sig = f"Squeeze Breakout (BW {bb_width:.1f}%)"
            elif bb_width < 4 and price < float(lower_bb.iloc[-1]):
                votes_sell += 1; vote = "SELL"; sig = f"Squeeze Breakdown (BW {bb_width:.1f}%)"
            elif price > float(sma20.iloc[-1]) and bb_width > bb_width_prev:
                votes_buy += 1; vote = "BUY"; sig = f"BB Expanding Up (BW {bb_width:.1f}%)"
            elif price < float(sma20.iloc[-1]) and bb_width > bb_width_prev:
                votes_sell += 1; vote = "SELL"; sig = f"BB Expanding Down (BW {bb_width:.1f}%)"
            else:
                votes_hold += 1; vote = "HOLD"; sig = f"BB Neutral (BW {bb_width:.1f}%)"
            details.append({"Tool": "Bollinger Bands", "Signal": sig, "Vote": vote, "Score": round(bb_width, 1)})
        except NON_FATAL_ANALYSIS_ERROR as e:
            logger.debug(f"Bollinger Bands tool error: {e}")
            details.append({"Tool": "Bollinger Bands", "Signal": "ERROR", "Vote": "-", "Score": 0})

        # --- 6. Multi-Timeframe EMA (21/50/200 alignment) ---
        try:
            close = df["close"]
            ema21_val = float(ema(close, 21).iloc[-1])
            sma50_val = float(close.rolling(50).mean().iloc[-1])
            sma200_val = float(close.rolling(200).mean().iloc[-1])
            # Perfect alignment: price > EMA21 > SMA50 > SMA200
            if price > ema21_val > sma50_val > sma200_val:
                votes_buy += 2; vote = "BUY"  # double weight for strong trend
                sig = "Perfect Uptrend (P>21>50>200)"
            elif price > ema21_val > sma50_val:
                votes_buy += 1; vote = "BUY"
                sig = "Uptrend (P>21>50)"
            elif price < ema21_val < sma50_val < sma200_val:
                votes_sell += 2; vote = "SELL"
                sig = "Perfect Downtrend (P<21<50<200)"
            elif price < ema21_val < sma50_val:
                votes_sell += 1; vote = "SELL"
                sig = "Downtrend (P<21<50)"
            else:
                votes_hold += 1; vote = "HOLD"
                sig = "Mixed alignment"
            details.append({"Tool": "EMA Alignment", "Signal": sig, "Vote": vote, "Score": 0})
        except NON_FATAL_ANALYSIS_ERROR as e:
            logger.debug(f"EMA Alignment tool error: {e}")
            details.append({"Tool": "EMA Alignment", "Signal": "ERROR", "Vote": "-", "Score": 0})

        # --- 7. XGBoost Quick Forecast ---
        try:
            close = df["close"]
            # Feature engineering: returns, RSI, MACD, volume ratio
            rets = close.pct_change().dropna()
            rsi_s = rsi(close)
            _, _, macd_hist = macd(close)
            features = pd.DataFrame({
                "ret_1": rets, "ret_5": rets.rolling(5).mean(),
                "ret_10": rets.rolling(10).mean(), "rsi": rsi_s,
                "macd_h": macd_hist,
                "vol_ratio": df["volume"] / df["volume"].rolling(20).mean(),
            }).dropna()
            target = (close.shift(-horizon) > close).astype(int)  # 1 if price goes up
            features = features.iloc[:-horizon]
            target = target.iloc[features.index[0]:features.index[-1]+1]
            # Train/test split
            split_idx = int(len(features) * 0.8)
            X_train, X_test = features.iloc[:split_idx], features.iloc[split_idx:]
            y_train, y_test = target.iloc[:split_idx], target.iloc[split_idx:]
            import xgboost as xgb
            model = xgb.XGBClassifier(n_estimators=50, max_depth=3, use_label_encoder=False,
                                       eval_metric="logloss", verbosity=0)
            model.fit(X_train.values, y_train.values)
            # Predict on latest data
            latest = features.iloc[-1:].values
            prob_up = float(model.predict_proba(latest)[0][1])
            accuracy = float((model.predict(X_test.values) == y_test.values).mean() * 100)
            if prob_up > 0.6: votes_buy += 1; vote = "BUY"
            elif prob_up < 0.4: votes_sell += 1; vote = "SELL"
            else: votes_hold += 1; vote = "HOLD"
            sig = f"P(up)={prob_up:.0%} Acc={accuracy:.0f}%"
            details.append({"Tool": "XGBoost", "Signal": sig, "Vote": vote, "Score": round(prob_up * 100, 1)})
            del model, X_train, X_test, y_train, y_test  # free memory
        except NON_FATAL_ANALYSIS_ERROR as e:
            logger.debug(f"XGBoost tool error: {e}")
            details.append({"Tool": "XGBoost", "Signal": "ERROR", "Vote": "-", "Score": 0})

        # --- 8. Momentum Score (Rate of Change + ADX) ---
        try:
            close = df["close"]
            roc_10 = float((price / close.iloc[-10] - 1) * 100)
            roc_20 = float((price / close.iloc[-20] - 1) * 100)
            roc_60 = float((price / close.iloc[-60] - 1) * 100)
            # ADX approximation
            high, low = df["high"], df["low"]
            plus_dm = high.diff().clip(lower=0)
            minus_dm = (-low.diff()).clip(lower=0)
            atr_14 = atr(df, 14)
            plus_di = (plus_dm.rolling(14).mean() / atr_14 * 100).iloc[-1]
            minus_di = (minus_dm.rolling(14).mean() / atr_14 * 100).iloc[-1]
            dx = abs(plus_di - minus_di) / (plus_di + minus_di) * 100 if (plus_di + minus_di) > 0 else 0
            # Strong upward momentum
            if roc_10 > 2 and roc_20 > 3 and dx > 20 and plus_di > minus_di:
                votes_buy += 1; vote = "BUY"; sig = f"Strong Up (ROC10={roc_10:+.1f}% ADX={dx:.0f})"
            elif roc_10 < -2 and roc_20 < -3 and dx > 20 and minus_di > plus_di:
                votes_sell += 1; vote = "SELL"; sig = f"Strong Down (ROC10={roc_10:+.1f}% ADX={dx:.0f})"
            else:
                votes_hold += 1; vote = "HOLD"; sig = f"Weak (ROC10={roc_10:+.1f}% ADX={dx:.0f})"
            details.append({"Tool": "Momentum", "Signal": sig, "Vote": vote, "Score": round(roc_10, 1)})
        except NON_FATAL_ANALYSIS_ERROR as e:
            logger.debug(f"Momentum tool error: {e}")
            details.append({"Tool": "Momentum", "Signal": "ERROR", "Vote": "-", "Score": 0})

        # --- 9. Volume-Price Divergence ---
        try:
            close = df["close"]
            vol = df["volume"]
            price_chg_5d = float((price / close.iloc[-5] - 1) * 100)
            vol_chg_5d = float((vol.iloc[-5:].mean() / vol.iloc[-25:-5].mean() - 1) * 100)
            # Price up + volume up = confirmed strength
            if price_chg_5d > 1 and vol_chg_5d > 20:
                votes_buy += 1; vote = "BUY"; sig = f"Confirmed Up (P={price_chg_5d:+.1f}% V={vol_chg_5d:+.0f}%)"
            # Price up + volume down = weak rally (divergence)
            elif price_chg_5d > 1 and vol_chg_5d < -10:
                votes_sell += 1; vote = "SELL"; sig = f"Weak Rally (P={price_chg_5d:+.1f}% V={vol_chg_5d:+.0f}%)"
            # Price down + volume up = selling pressure
            elif price_chg_5d < -1 and vol_chg_5d > 20:
                votes_sell += 1; vote = "SELL"; sig = f"Sell Pressure (P={price_chg_5d:+.1f}% V={vol_chg_5d:+.0f}%)"
            # Price down + volume down = exhaustion, potential bounce
            elif price_chg_5d < -1 and vol_chg_5d < -10:
                votes_buy += 1; vote = "BUY"; sig = f"Exhaustion (P={price_chg_5d:+.1f}% V={vol_chg_5d:+.0f}%)"
            else:
                votes_hold += 1; vote = "HOLD"; sig = f"Neutral (P={price_chg_5d:+.1f}% V={vol_chg_5d:+.0f}%)"
            details.append({"Tool": "Volume-Price", "Signal": sig, "Vote": vote, "Score": round(price_chg_5d, 1)})
        except NON_FATAL_ANALYSIS_ERROR as e:
            logger.debug(f"Volume-Price tool error: {e}")
            details.append({"Tool": "Volume-Price", "Signal": "ERROR", "Vote": "-", "Score": 0})

        # === ML MODELS (re-enabled with 8GB RAM tier) ===

        # --- 10. LSTM Neural Network ---
        try:
            lstm_r = run_lstm(symbol, horizon, df=df)
            if lstm_r and len(lstm_r) > 1:
                # Forecasts are in items [1:] with "Predicted Price" and "Change %"
                last_forecast = lstm_r[-1]  # last day forecast
                pct_chg = float(last_forecast.get("Change %", 0))
                forecast_price = float(last_forecast.get("Predicted Price", price))
                if pct_chg > 2:
                    votes_buy += 1; vote = "BUY"
                elif pct_chg < -2:
                    votes_sell += 1; vote = "SELL"
                else:
                    votes_hold += 1; vote = "HOLD"
                sig = f"${forecast_price:.2f} ({pct_chg:+.1f}%)"
                details.append({"Tool": "LSTM", "Signal": sig, "Vote": vote, "Score": round(pct_chg, 1)})
            else:
                votes_hold += 1
                details.append({"Tool": "LSTM", "Signal": "No forecast", "Vote": "HOLD", "Score": 0})
        except NON_FATAL_ANALYSIS_ERROR as e:
            logger.debug(f"LSTM tool error: {e}")
            details.append({"Tool": "LSTM", "Signal": "ERROR", "Vote": "-", "Score": 0})

        # --- 11. Transformer Attention Network ---
        try:
            tf_r = run_transformer(symbol, horizon, df=df)
            if tf_r and len(tf_r) > 1:
                last_forecast = tf_r[-1]
                pct_chg = float(last_forecast.get("Change %", 0))
                forecast_price = float(last_forecast.get("Predicted Price", price))
                if pct_chg > 2:
                    votes_buy += 1; vote = "BUY"
                elif pct_chg < -2:
                    votes_sell += 1; vote = "SELL"
                else:
                    votes_hold += 1; vote = "HOLD"
                sig = f"${forecast_price:.2f} ({pct_chg:+.1f}%)"
                details.append({"Tool": "Transformer", "Signal": sig, "Vote": vote, "Score": round(pct_chg, 1)})
            else:
                votes_hold += 1
                details.append({"Tool": "Transformer", "Signal": "No forecast", "Vote": "HOLD", "Score": 0})
        except NON_FATAL_ANALYSIS_ERROR as e:
            logger.debug(f"Transformer tool error: {e}")
            details.append({"Tool": "Transformer", "Signal": "ERROR", "Vote": "-", "Score": 0})

        # --- 12. Stacking Ensemble (XGB + RF + GBM → Ridge) ---
        try:
            ens_r = run_ensemble(symbol, horizon, df=df)
            if ens_r and len(ens_r) > 1:
                last_forecast = ens_r[-1]
                pct_chg = float(last_forecast.get("Change %", 0))
                forecast_price = float(last_forecast.get("Predicted Price", price))
                if pct_chg > 2:
                    votes_buy += 1; vote = "BUY"
                elif pct_chg < -2:
                    votes_sell += 1; vote = "SELL"
                else:
                    votes_hold += 1; vote = "HOLD"
                sig = f"${forecast_price:.2f} ({pct_chg:+.1f}%)"
                details.append({"Tool": "Ensemble", "Signal": sig, "Vote": vote, "Score": round(pct_chg, 1)})
            else:
                votes_hold += 1
                details.append({"Tool": "Ensemble", "Signal": "No forecast", "Vote": "HOLD", "Score": 0})
        except NON_FATAL_ANALYSIS_ERROR as e:
            logger.debug(f"Ensemble tool error: {e}")
            details.append({"Tool": "Ensemble", "Signal": "ERROR", "Vote": "-", "Score": 0})

        # --- 13. Double DQN Reinforcement Learning ---
        try:
            dqn_r = run_dqn(symbol, episodes, df=df)
            if dqn_r and len(dqn_r) > 0:
                summary_item = dqn_r[0]
                action = summary_item.get("Action", summary_item.get("Recommendation", ""))
                signal_str = summary_item.get("Signal", "")
                reward = summary_item.get("Total Reward", summary_item.get("Final Portfolio", 0))
                reward_f = float(reward) if isinstance(reward, (int, float)) else 0
                # Only count vote if DQN gave a meaningful signal (reward != 0)
                if "BUY" in str(action).upper() or "BUY" in str(signal_str).upper():
                    votes_buy += 1; vote = "BUY"
                elif "SELL" in str(action).upper() or "SELL" in str(signal_str).upper():
                    votes_sell += 1; vote = "SELL"
                elif reward_f == 0:
                    vote = "SKIP"  # Don't count non-functional result
                else:
                    votes_hold += 1; vote = "HOLD"
                sig = f"{signal_str or action} (R={reward_f:.1f})"
                details.append({"Tool": "DQN", "Signal": sig, "Vote": vote, "Score": round(reward_f, 1)})
            else:
                details.append({"Tool": "DQN", "Signal": "No action", "Vote": "SKIP", "Score": 0})
        except NON_FATAL_ANALYSIS_ERROR as e:
            logger.debug(f"DQN tool error: {e}")
            details.append({"Tool": "DQN", "Signal": "ERROR", "Vote": "-", "Score": 0})

        # --- 14. Policy Gradient (REINFORCE) ---
        try:
            pg_r = run_policy_gradient(symbol, episodes, df=df)
            if pg_r and len(pg_r) > 0:
                summary_item = pg_r[0]
                action = summary_item.get("Action", summary_item.get("Recommendation", ""))
                signal_str = summary_item.get("Signal", "")
                reward = summary_item.get("Total Reward", summary_item.get("Final Portfolio", 0))
                reward_f = float(reward) if isinstance(reward, (int, float)) else 0
                if "BUY" in str(action).upper() or "BUY" in str(signal_str).upper():
                    votes_buy += 1; vote = "BUY"
                elif "SELL" in str(action).upper() or "SELL" in str(signal_str).upper():
                    votes_sell += 1; vote = "SELL"
                elif reward_f == 0:
                    vote = "SKIP"
                else:
                    votes_hold += 1; vote = "HOLD"
                sig = f"{signal_str or action} (R={reward_f:.1f})"
                details.append({"Tool": "PolicyGrad", "Signal": sig, "Vote": vote, "Score": round(reward_f, 1)})
            else:
                details.append({"Tool": "PolicyGrad", "Signal": "No action", "Vote": "SKIP", "Score": 0})
        except NON_FATAL_ANALYSIS_ERROR as e:
            logger.debug(f"PolicyGrad tool error: {e}")
            details.append({"Tool": "PolicyGrad", "Signal": "ERROR", "Vote": "-", "Score": 0})

        # --- Final Verdict (Smart Ensemble — weighted consensus) ---
        try:
            ensemble = weighted_consensus(details)
            verdict = ensemble["verdict"]
            confidence = ensemble["confidence"]
            if verdict == "STRONG BUY":
                action_str = "STRONG ENTER"
            elif verdict == "BUY":
                action_str = "ENTER"
            elif verdict == "SELL":
                action_str = "AVOID"
            elif verdict == "NEUTRAL":
                action_str = "WAIT"
            else:
                action_str = "HOLD"
            logger.info(
                "Smart Ensemble for %s: %s (%.1f%%) — top models: %s",
                symbol, verdict, confidence,
                ", ".join(ensemble.get("top_models", [])),
            )
        except Exception:
            logger.warning("Smart Ensemble failed, falling back to equal-weighted vote")
            total = votes_buy + votes_sell + votes_hold
            if total == 0: total = 1
            confidence = round(max(votes_buy, votes_sell) / total * 100, 1)
            if votes_buy >= 5:
                verdict, action_str = "STRONG BUY", "STRONG ENTER"
            elif votes_buy > votes_sell and confidence >= 35:
                verdict, action_str = "BUY", "ENTER"
            elif votes_buy > votes_sell:
                verdict, action_str = "WEAK BUY", "WEAK SIGNAL"
            elif votes_sell >= 5:
                verdict, action_str = "STRONG SELL", "STRONG AVOID"
            elif votes_sell > votes_buy and confidence >= 35:
                verdict, action_str = "SELL", "AVOID"
            elif votes_sell > votes_buy:
                verdict, action_str = "WEAK SELL", "WEAK SELL"
            else:
                verdict, action_str = "NEUTRAL", "WAIT"

        levels = ATR_TARGETS["base"]
        sl  = round(price - levels["sl"] * atr_val, 2)
        tp1 = round(price + levels["tp1"] * atr_val, 2)
        tp2 = round(price + levels["tp2"] * atr_val, 2)
        tp3 = round(price + levels["tp3"] * atr_val, 2)

        summary = [{
            "Symbol":       symbol.upper(),
            "Price":        round(price, 2),
            "Verdict":      verdict,
            "Confidence %": confidence,
            "Votes BUY":    votes_buy,
            "Votes SELL":   votes_sell,
            "Votes HOLD":   votes_hold,
            "Total Tools":  len(details),
            "Action":       action_str,
            "Stop Loss":    sl,
            "TP1":          tp1,
            "TP2":          tp2,
            "TP3":          tp3,
        }]

        # Record signal for audit trail (Phase 4.3: accuracy tracking)
        try:
            record_signal(
                symbol=symbol.upper(),
                signal_type="consensus",
                signal=verdict,
                score=confidence,
                price=round(price, 2),
                stop_loss=sl,
                take_profit=tp1,
                confidence=confidence,
                details={"votes_buy": votes_buy, "votes_sell": votes_sell,
                         "votes_hold": votes_hold, "tools": len(details)},
            )
        except NON_FATAL_ANALYSIS_ERROR as e:
            logger.error(f"record_signal failed (non-critical): {e}")

        # Telegram: chart + breakdown for all actionable verdicts
        try:
            if verdict not in ("NEUTRAL", "HOLD"):
                alert_signal_with_chart(
                    symbol=symbol.upper(), verdict=verdict,
                    confidence=confidence, price=round(price, 2),
                    stop_loss=sl, tp1=tp1, tp2=tp2, tp3=tp3,
                    votes_buy=votes_buy, votes_sell=votes_sell,
                    votes_hold=votes_hold, df=df,
                )
                _send_consensus_breakdown(
                    symbol=symbol.upper(), verdict=verdict,
                    confidence=confidence, price=round(price, 2),
                    votes_buy=votes_buy, votes_sell=votes_sell,
                    votes_hold=votes_hold, sl=sl, tp1=tp1, tp2=tp2, tp3=tp3,
                    details=details,
                )
        except NON_FATAL_ANALYSIS_ERROR as e:
            logger.debug(f"Telegram alert error for {symbol}: {e}")

        # Auto-trade execution (Stage 1: Paper Trading)
        try:
            trade_result = auto_trade_signal(
                symbol=symbol.upper(),
                verdict=verdict,
                confidence=confidence,
                price=round(price, 2),
                stop_loss=sl,
                take_profit=tp1,
                votes_buy=votes_buy,
                votes_sell=votes_sell,
                votes_hold=votes_hold,
                details={"tools": len(details)},
            )
            if trade_result:
                summary[0]["Auto_Trade"] = "EXECUTED" if trade_result.get("executed") else "REJECTED"
                summary[0]["Trade_Reason"] = trade_result.get("reason", "")
        except NON_FATAL_ANALYSIS_ERROR as e:
            logger.debug(f"Auto-trade hook error: {e}")

        return summary + details

    except NON_FATAL_ANALYSIS_ERROR as e:
        return [{"Error": str(e)}]


# ============================================================================
# MULTI-STRATEGY CONSENSUS FUNCTIONS
# ============================================================================

def _impl_run_consensus_momentum(symbol, horizon=5, df_override=None, as_of=None):
    """Strategy A: Momentum Alpha — trend-following with 5 tools.

    Tools: EMA Alignment (2x), Momentum ROC+ADX, Backtest 2Y, XGBoost, Monte Carlo
    Entry: 4+ of 5 tools BUY + price > SMA50
    Exit: Trailing stop 3%, TP at 2.5x ATR
    """
    try:
        is_halal, halal_reason = verify_halal(symbol)
        if not is_halal:
            return [{"Symbol": symbol.upper(), "Verdict": "BLOCKED",
                     "Strategy": "A-Momentum", "Error": f"Not halal: {halal_reason}"}]

        votes_buy, votes_sell, votes_hold = 0, 0, 0
        details = []

        df = _load_consensus_df(symbol, df_override=df_override, as_of=as_of)
        if df is None:
            return [{"Error": f"No data for {symbol}"}]
        price = float(df["close"].iloc[-1])
        atr_val = float(atr(df).iloc[-1])
        close = df["close"]

        # SMA50 gate — only buy if above 50-day SMA (trend filter)
        sma50_val = float(close.rolling(50).mean().iloc[-1])
        above_sma50 = price > sma50_val

        # --- Tool 0a: Strategy regime router (Chan Ch.6) ---
        # If the asset is classified "ranging" (mean-reverting), running
        # a momentum strategy on it is structurally wrong — vote SELL.
        try:
            from app.services.strategy_regime import classify_strategy_regime
            sr = classify_strategy_regime(close.tail(300))
            if sr.label == "trending":
                votes_buy += 1
                details.append({"Tool": "Regime Router", "Signal": f"Trending ({sr.reason})", "Vote": "BUY"})
            elif sr.label == "ranging":
                votes_sell += 1
                details.append({"Tool": "Regime Router", "Signal": f"Ranging � wrong strategy ({sr.reason})", "Vote": "SELL"})
            else:
                votes_hold += 1
                details.append({"Tool": "Regime Router", "Signal": f"Noisy ({sr.reason})", "Vote": "HOLD"})
        except NON_FATAL_ANALYSIS_ERROR as e:
            logger.debug(f"Regime Router error: {e}")
            details.append({"Tool": "Regime Router", "Signal": "ERROR", "Vote": "-"})

        # --- Tool 0: Momentum Quality gate (Chan Ch.5) ---
        # t-statistic of returns + 12-1 momentum + Hurst persistence.
        # Vetoes momentum entries on series whose past returns are not
        # statistically distinguishable from drift-of-noise.
        try:
            from app.services.momentum_quality import momentum_quality_score
            mom_rep = momentum_quality_score(close.tail(300))
            if mom_rep.verdict == "trending":
                votes_buy += 1; vote = "BUY"
                sig = f"Trending (t={mom_rep.tstat:.1f}, mom={mom_rep.mom_12_1*100:+.1f}%, H={mom_rep.hurst:.2f})"
            elif mom_rep.verdict == "mean_reverting":
                votes_sell += 1; vote = "SELL"
                sig = f"Mean-reverting (H={mom_rep.hurst:.2f}) � anti-momentum"
            else:
                votes_hold += 1; vote = "HOLD"
                sig = f"Neutral: {mom_rep.reason}"
            details.append({"Tool": "Momentum Quality", "Signal": sig, "Vote": vote})
        except NON_FATAL_ANALYSIS_ERROR as e:
            logger.debug(f"Momentum Quality gate error: {e}")
            details.append({"Tool": "Momentum Quality", "Signal": "ERROR", "Vote": "-"})

        # --- Tool 1: EMA Alignment (2x weight for strong trends) ---
        try:
            ema21_val = float(ema(close, 21).iloc[-1])
            sma200_val = float(close.rolling(200).mean().iloc[-1])
            if price > ema21_val > sma50_val > sma200_val:
                votes_buy += 2; vote = "BUY"; sig = "Perfect Uptrend (P>21>50>200)"
            elif price > ema21_val > sma50_val:
                votes_buy += 1; vote = "BUY"; sig = "Uptrend (P>21>50)"
            elif price < ema21_val < sma50_val < sma200_val:
                votes_sell += 2; vote = "SELL"; sig = "Perfect Downtrend (P<21<50<200)"
            elif price < ema21_val < sma50_val:
                votes_sell += 1; vote = "SELL"; sig = "Downtrend (P<21<50)"
            else:
                votes_hold += 1; vote = "HOLD"; sig = "Mixed alignment"
            details.append({"Tool": "EMA Alignment", "Signal": sig, "Vote": vote})
        except NON_FATAL_ANALYSIS_ERROR as e:
            logger.debug(f"EMA Alignment tool error: {e}")
            details.append({"Tool": "EMA Alignment", "Signal": "ERROR", "Vote": "-"})

        # --- Tool 2: Momentum (ROC + ADX) ---
        try:
            roc_10 = float((price / close.iloc[-10] - 1) * 100)
            roc_20 = float((price / close.iloc[-20] - 1) * 100)
            high, low = df["high"], df["low"]
            plus_dm = high.diff().clip(lower=0)
            minus_dm = (-low.diff()).clip(lower=0)
            atr_14 = atr(df, 14)
            plus_di = (plus_dm.rolling(14).mean() / atr_14 * 100).iloc[-1]
            minus_di = (minus_dm.rolling(14).mean() / atr_14 * 100).iloc[-1]
            dx = abs(plus_di - minus_di) / (plus_di + minus_di) * 100 if (plus_di + minus_di) > 0 else 0
            if roc_10 > 2 and roc_20 > 3 and dx > 20 and plus_di > minus_di:
                votes_buy += 1; vote = "BUY"; sig = f"Strong Up (ROC10={roc_10:+.1f}% ADX={dx:.0f})"
            elif roc_10 < -2 and roc_20 < -3 and dx > 20 and minus_di > plus_di:
                votes_sell += 1; vote = "SELL"; sig = f"Strong Down (ROC10={roc_10:+.1f}% ADX={dx:.0f})"
            else:
                votes_hold += 1; vote = "HOLD"; sig = f"Weak (ROC10={roc_10:+.1f}% ADX={dx:.0f})"
            details.append({"Tool": "Momentum", "Signal": sig, "Vote": vote})
        except NON_FATAL_ANALYSIS_ERROR as e:
            logger.debug(f"Momentum tool error: {e}")
            details.append({"Tool": "Momentum", "Signal": "ERROR", "Vote": "-"})

        # --- Tool 3: Backtest 2Y ---
        try:
            today = _utc_now().date()
            end_date = today.strftime("%Y-%m-%d")
            start_date = (today - timedelta(days=365 * 2)).strftime("%Y-%m-%d")
            bt = run_backtest(symbol, start_date, end_date, settings.RISK_CAPITAL, 1.0, 3)
            if bt and len(bt) > 0 and "Return %" in bt[0]:
                ret = float(bt[0]["Return %"])
                win_rate_v = float(bt[0].get("Win Rate %", 0))
                sharpe = float(bt[0].get("Sharpe Ratio", 0))
                if ret > 5 and win_rate_v > 50 and sharpe > 0.5:
                    votes_buy += 1; vote = "BUY"
                elif ret < -5 or (win_rate_v < 40 and sharpe < 0):
                    votes_sell += 1; vote = "SELL"
                else:
                    votes_hold += 1; vote = "HOLD"
                sig = f"Return {ret:.1f}% WR {win_rate_v:.0f}%"
                details.append({"Tool": "Backtest 2Y", "Signal": sig, "Vote": vote})
            else:
                votes_hold += 1
                details.append({"Tool": "Backtest 2Y", "Signal": "No trades", "Vote": "HOLD"})
        except NON_FATAL_ANALYSIS_ERROR as e:
            logger.debug(f"Backtest 2Y tool error: {e}")
            details.append({"Tool": "Backtest 2Y", "Signal": "ERROR", "Vote": "-"})

        # --- Tool 4: XGBoost ---
        try:
            rets = close.pct_change().dropna()
            rsi_s = rsi(close)
            _, _, macd_hist = macd(close)
            features = pd.DataFrame({
                "ret_1": rets, "ret_5": rets.rolling(5).mean(),
                "ret_10": rets.rolling(10).mean(), "rsi": rsi_s,
                "macd_h": macd_hist,
                "vol_ratio": df["volume"] / df["volume"].rolling(20).mean(),
            }).dropna()
            target = (close.shift(-horizon) > close).astype(int)
            features = features.iloc[:-horizon]
            target = target.iloc[features.index[0]:features.index[-1] + 1]
            split_idx = int(len(features) * 0.8)
            X_train, X_test = features.iloc[:split_idx], features.iloc[split_idx:]
            y_train, y_test = target.iloc[:split_idx], target.iloc[split_idx:]
            import xgboost as xgb
            model = xgb.XGBClassifier(n_estimators=50, max_depth=3, use_label_encoder=False,
                                       eval_metric="logloss", verbosity=0)
            model.fit(X_train.values, y_train.values)
            prob_up = float(model.predict_proba(features.iloc[-1:].values)[0][1])
            accuracy = float((model.predict(X_test.values) == y_test.values).mean() * 100)
            if prob_up > 0.6: votes_buy += 1; vote = "BUY"
            elif prob_up < 0.4: votes_sell += 1; vote = "SELL"
            else: votes_hold += 1; vote = "HOLD"
            sig = f"P(up)={prob_up:.0%} Acc={accuracy:.0f}%"
            details.append({"Tool": "XGBoost", "Signal": sig, "Vote": vote})
            del model
        except NON_FATAL_ANALYSIS_ERROR as e:
            logger.debug(f"XGBoost tool error: {e}")
            details.append({"Tool": "XGBoost", "Signal": "ERROR", "Vote": "-"})

        # --- Tool 5: Monte Carlo ---
        try:
            from openbb_forecast.simulation.monte_carlo import MonteCarloSimulator
            prices_arr = np.array(close.values, dtype=np.float64).flatten()
            prices_arr = prices_arr[~np.isnan(prices_arr)]
            mc = MonteCarloSimulator(seed=42)
            result = mc.simulate(prices_arr, n_simulations=500, forecast_days=horizon)
            prob = float(result["summary"]["prob_profit"])
            exp_price = float(result["summary"]["expected_terminal_price"])
            chg = (exp_price - price) / price * 100
            if prob >= 0.60: votes_buy += 1; vote = "BUY"
            elif prob <= 0.45: votes_sell += 1; vote = "SELL"
            else: votes_hold += 1; vote = "HOLD"
            details.append({"Tool": "Monte Carlo", "Signal": f"Prob {prob * 100:.1f}% Exp {chg:+.1f}%", "Vote": vote})
        except NON_FATAL_ANALYSIS_ERROR as e:
            logger.debug(f"Monte Carlo tool error: {e}")
            details.append({"Tool": "Monte Carlo", "Signal": "ERROR", "Vote": "-"})

        # --- Verdict (max ~7 votes with EMA 2x) ---
        total = votes_buy + votes_sell + votes_hold
        if total == 0: total = 1
        confidence = round(max(votes_buy, votes_sell) / total * 100, 1)

        # STRONG requires 3+ BUY votes AND price above SMA50
        if votes_buy >= 3 and above_sma50:
            verdict = "STRONG BUY"
        elif votes_buy > votes_sell and above_sma50 and confidence >= 50:
            verdict = "BUY"
        elif votes_sell >= 3:
            verdict = "STRONG SELL"
        elif votes_sell > votes_buy and confidence >= 50:
            verdict = "SELL"
        elif votes_buy > votes_sell:
            verdict = "WEAK BUY"
        elif votes_sell > votes_buy:
            verdict = "WEAK SELL"
        else:
            verdict = "NEUTRAL"

        levels = ATR_TARGETS["momentum"]
        sl = round(price - levels["sl"] * atr_val, 2)
        tp1 = round(price + levels["tp1"] * atr_val, 2)
        tp2 = round(price + levels["tp2"] * atr_val, 2)
        tp3 = round(price + levels["tp3"] * atr_val, 2)

        summary = [{
            "Symbol": symbol.upper(), "Strategy": "A-Momentum Alpha",
            "Price": round(price, 2), "Verdict": verdict,
            "Confidence %": confidence,
            "Votes BUY": votes_buy, "Votes SELL": votes_sell, "Votes HOLD": votes_hold,
            "SMA50 Filter": "ABOVE" if above_sma50 else "BELOW",
            "Stop Loss": sl, "TP1": tp1, "TP2": tp2, "TP3": tp3,
        }]

        # Telegram: chart + breakdown for all actionable verdicts
        if verdict not in ("NEUTRAL", "HOLD"):
            try:
                alert_signal_with_chart(
                    symbol=symbol.upper(), verdict=f"[A] Momentum: {verdict}",
                    confidence=confidence, price=round(price, 2),
                    stop_loss=sl, tp1=tp1, tp2=tp2, tp3=tp3,
                    votes_buy=votes_buy, votes_sell=votes_sell,
                    votes_hold=votes_hold, df=df,
                )
                _send_consensus_breakdown(
                    symbol=symbol.upper(), verdict=f"[A] Momentum: {verdict}",
                    confidence=confidence, price=round(price, 2),
                    votes_buy=votes_buy, votes_sell=votes_sell, votes_hold=votes_hold,
                    sl=sl, tp1=tp1, tp2=tp2, tp3=tp3, details=details,
                )
            except NON_FATAL_ANALYSIS_ERROR as e:
                logger.warning(f"[A] Telegram alert failed: {e}")

        # Auto-trade via Strategy A's account
        # Send BUY/STRONG BUY/SELL/STRONG SELL — let on_signal() decide
        if verdict not in ("NEUTRAL", "HOLD"):
            try:
                trade_result = auto_trade_signal(
                    symbol=symbol.upper(), verdict=verdict,
                    confidence=confidence, price=round(price, 2),
                    stop_loss=sl, take_profit=tp1,
                    votes_buy=votes_buy, votes_sell=votes_sell,
                    votes_hold=votes_hold, details={"strategy": "A"},
                    strategy_id="A",
                )
                if trade_result:
                    summary[0]["Auto_Trade"] = "EXECUTED" if trade_result.get("executed") else "REJECTED"
                    summary[0]["Trade_Reason"] = trade_result.get("reason", "")
            except NON_FATAL_ANALYSIS_ERROR as e:
                logger.error(f"[A] Auto-trade error for {symbol}: {e}")

        return summary + details

    except NON_FATAL_ANALYSIS_ERROR as e:
        return [{"Error": str(e), "Strategy": "A-Momentum"}]


def _impl_run_consensus_reversion(symbol, horizon=3, df_override=None, as_of=None):
    """Strategy B: Mean Reversion — buy oversold stocks.

    Tools: Stationarity gate, Bollinger Bands, RSI, Volume-Price Divergence, Stochastic, OBV
    Entry: RSI < 35 + price near lower BB + volume confirmation
    Exit: ATR-based SL (2× ATR below 20-day low), TP middle BB / +ATR

    Per Chan Ch.3 a mean-reversion strategy is only valid on a series
    that actually mean-reverts. We compute an ADF/Hurst/half-life
    report on the last 250 closes. If stationarity fails, the signal is
    HARD-BLOCKED regardless of other tool votes — running mean-reversion
    on a non-stationary series is structurally wrong.
    """
    try:
        is_halal, halal_reason = verify_halal(symbol)
        if not is_halal:
            return [{"Symbol": symbol.upper(), "Verdict": "BLOCKED",
                     "Strategy": "B-Reversion", "Error": f"Not halal: {halal_reason}"}]

        votes_buy, votes_sell, votes_hold = 0, 0, 0
        details = []

        df = _load_consensus_df(symbol, df_override=df_override, as_of=as_of)
        if df is None:
            return [{"Error": f"No data for {symbol}"}]
        price = float(df["close"].iloc[-1])
        atr_val = float(atr(df).iloc[-1])
        close = df["close"]

        # Sharpe ratio from price history (annualised)
        daily_ret = close.pct_change().dropna()
        if len(daily_ret) > 20 and daily_ret.std() > 0:
            sharpe_ratio = float((daily_ret.mean() / daily_ret.std()) * np.sqrt(252))
        else:
            sharpe_ratio = 0.0

        # --- Tool 0a: Strategy regime router (Chan Ch.6) ---
        # If the asset is "trending", running a mean-reversion strategy
        # on it is structurally wrong — vote SELL on the entry.
        try:
            from app.services.strategy_regime import classify_strategy_regime
            sr = classify_strategy_regime(close.tail(300))
            if sr.label == "ranging":
                votes_buy += 1
                details.append({"Tool": "Regime Router", "Signal": f"Ranging ({sr.reason})", "Vote": "BUY"})
            elif sr.label == "trending":
                votes_sell += 1
                details.append({"Tool": "Regime Router", "Signal": f"Trending — wrong strategy ({sr.reason})", "Vote": "SELL"})
            else:
                votes_hold += 1
                details.append({"Tool": "Regime Router", "Signal": f"Noisy ({sr.reason})", "Vote": "HOLD"})
        except NON_FATAL_ANALYSIS_ERROR as e:
            logger.debug(f"Regime Router error: {e}")
            details.append({"Tool": "Regime Router", "Signal": "ERROR", "Vote": "-"})

        # --- Tool 0: Stationarity gate (Chan Ch.3) ---
        try:
            from app.services.stationarity import stationarity_report
            window = close.tail(250)
            rep = stationarity_report(window, max_half_life_bars=30.0)
            if rep.is_mean_reverting:
                votes_buy += 1
                details.append({"Tool": "Stationarity",
                                "Signal": f"Mean-reverting (H={rep.hurst:.2f}, ADF p={rep.adf_pvalue:.2f}, t1/2={rep.half_life:.1f})",
                                "Vote": "BUY"})
            else:
                votes_sell += 1
                details.append({"Tool": "Stationarity",
                                "Signal": f"Not mean-reverting: {rep.reason}",
                                "Vote": "SELL"})
        except NON_FATAL_ANALYSIS_ERROR as e:
            logger.debug(f"Stationarity gate error: {e}")
            details.append({"Tool": "Stationarity", "Signal": "ERROR", "Vote": "-"})

        # --- Tool 1: Bollinger Bands ---
        try:
            sma20 = close.rolling(20).mean()
            std20 = close.rolling(20).std()
            upper_bb = sma20 + 2 * std20
            lower_bb = sma20 - 2 * std20
            mid_bb = float(sma20.iloc[-1])
            bb_width = ((upper_bb - lower_bb) / sma20 * 100).iloc[-1]
            lower_bb_val = float(lower_bb.iloc[-1])
            upper_bb_val = float(upper_bb.iloc[-1])

            # Mean reversion: BUY when price near/below lower band
            if price <= lower_bb_val:
                votes_buy += 1; vote = "BUY"; sig = f"At/Below Lower BB (${lower_bb_val:.2f})"
            elif price >= upper_bb_val:
                votes_sell += 1; vote = "SELL"; sig = f"At/Above Upper BB (${upper_bb_val:.2f})"
            else:
                pct_bb = (price - lower_bb_val) / (upper_bb_val - lower_bb_val) * 100
                if pct_bb < 25:
                    votes_buy += 1; vote = "BUY"; sig = f"Near Lower BB ({pct_bb:.0f}%)"
                elif pct_bb > 75:
                    votes_sell += 1; vote = "SELL"; sig = f"Near Upper BB ({pct_bb:.0f}%)"
                else:
                    votes_hold += 1; vote = "HOLD"; sig = f"Mid-range ({pct_bb:.0f}%)"
            details.append({"Tool": "Bollinger Bands", "Signal": sig, "Vote": vote})
        except NON_FATAL_ANALYSIS_ERROR as e:
            logger.debug(f"Bollinger Bands error: {e}")
            mid_bb = price  # fallback
            details.append({"Tool": "Bollinger Bands", "Signal": "ERROR", "Vote": "-"})

        # --- Tool 2: RSI ---
        try:
            rsi_val = float(rsi(close).iloc[-1])
            if rsi_val < 30:
                votes_buy += 1; vote = "BUY"; sig = f"Oversold RSI={rsi_val:.0f}"
            elif rsi_val < 40:
                votes_buy += 1; vote = "BUY"; sig = f"Weak RSI={rsi_val:.0f}"
            elif rsi_val > 70:
                votes_sell += 1; vote = "SELL"; sig = f"Overbought RSI={rsi_val:.0f}"
            elif rsi_val > 60:
                votes_sell += 1; vote = "SELL"; sig = f"Elevated RSI={rsi_val:.0f}"
            else:
                votes_hold += 1; vote = "HOLD"; sig = f"Neutral RSI={rsi_val:.0f}"
            details.append({"Tool": "RSI", "Signal": sig, "Vote": vote})
        except NON_FATAL_ANALYSIS_ERROR as e:
            logger.debug(f"RSI error: {e}")
            rsi_val = 50
            details.append({"Tool": "RSI", "Signal": "ERROR", "Vote": "-"})

        # --- Tool 3: Volume-Price Divergence ---
        try:
            vol = df["volume"]
            price_chg_5d = float((price / close.iloc[-5] - 1) * 100)
            vol_chg_5d = float((vol.iloc[-5:].mean() / vol.iloc[-25:-5].mean() - 1) * 100)
            # For mean reversion: price down + high volume = capitulation (BUY)
            if price_chg_5d < -2 and vol_chg_5d > 30:
                votes_buy += 1; vote = "BUY"; sig = f"Capitulation (P={price_chg_5d:+.1f}% V={vol_chg_5d:+.0f}%)"
            elif price_chg_5d > 2 and vol_chg_5d < -15:
                votes_sell += 1; vote = "SELL"; sig = f"Weak Rally (P={price_chg_5d:+.1f}% V={vol_chg_5d:+.0f}%)"
            elif price_chg_5d < -1 and vol_chg_5d < -10:
                votes_buy += 1; vote = "BUY"; sig = f"Exhaustion (P={price_chg_5d:+.1f}% V={vol_chg_5d:+.0f}%)"
            else:
                votes_hold += 1; vote = "HOLD"; sig = f"Neutral (P={price_chg_5d:+.1f}% V={vol_chg_5d:+.0f}%)"
            details.append({"Tool": "Volume-Price", "Signal": sig, "Vote": vote})
        except NON_FATAL_ANALYSIS_ERROR as e:
            logger.debug(f"Volume-Price tool error: {e}")
            details.append({"Tool": "Volume-Price", "Signal": "ERROR", "Vote": "-"})

        # --- Tool 4: Stochastic Oscillator ---
        try:
            from app.services.technical import stochastic
            pct_k, pct_d = stochastic(df, k_period=14, d_period=3)
            k_val = float(pct_k.iloc[-1])
            d_val = float(pct_d.iloc[-1])
            # Oversold + bullish crossover = BUY
            if k_val < 20 and k_val > d_val:
                votes_buy += 1; vote = "BUY"; sig = f"Oversold Crossover (K={k_val:.0f} D={d_val:.0f})"
            elif k_val < 25:
                votes_buy += 1; vote = "BUY"; sig = f"Oversold (K={k_val:.0f})"
            elif k_val > 80 and k_val < d_val:
                votes_sell += 1; vote = "SELL"; sig = f"Overbought Crossdown (K={k_val:.0f})"
            elif k_val > 75:
                votes_sell += 1; vote = "SELL"; sig = f"Overbought (K={k_val:.0f})"
            else:
                votes_hold += 1; vote = "HOLD"; sig = f"Neutral (K={k_val:.0f})"
            details.append({"Tool": "Stochastic", "Signal": sig, "Vote": vote})
        except NON_FATAL_ANALYSIS_ERROR as e:
            logger.debug(f"Stochastic tool error: {e}")
            details.append({"Tool": "Stochastic", "Signal": "ERROR", "Vote": "-"})

        # --- Tool 5: OBV Trend ---
        try:
            from app.services.technical import obv
            obv_series = obv(df)
            obv_now = float(obv_series.iloc[-1])
            obv_10ago = float(obv_series.iloc[-10])
            obv_slope = (obv_now - obv_10ago) / abs(obv_10ago) * 100 if obv_10ago != 0 else 0
            price_chg_10 = float((price / close.iloc[-10] - 1) * 100)
            # OBV rising while price falling = accumulation (bullish divergence)
            if obv_slope > 5 and price_chg_10 < -1:
                votes_buy += 1; vote = "BUY"; sig = f"Accumulation (OBV={obv_slope:+.1f}% P={price_chg_10:+.1f}%)"
            elif obv_slope < -5 and price_chg_10 > 1:
                votes_sell += 1; vote = "SELL"; sig = f"Distribution (OBV={obv_slope:+.1f}% P={price_chg_10:+.1f}%)"
            elif obv_slope > 5:
                votes_buy += 1; vote = "BUY"; sig = f"OBV Rising ({obv_slope:+.1f}%)"
            elif obv_slope < -5:
                votes_sell += 1; vote = "SELL"; sig = f"OBV Falling ({obv_slope:+.1f}%)"
            else:
                votes_hold += 1; vote = "HOLD"; sig = f"OBV Flat ({obv_slope:+.1f}%)"
            details.append({"Tool": "OBV", "Signal": sig, "Vote": vote})
        except NON_FATAL_ANALYSIS_ERROR as e:
            logger.debug(f"OBV tool error: {e}")
            details.append({"Tool": "OBV", "Signal": "ERROR", "Vote": "-"})

        # --- Verdict ---
        total = votes_buy + votes_sell + votes_hold
        if total == 0: total = 1
        confidence = round(max(votes_buy, votes_sell) / total * 100, 1)

        if votes_buy >= 3:
            verdict = "STRONG BUY"
        elif votes_buy >= 2 and confidence >= 50:
            verdict = "BUY"
        elif votes_sell >= 3:
            verdict = "STRONG SELL"
        elif votes_sell >= 2 and confidence >= 50:
            verdict = "SELL"
        elif votes_buy > votes_sell:
            verdict = "WEAK BUY"
        elif votes_sell > votes_buy:
            verdict = "WEAK SELL"
        else:
            verdict = "NEUTRAL"

        # ── Hard gate: per Chan §3.1, mean reversion requires stationarity ──
        # If the stationarity gate voted SELL (not mean-reverting), block the
        # signal regardless of what the other tools voted.  Non-stationary series
        # cannot be traded with mean-reversion logic — it's a falling-knife chase.
        stationarity_blocked = False
        try:
            for d in details:
                if d.get("Tool") == "Stationarity" and d.get("Vote") == "SELL":
                    stationarity_blocked = True
                    break
        except Exception:
            pass
        if stationarity_blocked and verdict not in ("NEUTRAL", "HOLD"):
            verdict = "BLOCKED"
            confidence = 0

        # SL = 2× ATR below 20-day low (not fixed % — respects actual volatility)
        # Chan Ch.6: stops should be volatility-aware, not arbitrary percentages.
        recent_20d_low = float(close.tail(20).min())
        sl = round(recent_20d_low - 2 * atr_val, 2)
        levels = ATR_TARGETS["reversion"]
        tp1 = round(mid_bb, 2)            # Target: middle BB (mean reversion target)
        tp2 = round(price + levels["tp2"] * atr_val, 2)
        tp3 = round(price + levels["tp3"] * atr_val, 2)

        summary = [{
            "Symbol": symbol.upper(), "Strategy": "B-Mean Reversion",
            "Price": round(price, 2), "Verdict": verdict,
            "Confidence %": confidence,
            "Votes BUY": votes_buy, "Votes SELL": votes_sell, "Votes HOLD": votes_hold,
            "RSI": round(rsi_val, 1) if 'rsi_val' in dir() else "N/A",
            "Stop Loss": sl, "TP1": tp1, "TP2": tp2, "TP3": tp3,
            "Sharpe": round(sharpe_ratio, 2),
        }]

        # Telegram: chart + breakdown for all actionable verdicts
        if verdict not in ("NEUTRAL", "HOLD"):
            try:
                alert_signal_with_chart(
                    symbol=symbol.upper(), verdict=f"[B] Reversion: {verdict}",
                    confidence=confidence, price=round(price, 2),
                    stop_loss=sl, tp1=tp1, tp2=tp2, tp3=tp3,
                    votes_buy=votes_buy, votes_sell=votes_sell,
                    votes_hold=votes_hold, df=df,
                )
                _send_consensus_breakdown(
                    symbol=symbol.upper(), verdict=f"[B] Reversion: {verdict}",
                    confidence=confidence, price=round(price, 2),
                    votes_buy=votes_buy, votes_sell=votes_sell, votes_hold=votes_hold,
                    sl=sl, tp1=tp1, tp2=tp2, tp3=tp3, details=details,
                )
            except NON_FATAL_ANALYSIS_ERROR as e:
                logger.warning(f"[B] Telegram alert failed: {e}")

        # Send BUY/STRONG BUY/SELL/STRONG SELL — let on_signal() decide
        if verdict not in ("NEUTRAL", "HOLD"):
            try:
                trade_result = auto_trade_signal(
                    symbol=symbol.upper(), verdict=verdict,
                    confidence=confidence, price=round(price, 2),
                    stop_loss=sl, take_profit=tp1,
                    votes_buy=votes_buy, votes_sell=votes_sell,
                    votes_hold=votes_hold, details={"strategy": "B"},
                    strategy_id="B",
                )
                if trade_result:
                    summary[0]["Auto_Trade"] = "EXECUTED" if trade_result.get("executed") else "REJECTED"
                    summary[0]["Trade_Reason"] = trade_result.get("reason", "")
            except NON_FATAL_ANALYSIS_ERROR as e:
                logger.error(f"[B] Auto-trade error for {symbol}: {e}")

        return summary + details

    except NON_FATAL_ANALYSIS_ERROR as e:
        return [{"Error": str(e), "Strategy": "B-Reversion"}]


# Forecast models + RL agents for consensus_ml — DISABLED by default (2026-06).
# The forecast models are broken: the loss compares a [B, horizon] target against a
# [B, 1] prediction (torch broadcasting warning), so training is meaningless —
# measured directional accuracy ~= 50% (coin-flip), Sharpe ~= 0, and none pass the
# model_registry quality gate. Invoking them on every scan only burned Railway
# compute, failed 3x each ("giving up until restart"), and flooded the error log
# with ~900 warnings per scan. Defaulting to EMPTY makes consensus_ml fall through
# to the technical path instead of acting on garbage model votes. Re-enable via
# app_cfg.consensus_ml_models / consensus_ml_agents ONLY after the shape bug is fixed
# and the models pass the quality gate.
_CONSENSUS_ML_MODELS = getattr(app_cfg, "consensus_ml_models", [])
_CONSENSUS_ML_AGENTS = getattr(app_cfg, "consensus_ml_agents", [])


def _run_consensus_forecast_model(model_name, symbol, horizon, df, price):
    """Run a single forecast model via the factory, return (vote, signal_str, forecast_price)."""
    try:
        SEQ_LEN = 30
        X_train, y_train, X_test, y_test, mean_p, std_p = _build_feature_sequences(df, SEQ_LEN, horizon)

        preds = _predict_persisted_model(model_name, X_test)
        if preds is None:
            # Try on-the-fly training for lightweight models
            try:
                model = _create_forecast_model(model_name)
                model.fit(X_train, y_train)
                preds = model.predict(X_test[-1:])
            except Exception:
                return "HOLD", f"{model_name}: unavailable", None

        pred_prices = preds[0] * std_p + mean_p
        last_forecast = pred_prices[-1] if hasattr(pred_prices, "__len__") else float(pred_prices)
        pct_chg = float((last_forecast - price) / price * 100)

        if pct_chg > 2:
            vote = "BUY"
        elif pct_chg < -2:
            vote = "SELL"
        else:
            vote = "HOLD"
        sig = f"${last_forecast:.2f} ({pct_chg:+.1f}%)"
        return vote, sig, float(last_forecast)
    except Exception as e:
        logger.debug(f"{model_name} forecast error: {e}")
        return "-", f"{model_name}: ERROR", None


def _run_consensus_agent(agent_name, symbol, episodes, df):
    """Run a single RL agent via factory, return (vote, signal_str)."""
    # Check DB cache for pre-trained RL results
    # CR-6: on cache miss → skip (don't train on the fly)
    _pretrained_agents = ("double_dqn", "policy_gradient")
    if df is None and agent_name in _pretrained_agents:
        prefix_map = {
            "double_dqn": "pretrained_dqn",
            "policy_gradient": "pretrained_policy_gradient",
        }
        cached_key = f"{prefix_map[agent_name]}|symbol={symbol.upper()}"
        cached = db_cache_get(cached_key)
        if cached is not None and len(cached) > 0:
            summary = cached[0]
            signal_str = str(summary.get("Signal", summary.get("Recommendation", "HOLD")))
            reward = summary.get("Avg Reward", summary.get("Final Reward", 0))
            try:
                reward_f = float(reward) if reward else 0.0
            except (TypeError, ValueError):
                reward_f = 0.0
            if signal_str.upper() == "BUY":
                vote = "BUY"
            elif signal_str.upper() == "SELL":
                vote = "SELL"
            else:
                vote = "HOLD"
            sig = f"{signal_str} (R={reward_f:.1f})"
            return vote, sig
        # Cache miss for pre-trained agent → skip (no inline training)
        logger.warning("CR-6: %s cache miss for %s — skipping", agent_name, symbol)
        return "-", f"{agent_name}: no cache (skipped)"

    try:
        from openbb_forecast.agents.environment import TradingEnvironment
        from openbb_forecast.backtesting.transaction_costs import TransactionCostModel
        from openbb_forecast.risk.manager import RiskManager

        prices = np.array(df["close"].values, dtype=np.float64).flatten()
        prices = prices[~np.isnan(prices)]
        split = int(len(prices) * 0.8)
        train_prices = prices[:split]

        cost_model = TransactionCostModel(commission_bps=10)
        try:
            risk_mgr = RiskManager(max_position_size=0.2, stop_loss_pct=0.05, max_drawdown_pct=0.15)
        except Exception:
            risk_mgr = None

        env = TradingEnvironment(
            prices=train_prices, window_size=30,
            initial_capital=10000, cost_model=cost_model,
            risk_manager=risk_mgr,
        )

        agent = _create_rl_agent(agent_name, state_size=env.state_size, action_size=env.action_size)
        train_result = agent.train(env, episodes=episodes)
        rewards = train_result["episode_rewards"] if isinstance(train_result, dict) else train_result
        state = env.reset()
        action = agent.select_action(state)
        action_map = {0: "HOLD", 1: "BUY", 2: "SELL"}
        action_str = action_map.get(int(action), "HOLD")

        reward = float(np.mean(rewards[-10:])) if len(rewards) >= 10 else float(np.mean(rewards)) if rewards else 0
        if action_str == "BUY":
            vote = "BUY"
        elif action_str == "SELL":
            vote = "SELL"
        else:
            vote = "HOLD"

        sig = f"{action_str} (R={reward:.1f})"
        return vote, sig
    except Exception as e:
        logger.debug(f"{agent_name} agent error: {e}")
        return "-", f"{agent_name}: ERROR"


def _impl_run_consensus_ml(symbol, horizon=7, episodes=5, df_override=None, as_of=None):
    """Strategy C: AI Ensemble — pure ML decision-making.

    Dynamically discovers all registered forecast models and RL agents
    via the factory, runs each as a voting tool.

    Entry: 60% of tools vote BUY
    Exit: default trailing stop, TP at average predicted price
    """
    try:
        is_halal, halal_reason = verify_halal(symbol)
        if not is_halal:
            return [{"Symbol": symbol.upper(), "Verdict": "BLOCKED",
                     "Strategy": "C-ML", "Error": f"Not halal: {halal_reason}"}]

        votes_buy, votes_sell, votes_hold = 0, 0, 0
        details = []
        predicted_prices = []

        df = _load_consensus_df(symbol, df_override=df_override, as_of=as_of)
        if df is None:
            return [{"Error": f"No data for {symbol}"}]
        price = float(df["close"].iloc[-1])
        atr_val = float(atr(df).iloc[-1])

        # --- Forecast models (price prediction) ---
        for model_name in _CONSENSUS_ML_MODELS:
            vote, sig, fcast_price = _run_consensus_forecast_model(model_name, symbol, horizon, df, price)
            if vote == "BUY":
                votes_buy += 1
                if fcast_price is not None:
                    predicted_prices.append(fcast_price)
            elif vote == "SELL":
                votes_sell += 1
            elif vote == "-":
                pass  # skip non-functional, don't count as HOLD
            else:
                votes_hold += 1
            details.append({"Tool": model_name.upper(), "Signal": sig, "Vote": vote})

        # --- RL agents (action-based) ---
        for agent_name in _CONSENSUS_ML_AGENTS:
            vote, sig = _run_consensus_agent(agent_name, symbol, episodes, df)
            if vote == "BUY":
                votes_buy += 1
            elif vote == "SELL":
                votes_sell += 1
            elif vote == "-":
                pass
            else:
                votes_hold += 1
            details.append({"Tool": agent_name.upper(), "Signal": sig, "Vote": vote})

        # --- Verdict ---
        total = votes_buy + votes_sell + votes_hold
        if total == 0: total = 1
        confidence = round(max(votes_buy, votes_sell) / total * 100, 1)

        if votes_buy >= 3:
            verdict = "STRONG BUY"
        elif votes_buy >= 2 and confidence >= 50:
            verdict = "BUY"
        elif votes_sell >= 3:
            verdict = "STRONG SELL"
        elif votes_sell >= 2 and confidence >= 50:
            verdict = "SELL"
        elif votes_buy > votes_sell:
            verdict = "WEAK BUY"
        elif votes_sell > votes_buy:
            verdict = "WEAK SELL"
        else:
            verdict = "NEUTRAL"

        # TP at average ML predicted price or 2x ATR
        if predicted_prices:
            tp1 = round(sum(predicted_prices) / len(predicted_prices), 2)
        else:
            tp1 = round(price + 2.0 * atr_val, 2)
        sl = round(price - 1.5 * atr_val, 2)
        tp2 = round(price + 3.0 * atr_val, 2)
        tp3 = round(price + 5.0 * atr_val, 2)

        summary = [{
            "Symbol": symbol.upper(), "Strategy": "C-AI Ensemble",
            "Price": round(price, 2), "Verdict": verdict,
            "Confidence %": confidence,
            "Votes BUY": votes_buy, "Votes SELL": votes_sell, "Votes HOLD": votes_hold,
            "ML Predicted TP": tp1 if predicted_prices else "N/A",
            "Stop Loss": sl, "TP1": tp1, "TP2": tp2, "TP3": tp3,
        }]

        # Telegram: chart + breakdown for all actionable verdicts
        if verdict not in ("NEUTRAL", "HOLD"):
            try:
                alert_signal_with_chart(
                    symbol=symbol.upper(), verdict=f"[C] AI: {verdict}",
                    confidence=confidence, price=round(price, 2),
                    stop_loss=sl, tp1=tp1, tp2=tp2, tp3=tp3,
                    votes_buy=votes_buy, votes_sell=votes_sell,
                    votes_hold=votes_hold, df=df,
                )
                _send_consensus_breakdown(
                    symbol=symbol.upper(), verdict=f"[C] AI: {verdict}",
                    confidence=confidence, price=round(price, 2),
                    votes_buy=votes_buy, votes_sell=votes_sell, votes_hold=votes_hold,
                    sl=sl, tp1=tp1, tp2=tp2, tp3=tp3, details=details,
                )
            except NON_FATAL_ANALYSIS_ERROR as e:
                logger.warning(f"[C] Telegram alert failed: {e}")

        # Send BUY/STRONG BUY/SELL/STRONG SELL — let on_signal() decide
        if verdict not in ("NEUTRAL", "HOLD"):
            try:
                trade_result = auto_trade_signal(
                    symbol=symbol.upper(), verdict=verdict,
                    confidence=confidence, price=round(price, 2),
                    stop_loss=sl, take_profit=tp1,
                    votes_buy=votes_buy, votes_sell=votes_sell,
                    votes_hold=votes_hold, details={"strategy": "C"},
                    strategy_id="C",
                )
                if trade_result:
                    summary[0]["Auto_Trade"] = "EXECUTED" if trade_result.get("executed") else "REJECTED"
                    summary[0]["Trade_Reason"] = trade_result.get("reason", "")
            except NON_FATAL_ANALYSIS_ERROR as e:
                logger.error(f"[C] Auto-trade error for {symbol}: {e}")

        return summary + details

    except NON_FATAL_ANALYSIS_ERROR as e:
        return [{"Error": str(e), "Strategy": "C-ML"}]


def run_batch_consensus(min_swing_score=55, horizon=5, episodes=5, max_stocks=10):
    try:
        # الخطوة 1: جلب Active Buy Signals
        screener_results = run_screener()
        buy_signals = [r for r in screener_results if r.get("swing_score", 0) >= min_swing_score]
        
        if not buy_signals:
            return [{"Message": "No active buy signals found"}]
        
        # حد أقصى للسرعة
        buy_signals = buy_signals[:max_stocks]
        
        summary_header = [{
            "Info": f"Scanning {len(buy_signals)} stocks from Active Buy Signals",
            "Min Score": min_swing_score,
            "Horizon": f"{horizon} days",
            "Status": "Running..."
        }]
        
        results = []
        for stock in buy_signals:
            symbol = stock["symbol"]
            try:
                consensus = run_consensus(symbol, horizon=horizon, episodes=episodes)
                if consensus and len(consensus) > 0:
                    row = consensus[0].copy()
                    row["Swing Score"] = stock.get("swing_score", 0)
                    row["ATR Pct"] = stock.get("atr_pct", 0)
                    results.append(row)
            except NON_FATAL_ANALYSIS_ERROR as e:
                results.append({
                    "Symbol": symbol,
                    "Verdict": "ERROR",
                    "Confidence %": 0,
                    "Votes BUY": 0,
                    "Votes SELL": 0,
                    "Votes HOLD": 0,
                    "Action": f"Error: {str(e)[:50]}",
                    "Swing Score": stock.get("swing_score", 0),
                })
        
        # ترتيب حسب الثقة والتصويت
        results.sort(key=lambda x: (
            x.get("Votes BUY", 0) - x.get("Votes SELL", 0),
            x.get("Confidence %", 0)
        ), reverse=True)
        
        return summary_header + results

    except NON_FATAL_ANALYSIS_ERROR as e:
        return [{"Error": str(e)}]


@dataclass(frozen=True)
class ConsensusProfile:
    name: str
    strategy: str
    weights: dict[str, float]
    default_horizon: int
    default_episodes: int


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _coerce_as_of(as_of):
    if as_of is None or isinstance(as_of, datetime):
        return as_of
    try:
        return pd.Timestamp(as_of).to_pydatetime()
    except NON_FATAL_ANALYSIS_ERROR:
        return None


def _to_utc_datetimes(values):
    ts = pd.to_datetime(values, errors="coerce")
    if isinstance(ts, pd.Series):
        if ts.dt.tz is None:
            ts = ts.dt.tz_localize("America/New_York")
        return ts.dt.tz_convert("UTC")
    idx = pd.DatetimeIndex(ts)
    if idx.tz is None:
        idx = idx.tz_localize("America/New_York")
    return idx.tz_convert("UTC")


def _apply_signal_cutoff(df, as_of=None):
    if df is None or len(df) == 0:
        return df

    cutoff = signal_cutoff(_coerce_as_of(as_of)).astimezone(timezone.utc)
    if "date" in df.columns:
        date_series = _to_utc_datetimes(df["date"])
        mask = date_series <= cutoff
        sliced = df.loc[mask.fillna(False)].copy()
        return sliced if not sliced.empty else df.copy()

    index_series = _to_utc_datetimes(df.index)
    sliced = df.loc[index_series <= cutoff].copy()
    return sliced if not sliced.empty else df.copy()


def _load_consensus_df(symbol, df_override=None, as_of=None):
    df = df_override.copy() if df_override is not None else fetch_yf(symbol)
    if df is None:
        return None
    return _apply_signal_cutoff(df, as_of=as_of)


def _persist_consensus_result(symbol: str, profile: str, result: list[dict]) -> None:
    if not result or not isinstance(result, list):
        return
    summary = result[0] or {}
    if summary.get("Error"):
        return

    try:
        from app.db.database import SessionLocal
        from app.db.models import ConsensusLog, SignalHistory

        db = SessionLocal()
        try:
            payload = {
                "profile": profile,
                "rows": result[1:],
                # Phase 0 instrumentation: numeric summary scores under
                # details["breakdown"] so attribution can measure components
                # exactly on future signals (additive — no behavior change).
                "breakdown": {
                    k: v for k, v in summary.items()
                    if isinstance(v, (int, float)) and not isinstance(v, bool)
                },
            }
            db.add(
                ConsensusLog(
                    symbol=symbol.upper(),
                    profile=profile,
                    verdict=summary.get("Verdict", "UNKNOWN"),
                    confidence=float(summary.get("Confidence %", 0) or 0),
                    votes_buy=int(summary.get("Votes BUY", 0) or 0),
                    votes_sell=int(summary.get("Votes SELL", 0) or 0),
                    votes_hold=int(summary.get("Votes HOLD", 0) or 0),
                    price=float(summary.get("Price", 0) or 0),
                    details=payload,
                )
            )
            db.add(
                SignalHistory(
                    symbol=symbol.upper(),
                    signal_type="consensus",
                    signal=summary.get("Verdict", "UNKNOWN"),
                    score=float(summary.get("Confidence %", 0) or 0),
                    price=float(summary.get("Price", 0) or 0),
                    stop_loss=float(summary.get("Stop Loss", 0) or 0),
                    take_profit=float(summary.get("TP1", 0) or 0),
                    confidence=float(summary.get("Confidence %", 0) or 0),
                    details=payload,
                )
            )
            db.commit()
        finally:
            db.close()
    except NON_FATAL_PERSISTENCE_ERROR as exc:
        logger.warning("Consensus persistence skipped for %s/%s: %s", symbol, profile, exc)


_legacy_run_consensus_base = _impl_run_consensus_base
_legacy_run_consensus_momentum = _impl_run_consensus_momentum
_legacy_run_consensus_reversion = _impl_run_consensus_reversion
_legacy_run_consensus_ml = _impl_run_consensus_ml

CONSENSUS_PROFILES = {
    "base": ConsensusProfile(
        name="base",
        strategy="Base",
        weights={"technical": 0.4, "stat": 0.3, "ml": 0.3},
        default_horizon=5,
        default_episodes=10,
    ),
    "momentum": ConsensusProfile(
        name="momentum",
        strategy="A-Momentum",
        weights={"trend": 0.5, "confirmation": 0.3, "ml": 0.2},
        default_horizon=5,
        default_episodes=3,
    ),
    "reversion": ConsensusProfile(
        name="reversion",
        strategy="B-Reversion",
        weights={"oversold": 0.5, "volatility": 0.3, "confirmation": 0.2},
        default_horizon=3,
        default_episodes=3,
    ),
    "ml": ConsensusProfile(
        name="ml",
        strategy="C-ML",
        weights={"lstm": 0.34, "transformer": 0.33, "ensemble": 0.33},
        default_horizon=7,
        default_episodes=5,
    ),
}


def _consensus_technical_fallback(symbol, df, profile_name="ml"):
    """Fallback to technical scoring when no ML/RL models can analyze."""
    from app.services.scoring import weighted_score
    try:
        spy_df = _load_consensus_df("SPY")
        result = weighted_score(df, spy_df=spy_df)
        total = result.get("total", 0)
        verdict = "NEUTRAL"
        if total >= 80:
            verdict = "STRONG BUY"
        elif total >= 65:
            verdict = "BUY"
        elif total <= 20:
            verdict = "STRONG SELL"
        elif total <= 35:
            verdict = "SELL"
        return [{
            "Symbol": symbol.upper(), "Strategy": f"C-{profile_name}",
            "Price": round(float(df["close"].iloc[-1]), 2),
            "Verdict": verdict, "Confidence %": result.get("confidence", 0),
            "Votes BUY": 1 if total >= 65 else 0,
            "Votes SELL": 1 if total <= 35 else 0,
            "Votes HOLD": 0,
            "Detail": "التحليل تقني فقط — خارج universe التدريب",
            "Technical Score": total,
        }]
    except Exception as e:
        return [{"Error": f"Technical fallback failed: {e}"}]


def run_consensus(symbol, horizon=5, episodes=10, profile="base", df_override=None, as_of=None):
    profile_name = (profile or "base").lower()
    meta = CONSENSUS_PROFILES.get(profile_name)
    if meta is None:
        return [{"Error": f"Unknown consensus profile: {profile_name}"}]

    # ── Halal gate ──
    is_halal, reason = verify_halal(symbol)
    curated = symbol.upper() in _get_curated_set()
    if not is_halal:
        return [{
            "Symbol": symbol.upper(), "Price": 0, "Verdict": "BLOCKED",
            "Confidence %": 0, "Signal": "BLOCKED",
            "Tool": "HalalGate", "Vote": "-",
            "Error": f"❌ خارج قائمة الحلال — {reason}",
        }]
    if not curated:
        logger.info("Consensus for %s: خارج universe التدريب — التحليل تقني فقط", symbol)

    prepared_df = _load_consensus_df(symbol, df_override=df_override, as_of=as_of)

    if profile_name == "base":
        result = _legacy_run_consensus_base(
            symbol,
            horizon=horizon or meta.default_horizon,
            episodes=episodes or meta.default_episodes,
            df_override=prepared_df,
            as_of=as_of,
        )
    elif profile_name == "momentum":
        result = _legacy_run_consensus_momentum(
            symbol,
            horizon=horizon or meta.default_horizon,
            df_override=prepared_df,
            as_of=as_of,
        )
    elif profile_name == "reversion":
        result = _legacy_run_consensus_reversion(
            symbol,
            horizon=horizon or meta.default_horizon,
            df_override=prepared_df,
            as_of=as_of,
        )
    else:
        result = _legacy_run_consensus_ml(
            symbol,
            horizon=horizon or meta.default_horizon,
            episodes=episodes or meta.default_episodes,
            df_override=prepared_df,
            as_of=as_of,
        )

    # ── Fallback: if ML profile returned too few votes, use technical score ──
    if profile_name == "ml" and not curated and result and isinstance(result, list):
        first = result[0]
        votes_buy = int(first.get("Votes BUY", 0))
        votes_sell = int(first.get("Votes SELL", 0))
        votes_total = votes_buy + votes_sell + int(first.get("Votes HOLD", 0))
        if votes_total < 3 and first.get("Error") is None:
            logger.info("Consensus ML: %d votes only (untrained symbol) — using technical fallback", votes_total)
            fallback = _consensus_technical_fallback(symbol, prepared_df, profile_name)
            if fallback and "Error" not in fallback[0]:
                result = fallback

    _persist_consensus_result(symbol, profile_name, result)
    return result


def run_consensus_momentum(symbol, horizon=5, df_override=None, as_of=None):
    return run_consensus(
        symbol,
        horizon=horizon,
        episodes=CONSENSUS_PROFILES["momentum"].default_episodes,
        profile="momentum",
        df_override=df_override,
        as_of=as_of,
    )


def run_consensus_reversion(symbol, horizon=3, df_override=None, as_of=None):
    return run_consensus(
        symbol,
        horizon=horizon,
        episodes=CONSENSUS_PROFILES["reversion"].default_episodes,
        profile="reversion",
        df_override=df_override,
        as_of=as_of,
    )


def run_consensus_ml(symbol, horizon=7, episodes=5, df_override=None, as_of=None):
    return run_consensus(
        symbol,
        horizon=horizon,
        episodes=episodes,
        profile="ml",
        df_override=df_override,
        as_of=as_of,
    )


# Backward compat shim
from app.services.universe import HALAL_STOCKS_FALLBACK as RUSSELL_1000_HALAL  # noqa: F811

def _quick_filter_one(symbol):
    """Quick filter for pipeline stage 1."""
    try:
        df = fetch_yf(symbol)
        if df is None: return None
        close = df["close"]
        ema21_val = float(ema(close, 21).iloc[-1])
        ema9_val  = float(ema(close, 9).iloc[-1])
        rsi_val   = float(rsi(close).iloc[-1])
        vol_ma    = float(df["volume"].rolling(20).mean().iloc[-1])
        vol_ratio = float(df["volume"].iloc[-1] / vol_ma) if vol_ma > 0 else 0
        price     = float(close.iloc[-1])
        score = sum([price > ema21_val, ema9_val > ema21_val, 30 <= rsi_val <= 75, vol_ratio >= 0.8])
        if score >= 2:
            return {"symbol": symbol, "price": round(price,2), "rsi": round(rsi_val,1), "quick_score": score}
    except NON_FATAL_ANALYSIS_ERROR as e:
        logger.debug(f"Quick filter {symbol}: {e}")
    return None

def run_pipeline(min_confidence=40, max_final=15, horizon=5, episodes=5):
    try:
        # Stage 1: Quick filter (concurrent)
        quick_results = []
        with ThreadPoolExecutor(max_workers=settings.SCREENER_WORKERS) as pool:
            futures = {pool.submit(_quick_filter_one, s): s for s in _universe_symbols()}
            for f in as_completed(futures):
                r = f.result()
                if r: quick_results.append(r)
        quick_results.sort(key=lambda x: x["quick_score"], reverse=True)
        top100 = quick_results[:100]

        # المرحلة 2: Halal Screener
        screener_results = []
        for item in top100:
            try:
                df = fetch_yf(item["symbol"])
                if df is None: continue
                r = analyze(item["symbol"], df)
                if r and r["swing_score"] >= 45:
                    screener_results.append(r)
            except NON_FATAL_ANALYSIS_ERROR as e:
                logger.debug(f"Pipeline screener {item['symbol']}: {e}")
                continue
        screener_results.sort(key=lambda x: x["swing_score"], reverse=True)
        top30 = screener_results[:30]

        # المرحلة 3: USX Pro
        top30_symbols = [r["symbol"] for r in top30]
        usx_filtered = []
        try:
            usx_all = run_usx_screener(min_score=5)
            usx_filtered = [x for x in usx_all if x.get("Symbol") in top30_symbols]
            usx_filtered.sort(key=lambda x: int(x.get("Score","0/10").split("/")[0]), reverse=True)
            top15 = [x["Symbol"] for x in usx_filtered[:15]]
        except NON_FATAL_ANALYSIS_ERROR as e:
            logger.warning(f"Pipeline USX stage failed: {e}")
            top15 = top30_symbols[:15]

        # المرحلة 4: AI Consensus
        final_results = []
        for symbol in top15:
            try:
                consensus = run_consensus(symbol, horizon=horizon, episodes=episodes)
                if consensus and len(consensus) > 0:
                    row = consensus[0].copy()
                    screener_info = next((r for r in screener_results if r["symbol"] == symbol), {})
                    usx_info = next((r for r in usx_filtered if r.get("Symbol") == symbol), {})
                    row["Swing Score"] = screener_info.get("swing_score", 0)
                    row["USX Score"]   = usx_info.get("Score", "N/A")
                    row["RSI"]         = screener_info.get("rsi", 0)
                    row["Signals"]     = screener_info.get("signals", "")
                    final_results.append(row)
            except NON_FATAL_ANALYSIS_ERROR as e:
                final_results.append({"Symbol": symbol, "Verdict": "ERROR", "Confidence %": 0, "Action": str(e)[:30], "Swing Score": 0})

        final_results.sort(key=lambda x: (
            x.get("Votes BUY", 0) - x.get("Votes SELL", 0),
            x.get("Confidence %", 0)
        ), reverse=True)

        strong_buy = [r for r in final_results if "STRONG BUY" in r.get("Verdict","")]
        buy        = [r for r in final_results if r.get("Verdict","") == "BUY"]
        weak_buy   = [r for r in final_results if r.get("Verdict","") == "WEAK BUY"]

        header = [{
            "Pipeline":         "Universe → Full AI Pipeline",
            "Total Scanned":    len(_universe_symbols()),
            "After Quick Filter": len(top100),
            "After Screener":   len(top30),
            "After USX Pro":    len(top15),
            "Final Results":    len(final_results),
            "STRONG BUY":       len(strong_buy),
            "BUY":              len(buy),
            "WEAK BUY":         len(weak_buy),
            "Best Stock":       final_results[0]["Symbol"] if final_results else "None",
            "Best Confidence":  f'{final_results[0].get("Confidence %", 0)}%' if final_results else "0%",
            "Best Action":      final_results[0].get("Action","") if final_results else "",
        }]

        return header + final_results

    except NON_FATAL_ANALYSIS_ERROR as e:
        return [{"Error": str(e)}]

async def widgets():
    return {
        # ===== MAIN: Ready to Trade (THE one screen) =====
        "ready_to_trade": {
            "name": "Ready to Trade",
            "description": "Stocks that passed BOTH screener AND AI consensus — ready for execution",
            "category": "Trading", "type": "table",
            "endpoint": "/ready",
            "gridData": {"w": 20, "h": 12},
            "params": [
                {"paramName": "min_swing", "value": "55", "label": "Min Swing Score", "type": "text", "show": True},
                {"paramName": "max_stocks", "value": "25", "label": "Max Stocks", "type": "text", "show": True},
            ],
        },

        # ===== Portfolio =====
        "portfolio_summary": {
            "name": "Portfolio & Positions",
            "description": "Equity, cash, P&L, open positions",
            "category": "Portfolio", "type": "table",
            "endpoint": "/portfolio/summary",
            "gridData": {"w": 10, "h": 5},
        },

        # ===== Claude AI Agent =====
        "ai_agent": {
            "name": "Claude AI Agent",
            "description": "Ask anything: halal check, analysis, trade plan",
            "category": "AI", "type": "table",
            "endpoint": "/agent/analyze",
            "gridData": {"w": 10, "h": 9},
            "params": [{"paramName": "symbol", "value": "AAPL", "label": "Symbol", "type": "text", "show": True}],
        },

        # ===== Deep Dive (optional, for single stock research) =====
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

# ============================================================
# UNIFIED BACKGROUND CACHE — ALL endpoints serve cached results
# OpenBB Pro has ~30s widget timeout. No endpoint should ever
# make it wait. Everything computes in background, serves instantly.
# ============================================================
_bg_cache = {}          # {"endpoint_key": result}
_cache_status = {}   # {"endpoint_key": "idle"|"running"|"done"}
_cache_time = {}     # {"endpoint_key": timestamp}
_cache_lock = threading.Lock()
_BG_CACHE_TTL = settings.BG_CACHE_TTL

def _cache_key(endpoint, **params):
    parts = [endpoint] + [f"{k}={v}" for k, v in sorted(params.items())]
    return "|".join(parts)

def _get_cached(key):
    try:
        from app.services.metrics import metrics
    except Exception:
        metrics = None
    with _cache_lock:
        cached = _bg_cache.get(key)
        status = _cache_status.get(key, "idle")
        ts = _cache_time.get(key, 0)
    if cached:
        if (time.time() - ts) > _BG_CACHE_TTL:
            if metrics:
                metrics.cache_miss("bg_cache")
            return cached, "stale"
        if metrics:
            metrics.cache_hit("bg_cache")
        return cached, status
    if metrics:
        metrics.cache_miss("bg_cache")
    return cached, status

def _bg_compute(key, func, args=(), kwargs=None):
    from app.services.metrics import metrics as _m
    with _cache_lock:
        if _cache_status.get(key) == "running":
            return
        _cache_status[key] = "running"
    _m.incr("bg_compute_starts", key=key.split("|")[0])
    try:
        start = time.perf_counter()
        result = func(*args, **(kwargs or {}))
        elapsed = time.perf_counter() - start
        _m.record_timing("bg_compute_duration", elapsed, key=key.split("|")[0])
        with _cache_lock:
            _bg_cache[key] = result
            _cache_status[key] = "done"
            _cache_time[key] = time.time()
    except NON_FATAL_ANALYSIS_ERROR as e:
        logger.error(f"Background compute {key} failed: {e}")
        with _cache_lock:
            _bg_cache[key] = [{"Error": str(e)}]
            _cache_status[key] = "done"
            _cache_time[key] = time.time()

def _serve_or_compute(key, func, args=(), kwargs=None, msg="Computing in background..."):
    """Return cached result instantly, or start background compute and return status message."""
    cached, status = _get_cached(key)
    if status == "stale":
        threading.Thread(target=_bg_compute, args=(key, func, args, kwargs), daemon=True).start()
        return cached  # serve stale while refreshing
    if cached:
        return cached
    if status != "running":
        threading.Thread(target=_bg_compute, args=(key, func, args, kwargs), daemon=True).start()
    return [{"Status": msg, "Info": "Data is being computed. Refresh the widget in 1-3 minutes."}]

# --- Screener-based endpoints (screener, buys, watchlist) ---
async def screener():
    key = _cache_key("screener")
    return _serve_or_compute(key, run_screener, msg="Computing halal screener...")

async def buys():
    key = _cache_key("screener")
    cached, status = _get_cached(key)
    if cached and isinstance(cached, list):
        return [r for r in cached if r.get("swing_score", 0) >= 55]
    if status != "running":
        threading.Thread(target=_bg_compute, args=(key, run_screener), daemon=True).start()
    return [{"Status": "Computing buy signals...", "Info": "Refresh in 1-2 minutes."}]

async def watchlist():
    key = _cache_key("screener")
    cached, status = _get_cached(key)
    if cached and isinstance(cached, list):
        return [r for r in cached if 35 <= r.get("swing_score", 0) < 55]
    if status != "running":
        threading.Thread(target=_bg_compute, args=(key, run_screener), daemon=True).start()
    return [{"Status": "Computing watchlist...", "Info": "Refresh in 1-2 minutes."}]

async def bcf_screener(portfolio: float = 100000):
    """Balanced Confluence Framework — moderate risk strategy."""
    validate_range(portfolio, "portfolio", 1000, 10_000_000)
    key = _cache_key("bcf", portfolio=portfolio)
    return _serve_or_compute(key, run_bcf_screener, args=(portfolio,), msg="Computing BCF screener...")

# --- Single-symbol endpoints ---
async def backtest(symbol: str = "AAPL", start_date: str = "2022-01-01", end_date: str = "2024-12-31", portfolio: float = 100000, risk_pct: float = 1.0, hold_days: int = 3):
    s = validate_symbol(symbol)
    validate_date(start_date); validate_date(end_date)
    validate_range(portfolio, "portfolio", 1000, 10_000_000)
    validate_range(risk_pct, "risk_pct", 0.1, 10.0)
    validate_range(hold_days, "hold_days", 1, 60)
    key = _cache_key("backtest", symbol=s, start=start_date, end=end_date, portfolio=portfolio, risk=risk_pct, hold=hold_days)
    return _serve_or_compute(key, run_backtest, args=(s, start_date, end_date, portfolio, risk_pct, hold_days), msg=f"Computing backtest for {s}...")

async def monte_carlo(symbol: str = "AAPL", days: int = 30, simulations: int = 1000):
    s = validate_symbol(symbol)
    validate_range(days, "days", 1, 365)
    validate_range(simulations, "simulations", 100, 10000)
    key = _cache_key("monte_carlo", symbol=s, days=days, sims=simulations)
    return _serve_or_compute(key, run_monte_carlo, args=(s, days, simulations), msg=f"Running Monte Carlo for {s}...")

async def lstm(symbol: str = "AAPL", horizon: int = 5):
    s = validate_symbol(symbol)
    validate_range(horizon, "horizon", 1, 30)
    if not check_rate_limit("lstm", 3):
        return [{"Status": "Rate limited", "Info": "Too many LSTM requests. Try again in 1 minute."}]
    key = _cache_key("lstm", symbol=s, horizon=horizon)
    return _serve_or_compute(key, run_lstm, args=(s, horizon), msg=f"Running LSTM for {s}...")

async def transformer(symbol: str = "AAPL", horizon: int = 5):
    s = validate_symbol(symbol)
    validate_range(horizon, "horizon", 1, 30)
    if not check_rate_limit("transformer", 3):
        return [{"Status": "Rate limited", "Info": "Too many Transformer requests. Try again in 1 minute."}]
    key = _cache_key("transformer", symbol=s, horizon=horizon)
    return _serve_or_compute(key, run_transformer, args=(s, horizon), msg=f"Running Transformer for {s}...")

async def ensemble(symbol: str = "AAPL", horizon: int = 5):
    s = validate_symbol(symbol)
    validate_range(horizon, "horizon", 1, 30)
    key = _cache_key("ensemble", symbol=s, horizon=horizon)
    return _serve_or_compute(key, run_ensemble, args=(s, horizon), msg=f"Running Ensemble for {s}...")

async def dqn(symbol: str = "AAPL", episodes: int = 20):
    s = validate_symbol(symbol)
    validate_range(episodes, "episodes", 1, 100)
    if not check_rate_limit("dqn", 2):
        return [{"Status": "Rate limited", "Info": "Too many DQN requests. Try again in 1 minute."}]
    key = _cache_key("dqn", symbol=s, episodes=episodes)
    return _serve_or_compute(key, run_dqn, args=(s, episodes), msg=f"Running DQN for {s}...")

async def policy_gradient(symbol: str = "AAPL", episodes: int = 20):
    s = validate_symbol(symbol)
    validate_range(episodes, "episodes", 1, 100)
    if not check_rate_limit("policy_gradient", 2):
        return [{"Status": "Rate limited", "Info": "Too many Policy Gradient requests. Try again in 1 minute."}]
    key = _cache_key("policy_gradient", symbol=s, episodes=episodes)
    return _serve_or_compute(key, run_policy_gradient, args=(s, episodes), msg=f"Running Policy Gradient for {s}...")

async def usx(min_score: int = 7):
    validate_range(min_score, "min_score", 1, 10)
    key = _cache_key("usx", min_score=min_score)
    return _serve_or_compute(key, run_usx_screener, args=(min_score,), msg="Computing USX screener...")

async def consensus(symbol: str = "AAPL", horizon: int = 5, episodes: int = 10):
    s = validate_symbol(symbol)
    validate_range(horizon, "horizon", 1, 30)
    validate_range(episodes, "episodes", 1, 50)
    # No rate limiter — the background cache already deduplicates work
    key = _cache_key("consensus", symbol=s, horizon=horizon, episodes=episodes)
    return _serve_or_compute(key, run_consensus, args=(s, horizon, episodes), msg=f"Computing AI consensus for {s}...")

async def consensus_momentum(symbol: str = "AAPL", horizon: int = 5):
    s = validate_symbol(symbol)
    validate_range(horizon, "horizon", 1, 30)
    key = _cache_key("consensus_momentum", symbol=s, horizon=horizon)
    return _serve_or_compute(key, run_consensus_momentum, args=(s, horizon), msg=f"Computing momentum consensus for {s}...")

async def consensus_reversion(symbol: str = "AAPL", horizon: int = 3):
    s = validate_symbol(symbol)
    validate_range(horizon, "horizon", 1, 30)
    key = _cache_key("consensus_reversion", symbol=s, horizon=horizon)
    return _serve_or_compute(key, run_consensus_reversion, args=(s, horizon), msg=f"Computing reversion consensus for {s}...")

async def consensus_ml(symbol: str = "AAPL", horizon: int = 7, episodes: int = 5):
    s = validate_symbol(symbol)
    validate_range(horizon, "horizon", 1, 30)
    validate_range(episodes, "episodes", 1, 50)
    key = _cache_key("consensus_ml", symbol=s, horizon=horizon, episodes=episodes)
    return _serve_or_compute(key, run_consensus_ml, args=(s, horizon, episodes), msg=f"Computing ML consensus for {s}...")

async def batch_consensus(min_swing_score: int = 55, horizon: int = 5, episodes: int = 5, max_stocks: int = 10):
    key = _cache_key("batch_consensus", min=min_swing_score, h=horizon, ep=episodes, max=max_stocks)
    return _serve_or_compute(key, run_batch_consensus, args=(min_swing_score, horizon, episodes, max_stocks), msg="Computing batch consensus...")

async def pipeline(min_confidence: int = 40, max_final: int = 15, horizon: int = 5, episodes: int = 5):
    key = _cache_key("pipeline", conf=min_confidence, max=max_final, h=horizon, ep=episodes)
    return _serve_or_compute(key, run_pipeline, args=(min_confidence, max_final, horizon, episodes), msg="Computing full pipeline...")


# ============================================================================
# READY TO TRADE — Single clean screen showing only actionable stocks
# ============================================================================

def run_ready_to_trade(min_swing=55, max_stocks=25):
    """Full pipeline → only BUY/STRONG BUY stocks with all details.

    Flow: Screener (355 stocks) → Top by score → AI Consensus (14 tools)
          → Filter: only BUY or STRONG BUY → Clean output with trade plan

    Runs automatically at 9:00 AM ET (pre-market) so results are cached
    and ready when the market opens at 9:30 AM.
    """
    try:
        screener_results = run_screener()
        if not screener_results:
            return [{"Message": "Screener returned no results. Market may be closed."}]

        # Top candidates by swing score
        candidates = [r for r in screener_results if r.get("swing_score", 0) >= min_swing]
        candidates.sort(key=lambda x: x.get("swing_score", 0), reverse=True)
        candidates = candidates[:max_stocks]

        if not candidates:
            return [{"Message": f"No stocks with swing score >= {min_swing}",
                     "Total Scanned": len(screener_results)}]

        ready = []
        rejected = []

        for stock in candidates:
            symbol = stock["symbol"]
            try:
                _flush_all_caches()
                consensus = run_consensus(symbol, horizon=5, episodes=3)
                if not consensus or consensus[0].get("Error"):
                    continue

                c = consensus[0]
                verdict = c.get("Verdict", "")

                row = {
                    "Symbol": symbol,
                    "Verdict": verdict,
                    "Confidence %": c.get("Confidence %", 0),
                    "Price": c.get("Price", 0),
                    "Votes BUY": c.get("Votes BUY", 0),
                    "Votes SELL": c.get("Votes SELL", 0),
                    "Votes HOLD": c.get("Votes HOLD", 0),
                    "Swing Score": stock.get("swing_score", 0),
                    "RSI": stock.get("rsi", 0),
                    "ATR %": stock.get("atr_pct", 0),
                    "Stop Loss": c.get("Stop Loss", 0),
                    "TP1": c.get("TP1", 0),
                    "TP2": c.get("TP2", 0),
                    "TP3": c.get("TP3", 0),
                    "Chg 1W": stock.get("chg_1w", 0),
                    "Chg 1M": stock.get("chg_1m", 0),
                    "Volume Ratio": stock.get("volume_ratio", 0),
                }

                if verdict in ("STRONG BUY", "BUY", "WEAK BUY"):
                    ready.append(row)
                else:
                    rejected.append(f"{symbol}({verdict})")

            except NON_FATAL_ANALYSIS_ERROR as e:
                logger.error(f"Ready-to-trade {symbol}: {e}")
            finally:
                _flush_all_caches()
                gc.collect()

        # Sort: STRONG BUY first, then by confidence
        ready.sort(key=lambda x: (
            1 if x["Verdict"] == "STRONG BUY" else 0,
            x["Confidence %"]
        ), reverse=True)

        header = [{
            "Pipeline": f"Scanned {len(screener_results)} → Top {len(candidates)} → AI Consensus → {len(ready)} READY",
            "Ready to Trade": len(ready),
            "Rejected by AI": ", ".join(rejected) if rejected else "None",
            "Status": "READY" if ready else "NO TRADES — AI consensus blocked all candidates",
        }]

        if not ready:
            header[0]["Tip"] = "All top stocks were rejected by AI consensus. Market conditions may be unfavorable."

        return header + ready

    except NON_FATAL_ANALYSIS_ERROR as e:
        return [{"Error": str(e)}]


async def ready_to_trade(min_swing: int = 55, max_stocks: int = 25):
    """Single clean screen: only stocks that passed BOTH screener AND AI consensus."""
    key = _cache_key("ready_to_trade", min=min_swing, max=max_stocks)
    return _serve_or_compute(key, run_ready_to_trade, args=(min_swing, max_stocks),
                            msg="Analyzing top stocks through full AI pipeline... Pre-market scan runs at 9:00 AM ET.")


async def refresh_ready(min_swing: int = 55, max_stocks: int = 25, x_api_key: OperatorAPIKey = None):
    _require_api_key(x_api_key)
    key = _cache_key("ready_to_trade", min=min_swing, max=max_stocks)
    with _cache_lock:
        _bg_cache.pop(key, None)
        _cache_status[key] = "idle"
    threading.Thread(target=_bg_compute, args=(key, run_ready_to_trade, (min_swing, max_stocks)), daemon=True).start()
    return [{"Status": "Ready-to-Trade refresh started."}]

# --- Refresh endpoints (clear cache + recompute) ---
async def refresh_consensus(symbol: str = "AAPL", x_api_key: OperatorAPIKey = None):
    _require_api_key(x_api_key)
    s = validate_symbol(symbol)
    key = _cache_key("consensus", symbol=s, horizon=5, episodes=10)
    with _cache_lock:
        _bg_cache.pop(key, None)
        _cache_status[key] = "idle"
    threading.Thread(target=_bg_compute, args=(key, run_consensus, (s, 5, 10)), daemon=True).start()
    return {"Status": f"Consensus refresh started for {s}."}

async def refresh_batch(x_api_key: OperatorAPIKey = None):
    _require_api_key(x_api_key)
    key = _cache_key("batch_consensus", min=55, h=5, ep=5, max=10)
    with _cache_lock:
        _bg_cache.pop(key, None)
        _cache_status[key] = "idle"
    threading.Thread(target=_bg_compute, args=(key, run_batch_consensus, (55, 5, 5, 10)), daemon=True).start()
    return [{"Status": "Batch consensus refresh started"}]

async def refresh_pipeline(x_api_key: OperatorAPIKey = None):
    _require_api_key(x_api_key)
    key = _cache_key("pipeline", conf=40, max=15, h=5, ep=5)
    with _cache_lock:
        _bg_cache.pop(key, None)
        _cache_status[key] = "idle"
    threading.Thread(target=_bg_compute, args=(key, run_pipeline, (40, 15, 5, 5)), daemon=True).start()
    return [{"Status": "Pipeline refresh started"}]

# --- Pre-compute screener + USX + consensus on startup so widgets load instantly ---
def _precompute_consensus_for_top_buys():
    """After screener finishes, pre-warm consensus for top buy signals."""
    # Wait for screener cache to be ready (poll every 10s, max 5 min)
    screener_key = _cache_key("screener")
    for _ in range(30):
        cached, status = _get_cached(screener_key)
        if cached and isinstance(cached, list) and len(cached) > 0:
            break
        time.sleep(10)
    else:
        return  # screener never finished
    # Get top 1 buy signal and pre-warm its consensus (limit to 1 to avoid OOM on small containers)
    buys = [r for r in cached if r.get("swing_score", 0) >= 55][:1]
    for stock in buys:
        sym = stock["symbol"]
        key = _cache_key("consensus", symbol=sym, horizon=5, episodes=10)
        logger.info(f"Pre-warming consensus for {sym}...")
        _bg_compute(key, run_consensus, (sym, 5, 10))

async def _startup_bootstrap():
    # Fail-fast configuration guard (M1 hardening).
    # If AUTO_TRADE_ENABLED=True but required keys are missing or the base
    # URL points at live while we're still in M1, refuse to boot.
    try:
        from app.config import assert_ready_for_auto_trade, ConfigurationError
        assert_ready_for_auto_trade()
    except ConfigurationError as e:
        logger.critical(str(e))
        # Disable auto-trade rather than crash the read-only UI; an operator
        # who wants trading must fix the config and restart.
        from app.config import settings as _s
        _s.AUTO_TRADE_ENABLED = False
        try:
            from app.services.telegram_alert import send_message as _tg
            _tg(f"STARTUP BLOCKED auto-trade\n{e}")
        except NON_FATAL_ANALYSIS_ERROR:
            logger.exception("Failed to send startup-blocked Telegram alert")
    except NON_FATAL_ANALYSIS_ERROR as e:
        logger.error(f"Config validation error (non-fatal): {e}", exc_info=True)

    # Initialize database tables
    try:
        init_db()
        logger.info("Database initialized successfully.")
    except NON_FATAL_ANALYSIS_ERROR as e:
        logger.warning(f"Database init failed (non-fatal, using in-memory caches): {e}")

    # Reconcile broker positions with DB (M1 hardening).
    # If the process was killed mid-order, this closes the accounting gap
    # before any new signal can fire.
    try:
        from app.services.trading_engine import reconcile_all_strategies
        recon = reconcile_all_strategies()
        logger.info(f"Startup reconciliation complete: {recon}")
    except NON_FATAL_ANALYSIS_ERROR as e:
        logger.error(f"Startup reconciliation failed (non-fatal): {e}", exc_info=True)
    try:
        from app.services.regime import refresh_regime

        snapshot = refresh_regime()
        logger.info(f"Startup regime snapshot: {snapshot}")
    except NON_FATAL_ANALYSIS_ERROR as e:
        logger.error(f"Startup regime refresh failed (non-fatal): {e}", exc_info=True)
    logger.info("Pre-computing screener and USX data on startup...")
    threading.Thread(target=_bg_compute, args=(_cache_key("screener"), run_screener), daemon=True).start()
    # Wait for screener to finish before starting USX to avoid memory pressure
    def _delayed_usx():
        screener_key = _cache_key("screener")
        for _ in range(60):
            cached, status = _get_cached(screener_key)
            if cached and isinstance(cached, list) and len(cached) > 0:
                break
            time.sleep(5)
        _bg_compute(_cache_key("usx", min_score=7), run_usx_screener, (7,))
    threading.Thread(target=_delayed_usx, daemon=True).start()

    # Start the automated scheduler (only if not running as standalone worker)
    if os.environ.get("WORKER_SERVICE", "").lower() != "true":
        try:
            from app.services.scheduler import start_scheduler
            start_scheduler()
            logger.info("Automated scheduler started successfully (in-process)")
        except NON_FATAL_ANALYSIS_ERROR as e:
            logger.warning(f"Scheduler failed to start (non-fatal): {e}")
    else:
        logger.info("WORKER_SERVICE=true — scheduler delegated to standalone worker process")
    try:
        from app.services.fill_watcher import start_fill_watcher

        start_fill_watcher()
        logger.info("Fill watcher started successfully")
    except NON_FATAL_ANALYSIS_ERROR as e:
        logger.warning(f"Fill watcher failed to start (non-fatal): {e}")

# ============================================================
# PHASE 2: AAOIFI Halal Screening Endpoints
# ============================================================

def _json_safe(val, default=0):
    """Ensure a numeric value is JSON-serializable (no NaN/Inf)."""
    import math
    if val is None:
        return default
    if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
        return default
    return val


def _require_api_key(api_key: str | None):
    """Fail closed on operator/admin endpoints unless X-API-Key is valid."""
    if not settings.API_KEY:
        raise HTTPException(status_code=503, detail="Operator API key not configured")
    if api_key != settings.API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


# --- Uptime tracking ---
_BOOT_TIMESTAMP = time.time()


def _uptime_seconds() -> float:
    return time.time() - _BOOT_TIMESTAMP


def _check_model_degradation() -> dict:
    """Check persisted model performance against degradation thresholds.

    Returns dict with model status entries. Flags any model whose test
    Sharpe has dropped below 0.0 or test accuracy below 0.5.
    """
    from openbb_forecast.models.persistence import resolve_latest
    import json

    results = {}
    for model_name in ("lstm", "transformer", "ensemble"):
        suffix = ".pt" if model_name != "ensemble" else ".pkl"
        try:
            latest = resolve_latest(model_name, suffix)
            if latest is None:
                results[model_name] = {"status": "no_artifact", "sharpe": None}
                continue
            meta_path = latest.with_suffix(".meta.json")
            if not meta_path.exists():
                results[model_name] = {"status": "no_metadata", "sharpe": None}
                continue
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            test_sharpe = meta.get("test_sharpe", 0.0)
            test_acc = meta.get("test_acc", 0.0)
            if test_sharpe < 0.0:
                results[model_name] = {"status": "degraded", "sharpe": test_sharpe, "acc": test_acc}
            elif test_acc < 0.5:
                results[model_name] = {"status": "below_chance", "sharpe": test_sharpe, "acc": test_acc}
            else:
                results[model_name] = {"status": "healthy", "sharpe": test_sharpe, "acc": test_acc}
        except Exception as exc:
            results[model_name] = {"status": "check_error", "error": str(exc)}
    return results


async def halal_status(symbol: str = "AAPL"):
    """Get AAOIFI Halal compliance status for a single symbol."""
    s = validate_symbol(symbol)
    if not settings.FMP_API_KEY:
        return [{"Error": "FMP_API_KEY not configured. Get a free key at https://site.financialmodelingprep.com/developer/docs"}]
    try:
        # Run in thread pool — get_halal_status does blocking HTTP (FMP + yfinance fallback)
        result = await asyncio.to_thread(get_halal_status, s)
    except NON_FATAL_ANALYSIS_ERROR as e:
        logger.error(f"Halal screening crashed for {s}: {e}")
        result = None
    if result is None:
        return [{"Error": f"Could not retrieve fundamental data for {s}"}]
    # Format for OpenBB widget display — _json_safe guards against NaN from yfinance
    status = "HALAL" if result.get("is_halal") else "HARAM"
    return [{
        "Symbol": s,
        "Status": status,
        "Company": result.get("company_name", s),
        "Sector": result.get("sector", ""),
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

async def halal_verify(symbol: str):
    """Quick halal verification gate check for any symbol.

    Returns whether the stock is allowed for trading.
    Uses the 3-layer defense: sector exclusion → FMP AAOIFI → fail-closed.
    """
    s = validate_symbol(symbol)
    is_halal, reason = verify_halal(s)
    return [{
        "Symbol": s,
        "Allowed": is_halal,
        "Status": "HALAL - OK TO TRADE" if is_halal else "BLOCKED",
        "Reason": reason,
        "Gate": "verify_halal()",
    }]


async def halal_blocked():
    """List all stocks currently blocked by the halal verification gate."""
    blocked = []
    for sym in sorted(_VERIFIED_HARAM):
        blocked.append({"Symbol": sym, "Reason": "Verified haram (AAOIFI)"})
    for sym in sorted(_HARAM_EXCLUDE):
        blocked.append({"Symbol": sym, "Reason": "Sector exclusion (banks/insurance/alcohol/etc)"})
    return blocked if blocked else [{"Message": "No stocks blocked yet (gate runs on first access)"}]


async def screening_report():
    """Get all AAOIFI screening results from the database."""
    results = get_screening_report()
    if not results:
        return [{"Message": "No screening results yet. Use /screen_stocks to start screening, or /halal_status/{symbol} for a single stock."}]
    return results

async def screen_stocks_endpoint(max_stocks: int = 80):
    """Trigger batch AAOIFI screening for all stocks in the universe."""
    if not settings.FMP_API_KEY:
        return [{"Error": "FMP_API_KEY not configured. Get a free key at https://site.financialmodelingprep.com/developer/docs"}]
    validate_range(max_stocks, "max_stocks", 1, 200)
    all_symbols = list(set(_universe_symbols()))
    # Run in background to avoid timeout
    key = _cache_key("screening_batch", max=max_stocks)
    def _run_batch():
        return [batch_screen(all_symbols, max_per_run=max_stocks)]
    return _serve_or_compute(key, _run_batch, msg=f"Screening up to {max_stocks} stocks (3 API calls each)... Refresh in a few minutes.")


# ============================================================
# MULTI-STRATEGY ENDPOINTS
# ============================================================

async def list_strategies(x_api_key: OperatorAPIKey = None):
    """List all configured strategies and their settings."""
    _require_api_key(x_api_key)
    from app.config import STRATEGY_CONFIGS
    result = []
    for sid, cfg in STRATEGY_CONFIGS.items():
        result.append({
            "id": cfg.strategy_id,
            "name": cfg.name,
            "max_positions": cfg.max_positions,
            "position_pct": cfg.position_pct,
            "trailing_stop": cfg.trailing_stop_enabled,
            "trailing_stop_pct": cfg.trailing_stop_pct,
            "static_sl_pct": cfg.static_sl_pct,
            "min_confidence": cfg.min_confidence,
            "configured": bool(cfg.alpaca_api_key),
        })
    return result if result else [{"message": "No strategies configured. Set ALPACA_API_KEY_A/B/C env vars."}]


async def strategy_account(strategy_id: str, x_api_key: OperatorAPIKey = None):
    """Get account info for a specific strategy."""
    _require_api_key(x_api_key)
    sid = strategy_id.upper()
    from app.config import STRATEGY_CONFIGS
    if sid not in STRATEGY_CONFIGS:
        return [{"Error": f"Strategy {sid} not configured"}]
    account = alpaca_get_account(strategy_id=sid)
    if not account:
        return [{"Error": f"Cannot fetch account for strategy {sid}"}]
    account["strategy"] = f"{sid}: {STRATEGY_CONFIGS[sid].name}"
    return [account]


async def debug_alpaca(x_api_key: OperatorAPIKey = None):
    """Debug Alpaca connection — shows key prefix, base URL, and test result."""
    _require_api_key(x_api_key)
    import httpx
    from app.config import STRATEGY_CONFIGS
    results = []
    for sid, cfg in STRATEGY_CONFIGS.items():
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
            "strategy": f"{sid}: {cfg.name}",
            "key_prefix": key_prefix,
            "secret_prefix": secret_prefix,
            "base_url": base_url,
            "test_url": test_url,
            "http_status": status,
            "response": body,
        })
    return results


async def strategy_positions(strategy_id: str, x_api_key: OperatorAPIKey = None):
    """Get positions for a specific strategy."""
    _require_api_key(x_api_key)
    sid = strategy_id.upper()
    from app.config import STRATEGY_CONFIGS
    if sid not in STRATEGY_CONFIGS:
        return [{"Error": f"Strategy {sid} not configured"}]
    positions = alpaca_get_positions(strategy_id=sid)
    if not positions:
        return [{"message": f"No open positions for strategy {sid}: {STRATEGY_CONFIGS[sid].name}"}]
    for p in positions:
        p["strategy"] = f"{sid}: {STRATEGY_CONFIGS[sid].name}"
    return positions


async def strategy_scan(strategy_id: str, symbol: str = "AAPL", x_api_key: OperatorAPIKey = None):
    """Manually run a strategy consensus for a symbol."""
    _require_api_key(x_api_key)
    sid = strategy_id.upper()
    if sid == "A":
        return run_consensus_momentum(symbol)
    elif sid == "B":
        return run_consensus_reversion(symbol)
    elif sid == "C":
        return run_consensus_ml(symbol)
    else:
        return [{"Error": f"Unknown strategy {sid}. Use A, B, or C."}]


async def strategy_comparison(x_api_key: OperatorAPIKey = None):
    """Get side-by-side comparison of all strategy accounts."""
    _require_api_key(x_api_key)
    from app.config import STRATEGY_CONFIGS
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
                "strategy_id": sid,
                "name": cfg.name,
                "equity": round(equity, 2),
                "pnl": round(pnl, 2),
                "pnl_pct": round(pnl / last_eq * 100, 2) if last_eq > 0 else 0,
                "positions": len(positions),
                "max_positions": cfg.max_positions,
                "position_symbols": [p["symbol"] for p in positions],
            })
    if result:
        result.append({
            "total_equity": round(total_equity, 2),
            "total_pnl": round(total_pnl, 2),
        })
    return result if result else [{"message": "No strategies configured"}]


# ============================================================
# PHASE 3: Alpaca Portfolio Integration (Read-Only)
# ============================================================

async def portfolio_summary(x_api_key: OperatorAPIKey = None):
    """Get Alpaca account summary: equity, cash, buying power, P&L."""
    _require_api_key(x_api_key)
    if not _has_alpaca_broker_config():
        return [{"Error": "Alpaca API keys not configured"}]
    sid = _primary_broker_strategy_id()
    account = alpaca_get_account(strategy_id=sid)
    if not account:
        return [{"Error": "Could not connect to Alpaca"}]
    # Calculate daily P&L
    daily_pl = account["equity"] - account["last_equity"]
    daily_pl_pct = (daily_pl / account["last_equity"] * 100) if account["last_equity"] > 0 else 0
    return [{
        "Equity": f"${account['equity']:,.2f}",
        "Cash": f"${account['cash']:,.2f}",
        "Buying Power": f"${account['buying_power']:,.2f}",
        "Portfolio Value": f"${account['portfolio_value']:,.2f}",
        "Long Mkt Value": f"${account['long_market_value']:,.2f}",
        "Daily P&L": f"${daily_pl:+,.2f}",
        "Daily P&L %": f"{daily_pl_pct:+.2f}%",
        "Day Trades": account["daytrade_count"],
        "Status": account["status"],
        "Currency": account["currency"],
    }]

async def portfolio_positions(x_api_key: OperatorAPIKey = None):
    """Get all open positions with unrealized P&L."""
    _require_api_key(x_api_key)
    if not _has_alpaca_broker_config():
        return [{"Error": "Alpaca API keys not configured"}]
    sid = _primary_broker_strategy_id()
    positions = alpaca_get_positions(strategy_id=sid)
    if not positions:
        return [{"Message": "No open positions"}]
    result = []
    for p in positions:
        result.append({
            "Symbol": p["symbol"],
            "Qty": p["qty"],
            "Side": p["side"].upper(),
            "Avg Entry": f"${p['avg_entry_price']:.2f}",
            "Current": f"${p['current_price']:.2f}",
            "Market Value": f"${p['market_value']:,.2f}",
            "Cost Basis": f"${p['cost_basis']:,.2f}",
            "Unrealized P&L": f"${p['unrealized_pl']:+,.2f}",
            "P&L %": f"{p['unrealized_plpc']:+.2f}%",
            "Today %": f"{p['change_today']:+.2f}%",
        })
    return result

async def portfolio_orders(status: str = "all", limit: int = 20, x_api_key: OperatorAPIKey = None):
    """Get recent Alpaca orders."""
    _require_api_key(x_api_key)
    if not _has_alpaca_broker_config():
        return [{"Error": "Alpaca API keys not configured"}]
    sid = _primary_broker_strategy_id()
    orders = alpaca_get_orders(status=status, limit=limit, strategy_id=sid)
    if not orders:
        return [{"Message": "No orders found"}]
    return orders

async def portfolio_history(period: str = "1M", x_api_key: OperatorAPIKey = None):
    """Get portfolio equity curve history."""
    _require_api_key(x_api_key)
    if not _has_alpaca_broker_config():
        return [{"Error": "Alpaca API keys not configured"}]
    sid = _primary_broker_strategy_id()
    valid_periods = ["1D", "1W", "1M", "3M", "1A", "all"]
    if period not in valid_periods:
        return [{"Error": f"Invalid period. Use one of: {valid_periods}"}]
    data = alpaca_get_portfolio_history(period=period, strategy_id=sid)
    if not data:
        return [{"Error": "Could not fetch portfolio history"}]
    summary = [{
        "Period": period,
        "Base Value": f"${data['base_value']:,.2f}" if data["base_value"] else "N/A",
        "Data Points": len(data["history"]),
    }]
    return summary + data["history"]


# ============================================================
# PHASE 4: Signal Tracking, Alerts & Monitoring
# ============================================================

async def signals_accuracy(period: int = 30):
    """Signal accuracy report: hit rates, avg returns by source."""
    validate_range(period, "period", 1, 365)
    # Trigger outcome checking first
    try:
        threading.Thread(target=check_signal_outcomes, args=(5,), daemon=True).start()
    except NON_FATAL_ANALYSIS_ERROR as e:
        logger.error(f"check_signal_outcomes thread failed: {e}")
    return get_accuracy_report(period_days=period)

async def signals_history(symbol: str = "", limit: int = 50):
    """Recent signal history with outcomes."""
    s = validate_symbol(symbol) if symbol else None
    validate_range(limit, "limit", 1, 500)
    return get_signal_history(symbol=s, limit=limit)

async def signals_check_outcomes(days: int = 5, x_api_key: OperatorAPIKey = None):
    """Manually trigger outcome checking for mature signals."""
    _require_api_key(x_api_key)
    validate_range(days, "days", 1, 60)
    result = check_signal_outcomes(lookback_days=days)
    return [{"Action": "Outcome check complete", **result}]

async def telegram_test(x_api_key: OperatorAPIKey = None):
    """Send a test message to Telegram."""
    _require_api_key(x_api_key)
    if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHAT_ID:
        return [{"Error": "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID not configured in .env"}]
    ok = tg_send("Test message from Halal Trading Bot\n\nTelegram alerts are working!")
    return [{"Status": "sent" if ok else "failed"}]


async def telegram_test_chart(symbol: str = "AAPL", x_api_key: OperatorAPIKey = None):
    """Test chart generation + Telegram photo sending. Returns diagnostic info."""
    _require_api_key(x_api_key)
    symbol = validate_symbol(symbol)
    result = {"symbol": symbol, "steps": []}

    # Step 1: Fetch data
    try:
        df = fetch_market_data(symbol, period="2y")
        if df is None or len(df) < 30:
            result["steps"].append({"fetch_data": "FAILED", "reason": "No data or < 30 bars"})
            return result
        result["steps"].append({"fetch_data": "OK", "bars": len(df)})
    except NON_FATAL_ANALYSIS_ERROR as e:
        result["steps"].append({"fetch_data": "ERROR", "error": str(e)})
        return result

    # Step 2: Generate chart
    try:
        from app.services.chart_generator import generate_signal_chart
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

    # Step 3: Send to Telegram
    try:
        ok = tg_send_photo(chart_bytes, caption=f"Test chart for {symbol} @ ${price:.2f}")
        result["steps"].append({"send_photo": "OK" if ok else "FAILED"})
    except NON_FATAL_ANALYSIS_ERROR as e:
        result["steps"].append({"send_photo": "ERROR", "error": str(e)})

    return result

async def telegram_daily_summary(x_api_key: OperatorAPIKey = None):
    """Manually trigger daily summary to Telegram."""
    _require_api_key(x_api_key)
    if not settings.TELEGRAM_BOT_TOKEN:
        return [{"Error": "Telegram not configured"}]
    screener_key = _cache_key("screener")
    cached, _ = _get_cached(screener_key)
    if not cached or not isinstance(cached, list):
        return [{"Error": "Screener data not available yet"}]
    from app.config import STRATEGY_CONFIGS
    _sid = next(iter(STRATEGY_CONFIGS), None)
    portfolio = alpaca_get_account(strategy_id=_sid)
    portfolio_info = None
    if portfolio:
        daily_pl = portfolio["equity"] - portfolio["last_equity"]
        portfolio_info = {"equity": portfolio["equity"], "daily_pl": daily_pl}
    ok = alert_daily_summary(cached, portfolio_info)
    return [{"Status": "sent" if ok else "failed"}]


def _run_post_market_scan_bg(top_n: int, min_score: int):
    """Background worker for post-market scan (runs in thread).

    Memory-safe: flushes model cache between each stock,
    limits to top_n stocks to prevent OOM on Railway free tier.
    """
    try:
        # Step 1: Run screener to find top stocks
        screener_results = run_screener()
        if not screener_results:
            tg_send("POST-MARKET SCAN\n\nScreener returned no results.")
            return

        # Filter for strong scores
        top_stocks = [r for r in screener_results if r.get("swing_score", 0) >= min_score]
        top_stocks.sort(key=lambda x: x.get("swing_score", 0), reverse=True)
        top_stocks = top_stocks[:min(top_n, 5)]  # hard cap at 5 to prevent OOM

        if not top_stocks:
            tg_send(f"POST-MARKET SCAN\n\nNo stocks with score >= {min_score} found.\n{len(screener_results)} stocks scanned.")
            return

        # Step 2: Send header
        tg_send(
            f"POST-MARKET ANALYSIS\n"
            f"{len(screener_results)} stocks scanned\n"
            f"{len(top_stocks)} top signals (score >= {min_score})\n"
            f"\nAnalyzing with AI consensus..."
        )

        # Step 3: Run consensus on each — this triggers chart alerts automatically
        results = []
        for i, stock in enumerate(top_stocks):
            symbol = stock["symbol"]
            try:
                # Flush caches BEFORE each consensus to prevent OOM
                _flush_all_caches()

                consensus = run_consensus(symbol, horizon=5, episodes=3)  # fewer episodes to save memory
                if consensus and not consensus[0].get("Error"):
                    summary = consensus[0]
                    results.append({
                        "symbol": symbol,
                        "verdict": summary.get("Verdict", "N/A"),
                        "confidence": summary.get("Confidence %", 0),
                    })
                else:
                    results.append({"symbol": symbol, "error": "Consensus failed"})
            except NON_FATAL_ANALYSIS_ERROR as e:
                logger.error(f"Post-market scan {symbol}: {e}")
                results.append({"symbol": symbol, "error": str(e)})

            # Aggressive memory cleanup between stocks
            _flush_all_caches()
            time.sleep(3)

        # Step 4: Summary
        strong = [r for r in results if "STRONG" in r.get("verdict", "")]
        summary_lines = []
        for r in results:
            if "error" not in r:
                summary_lines.append(f"  {r['symbol']}: {r['verdict']} ({r['confidence']:.0f}%)")

        tg_send(
            f"SCAN COMPLETE\n\n"
            f"Analyzed: {len(top_stocks)} stocks\n"
            f"STRONG signals: {len(strong)}\n"
            f"\nResults:\n" + "\n".join(summary_lines) if summary_lines else "No signals"
        )

    except NON_FATAL_ANALYSIS_ERROR as e:
        logger.error(f"Post-market scan failed: {e}")
        tg_send(f"POST-MARKET SCAN ERROR\n\n{str(e)[:200]}")


async def post_market_scan(top_n: int = 5, min_score: int = 55, x_api_key: OperatorAPIKey = None):
    """Post-market analysis: scan top stocks, send chart alerts via Telegram.

    Runs in the BACKGROUND — returns immediately. Results sent to Telegram.

    Runs the screener, picks the top N stocks by swing score,
    runs consensus on each, and sends STRONG signals with professional
    charts to Telegram (entry, SL, TP lines + candlestick + arrows).

    Call this after market close (4 PM ET) for next-day trade ideas.
    """
    _require_api_key(x_api_key)
    if not settings.TELEGRAM_BOT_TOKEN:
        return [{"Error": "Telegram not configured"}]

    # Launch in background thread — don't block the HTTP response
    thread = threading.Thread(
        target=_run_post_market_scan_bg,
        args=(top_n, min_score),
        daemon=True,
    )
    thread.start()

    return [{
        "Status": "Post-market scan started",
        "top_n": top_n,
        "min_score": min_score,
        "Message": "Results will be sent to Telegram. Check your bot.",
    }]


# ══════════════════════════════════════════════════════════════════════
# AUTO-TRADING ENDPOINTS (Stage 1: Paper Trading)
# ══════════════════════════════════════════════════════════════════════

async def trading_status(x_api_key: OperatorAPIKey = None):
    """Get auto-trading status: enabled/disabled, risk dashboard, PDT tracker."""
    _require_api_key(x_api_key)
    sid = _primary_broker_strategy_id()
    broker_configured = _has_alpaca_broker_config()
    if not broker_configured:
        return {
            "error": "Alpaca API keys not configured",
            "broker_configured": False,
            "broker_connected": False,
            "auto_trade_enabled": settings.AUTO_TRADE_ENABLED,
            "min_confidence": settings.MIN_TRADE_CONFIDENCE,
            "trade_risk_pct": settings.TRADE_RISK_PCT,
            "max_position_pct": settings.MAX_POSITION_PCT,
        }

    account = alpaca_get_account(strategy_id=sid)
    if not account:
        broker_error = alpaca_get_last_error(strategy_id=sid)
        return {
            "error": "Cannot connect to Alpaca",
            "broker_configured": True,
            "broker_connected": False,
            "broker_reason": broker_error.get("reason") or "unknown",
            "broker_status_code": broker_error.get("status_code"),
            "auto_trade_enabled": settings.AUTO_TRADE_ENABLED,
            "min_confidence": settings.MIN_TRADE_CONFIDENCE,
            "trade_risk_pct": settings.TRADE_RISK_PCT,
            "max_position_pct": settings.MAX_POSITION_PCT,
            "strategy_id": sid or "default",
        }

    positions = alpaca_get_positions(strategy_id=sid)
    risk = get_risk_status(account, positions)
    risk["broker_configured"] = True
    risk["broker_connected"] = True
    risk["strategy_id"] = sid or "default"
    risk["auto_trade_enabled"] = settings.AUTO_TRADE_ENABLED
    risk["min_confidence"] = settings.MIN_TRADE_CONFIDENCE
    risk["trade_risk_pct"] = settings.TRADE_RISK_PCT
    risk["max_position_pct"] = settings.MAX_POSITION_PCT
    return risk


async def trading_history(limit: int = 50, x_api_key: OperatorAPIKey = None):
    """Get auto-trade execution history."""
    _require_api_key(x_api_key)
    return get_trade_history(limit=limit)


async def trading_performance(x_api_key: OperatorAPIKey = None):
    """Get trading performance report: win rate, Sharpe, drawdown, profit factor."""
    _require_api_key(x_api_key)
    return get_performance_report()


async def trading_enable(x_api_key: OperatorAPIKey = None):
    """Enable auto-trading (paper account only)."""
    _require_api_key(x_api_key)
    settings.AUTO_TRADE_ENABLED = True
    tg_send("AUTO-TRADING ENABLED\n\nPaper account auto-execution is now active.\nOnly STRONG BUY/SELL signals will be executed.")
    return {"auto_trade_enabled": True, "message": "Paper trading auto-execution enabled"}


async def trading_disable(x_api_key: OperatorAPIKey = None):
    """Disable auto-trading (emergency stop)."""
    _require_api_key(x_api_key)
    settings.AUTO_TRADE_ENABLED = False
    tg_send("AUTO-TRADING DISABLED\n\nAuto-execution has been stopped.\nAll existing positions remain open.")
    return {"auto_trade_enabled": False, "message": "Auto-trading disabled"}


# ══════════════════════════════════════════════════════════════════════
# PARAMETER OPTIMIZATION ENDPOINT
# ══════════════════════════════════════════════════════════════════════

async def optimize_parameters(n_stocks: int = 10, x_api_key: OperatorAPIKey = None):
    """Run parameter sweep optimization on historical data.

    Tests different consensus thresholds, SL/TP multipliers, and tool
    parameters to find optimal values. Results show improvement vs current defaults.
    Runs in background — returns immediately, results sent to Telegram.
    """
    _require_api_key(x_api_key)

    def _run_optimizer():
        try:
            from app.services.optimizer import optimize_params
            result = optimize_params(n_samples=n_stocks)
            if "error" not in result:
                msg = (
                    f"OPTIMIZER RESULTS\n\n"
                    f"Stocks tested: {len(result['stocks_tested'])}\n\n"
                    f"CURRENT vs OPTIMAL:\n"
                    f"Win Rate: {result['baseline_performance']['win_rate']}% -> {result['optimal_performance']['win_rate']}%\n"
                    f"Profit Factor: {result['baseline_performance']['profit_factor']} -> {result['optimal_performance']['profit_factor']}\n"
                    f"Sharpe: {result['baseline_performance']['sharpe']} -> {result['optimal_performance']['sharpe']}\n\n"
                    f"Best Params:\n"
                    f"Strong BUY votes: {result['optimal_params']['strong_buy_votes']}\n"
                    f"Min confidence: {result['optimal_params']['min_confidence']}%\n"
                    f"SL mult: {result['optimal_params']['sl_atr_mult']}x ATR\n"
                    f"TP1 mult: {result['optimal_params']['tp1_atr_mult']}x ATR\n"
                    f"BB squeeze: {result['optimal_params']['bb_squeeze_width']}%\n"
                    f"Momentum ROC: {result['optimal_params']['momentum_roc_threshold']}%"
                )
                tg_send(msg)
        except NON_FATAL_ANALYSIS_ERROR as e:
            logger.error(f"Optimizer error: {e}")

    threading.Thread(target=_run_optimizer, daemon=True).start()
    return [{"Status": "Optimizer started", "Message": "Results will be sent to Telegram"}]


# ════════���═════════════════��═══════════════════════════════════════════
# INTRADAY DATA ENDPOINT
# ══════════════════════════════════════════���═══════════════════════════

async def intraday_bars(symbol: str, timeframe: str = "15Min", days: int = 5):
    """Get intraday bars for a symbol (15Min default).

    Supports: 1Min, 5Min, 15Min, 1Hour.
    Includes basic technical overlay: EMA9, VWAP approximation, RSI.
    """
    s = validate_symbol(symbol)
    valid_tf = {"1Min", "5Min", "15Min", "1Hour"}
    if timeframe not in valid_tf:
        return [{"Error": f"Invalid timeframe. Use: {valid_tf}"}]

    df = fetch_alpaca_intraday(s, timeframe=timeframe, days_back=days)
    if df is None or df.empty:
        return [{"Error": f"No intraday data for {s}"}]

    # Add technical overlays
    close = df["close"]
    df["ema9"] = ema(close, 9)
    df["rsi14"] = rsi(close, 14)

    # Rolling 20-day VWAP instead of all-time cumulative
    _vwap_window = 20
    hlc3 = (df["high"] + df["low"] + df["close"]) / 3
    df["vwap"] = (hlc3 * df["volume"]).rolling(_vwap_window).sum() / df["volume"].rolling(_vwap_window).sum()

    # Format for response
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


async def ping():
    """Unauthenticated lightweight health check for load balancers."""
    return {
        "status": "ok",
        "uptime_seconds": round(_uptime_seconds(), 1),
    }


async def health():
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
        with engine.connect() as conn:
            conn.execute(__import__("sqlalchemy").text("SELECT 1"))
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
            host = os.environ.get("IBKR_HOST", "127.0.0.1")
            port = int(os.environ.get("IBKR_PORT", "7497"))
            sock = socket.create_connection((host, port), timeout=5)
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
        "auto_trading": (
            "enabled"
            if settings.AUTO_TRADE_ENABLED and broker_connected
            else "blocked_broker_unavailable" if settings.AUTO_TRADE_ENABLED else "disabled"
        ),
        "config": "loaded",
        "uptime_seconds": round(_uptime_seconds(), 1),
        "uptime_human": f"{_uptime_seconds() / 3600:.1f}h",
        "model_degradation": model_degradation,
        "dependencies": checks,
    }

# ══════════════════════════════════════════════════════════════════════
# CLAUDE AI AGENT ENDPOINTS
# ══════════════════════════════════════════════════════════════════════







def _equity_curve_svg(history):
    values = [float(item.get("Equity", 0) or 0) for item in history][-30:]
    if len(values) < 2:
        return "<p>No 30d equity curve available.</p>"

    width = 520
    height = 140
    low = min(values)
    high = max(values)
    span = max(high - low, 1.0)
    step = width / max(len(values) - 1, 1)
    points = " ".join(
        f"{idx * step:.1f},{height - (((value - low) / span) * (height - 20) + 10):.1f}"
        for idx, value in enumerate(values)
    )
    return (
        f"<svg viewBox='0 0 {width} {height}' width='100%' height='{height}' "
        f"style='background:#faf7ef;border:1px solid #ddd;border-radius:12px'>"
        f"<polyline fill='none' stroke='#0d5c63' stroke-width='3' points='{points}' />"
        f"</svg>"
    )


def _render_ops_fragment(api_key: str | None) -> str:
    from app.db.database import SessionLocal
    from app.db.models import GuardLog, TradeHistory
    from app.services.regime import get_regime

    regime = get_regime()
    db = SessionLocal()
    try:
        guards = db.query(GuardLog).order_by(GuardLog.ts.desc()).limit(100).all()
        rejects = db.query(TradeHistory).filter(
            TradeHistory.status.in_(["rejected", "canceled", "expired", "canceled_leg_missing"])
        ).order_by(TradeHistory.created_at.desc()).limit(10).all()
    finally:
        db.close()

    strategy_sections = []
    for sid, cfg in STRATEGY_CONFIGS.items():
        account = alpaca_get_account(strategy_id=sid) or {}
        positions = alpaca_get_positions(strategy_id=sid) or []
        position_rows = "".join(
            f"<tr><td>{escape(str(position.get('symbol', '')))}</td>"
            f"<td>{float(position.get('qty', 0) or 0):,.0f}</td>"
            f"<td>${float(position.get('market_value', 0) or 0):,.2f}</td></tr>"
            for position in positions
        ) or "<tr><td colspan='3'>No open positions.</td></tr>"
        strategy_sections.append(
            f"<section style='padding:16px;border:1px solid #ddd;border-radius:14px;background:#fff'>"
            f"<h3 style='margin:0 0 8px 0'>[{sid}] {escape(cfg.name)}</h3>"
            f"<p style='margin:0 0 12px 0'>Equity: ${float(account.get('equity', 0) or 0):,.2f} | "
            f"Positions: {len(positions)}</p>"
            f"<table style='width:100%;border-collapse:collapse'>"
            f"<tr><th align='left'>Symbol</th><th align='left'>Qty</th><th align='left'>Market Value</th></tr>"
            f"{position_rows}</table></section>"
        )

    history = None
    for sid in STRATEGY_CONFIGS:
        payload = alpaca_get_portfolio_history(period="1M", timeframe="1D", strategy_id=sid)
        if payload and payload.get("history"):
            history = payload["history"]
            break

    guard_rows = "".join(
        f"<tr><td>{escape(str(row.ts or ''))}</td><td>{escape(row.symbol)}</td>"
        f"<td>{escape(row.guard_name)}</td><td>{'PASS' if row.passed else 'BLOCK'}</td>"
        f"<td>{escape(row.reason)}</td></tr>"
        for row in guards
    ) or "<tr><td colspan='5'>No guard rows yet.</td></tr>"
    reject_rows = "".join(
        f"<tr><td>{escape(str(row.created_at or ''))}</td><td>{escape(row.symbol)}</td>"
        f"<td>{escape(row.status or '')}</td><td>{escape(str(row.signal_details or {}))}</td></tr>"
        for row in rejects
    ) or "<tr><td colspan='4'>No rejected trades.</td></tr>"

    headers_json = escape(json.dumps({"X-API-Key": api_key}) if api_key else "{}", quote=True)
    return f"""
    <div style="display:grid;gap:18px">
      <section style="padding:18px;border-radius:16px;background:#fff3e6;border:1px solid #f0d3ad">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap">
          <div>
            <h1 style="margin:0 0 8px 0">Ops Dashboard</h1>
            <p style="margin:0">Regime: <strong>{escape(regime.state)}</strong> | Kill-switch: <strong>{app_cfg.killed}</strong></p>
          </div>
          <div style="display:flex;gap:10px;flex-wrap:wrap">
            <button
              hx-post="/admin/killswitch?killed=true"
              hx-swap="none"
              hx-headers='{headers_json}'
              style="background:#b42318;color:#fff;border:none;border-radius:10px;padding:10px 14px;font-weight:700;cursor:pointer"
            >Enable Kill Switch</button>
            <button
              hx-post="/admin/killswitch?killed=false"
              hx-swap="none"
              hx-headers='{headers_json}'
              style="background:#0d5c63;color:#fff;border:none;border-radius:10px;padding:10px 14px;font-weight:700;cursor:pointer"
            >Disable Kill Switch</button>
          </div>
        </div>
      </section>
      <section style="padding:18px;border-radius:16px;background:#fff;border:1px solid #ddd">
        <h2 style="margin-top:0">30d Equity Curve</h2>
        {_equity_curve_svg(history or [])}
      </section>
      <section style="display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:16px">
        {''.join(strategy_sections) or '<p>No strategy accounts configured.</p>'}
      </section>
      <section style="padding:18px;border-radius:16px;background:#fff;border:1px solid #ddd">
        <h2 style="margin-top:0">Today's Guard Log</h2>
        <table style="width:100%;border-collapse:collapse">
          <tr><th align="left">Timestamp</th><th align="left">Symbol</th><th align="left">Guard</th><th align="left">Result</th><th align="left">Reason</th></tr>
          {guard_rows}
        </table>
      </section>
      <section style="padding:18px;border-radius:16px;background:#fff;border:1px solid #ddd">
        <h2 style="margin-top:0">Last 10 Rejected Trades</h2>
        <table style="width:100%;border-collapse:collapse">
          <tr><th align="left">Timestamp</th><th align="left">Symbol</th><th align="left">Status</th><th align="left">Details</th></tr>
          {reject_rows}
        </table>
      </section>
    </div>
    """


# --- HTML page routes (must be BEFORE routers to avoid path conflicts) ---
@app.get("/trading-lab", include_in_schema=False)
async def trading_lab_page():
    from fastapi.responses import FileResponse
    _f = os.path.join(os.path.dirname(__file__), "app", "static", "trading-lab.html")
    if os.path.isfile(_f):
        return FileResponse(_f)
    return {"error": "Trading Lab not found"}

@app.get("/backtest", include_in_schema=False)
async def backtest_page():
    from fastapi.responses import FileResponse
    _f = os.path.join(os.path.dirname(__file__), "app", "static", "trading.html")
    if os.path.isfile(_f):
        return FileResponse(_f)
    return {"error": "Backtest not found"}

@app.get("/risk-desk", include_in_schema=False)
async def risk_desk_page():
    from fastapi.responses import FileResponse
    _f = os.path.join(os.path.dirname(__file__), "app", "static", "risk-desk.html")
    if os.path.isfile(_f):
        return FileResponse(_f)
    return {"error": "Risk Desk not found"}

@app.get("/screener", include_in_schema=False)
async def screener_page():
    from fastapi.responses import FileResponse
    _f = os.path.join(os.path.dirname(__file__), "app", "static", "screener.html")
    if os.path.isfile(_f):
        return FileResponse(_f)
    return {"error": "Screener not found"}

# --- Include routers ---
try:
    from app.routers.screener import router as screener_router
    app.include_router(screener_router)
except ImportError as e:
    logger.warning(f"Screener router not available: {e}")

try:
    from app.routers.forecast import router as forecast_router
    app.include_router(forecast_router)
except ImportError as e:
    logger.warning(f"Forecast router not available: {e}")

try:
    from app.routers.consensus import router as consensus_router
    app.include_router(consensus_router)
except ImportError as e:
    logger.warning(f"Consensus router not available: {e}")

try:
    from app.routers.portfolio import router as portfolio_router
    app.include_router(portfolio_router)
except ImportError as e:
    logger.warning(f"Portfolio router not available: {e}")

try:
    from app.routers.admin import router as admin_router
    app.include_router(admin_router)
except ImportError as e:
    logger.warning(f"Admin router not available: {e}")

try:
    from app.routers.dashboard import router as dashboard_router
    app.include_router(dashboard_router)
except ImportError as e:
    logger.warning(f"Dashboard router not available: {e}")

try:
    from app.api.v1 import v1_router
    app.include_router(v1_router)
except ImportError as e:
    logger.warning(f"V1 router not available: {e}")

# Mount static files for dashboard
from fastapi.staticfiles import StaticFiles
import os
_static_dir = os.path.join(os.path.dirname(__file__), "app", "static")
if os.path.isdir(_static_dir):
    app.mount("/static", StaticFiles(directory=_static_dir), name="static")


@app.get("/api/info", include_in_schema=False)
async def api_info():
    """Model/agent info for the dashboard."""
    from app.routers.dashboard import _dashboard_health
    from app.config import STRATEGY_CONFIGS
    from app.services.regime import get_regime
    health = await _dashboard_health()
    regime = get_regime()
    model_status = _check_model_degradation()
    model_items = [{"name": n, "category": "Neural Network", "status": s.get("status", "idle"), "sharpe": s.get("sharpe", 0), "win_rate": s.get("acc", 0)} for n, s in model_status.items()]
    return {
        "status": health.get("status", "degraded"), "broker": health.get("broker", "unknown"),
        "database": health.get("database", "unknown"), "regime": regime.state if regime else "UNKNOWN",
        "symbols_count": len(_universe_symbols()), "strategies_count": len(STRATEGY_CONFIGS),
        "model_items": model_items, "agent_items": [],
        "last_full_retrain": None, "smart_scan_freshness": None,
        "uptime_hours": health.get("uptime_seconds", 0) / 3600 if health.get("uptime_seconds") else 0,
        "version": "17.0.0",
    }


@app.get("/dashboard", include_in_schema=False)
async def dashboard_page():
    """Serve the professional dashboard UI."""
    from fastapi.responses import FileResponse
    _dash = os.path.join(os.path.dirname(__file__), "app", "static", "dashboard.html")
    if os.path.isfile(_dash):
        return FileResponse(_dash)
    return {"error": "Dashboard not found"}


@app.get("/forecast", include_in_schema=False)
async def forecast_panel():
    """Serve the ForecastML calibration suite — Ensemble, LSTM-CNN, Transformer."""
    from fastapi.responses import FileResponse
    _f = os.path.join(os.path.dirname(__file__), "app", "static", "forecast-panel.html")
    if os.path.isfile(_f):
        return FileResponse(_f)
    return {"error": "Forecast panel not found"}



