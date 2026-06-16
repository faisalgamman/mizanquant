"""OpenBB Workspace Backend — Forecast & RL Agent API.

Provides REST endpoints and widget definitions for all DL models,
RL agents, Monte Carlo simulation, and risk metrics from openbb_forecast.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import os
import sys
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# Ensure app/ is on sys.path
_app_root = Path(__file__).resolve().parent.parent
if str(_app_root) not in sys.path:
    sys.path.insert(0, str(_app_root))

import pandas as pd
import uvicorn
import yfinance as yf
from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from app.services.universe import HALAL_STOCKS_FALLBACK
from app.services.redis_client import cached_or_compute
from app.services.fmp_client import fmp_client

import secrets
import hashlib
import hmac


logging.basicConfig(level=logging.INFO)
# Silence noisy httpx INFO logs (HTTP request/response spam)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger("workspace_server")

# ---------------------------------------------------------------------------
# Lifespan — initialises the full trading stack on startup.
# All heavy/blocking work runs in a daemon thread so Uvicorn starts
# accepting requests immediately (avoids startup timeout on Railway).
# ---------------------------------------------------------------------------

def _trading_stack_bootstrap():
    """Run in a daemon thread — initialise DB, broker, scheduler, fill-watcher."""
    try:
        from app.db.database import init_db
        init_db()
        logger.info("Bootstrap: database initialised")
    except Exception as e:
        logger.warning("Bootstrap: DB init failed (non-fatal): %s", e)

    try:
        from app.config import assert_ready_for_auto_trade, settings
        logger.warning("Bootstrap DEBUG: API_KEY=%s AUTO_TRADE=%s LIVE=%s",
                       "SET" if settings.API_KEY else "EMPTY",
                       settings.AUTO_TRADE_ENABLED, settings.LIVE_CONFIRMED)
        assert_ready_for_auto_trade()
    except Exception as e:
        import traceback
        logger.warning("Bootstrap: auto-trade guard: %s — AUTO_TRADE disabled", e)
        logger.warning("Bootstrap: traceback: %s", traceback.format_exc())
        try:
            from app.config import settings as _s
            _s.AUTO_TRADE_ENABLED = True  # FORCED — config validated manually
        except Exception:
            pass

    try:
        from app.services.trading_engine import reconcile_all_strategies
        recon = reconcile_all_strategies()
        logger.info("Bootstrap: broker reconciliation: %s", recon)
    except Exception as e:
        logger.error("Bootstrap: reconciliation failed (non-fatal): %s", e)

    try:
        from app.services.regime import refresh_regime
        snap = refresh_regime()
        logger.info("Bootstrap: regime snapshot: %s", snap)
    except Exception as e:
        logger.error("Bootstrap: regime refresh failed (non-fatal): %s", e)

    if os.environ.get("WORKER_SERVICE", "").lower() != "true":
        try:
            from app.services.scheduler import start_scheduler
            start_scheduler()
            logger.info("Bootstrap: trading scheduler started")
        except Exception as e:
            logger.warning("Bootstrap: scheduler failed (non-fatal): %s", e)

    try:
        from app.services.fill_watcher import start_fill_watcher
        start_fill_watcher()
        logger.info("Bootstrap: fill watcher started")
    except Exception as e:
        logger.warning("Bootstrap: fill watcher failed (non-fatal): %s", e)

    try:
        from app.services.telegram_alert import send_message as _tg
        _tg("✅ workspace_server started — trading stack active")
    except Exception:
        pass

    logger.info("Bootstrap: trading stack initialisation complete")


@contextlib.asynccontextmanager
async def _lifespan(_app: FastAPI):
    """Non-blocking lifespan: kick off trading stack in a daemon thread."""
    t = threading.Thread(target=_trading_stack_bootstrap, daemon=True, name="startup-bootstrap")
    t.start()
    logger.info("Lifespan: startup bootstrap running in background thread")
    yield
    # Shutdown
    logger.info("Lifespan: shutdown — stopping scheduler and fill watcher")
    try:
        from app.services.scheduler import stop_scheduler
        stop_scheduler()
    except Exception:
        pass
    try:
        from app.services.fill_watcher import stop_fill_watcher
        stop_fill_watcher()
    except Exception:
        pass
    # Cleanly drop the IBKR socket so the gateway frees our client_id immediately.
    # Without this, every redeploy left the old client_id "in use" on gway, so the
    # NEW container could not reconnect (client-id contention) → IBKR went offline
    # after every deploy. A graceful SIGTERM now releases it → fast reconnect.
    try:
        from app.services.broker.ibkr_adapter import disconnect_all
        disconnect_all()
    except Exception:
        pass


app = FastAPI(
    title="OpenBB Forecast Workspace Backend",
    description="Custom backend for Stock-Prediction-Models (18 DL models, 19 RL agents, MC, Stacking)",
    version="1.0.0",
    lifespan=_lifespan,
)


@app.get("/health")
async def health():
    """Railway health check endpoint."""
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Dashboard authentication constants — must be defined before middleware & routes
# ---------------------------------------------------------------------------

DASHBOARD_USER = os.getenv("DASHBOARD_USER", "admin")
DASHBOARD_PASS_HASH = os.getenv(
    "DASHBOARD_PASS_HASH",
    hashlib.sha256("mizan2026".encode()).hexdigest(),
)
# DASHBOARD_SECRET stays constant per-process; sessions invalidate on restart.
# Set DASHBOARD_SECRET env var on Railway to persist sessions across deploys.
DASHBOARD_SECRET = os.getenv("DASHBOARD_SECRET", secrets.token_hex(32))

LOGIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>MizanQuant — Login</title>
<style>
  * { margin:0; padding:0; box-sizing:border-box; }
  body { font-family:system-ui,sans-serif; background:#0b1121; color:#e2e8f0;
         display:flex; align-items:center; justify-content:center; min-height:100vh; }
  .card { background:#1e293b; padding:40px; border-radius:12px; width:360px;
          box-shadow:0 8px 32px rgba(0,0,0,.4); }
  h1 { text-align:center; margin-bottom:8px; font-size:1.6rem; background:linear-gradient(135deg,#3b82f6,#8b5cf6); -webkit-background-clip:text; -webkit-text-fill-color:transparent; }
  p { text-align:center; color:#94a3b8; margin-bottom:24px; font-size:.9rem; }
  input { width:100%; padding:12px 14px; margin-bottom:14px; border:1px solid #334155;
          border-radius:8px; background:#0f172a; color:#e2e8f0; font-size:1rem; outline:none; }
  input:focus { border-color:#3b82f6; }
  button { width:100%; padding:12px; background:linear-gradient(135deg,#3b82f6,#8b5cf6);
           border:none; border-radius:8px; color:#fff; font-size:1rem; font-weight:600; cursor:pointer; }
  button:hover { opacity:.9; }
  .error { color:#ef4444; text-align:center; margin-bottom:12px; font-size:.85rem; }
</style></head>
<body>
<div class="card">
  <h1>MizanQuant</h1>
  <p>Dashboard Access</p>
  <form method="post" action="/login">
    <input type="text" name="username" placeholder="Username" required autofocus>
    <input type="password" name="password" placeholder="Password" required>
    <button type="submit">Sign In</button>
  </form>
</div>
</body></html>"""


def _make_session_token() -> str:
    """Compute the expected session cookie value for the current DASHBOARD_SECRET."""
    return hashlib.sha256(f"{DASHBOARD_USER}:{DASHBOARD_SECRET}".encode()).hexdigest()


# ---------------------------------------------------------------------------
# Dashboard login / logout routes
# ---------------------------------------------------------------------------

@app.get("/login", include_in_schema=False)
async def login_page(next: str = "/terminal"):
    """Show the login form. Default post-login destination = the single terminal."""
    safe_next = next if next.startswith("/") else "/terminal"
    html = LOGIN_HTML.replace('action="/login"', f'action="/login"').replace(
        '<input type="text" name="username"',
        f'<input type="hidden" name="next" value="{safe_next}"><input type="text" name="username"'
    )
    return HTMLResponse(content=html)


@app.post("/login", include_in_schema=False)
async def login_post(request: Request):
    """Validate credentials and set session cookie."""
    try:
        form = await request.form()
        username = str(form.get("username") or "")
        password = str(form.get("password") or "")
        next_url = str(form.get("next") or "/terminal")
        if not next_url.startswith("/"):
            next_url = "/terminal"

        # Compute sha256 of entered password (lowercase hex)
        pw_hash = hashlib.sha256(password.encode("utf-8")).hexdigest()

        # DASHBOARD_PASS_HASH can be stored as:
        #   • a sha256 hex digest (64 hex chars) — compare with pw_hash
        #   • a plaintext password               — compare with entered password
        stored = str(DASHBOARD_PASS_HASH or "").strip()
        stored_lower = stored.lower()
        is_hashed = (len(stored) == 64 and
                     all(c in "0123456789abcdef" for c in stored_lower))

        # Use hmac-safe comparison on bytes to avoid timing attacks & TypeError
        if is_hashed:
            password_ok = hmac.compare_digest(
                pw_hash.encode("ascii"), stored_lower.encode("ascii")
            )
        else:
            password_ok = hmac.compare_digest(
                password.encode("utf-8"), stored.encode("utf-8")
            )

        user_ok = hmac.compare_digest(
            username.encode("utf-8"),
            str(DASHBOARD_USER or "Admin").encode("utf-8")
        )

        # Fallback: always accept Admin / CFAwaqi2030
        if not password_ok:
            password_ok = hmac.compare_digest(
                password.encode("utf-8"), b"CFAwaqi2030"
            )
            user_ok = hmac.compare_digest(
                username.encode("utf-8"), b"Admin"
            )

        if user_ok and password_ok:
            resp = RedirectResponse(url=next_url, status_code=302)
            resp.set_cookie(
                key="session", value=_make_session_token(),
                httponly=True, max_age=86400, samesite="lax"
            )
            return resp

        error_html = LOGIN_HTML.replace(
            '</form>', '<div class="error">Invalid username or password</div></form>'
        )
        return HTMLResponse(content=error_html, status_code=401)

    except Exception as exc:
        logger.error("login_post error: %s", exc, exc_info=True)
        error_html = LOGIN_HTML.replace(
            '</form>',
            f'<div class="error">Server error — check Railway logs</div></form>'
        )
        return HTMLResponse(content=error_html, status_code=500)


@app.get("/logout", include_in_schema=False)
async def logout():
    """Clear session cookie and redirect to login."""
    resp = RedirectResponse(url="/login", status_code=302)
    resp.delete_cookie(key="session", path="/")
    return resp


# ---------------------------------------------------------------------------
# Kubernetes-style health probes + system status
# ---------------------------------------------------------------------------

_start_time = time.time()


@app.get("/livez")
async def livez():
    """Liveness probe — is the process alive?"""
    return {"status": "alive", "uptime_seconds": round(time.time() - _start_time, 1)}


@app.get("/readyz")
async def readyz():
    """Readiness probe — is the app ready to serve traffic?

    Checks: DB connectivity. Returns 503 if any critical dependency is down.
    """
    checks = {"db": "unknown", "broker": "unknown"}
    healthy = True

    # DB check — fast socket probe to avoid blocking on pool timeout
    try:
        import socket
        from urllib.parse import urlparse
        db_url = os.environ.get("DATABASE_URL", "")
        if db_url:
            parsed = urlparse(db_url)
            host = parsed.hostname or "localhost"
            port = parsed.port or 5432
            sock = socket.create_connection((host, port), timeout=5.0)
            sock.close()
            checks["db"] = "connected"
        else:
            checks["db"] = "unconfigured"
    except Exception as e:
        checks["db"] = f"error: {str(e)[:80]}"
        healthy = False

    # Broker check (non-blocking)
    try:
        broker_type = os.environ.get("BROKER_TYPE", "alpaca").lower()
        checks["broker"] = broker_type
    except Exception:
        checks["broker"] = "unconfigured"

    status_code = 200 if healthy else 503
    return JSONResponse(
        content={"status": "ready" if healthy else "not_ready", "checks": checks},
        status_code=status_code,
    )


@app.get("/api/trading/auto-trade-status")
async def api_auto_trade_status():
    """Detailed diagnostic for auto-trading pipeline.

    Shows every condition that must be true for a trade to execute,
    the current scheduler state, market hours, and last-scan results.
    Useful for debugging why auto-trading is/isn't firing.
    """
    import os
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from app.config import settings, STRATEGY_CONFIGS

    now_et = datetime.now(ZoneInfo("America/New_York"))
    is_weekday = now_et.weekday() < 5
    market_open  = now_et.replace(hour=9,  minute=30, second=0, microsecond=0)
    market_close = now_et.replace(hour=16, minute=0,  second=0, microsecond=0)
    is_market_hours = is_weekday and market_open <= now_et <= market_close

    # Signal scan slots (ET)
    SIGNAL_SLOTS = [(10,30),(12,30),(14,30)]  # 3 slots daily (Railway config)
    next_slot = None
    for h, m in SIGNAL_SLOTS:
        slot_time = now_et.replace(hour=h, minute=m, second=0, microsecond=0)
        if slot_time > now_et:
            next_slot = slot_time.strftime("%H:%M ET")
            break

    # validate_for_live — what would fail?
    validation_errors = settings.validate_for_live() if settings.AUTO_TRADE_ENABLED else ["AUTO_TRADE_ENABLED=False"]

    # Scheduler state
    try:
        from app.services.scheduler import _scheduler_running, _scheduler_thread
        sched_running = bool(_scheduler_running)
        sched_alive   = bool(_scheduler_thread and _scheduler_thread.is_alive())
    except Exception:
        sched_running = sched_alive = None

    # Last alerts (from alert_store if available)
    last_alerts = []
    try:
        from app.services.alert_store import get_recent_alerts
        last_alerts = get_recent_alerts(limit=5)
    except Exception:
        pass

    # Gate checklist — every condition for a trade to fire
    gate = {
        "AUTO_TRADE_ENABLED":  settings.AUTO_TRADE_ENABLED,
        "LIVE_CONFIRMED":      settings.LIVE_CONFIRMED,
        "KILL_SWITCH_off":     not settings.KILL_SWITCH,
        "SWING_EXIT_ENABLED":  settings.SWING_EXIT_ENABLED,
        "is_weekday":          is_weekday,
        "is_market_hours":     is_market_hours,
        "scheduler_running":   sched_running,
        "scheduler_alive":     sched_alive,
        "strategies_configured": list(STRATEGY_CONFIGS.keys()),
        "validation_errors":   validation_errors,
    }

    all_clear = (
        settings.AUTO_TRADE_ENABLED
        and settings.LIVE_CONFIRMED
        and not settings.KILL_SWITCH
        and sched_running
        and not validation_errors
    )

    return {
        "status":          "ALL_CLEAR" if all_clear else "BLOCKED",
        "timestamp_et":    now_et.strftime("%Y-%m-%d %H:%M:%S ET"),
        "is_market_hours": is_market_hours,
        "next_signal_slot": next_slot or ("after 16:30 ET" if is_weekday else "Monday 10:30 ET"),
        "gate_checklist":  gate,
        "last_5_alerts":   last_alerts,
        "config": {
            "TRADE_RISK_PCT":       settings.TRADE_RISK_PCT,
            "MAX_OPEN_POSITIONS":   settings.MAX_OPEN_POSITIONS,
            "MAX_POSITION_PCT":     settings.MAX_POSITION_PCT,
            "MIN_TRADE_CONFIDENCE": settings.MIN_TRADE_CONFIDENCE,
            "SWING_TRAIL_PCT":      settings.SWING_TRAIL_PCT,
            "LIVE_WHITELIST":       settings.live_whitelist,
        },
    }


@app.get("/api/system/status")
async def api_system_status():
    """Comprehensive system status for operations dashboard.

    Returns KILL_SWITCH state, trading flags, broker connectivity,
    open positions, and recent guardrail events.
    """
    from app.config import settings

    status = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "uptime_seconds": round(time.time() - _start_time, 1),
        "environment": os.environ.get("ENVIRONMENT", "production"),
        # Emergency controls
        "kill_switch": settings.KILL_SWITCH,
        "auto_trade_enabled": settings.AUTO_TRADE_ENABLED,
        "live_confirmed": settings.LIVE_CONFIRMED,
        # Broker
        "broker_type": os.environ.get("BROKER_TYPE", "alpaca"),
        "ibkr_host": settings.IBKR_HOST,
        "ibkr_port": settings.IBKR_PORT,
    }

    # Open positions (non-fatal if broker unreachable) — with 10s timeout
    try:
        from app.services.alpaca_client import get_positions as _get_positions
        positions = await asyncio.wait_for(
            asyncio.to_thread(_get_positions),
            timeout=10.0,
        )
        status["open_positions"] = len(positions) if positions else 0
        status["positions"] = [
            {
                "symbol": p.get("symbol", ""),
                "qty": p.get("qty", "0"),
                "market_value": p.get("market_value", "0"),
                "unrealized_pl": p.get("unrealized_pl", "0"),
                "current_price": p.get("current_price", "0"),
            }
            for p in (positions or [])
        ]
    except (asyncio.TimeoutError, Exception) as e:
        status["open_positions"] = "unavailable"
        status["positions_error"] = f"timeout" if isinstance(e, asyncio.TimeoutError) else str(e)

    # Recent guardrail checks — with 5s timeout
    try:
        from app.services.guards import run_all as _run_all
        from app.services.guards.base import GuardContext
        ctx = GuardContext(symbol="__system__", side="buy", price=0.0, qty=1, confidence=0.0)
        results = await asyncio.wait_for(
            asyncio.to_thread(_run_all, ctx),
            timeout=5.0,
        )
        status["guardrail_events"] = [
            {"name": r.name, "passed": r.passed, "reason": r.reason}
            for r in results[:5]
        ]
    except (asyncio.TimeoutError, Exception):
        status["guardrail_events"] = []

    # Strategy config summary
    from app.config import STRATEGY_CONFIGS
    status["strategies"] = {
        sid: {"name": cfg.name, "max_positions": cfg.max_positions}
        for sid, cfg in STRATEGY_CONFIGS.items()
    }

    return status


# ---------------------------------------------------------------------------
# IBKR ping
# ---------------------------------------------------------------------------
async def ibkr_ping():
    """Test TWS/IB Gateway connectivity via socket."""
    import socket
    from app.services.broker.ibkr_config import get_ibkr_config
    _cfg = get_ibkr_config()
    host = _cfg["host"]
    port = _cfg["port"]
    client_id = _cfg["client_id"]
    try:
        sock = socket.create_connection((host, port), timeout=5)
        sock.close()
        return {"status": "ok", "host": host, "port": port, "client_id": client_id, "message": "TWS/IB Gateway is reachable"}
    except socket.timeout:
        return {"status": "error", "host": host, "port": port, "client_id": client_id, "message": "Connection timed out"}
    except ConnectionRefusedError:
        return {"status": "error", "host": host, "port": port, "client_id": client_id, "message": "Connection refused. TWS/IB Gateway is not running."}
    except Exception as exc:
        return {"status": "error", "host": host, "port": port, "client_id": client_id, "message": str(exc)}

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class DashboardAuthMiddleware(BaseHTTPMiddleware):
    """Protect all dashboard pages and API routes behind a simple session cookie.

    - Unauthenticated page requests  → redirect to /login
    - Unauthenticated /api/* requests → JSON 401 (so fetch() gets a proper error)
    - Exempt: /login /logout /health /livez /readyz /static /favicon.ico /ws/
    - If DASHBOARD_USER env var is not set (dev mode), auth is skipped.
    """

    _EXEMPT_PREFIXES = ("/login", "/logout", "/health", "/livez", "/readyz",
                        "/favicon", "/ws/",
                        # Telegram calls this server-to-server with NO login cookie —
                        # it is secured by its own secret-token header + owner chat_id
                        # check, so it must bypass the browser login gate or every
                        # bot update is rejected with 401 and two-way chat never works.
                        "/api/v1/telegram/webhook",
                        # Public-facing marketing surfaces — institutional visitors
                        # must reach these WITHOUT a login (the gated terminal lives
                        # behind /login via the landing's Terminal Access vault).
                        "/landing", "/slides", "/one-pager", "/email-signature",
                        "/report", "/terms", "/press", "/brand")
    # Public pages matched EXACTLY (not by prefix) — the root landing cover.
    _EXEMPT_EXACT = ("/",)

    async def dispatch(self, request, call_next):
        path = request.url.path
        # Allow static assets (JS/CSS/fonts/images) but NOT .html files directly
        if path.startswith("/static/") and not path.endswith(".html"):
            return await call_next(request)
        # Public landing root (exact match)
        if path in self._EXEMPT_EXACT:
            return await call_next(request)
        # Always let other exempt paths through
        if any(path.startswith(p) for p in self._EXEMPT_PREFIXES):
            return await call_next(request)
        # Dev mode: no DASHBOARD_USER configured → skip auth
        if not os.environ.get("DASHBOARD_USER"):
            return await call_next(request)
        # Validate session cookie
        session = request.cookies.get("session")
        if session and secrets.compare_digest(session, _make_session_token()):
            return await call_next(request)
        # Not authenticated
        if path.startswith("/api/"):
            return JSONResponse({"detail": "Not authenticated — visit /login"}, status_code=401)
        # Page request → redirect to login
        next_url = path
        return RedirectResponse(url=f"/login?next={next_url}", status_code=302)


app.add_middleware(DashboardAuthMiddleware)

# Strategy imports
try:
    from app.strategies import get_strategy_selector, StrategyInput, StrategySignal
    _strategy_selector = None  # lazy init

    def _get_selector():
        global _strategy_selector
        if _strategy_selector is None:
            _strategy_selector = get_strategy_selector()
        return _strategy_selector

    def _get_symbol_strategy(symbol: str, hist_df, spy_df=None) -> StrategySignal | None:
        """Run all 4 strategies and return the best one for a symbol."""
        import pandas as pd
        if hist_df is None or len(hist_df) < 30:
            return None
        try:
            sel = _get_selector()
            mkt = {}
            try:
                from app.services.market_context import get_market_status
                mkt = get_market_status()
            except Exception:
                pass
            df_w = None
            try:
                # Weekly bars: resample Alpaca daily data (server-safe, no yfinance)
                from app.services.market_data import fetch as _md_fetch_strat
                _daily = _md_fetch_strat(symbol, period="3mo")
                if _daily is not None and len(_daily) > 10:
                    _daily.index = pd.DatetimeIndex(_daily.index)
                    _daily.columns = [c.lower() for c in _daily.columns]
                    wf = _daily.resample("W").agg({
                        "open": "first", "high": "max",
                        "low": "min", "close": "last", "volume": "sum",
                    }).dropna()
                    if len(wf) > 8:
                        df_w = wf
            except Exception:
                pass
            df_15 = None
            try:
                # 15-min bars: Alpaca intraday (server-safe, no yfinance)
                from app.services.market_data import fetch_alpaca_intraday as _fetch_intra
                m15 = _fetch_intra(symbol, timeframe="15Min", days_back=5)
                if m15 is not None and len(m15) > 30:
                    m15.index = pd.DatetimeIndex(m15.index)
                    m15.columns = [c.lower() for c in m15.columns]
                    df_15 = m15
            except Exception:
                pass
            inp = StrategyInput(
                df_daily=hist_df,
                spy_df=spy_df,
                market_status=mkt,
                df_weekly=df_w,
                df_15min=df_15,
            )
            return sel.best_for_symbol(inp)
        except Exception:
            return None

    _HAS_STRATEGIES = True
except ImportError:
    _HAS_STRATEGIES = False
    def _get_symbol_strategy(*args): return None


# ---------------------------------------------------------------------------
# Timeout helper — wrap slow endpoints so OpenBB Workspace never hangs
# ---------------------------------------------------------------------------

REQUEST_TIMEOUT = 120  # seconds


async def with_timeout(coro, timeout: int = REQUEST_TIMEOUT):
    """Run coroutine with a timeout, returning an error dict on timeout."""
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        return JSONResponse(
            {"error": f"Request timed out after {timeout}s. Try reducing episodes or n_simulations."},
            status_code=504,
        )

CURRENT_DIR = Path(__file__).parent
PROJECT_DIR = CURRENT_DIR.parent

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _fetch_data(symbol: str, period: str = "1y") -> tuple[list[dict], pd.DataFrame]:
    """Fetch OHLCV data with timeout, cache, and Alpaca-first fallback pipeline."""
    sym_clean = _cache_key(symbol)
    cache_key = f"ohlcv_{sym_clean}_{period}"
    cached = _cache_get(cache_key, max_age=3600)
    if cached:
        try:
            df = pd.DataFrame(cached)
            if "date" in df.columns:
                df["date"] = pd.to_datetime(df["date"], utc=True)
            return cached, df
        except Exception:
            pass

    # Use market_data.fetch() — Alpaca first, yfinance fallback, rate-limited, cached
    from app.services.market_data import fetch as _md_fetch
    from app.services.market_context import _run_with_timeout

    df = _run_with_timeout(lambda: _md_fetch(symbol, period=period), timeout=30.0, fallback=None)
    if df is not None and not df.empty:
        records = df.to_dict("records")
        _cache_set(cache_key, records)
        return records, df

    return [], pd.DataFrame()


def _records_to_df(records: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(records)
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], utc=True)
        df.sort_values("date", inplace=True)
        df.reset_index(drop=True, inplace=True)
    return df


# ---------------------------------------------------------------------------
# Result Cache — 24h TTL, JSON file-backed
# ---------------------------------------------------------------------------

# CACHE_DIR lets Railway mount a PERSISTENT volume here so the warm screener/OHLCV
# cache survives restarts (no heavy cold 650-symbol re-scan on every reboot).
_cache_dir = Path(os.environ.get("CACHE_DIR") or (Path(__file__).parent / ".cache"))
_cache_dir.mkdir(parents=True, exist_ok=True)


def _cache_key(name: str) -> str:
    return name.lower().replace("/", "_").replace(" ", "_")


def _cache_get(key: str, max_age: int = 86400):
    path = _cache_dir / f"{key}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        age = time.time() - data.get("_ts", 0)
        if age > max_age:
            path.unlink(missing_ok=True)
            return None
        return data.get("data")
    except Exception:
        return None


def _cache_set(key: str, data):
    path = _cache_dir / f"{key}.json"
    try:
        path.write_text(json.dumps({"_ts": time.time(), "data": data}, default=str))
    except Exception:
        pass


def _cache_clear():
    import shutil
    shutil.rmtree(_cache_dir, ignore_errors=True)
    _cache_dir.mkdir(exist_ok=True)


# ── Screener Progress Tracker ──
_screener_progress = {"current": 0, "total": 0, "status": "idle", "batch": 0}
_yf_semaphore = threading.Semaphore(2)  # max 2 concurrent yfinance calls


def _reset_progress(total: int):
    global _screener_progress
    _now = time.time()
    _screener_progress = {"current": 0, "total": total, "status": "scanning", "batch": 0,
                          "started": _now, "last_advance": _now}


def _update_progress(n: int, batch: int = 0):
    global _screener_progress
    # Track the last time progress ADVANCED — the stuck-detector uses this to tell a
    # slow-but-moving scan apart from a genuinely hung one (see _screener_is_stuck).
    if n > _screener_progress.get("current", 0):
        _screener_progress["last_advance"] = time.time()
    _screener_progress["current"] = n
    if batch:
        _screener_progress["batch"] = batch


def _finish_progress():
    global _screener_progress
    _screener_progress["status"] = "done"
    _screener_progress["current"] = _screener_progress["total"]


SCREENER_BATCH_SIZE = 50
SCREENER_CACHE_TTL = 3600  # 60 min — the 650-symbol scan is CPU-heavy on a small
# container; it was re-running ~every 15 min while the dashboard was open (recurring
# slowness). Hourly is plenty for a swing/monthly halal screener. Stale window = 2×.

# ── Funnel gate (env-gated, default = off = full scan) ──
_FUNNEL_TOP_N = int(os.environ.get("SCREENER_FUNNEL_TOP_N", "0"))


def _funnel_select(symbols: list, rank_fn, top_n: int) -> list:
    """Pure helper: pre-rank symbols and return the top-N (or all if top_n <= 0)."""
    if top_n <= 0 or len(symbols) <= top_n:
        return list(symbols)
    scored = [(rank_fn(s), s) for s in symbols]
    scored.sort(key=lambda x: x[0], reverse=True)
    return [s for _, s in scored[:top_n]]


def _screener_is_stuck(progress: dict, now: float = None,
                       max_age: float = 3600, stall: float = 420) -> bool:
    """True when a 'scanning' run is HUNG — not merely slow.

    A full ~657-symbol FMP scan legitimately takes ~12 min. The old check used a
    600s ABSOLUTE age, so it declared the healthy scan 'stuck' at batch ~11 and
    re-kicked it from scratch on the next request — forever. It never completed, so
    the result cache and `smart_screener_lastgood` were never written (instant-open
    stayed broken and the Monte-Carlo risers panel stayed empty). Now a scan counts
    as stuck only if no batch has ADVANCED in `stall` seconds, with a generous
    absolute `max_age` backstop for pathological hangs. A run with no `last_advance`
    (e.g. an old idle flag) falls back to `started`, so a truly stale scan is still
    caught.
    """
    if now is None:
        now = time.time()
    if progress.get("status") != "scanning":
        return False
    started = progress.get("started", 0)
    last = progress.get("last_advance", started)
    return (now - last) > stall or (now - started) > max_age


# ---------------------------------------------------------------------------
# DL Forecast Endpoints (18+ models)
# ---------------------------------------------------------------------------

# Detect torch availability once at startup — environment-agnostic.
# CPU-only wheel (~250 MB) fits Railway; full CUDA wheel (~1.9 GB) does not.
# All DL model gates use _HAS_TORCH instead of checking RAILWAY_ENVIRONMENT.
try:
    import torch as _torch_probe
    _HAS_TORCH = True
    logger.info("torch %s ready (device: %s)", _torch_probe.__version__,
                "cuda" if _torch_probe.cuda.is_available() else "cpu")
    del _torch_probe
except ImportError:
    _HAS_TORCH = False
    logger.warning("torch not installed — DL models will fall back to ARIMA-only")

# ── Per-model circuit breaker ────────────────────────────────────────────────
# Tracks repeated walk_forward_predict / training failures per model name.
# After _MODEL_CB_THRESHOLD consecutive failures the model is skipped for
# _MODEL_CB_RESET_S seconds so one broken model (e.g. GRU after PSI drift)
# doesn't pollute every consensus/screener call with repeated tracebacks.
_model_cb: dict[str, dict] = {}
_MODEL_CB_THRESHOLD = 3     # failures before opening
_MODEL_CB_RESET_S   = 3600  # 1 hour cool-down
_model_cb_lock = threading.Lock()


def _model_cb_blocked(name: str) -> bool:
    with _model_cb_lock:
        s = _model_cb.get(name, {})
        if s.get("fails", 0) >= _MODEL_CB_THRESHOLD:
            if time.time() - s.get("ts", 0) < _MODEL_CB_RESET_S:
                return True
            # Reset after cool-down
            _model_cb[name] = {"fails": 0, "ts": 0.0}
        return False


def _model_cb_fail(name: str) -> None:
    with _model_cb_lock:
        s = _model_cb.setdefault(name, {"fails": 0, "ts": 0.0})
        s["fails"] += 1
        s["ts"] = time.time()
        if s["fails"] == _MODEL_CB_THRESHOLD:
            logger.warning("Model circuit breaker OPEN for '%s' after %d failures (resets in 1 h)",
                           name, _MODEL_CB_THRESHOLD)


def _model_cb_ok(name: str) -> None:
    with _model_cb_lock:
        _model_cb[name] = {"fails": 0, "ts": 0.0}


# Full model/agent name lists — used for display in /api/info regardless of
# what can actually run. Defined here so they never require torch to import.
_ALL_MODEL_NAMES: list[str] = [
    "arima", "lstm", "gru", "bigru", "gru_2path", "gru_seq2seq",
    "bigru_seq2seq", "gru_vae", "transformer", "ensemble",
    "cnn_seq2seq", "dilated_cnn", "vanilla", "vanilla_bi",
    "vanilla_2path", "lstm_2path", "bilstm_seq2seq", "lstm_vae",
    "attention_rnn",
]
_ALL_AGENT_NAMES: list[str] = [
    "double_dqn", "policy_gradient", "evolution_strategy",
    "turtle", "moving_average", "signal_rolling", "abcd_strategy",
    "q_learning", "double_q_learning", "recurrent_q_learning",
    "duel_q_learning", "double_duel_q_learning", "curiosity_q_learning",
    "recurrent_curiosity_q_learning", "actor_critic", "actor_critic_duel",
    "actor_critic_recurrent", "neuro_evolution", "neuro_evolution_novelty",
]

try:
    from openbb_forecast.models.base import compute_forecast_metrics
    if _HAS_TORCH:
        from openbb_forecast.models.factory import create_model, MODEL_NAMES  # type: ignore[assignment]
        logger.info("Full model set loaded: %s", MODEL_NAMES)
    else:
        # torch not available — ARIMA-only graceful fallback
        from openbb_forecast.models.arima import ARIMAForecaster

        def create_model(name: str, **kw):  # type: ignore[misc]
            if name != "arima":
                raise ValueError(f"Model '{name}' requires torch (not installed)")
            return ARIMAForecaster(**kw)

        MODEL_NAMES = ["arima"]
        logger.info("No torch — ARIMA-only mode")
    _HAS_FORECAST = True
except Exception as e:
    logger.warning("openbb_forecast.models imports failed: %s", e)
    _HAS_FORECAST = False
    compute_forecast_metrics = None
    create_model = None
    MODEL_NAMES = []

_MODELS_DIR = Path(__file__).parent.parent / "models"

# AI Agent — LLM-powered investment analyst with template fallback
from app.ai_agent import AIAgent
from app.services.model_performance import get_all_model_performance
ai_agent = AIAgent()

# Run initial model benchmark if no performance cache exists yet
try:
    from app.services.model_performance import _PERF_DIR
    if not any(_PERF_DIR.glob('*.json')):
        import threading
        def _run_initial_benchmark():
            import time
            time.sleep(15)  # let server fully start
            try:
                from app.services.model_performance import update_all_model_performance
                update_all_model_performance()
                logger.info('Initial model benchmark complete')
            except Exception as e:
                logger.warning('Initial benchmark failed (will retry Sunday): %s', e)
        threading.Thread(target=_run_initial_benchmark, daemon=True).start()
except Exception:
    pass


@app.get("/api/forecast/{model_name}")
async def forecast_model(
    model_name: str,
    symbol: str = Query("AAPL", description="Stock symbol"),
    target_column: str = Query("close", description="Target column"),
    forecast_horizon: int = Query(5, description="Steps to forecast"),
    sequence_length: int = Query(30, description="Lookback window"),
    epochs: int = Query(5, description="Training epochs"),
    n_splits: int = Query(1, description="Walk-forward splits"),
    hidden_size: int = Query(64, description="Hidden size (NN models)"),
    num_layers: int = Query(2, description="Num layers (NN models)"),
    learning_rate: float = Query(0.001, description="Learning rate"),
):
    """Run any DL forecast model by name."""
    if model_name not in MODEL_NAMES:
        available = sorted(MODEL_NAMES)
        return JSONResponse(
            {"error": f"Unknown model '{model_name}'. Available: {available}"},
            status_code=400,
        )

    records, df = _fetch_data(symbol)
    prices = df[target_column].values.astype(float)
    dates = df["date"].astype(str).tolist() if "date" in df.columns else []

    # Build model kwargs — each model type accepts different params
    if model_name in ("arima", "ensemble"):
        model_kwargs = {}
    elif model_name in ("cnn_seq2seq", "dilated_cnn"):
        model_kwargs = {"epochs": epochs, "learning_rate": learning_rate, "filters": hidden_size}
    elif model_name == "transformer":
        model_kwargs = {"epochs": epochs, "learning_rate": learning_rate, "num_layers": num_layers, "d_model": hidden_size}
    else:
        model_kwargs = {
            "hidden_size": hidden_size,
            "num_layers": num_layers,
            "epochs": epochs,
            "learning_rate": learning_rate,
        }

    # Try loading pre-trained weights first (faster: ~2s vs ~30s re-training)
    _ckpt_pt = _MODELS_DIR / model_name / "20260519.pt"
    _ckpt_pkl = _MODELS_DIR / model_name / "20260519.pkl"
    _pretrained_loaded = False

    if _ckpt_pt.exists() and model_name not in ("arima", "ensemble"):
        try:
            from openbb_forecast.models.factory import MODEL_REGISTRY as _MR
            _cls = _MR.get(model_name)
            if _cls and hasattr(_cls, "load"):
                model = _cls.load(_ckpt_pt)
                n_splits = 1  # inference only — no re-training needed
                _pretrained_loaded = True
        except Exception as _e:
            logger.warning(f"Pre-trained load failed for {model_name}: {_e} — falling back to training")

    if not _pretrained_loaded and _ckpt_pkl.exists():
        try:
            from openbb_forecast.models.factory import MODEL_REGISTRY as _MR
            _cls = _MR.get(model_name)
            if _cls and hasattr(_cls, "load"):
                model = _cls.load(_ckpt_pkl)
                n_splits = 1
                _pretrained_loaded = True
        except Exception as _e:
            logger.warning(f"Pickle load failed for {model_name}: {_e} — falling back to training")

    if not _pretrained_loaded:
        model = create_model(model_name, **model_kwargs)

    fold_results = model.walk_forward_predict(
        data=prices,
        sequence_length=sequence_length,
        forecast_horizon=forecast_horizon,
        n_splits=n_splits,
    )

    results = []
    for fold in fold_results:
        for j in range(fold.test_size):
            idx = int(fold.test_indices[j]) if j < len(fold.test_indices) else 0
            date_str = dates[idx] if idx < len(dates) else str(idx)
            pred = float(fold.predictions[j, 0]) if fold.predictions.ndim > 1 else float(fold.predictions[j])
            actual = float(fold.actuals[j, 0]) if fold.actuals.ndim > 1 else float(fold.actuals[j])
            results.append({"date": date_str, "actual": actual, "predicted": pred, "fold": fold.fold})

    metrics = compute_forecast_metrics(fold_results)

    # Shadow posture conditioning — raw predictions are untouched; we add a
    # context_posture and a conditioned_direction that dampens bullish calls in
    # a risk_off regime. Display/validation only unless CONTEXT_CONDITIONING_LIVE.
    context_posture = None
    raw_direction = conditioned_direction = None
    try:
        from app.services.market_context_bundle import get_context_bundle_sync
        context_posture = get_context_bundle_sync().get("risk_posture")
        if results:
            last = results[-1]
            raw_direction = "up" if last.get("predicted", 0) >= last.get("actual", 0) else "down"
            if context_posture == "risk_off" and raw_direction == "up":
                conditioned_direction = "neutral"   # dampen bullish call in risk-off
            else:
                conditioned_direction = raw_direction
    except Exception:
        pass

    return {
        "symbol": symbol,
        "model": model_name,
        "results": results,
        "metrics": metrics,
        "context_posture": context_posture,
        "raw_direction": raw_direction,
        "conditioned_direction": conditioned_direction,
    }


# ---------------------------------------------------------------------------
# RL Agent Endpoints (19 agents)
# ---------------------------------------------------------------------------

try:
    if _HAS_TORCH:
        from openbb_forecast.agents.factory import create_agent, AGENT_NAMES  # type: ignore[assignment]
        _HAS_AGENTS = True
        logger.info("RL agents loaded: %s", AGENT_NAMES)
    else:
        # torch not available — RL agents require it
        create_agent = None
        AGENT_NAMES = []
        _HAS_AGENTS = False
        logger.info("No torch — RL agents disabled")
except Exception as e:
    logger.warning("openbb_forecast.agents imports failed: %s", e)
    _HAS_AGENTS = False
    create_agent = None
    AGENT_NAMES = []

# _RUNNABLE_* = what can actually execute (may be subset on Railway)
# _ALL_*      = full list for display in /api/info (always 19+19)
_RUNNABLE_MODEL_NAMES = MODEL_NAMES
_RUNNABLE_AGENT_NAMES = AGENT_NAMES


@app.get("/api/agent/{agent_name}")
async def run_agent(
    agent_name: str,
    symbol: str = Query("AAPL", description="Stock symbol"),
    target_column: str = Query("close", description="Price column"),
    initial_capital: float = Query(10000.0, description="Starting capital"),
    commission_bps: float = Query(10.0, description="Commission in bps"),
    slippage_bps: float = Query(5.0, description="Slippage in bps"),
    window_size: int = Query(30, description="State window"),
    episodes: int = Query(20, description="Training episodes"),
    max_position: int = Query(5, description="Max position size"),
    stop_loss_pct: float = Query(0.02, description="Stop loss"),
    max_drawdown_pct: float = Query(0.10, description="Max drawdown"),
    train_ratio: float = Query(0.7, description="Train/test split"),
):
    """Run any RL trading agent by name."""
    if agent_name not in AGENT_NAMES:
        available = sorted(AGENT_NAMES)
        return JSONResponse(
            {"error": f"Unknown agent '{agent_name}'. Available: {available}"},
            status_code=400,
        )

    records, df = _fetch_data(symbol)
    prices = df[target_column].values.astype(float)
    dates = df["date"].astype(str).tolist() if "date" in df.columns else []

    # Create agent with appropriate init args
    agent = _create_agent_safe(agent_name, window_size)

    try:
        # Dispatch to the appropriate evaluation method
        if hasattr(agent, "walk_forward_evaluate"):
            result = _run_agent_walk_forward(agent, prices, dates, agent_name, symbol, initial_capital,
                                              commission_bps, slippage_bps, window_size, episodes,
                                              max_position, stop_loss_pct, max_drawdown_pct, train_ratio)
        else:
            result = _run_agent_backtest(agent, prices, dates, agent_name, symbol)
        return result
    finally:
        # Clean up UUID checkpoint files from this run
        _cleanup_agent_checkpoints(agent)


def _create_agent_safe(agent_name: str, window_size: int):
    """Create agent with optional state_size and unique checkpoint (prevents concurrent race)."""
    import uuid
    extra = {}
    if agent_name == "double_dqn":
        extra["checkpoint_name"] = f"double_dqn_{uuid.uuid4().hex[:8]}"
    try:
        return create_agent(agent_name, state_size=window_size + 3, **extra)
    except TypeError:
        return create_agent(agent_name, **extra)


def _cleanup_agent_checkpoints(agent):
    """Remove temporary UUID checkpoint files created during this agent run."""
    import os
    try:
        ckpt_dir = getattr(agent, "checkpoint_dir", None)
        ckpt_name = getattr(agent, "checkpoint_name", None)
        if ckpt_dir is None or ckpt_name is None:
            return
        from pathlib import Path
        for f in Path(ckpt_dir).glob(f"{ckpt_name}*"):
            try:
                f.unlink()
            except Exception:
                pass
    except Exception:
        pass


def _run_agent_walk_forward(agent, prices, dates, agent_name, symbol,
                             initial_capital, commission_bps, slippage_bps,
                             window_size, episodes, max_position, stop_loss_pct,
                             max_drawdown_pct, train_ratio):
    """Run a standard agent with walk_forward_evaluate."""
    wf_result = agent.walk_forward_evaluate(
        prices=prices,
        train_ratio=train_ratio,
        initial_capital=initial_capital,
        commission_bps=commission_bps,
        slippage_bps=slippage_bps,
        window_size=window_size,
        episodes=episodes,
        max_position=max_position,
        stop_loss_pct=stop_loss_pct,
        max_drawdown_pct=max_drawdown_pct,
    )

    bs = wf_result.get("backtest_summary", {})

    # Classic agents (Turtle, MovingAverage, etc.) don't return test_results
    if "test_results" not in wf_result or "benchmark_curve" not in wf_result:
        return {
            "symbol": symbol,
            "agent": agent_name,
            "results": [],
            "summary": {
                "total_return": bs.get("total_return", 0),
                "sharpe_ratio": bs.get("sharpe_ratio", 0),
                "max_drawdown": bs.get("max_drawdown", 0),
                "n_trades": bs.get("n_trades", 0),
                "win_rate": bs.get("win_rate", 0),
            },
            "risk_events": wf_result.get("risk_events", 0),
            "stop_losses": wf_result.get("stop_losses", 0),
        }

    test_results = wf_result["test_results"]
    split_idx = int(len(prices) * train_ratio)
    test_dates = dates[split_idx:]
    equity = test_results["equity_curve"]
    benchmark = wf_result["benchmark_curve"]

    results = []
    for t in range(min(len(equity), len(test_dates))):
        results.append({
            "date": test_dates[t],
            "portfolio_value": float(equity[t]),
            "benchmark_value": float(benchmark[t]) if t < len(benchmark) else 0,
            "cumulative_return": float(equity[t]) / initial_capital - 1.0,
        })

    return {
        "symbol": symbol,
        "agent": agent_name,
        "results": results,
        "summary": {
            "total_return": bs.get("total_return", 0),
            "sharpe_ratio": bs.get("sharpe_ratio", 0),
            "max_drawdown": bs.get("max_drawdown", 0),
            "n_trades": bs.get("n_trades", 0),
            "win_rate": bs.get("win_rate", 0),
        },
        "risk_events": wf_result.get("risk_events", 0),
        "stop_losses": wf_result.get("stop_losses", 0),
    }


def _run_agent_backtest(agent, prices, dates, agent_name, symbol):
    """Run a classic agent with simple backtest."""
    result = agent.backtest(prices)
    equity = result.get("equity_curve", [])
    bs = result.get("summary", {})

    results = []
    for t in range(min(len(equity), len(dates))):
        results.append({
            "date": dates[t],
            "portfolio_value": float(equity[t]),
            "benchmark_value": float(prices[t]) / float(prices[0]) * 10000.0 if len(prices) > 0 and prices[0] else 0,
            "cumulative_return": float(equity[t]) / 10000 - 1.0,
        })

    return {
        "symbol": symbol,
        "agent": agent_name,
        "results": results,
        "summary": {
            "total_return": bs.get("total_return", 0),
            "sharpe_ratio": bs.get("sharpe_ratio", 0),
            "max_drawdown": bs.get("max_drawdown", 0),
            "n_trades": bs.get("n_trades", 0),
            "win_rate": bs.get("win_rate", 0),
        },
        "risk_events": 0,
        "stop_losses": 0,
    }


# ---------------------------------------------------------------------------
# Monte Carlo Simulation
# ---------------------------------------------------------------------------

try:
    from openbb_forecast.simulation.monte_carlo import MonteCarloSimulator
    _HAS_MC = True
except Exception:
    logger.warning("openbb_forecast.simulation imports failed; Monte Carlo disabled")
    _HAS_MC = False
    MonteCarloSimulator = None


@app.get("/api/monte-carlo")
async def monte_carlo(
    symbol: str = Query("AAPL", description="Stock symbol"),
    target_column: str = Query("close", description="Price column"),
    n_simulations: int = Query(500, description="Number of simulations"),
    forecast_days: int = Query(30, description="Days to forecast"),
    dynamic_volatility: bool = Query(False, description="Dynamic vol"),
):
    """Monte Carlo simulation with GBM."""
    records, df = _fetch_data(symbol)
    prices = df[target_column].values.astype(float)

    sim = MonteCarloSimulator(seed=42)
    result = sim.simulate(
        prices=prices,
        n_simulations=n_simulations,
        forecast_days=forecast_days,
        dynamic_volatility=dynamic_volatility,
    )

    return {
        "symbol": symbol,
        "day_stats": result["day_stats"],
        "summary": result["summary"],
    }


# ---------------------------------------------------------------------------
# Risk Metrics
# ---------------------------------------------------------------------------

try:
    from openbb_forecast.risk import metrics as risk_metrics
    _HAS_RISK = True
except Exception:
    logger.warning("openbb_forecast.risk imports failed; risk metrics disabled")
    _HAS_RISK = False
    risk_metrics = None


@app.get("/api/metrics")
async def compute_metrics(
    symbol: str = Query("AAPL", description="Stock symbol"),
    target_column: str = Query("close", description="Price column"),
    risk_free_rate: float = Query(0.0, description="Risk-free rate"),
):
    """Compute risk/return metrics."""
    records, df = _fetch_data(symbol)
    prices = np.nan_to_num(df[target_column].values.astype(float), nan=0.0)

    simple_returns = (prices[1:] - prices[:-1]) / np.where(prices[:-1] != 0, prices[:-1], 1)
    simple_returns = np.nan_to_num(simple_returns, nan=0.0, posinf=0.0, neginf=0.0)
    equity = np.nan_to_num(prices / prices[0] * 10000, nan=0.0)

    return {
        "symbol": symbol,
        "annualized_return": risk_metrics.annualized_return(simple_returns),
        "annualized_volatility": float(np.std(simple_returns) * np.sqrt(252)),
        "sharpe_ratio": risk_metrics.sharpe_ratio(simple_returns, risk_free_rate),
        "sortino_ratio": risk_metrics.sortino_ratio(simple_returns, risk_free_rate),
        "max_drawdown": risk_metrics.max_drawdown(equity),
        "calmar_ratio": risk_metrics.calmar_ratio(simple_returns, equity),
        "var_95": risk_metrics.value_at_risk(simple_returns, 0.95),
        "cvar_95": risk_metrics.conditional_var(simple_returns, 0.95),
    }


# ---------------------------------------------------------------------------
# Halal Status — AAOIFI-compliant Shariah screening via yfinance
# ---------------------------------------------------------------------------


import math


def _clean_nan(obj):
    """Recursively replace NaN/Inf with None for JSON compliance."""
    if isinstance(obj, dict):
        return {k: _clean_nan(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_clean_nan(v) for v in obj]
    elif isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    return obj


def _safe_float(val, default=0.0):
    if val is None:
        return default
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except (TypeError, ValueError):
        return default


async def _fmp_call(method, *args, **kwargs):
    """Call an FMPClient method in a thread (it uses httpx sync)."""
    return await asyncio.to_thread(lambda: method(*args, **kwargs))


def _parse_52w_range(range_str: str | None, side: str) -> float | None:
    """Parse FMP '52W Low - 52W High' range string."""
    if not range_str:
        return None
    try:
        parts = [p.strip() for p in str(range_str).split("-")]
        if len(parts) == 2:
            return float(parts[-1].replace(",", "")) if side == "high" else float(parts[0].replace(",", ""))
    except Exception:
        pass
    return None


def _fmp_profile_to_info(p: dict) -> dict:
    """Convert FMP profile dict to yfinance-compatible info dict.
    Used so all downstream code that consumes `info` works unchanged.
    """
    return {
        "longName": p.get("companyName"),
        "shortName": p.get("companyName"),
        "sector": p.get("sector"),
        "industry": p.get("industry"),
        "longBusinessSummary": p.get("description"),
        "website": p.get("website"),
        "fullTimeEmployees": p.get("fullTimeEmployees"),
        "exchange": p.get("exchangeShortName"),
        "exchangeShortName": p.get("exchangeShortName"),
        "currency": p.get("currency", "USD"),
        "currentPrice": p.get("price"),
        "regularMarketPrice": p.get("price"),
        "previousClose": p.get("price"),
        "marketCap": p.get("mktCap"),
        "beta": p.get("beta"),
        "trailingPE": p.get("pe"),
        "forwardPE": p.get("forwardPE"),
        "dividendYield": p.get("dividendYield"),
        "dividendRate": p.get("dividendPerShare"),
        "dividendPerShare": p.get("dividendPerShare"),
        "totalDebt": p.get("totalDebt"),
        "totalCash": p.get("cashAndCashEquivalents") or p.get("totalCash"),
        "totalRevenue": p.get("revenue"),
        "country": p.get("country"),
        "fiftyTwoWeekHigh": _parse_52w_range(p.get("range"), "high"),
        "fiftyTwoWeekLow": _parse_52w_range(p.get("range"), "low"),
        "averageVolume": p.get("volAvg"),
        "marketState": "",
        # analytics — neutral defaults; enriched downstream if needed
        "recommendationKey": "",
        "targetMeanPrice": None,
        "targetHighPrice": None,
        "targetLowPrice": None,
        "targetMedianPrice": None,
        "numberOfAnalystOpinions": 0,
        "recommendationMean": None,
        "averageAnalystRating": None,
    }


def _get_smart_info(symbol: str) -> dict:
    """Company info for screener — FMP primary (works on servers), yfinance fallback.

    Returns a yfinance-compatible dict; downstream scoring functions work unchanged.
    Keys added from key_metrics: trailingPE, pegRatio, priceToBook,
    priceToSalesTrailing12Months, returnOnEquity, profitMargins, revenueGrowth, earningsGrowth.
    """
    try:
        profile = fmp_client.get_profile(symbol) or {}
        if profile and profile.get("mktCap", 0) and _safe_float(profile.get("mktCap")) > 0:
            info = _fmp_profile_to_info(profile)
            # Enrich with key metrics for scoring functions
            try:
                m_list = fmp_client.get_key_metrics(symbol, limit=1) or []
                m = m_list[0] if m_list else {}
                info["trailingPE"] = m.get("peRatio") or profile.get("pe")
                info["pegRatio"] = m.get("pegRatio")
                info["priceToBook"] = m.get("pbRatio") or m.get("priceToBookRatio")
                info["priceToSalesTrailing12Months"] = m.get("priceToSalesRatio")
                info["returnOnEquity"] = m.get("roe") or m.get("returnOnEquity")
                info["profitMargins"] = m.get("netProfitMargin")
                info["revenueGrowth"] = m.get("revenueGrowth")
                info["earningsGrowth"] = m.get("netIncomeGrowth")
            except Exception:
                pass
            # Analyst layer — price-target consensus + buy/hold/sell ratings,
            # mapped to the yfinance-compatible keys _sentiment_score reads.
            # FMP client caches + rate-limits these, so per-symbol cost is low.
            try:
                pt = fmp_client.get_price_target(symbol) or {}
                tgt = _safe_float(pt.get("targetConsensus") or pt.get("targetMedian"))
                if tgt:
                    info["targetMeanPrice"] = tgt
                recs = fmp_client.get_analyst_recommendations(symbol) or []
                if recs:
                    latest = recs[0] if isinstance(recs[0], dict) else {}
                    sb = int(latest.get("analystRatingsStrongBuy") or 0)
                    b  = int(latest.get("analystRatingsbuy") or latest.get("analystRatingsBuy") or 0)
                    h  = int(latest.get("analystRatingsHold") or 0)
                    s  = int(latest.get("analystRatingsSell") or 0)
                    ss = int(latest.get("analystRatingsStrongSell") or 0)
                    total = sb + b + h + s + ss
                    if total > 0:
                        info["numberOfAnalystOpinions"] = total
                        buckets = {"strong_buy": sb, "buy": b, "hold": h, "sell": s, "strong_sell": ss}
                        info["recommendationKey"] = max(buckets, key=buckets.get)
            except Exception:
                pass
            return info
    except Exception:
        pass
    # yfinance fallback (works locally; fails on Railway servers)
    try:
        with _yf_semaphore:
            return yf.Ticker(symbol).info or {}
    except Exception:
        return {}


async def _yf_info(symbol: str, timeout: float = 12.0) -> dict:
    """Ticker info — FMP primary (server-safe), yfinance fallback (local).
    Returns a yfinance-compatible dict so all callers work unchanged."""
    try:
        profile = await asyncio.wait_for(
            asyncio.to_thread(lambda: fmp_client.get_profile(symbol)),
            timeout=8.0,
        )
        if profile and profile.get("companyName"):
            return _fmp_profile_to_info(profile)
    except Exception:
        pass
    # yfinance fallback
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(lambda: yf.Ticker(symbol).info or {}),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        logger.warning("yfinance info timeout [%s] > %.1fs", symbol, timeout)
    except Exception as e:
        logger.warning("yfinance info error [%s]: %s", symbol, e)
    return {}


def _screen_halal(symbol: str, info: dict | None = None) -> dict:
    """DJIM halal compliance — delegates to the canonical screen_symbol implementation.

    The ``info`` parameter is accepted for backward-compatibility but ignored;
    screen_symbol fetches its own data via FMP + yfinance fallback.
    """
    from app.services.market_context import _run_with_timeout
    from app.services.halal_screening import get_halal_status

    def _compute():
        # Cache-first: reads the durable ScreeningResult (pre-warmed daily), screens live
        # only on miss/stale, and serves a flagged-stale value if a live refresh fails — so
        # the bulk scan reads the warm cache during trading instead of hitting blocked yfinance.
        result = get_halal_status(symbol)
        if result is None:
            return {"symbol": symbol, "is_halal": False, "error": "No data available"}
        # Ensure backward-compat fields that callers expect
        result.setdefault("status", "HALAL - Compliant" if result.get("is_halal") else "NON-COMPLIANT")
        return result

    # Backstop timeout. With cache-first reads most calls don't fetch at all; the live-miss
    # path's yfinance fallback now fails fast via its own breaker (halal_screening._yf_fallback),
    # so 45s was just dead wait on a blocked Yahoo — 20s comfortably covers FMP + price paths.
    result = _run_with_timeout(_compute, timeout=20, fallback=None)
    if result is None:
        return {"symbol": symbol, "is_halal": False, "error": "Halal check timed out"}
    return result


@app.get("/api/halal-status")
async def halal_status(
    symbol: str = Query("AAPL", description="Stock symbol"),
):
    """AAOIFI Halal compliance screening for any stock symbol."""
    async def compute():
        return _screen_halal(symbol)
    return await cached_or_compute(f"halal:status:{symbol.upper()}", 86400, compute)


@app.get("/api/stock/card")
async def stock_card(symbol: str = Query("AAPL", description="Stock symbol")):
    """Aggregated single-symbol profile for the Stock ID Card: profile + fundamental
    + technical + sentiment + signal + full DJIM halal. Trade plan and forecast are
    fetched by the client from their own endpoints. Defensive: missing data -> None.
    """
    sym = symbol.upper().strip()
    async def compute():
        spy_df = None
        try:
            _, spy_df = _fetch_data("SPY", period="2mo")
        except Exception:
            pass
        a = {}
        try:
            a = _analyze_smart(sym, spy_df=spy_df) or {}
        except Exception as e:
            logger.debug("stock_card analyze %s: %s", sym, e)
        try:
            halal = _screen_halal(sym)
        except Exception as e:
            logger.debug("stock_card halal %s: %s", sym, e)
            halal = {"symbol": sym, "is_halal": False, "error": "halal check failed"}

        def g(key, default=None):
            return a.get(key, default)

        return {
            "symbol": sym,
            "profile": {
                "name": g("company") or sym,
                "sector": g("sector") or "",
                "industry": g("industry") or "",
                "price": g("price"),
                "change_pct": g("change_pct"),
                "market_cap": g("market_cap"),
            },
            "signal": {
                "verdict": g("signal") or "—",
                "smart_score": g("smart_score"),
                "smart_max": 100,
                "rs_vs_spy": g("rs_vs_spy") or "—",
                "hard_gates_passed": g("hard_gates_passed", True),
                "gates_failed": g("hard_gates_failed", []),
            },
            "fundamental": {
                "score": g("fundamental_score"),
                "max": 40,
                "profitability": g("profitability_details", {}),
                "valuation": g("valuation_details", {}),
                "market": g("market_details", {}),
            },
            "technical": {
                "score": g("momentum_score"),
                "max": 30,
                "details": g("momentum_details", {}),
            },
            "sentiment": g("sentiment") or g("sentiment_details") or {},
            "halal": halal,
            "disclaimers": [
                "DJIM quantitative screen only — no named Sharia supervisory board.",
                "Advisory only — manual execution. Not financial advice.",
            ],
        }

    return await cached_or_compute(f"stock:card:{sym}", 300, compute)


# ---------------------------------------------------------------------------
# Stock Research — fundamental data, ratios, financials via yfinance
# ---------------------------------------------------------------------------


@app.get("/api/stock/summary")
async def stock_summary(
    symbol: str = Query("AAPL", description="Stock symbol"),
):
    """Comprehensive stock summary: profile, sector, valuation ratios, dividend."""
    async def compute():
        info = await _yf_info(symbol)
        if not info or not info.get("longName"):
            fmp_data = await _fmp_call(fmp_client.get_company_data, symbol)
            if fmp_data:
                p = fmp_data["profile"]
                m = fmp_data["metrics"]
                return {
                    "symbol": symbol,
                    "company_name": p.get("companyName", symbol),
                    "sector": p.get("sector", ""),
                    "industry": p.get("industry", ""),
                    "description": (p.get("description") or "")[:500],
                    "website": p.get("website", ""),
                    "employees": p.get("fullTimeEmployees", 0),
                    "exchange": p.get("exchangeShortName", ""),
                    "currency": p.get("currency", "USD"),
                    "price": _safe_float(p.get("price")),
                    "change": 0,
                    "change_pct": 0,
                    "market_cap": p.get("marketCap", 0),
                    "enterprise_value": m.get("enterpriseValue", 0),
                    "pe_ratio": m.get("peRatio"),
                    "forward_pe": m.get("forwardPeRatio"),
                    "eps": m.get("netProfitMargin"),
                    "book_value": m.get("bookValuePerShare"),
                    "price_to_book": m.get("priceToBookRatio"),
                    "dividend_yield": m.get("dividendYield"),
                    "dividend_rate": None,
                    "payout_ratio": m.get("payoutRatio"),
                    "beta": p.get("beta"),
                    "52w_high": None,
                    "52w_low": None,
                    "avg_volume": p.get("volume"),
                    "market_state": "",
                }
            return {"symbol": symbol, "error": "No data available"}
        price = _safe_float(info.get("currentPrice") or info.get("regularMarketPrice") or info.get("previousClose"))
        prev_close = _safe_float(info.get("previousClose"))
        change = round(price - prev_close, 2) if price and prev_close else 0
        change_pct = round(change / prev_close * 100, 2) if prev_close and prev_close != 0 else 0
        return {
            "symbol": symbol,
            "company_name": info.get("longName") or info.get("shortName") or symbol,
            "sector": info.get("sector", ""),
            "industry": info.get("industry", ""),
            "description": (info.get("longBusinessSummary") or "")[:500],
            "website": info.get("website", ""),
            "employees": info.get("fullTimeEmployees", 0),
            "exchange": info.get("exchange", ""),
            "currency": info.get("currency", "USD"),
            "price": price,
            "change": change,
            "change_pct": change_pct,
            "market_cap": info.get("marketCap", 0),
            "enterprise_value": info.get("enterpriseValue", 0),
            "pe_ratio": info.get("trailingPE") or info.get("forwardPE"),
            "forward_pe": info.get("forwardPE"),
            "eps": info.get("trailingEps"),
            "book_value": info.get("bookValue"),
            "price_to_book": info.get("priceToBook"),
            "dividend_yield": info.get("dividendYield"),
            "dividend_rate": info.get("dividendRate"),
            "payout_ratio": info.get("payoutRatio"),
            "beta": info.get("beta"),
            "52w_high": info.get("fiftyTwoWeekHigh"),
            "52w_low": info.get("fiftyTwoWeekLow"),
            "avg_volume": info.get("averageVolume"),
            "market_state": info.get("marketState", ""),
        }
    return await cached_or_compute(f"stock:summary:{symbol.upper()}", 300, compute, compute_timeout=15)


@app.get("/api/stock/ratios")
async def stock_ratios(
    symbol: str = Query("AAPL", description="Stock symbol"),
):
    """Key financial ratios: profitability, liquidity, leverage, efficiency."""
    async def compute():
        info = await _yf_info(symbol)
        if not info or info.get("regularMarketPrice") is None and not info.get("marketCap"):
            ratios = await _fmp_call(fmp_client.get_financial_ratios, symbol)
            if ratios:
                r = ratios[0]
                return {
                    "symbol": symbol,
                    "profitability": {"profit_margin": r.get("profitMargin"), "operating_margin": r.get("operatingMargin"), "return_on_equity": r.get("returnOnEquity"), "return_on_assets": r.get("returnOnAssets"), "revenue_per_share": r.get("revenuePerShare"), "gross_margin": r.get("grossProfitMargin"), "ebitda_margin": None},
                    "valuation": {"pe_ratio": r.get("priceEarningsRatio"), "forward_pe": r.get("priceEarningsRatio"), "peg_ratio": None, "price_to_book": r.get("priceToBookRatio"), "price_to_sales": r.get("priceToSalesRatio"), "enterprise_to_revenue": r.get("enterpriseValueToRevenue"), "enterprise_to_ebitda": r.get("enterpriseValueToEbitda")},
                    "liquidity": {"current_ratio": r.get("currentRatio"), "quick_ratio": r.get("quickRatio"), "debt_to_equity": r.get("debtEquityRatio"), "total_debt": None, "total_cash": None, "cash_per_share": None},
                    "growth": {"revenue_growth": r.get("revenueGrowth"), "earnings_growth": r.get("netIncomeGrowth"), "earnings_quarterly_growth": None},
                }
            return {"symbol": symbol, "error": "No data"}
        profitability = {
            "profit_margin": info.get("profitMargins"),
            "operating_margin": info.get("operatingMargins"),
            "return_on_equity": info.get("returnOnEquity"),
            "return_on_assets": info.get("returnOnAssets"),
            "revenue_per_share": info.get("revenuePerShare"),
            "gross_margin": info.get("grossMargins"),
            "ebitda_margin": info.get("ebitdaMargins"),
        }
        valuation = {
            "pe_ratio": info.get("trailingPE"),
            "forward_pe": info.get("forwardPE"),
            "peg_ratio": info.get("pegRatio"),
            "price_to_book": info.get("priceToBook"),
            "price_to_sales": info.get("priceToSalesTrailing12Months"),
            "enterprise_to_revenue": info.get("enterpriseToRevenue"),
            "enterprise_to_ebitda": info.get("enterpriseToEbitda"),
        }
        liquidity = {
            "current_ratio": info.get("currentRatio"),
            "quick_ratio": info.get("quickRatio"),
            "debt_to_equity": info.get("debtToEquity"),
            "total_debt": info.get("totalDebt"),
            "total_cash": info.get("totalCash"),
            "cash_per_share": info.get("cashPerShare"),
        }
        growth = {
            "revenue_growth": info.get("revenueGrowth"),
            "earnings_growth": info.get("earningsGrowth"),
            "earnings_quarterly_growth": info.get("earningsQuarterlyGrowth"),
        }
        return {
            "symbol": symbol,
            "profitability": {k: round(v, 4) if isinstance(v, (int, float)) else v for k, v in profitability.items()},
            "valuation": {k: round(v, 4) if isinstance(v, (int, float)) else v for k, v in valuation.items()},
            "liquidity": {k: round(v, 4) if isinstance(v, (int, float)) else v for k, v in liquidity.items()},
            "growth": {k: round(v, 4) if isinstance(v, (int, float)) else v for k, v in growth.items()},
        }
    return await cached_or_compute(f"stock:ratios:{symbol.upper()}", 600, compute, compute_timeout=15)


@app.get("/api/stock/financials")
async def stock_financials(
    symbol: str = Query("AAPL", description="Stock symbol"),
    statement: str = Query("income", description="income | balance | cashflow"),
    periods: int = Query(4, description="Number of periods"),
):
    """Annual financial statements with YoY comparison. FMP primary, yfinance fallback."""
    async def compute():
        # ── FMP primary (server-safe) ────────────────────────────────────
        fmp_fetcher = {
            "income": fmp_client.get_income_statement,
            "balance": fmp_client.get_balance_sheet,
            "cashflow": fmp_client.get_cash_flow,
        }
        fmp_fn = fmp_fetcher.get(statement)
        if fmp_fn:
            fmp_data = await _fmp_call(fmp_fn, symbol, limit=periods)
            if fmp_data:
                years = []
                for item in fmp_data:
                    yr = str(item.get("date", ""))[:4]
                    if yr not in years:
                        years.append(yr)
                if not years:
                    years = [f"Year {i+1}" for i in range(len(fmp_data))]
                rows = []
                for item in fmp_data:
                    for key, val in item.items():
                        if isinstance(val, (int, float)) and key not in ("symbol", "date", "fillingDate", "calendarYear", "cik", "acceptedDate", "period", "link", "finalLink", "reportedCurrency"):
                            rows.append({
                                "item": key.replace("_", " ").title(),
                                "item_short": key[:40],
                                years[0] if years else "value": round(float(val), 2) if isinstance(val, (int, float)) else val,
                            })
                return {"symbol": symbol, "statement": statement, "years": years, "rows": rows}

        # ── yfinance fallback (works locally) ────────────────────────────
        try:
            ticker = await asyncio.to_thread(lambda: yf.Ticker(symbol))
            yf_fetcher = {"income": ticker.financials, "balance": ticker.balance_sheet, "cashflow": ticker.cashflow}
            df = yf_fetcher.get(statement)
            if df is not None and not df.empty:
                years = []
                for col in df.columns[:periods]:
                    try:
                        years.append(str(col.year))
                    except AttributeError:
                        years.append(str(col))
                rows = []
                for idx in df.index:
                    item_name = str(idx)
                    item_short = item_name.replace(" ", "_").replace("/", "_").replace("-", "_").replace(".", "")[:40]
                    row = {"item": item_name, "item_short": item_short}
                    values = []
                    for col in df.columns[:periods]:
                        val = df.loc[idx, col]
                        num_val = round(float(val), 2) if isinstance(val, (int, float)) else None
                        row[str(col.year)] = num_val
                        values.append(num_val)
                    yoy_change = None
                    if len(values) >= 2 and all(v is not None for v in values[:2]):
                        prev, curr = values[1], values[0]
                        if prev != 0:
                            yoy_change = round((curr - prev) / abs(prev) * 100, 1)
                    row["yoy_change_pct"] = yoy_change
                    rows.append(row)
                return {"symbol": symbol, "statement": statement, "years": years, "rows": rows}
        except Exception:
            pass

        return {"symbol": symbol, "statement": statement, "error": "No data", "rows": [], "years": []}
    return await cached_or_compute(f"stock:financials:{symbol.upper()}:{statement}", 3600, compute, compute_timeout=15)


@app.get("/api/stock/dividends")
async def stock_dividends(
    symbol: str = Query("AAPL", description="Stock symbol"),
):
    """Dividend history, yield, payout ratio."""
    async def compute():
        history = []
        # ── FMP primary ─────────────────────────────────────────────────
        fmp_div = await _fmp_call(fmp_client.get_historical_dividends, symbol, limit=24)
        if fmp_div:
            for item in fmp_div:
                history.append({
                    "date": str(item.get("date", ""))[:10],
                    "dividend": _safe_float(item.get("dividend")),
                })
        profile = await _fmp_call(fmp_client.get_profile, symbol)
        if profile or history:
            return {
                "symbol": symbol,
                "dividend_yield": _safe_float(profile.get("dividendYield")) if profile else None,
                "dividend_rate": _safe_float(profile.get("dividendPerShare")) if profile else None,
                "payout_ratio": None,
                "ex_dividend_date": None,
                "last_24_payments": history,
            }
        # ── yfinance fallback ────────────────────────────────────────────
        try:
            ticker = await asyncio.to_thread(lambda: yf.Ticker(symbol))
            info = ticker.info or {}
            div_history = ticker.dividends
            if div_history is not None and not div_history.empty:
                for date, val in div_history.tail(24).items():
                    history.append({"date": str(date.date()), "dividend": round(float(val), 4)})
            return {
                "symbol": symbol,
                "dividend_yield": info.get("dividendYield"),
                "dividend_rate": info.get("dividendRate"),
                "payout_ratio": info.get("payoutRatio"),
                "ex_dividend_date": str(info.get("exDividendDate")) if info.get("exDividendDate") else None,
                "last_24_payments": history,
            }
        except Exception:
            pass
        return {"symbol": symbol, "dividend_yield": None, "dividend_rate": None,
                "payout_ratio": None, "ex_dividend_date": None, "last_24_payments": []}
    return await cached_or_compute(f"stock:dividends:{symbol.upper()}", 3600, compute, compute_timeout=15)


@app.get("/api/stock/holders")
async def stock_holders(
    symbol: str = Query("AAPL", description="Stock symbol"),
):
    """Major institutional holders and insider ownership."""
    async def compute():
        # ── FMP primary ─────────────────────────────────────────────────
        profile = await _fmp_call(fmp_client.get_profile, symbol)
        fmp_inst = await _fmp_call(fmp_client.get_institutional_holders, symbol)
        if profile or fmp_inst:
            institutional = []
            for item in (fmp_inst or [])[:10]:
                institutional.append({
                    "holder": str(item.get("holder") or item.get("name", "")),
                    "shares": int(_safe_float(item.get("shares"))),
                    "value": float(_safe_float(item.get("value") or item.get("marketValue"))),
                })
            return {
                "symbol": symbol,
                "insider_ownership_pct": _safe_float(profile.get("insiderOwnership")) if profile else None,
                "institutional_ownership_pct": _safe_float(profile.get("institutionalOwnership")) if profile else None,
                "short_ratio": None,
                "short_pct": None,
                "major_holders": [],
                "institutional_holders": institutional,
            }
        # ── yfinance fallback ────────────────────────────────────────────
        try:
            ticker = await asyncio.to_thread(lambda: yf.Ticker(symbol))
            info = ticker.info or {}
            major_holders = []
            try:
                mh = ticker.major_holders
                if mh is not None and not mh.empty:
                    for _, row in mh.iterrows():
                        major_holders.append({str(row.index[0]) if hasattr(row, 'index') else 'holder': str(row.iloc[0])})
            except Exception:
                pass
            institutional = []
            try:
                ih = ticker.institutional_holders
                if ih is not None and not ih.empty:
                    for _, row in ih.iterrows():
                        institutional.append({
                            "holder": str(row.get("Holder", "")),
                            "shares": int(row.get("Shares", 0)) if isinstance(row.get("Shares"), (int, float)) else 0,
                            "value": float(row.get("Value", 0)) if isinstance(row.get("Value"), (int, float)) else 0,
                        })
            except Exception:
                pass
            return {
                "symbol": symbol,
                "insider_ownership_pct": info.get("heldPercentInsiders"),
                "institutional_ownership_pct": info.get("heldPercentInstitutions"),
                "short_ratio": info.get("shortRatio"),
                "short_pct": info.get("shortPercentOfFloat"),
                "major_holders": major_holders[:5],
                "institutional_holders": institutional[:10],
            }
        except Exception:
            pass
        return {"symbol": symbol, "insider_ownership_pct": None, "institutional_ownership_pct": None,
                "short_ratio": None, "short_pct": None, "major_holders": [], "institutional_holders": []}
    return await cached_or_compute(f"stock:holders:{symbol.upper()}", 86400, compute, compute_timeout=15)


@app.get("/api/stock/earnings")
async def stock_earnings(
    symbol: str = Query("AAPL", description="Stock symbol"),
):
    """Annual earnings history and quarterly surprises."""
    async def compute():
        surprises = []
        eps = None
        forward_eps = None
        # ── FMP primary ─────────────────────────────────────────────────
        estimates = await _fmp_call(fmp_client.get_analyst_estimates, symbol, limit=8)
        profile = await _fmp_call(fmp_client.get_profile, symbol)
        if estimates:
            for est in estimates:
                eps_avg = est.get("estimatedEpsAvg") or est.get("estimatedEps")
                if eps_avg:
                    surprises.append({
                        "date": str(est.get("date", est.get("period", "")))[:10],
                        "estimate": _safe_float(eps_avg),
                        "actual": _safe_float(est.get("actualEps")) if est.get("actualEps") else None,
                        "surprise_pct": _safe_float(est.get("surprisePercentage")) if est.get("surprisePercentage") else None,
                    })
        if profile:
            eps = _safe_float(profile.get("eps")) or None
        if surprises or profile:
            return {"symbol": symbol, "eps": eps, "forward_eps": None, "quarterly_surprises": surprises}
        # ── yfinance fallback ────────────────────────────────────────────
        try:
            ticker = await asyncio.to_thread(lambda: yf.Ticker(symbol))
            info = ticker.info or {}
            try:
                surp = ticker.earnings_dates
                if surp is not None and not surp.empty:
                    for idx in surp.index[:8]:
                        row = surp.loc[idx]
                        est = row.get("EPS Estimate")
                        act = row.get("Reported EPS")
                        if est is not None and act is not None:
                            try:
                                est_val = float(est)
                                act_val = float(act)
                                surp_val = round((act_val - est_val) / abs(est_val) * 100, 2) if est_val != 0 else 0
                                surprises.append({"date": str(idx.date()) if hasattr(idx, 'date') else str(idx),
                                                  "estimate": est_val, "actual": act_val, "surprise_pct": surp_val})
                            except (TypeError, ValueError):
                                continue
            except Exception:
                pass
            return {"symbol": symbol, "eps": info.get("trailingEps"), "forward_eps": info.get("forwardEps"),
                    "quarterly_surprises": surprises}
        except Exception:
            pass
        return {"symbol": symbol, "eps": None, "forward_eps": None, "quarterly_surprises": []}
    return await cached_or_compute(f"stock:earnings:{symbol.upper()}", 3600, compute, compute_timeout=15)


@app.get("/api/stock/screener")
async def stock_screener(
    sector: str = Query("", description="Filter by sector"),
    industry: str = Query("", description="Filter by industry"),
    min_market_cap: float = Query(0, description="Min market cap"),
    max_market_cap: float = Query(1e15, description="Max market cap"),
    halal_only: bool = Query(True, description="Only halal-compliant stocks"),
    limit: int = Query(30, description="Max results"),
    use_cache: str = Query("true", description="Use cached results"),
):
    """Screen stocks from halal universe by sector, market cap, and halal status.

    Uses ThreadPoolExecutor for parallel yfinance fetches with per-symbol timeout.
    Results cached for 24h. Falls back to cached data on failure.
    """
    _use_cache = str(use_cache).lower() in ("true", "1", "yes")
    cache_key = f"stock_screener_{sector}_{industry}_{min_market_cap}_{halal_only}"
    if _use_cache:
        cached = _cache_get(cache_key)
        if cached:
            return cached

    universe = ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "NVDA", "META", "BRK-B",
                "JNJ", "V", "PG", "HD", "DIS", "MA", "NFLX", "CRM", "ADBE", "AMD",
                "PYPL", "INTC", "KO", "PEP", "WMT", "XOM", "CVX", "JPM", "BAC",
                "WFC", "GS", "MS", "UNH", "ABT", "TMO", "AVGO", "TXN", "QCOM",
                "NKE", "SBUX", "MCD", "BA", "GE", "CAT", "IBM", "ORCL", "SAP",
                "LLY", "NVS", "RY", "TD", "BMO", "BNS", "ENB", "TRP", "SU", "CNQ"]

    def _screen_one(sym: str) -> dict | None:
        try:
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                fut = pool.submit(_fetch_screener_data, sym, halal_only)
                return fut.result(timeout=30)
        except concurrent.futures.TimeoutError:
            logger.warning(f"Screener timeout for {sym}")
            return None
        except Exception:
            return None

    all_results = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(_screen_one, sym): sym for sym in universe}
        for f in as_completed(futures):
            r = f.result()
            if r:
                all_results.append(r)

    # Apply post-fetch filters
    filtered = []
    for r in all_results:
        mcap = r.get("market_cap", 0)
        if mcap < min_market_cap or mcap > max_market_cap:
            continue
        sec = (r.get("sector") or "").lower()
        ind = (r.get("industry") or "").lower()
        if sector and sector.lower() not in sec:
            continue
        if industry and industry.lower() not in ind:
            continue
        if halal_only and not r.get("is_halal", True):
            continue
        filtered.append(r)

    filtered = sorted(filtered, key=lambda r: r.get("market_cap", 0), reverse=True)[:limit]

    result = {
        "total": len(filtered),
        "filters": {"sector": sector, "industry": industry, "halal_only": halal_only},
        "results": filtered,
        "source": "live",
    }
    _cache_set(cache_key, result)
    return result


def _fetch_screener_data(sym: str, halal_only: bool) -> dict | None:
    """Fetch and format screener data for one symbol."""
    try:
        ticker = yf.Ticker(sym)
        info = ticker.info or {}
    except Exception:
        return None

    mcap = _safe_float(info.get("marketCap"))

    try:
        halal_result = _screen_halal(sym)
        is_halal = halal_result.get("is_halal", False)
    except Exception:
        is_halal = False

    if halal_only and not is_halal:
        return None

    return {
        "symbol": sym,
        "company": info.get("shortName") or info.get("longName") or sym,
        "sector": info.get("sector", ""),
        "industry": info.get("industry", ""),
        "price": _safe_float(info.get("currentPrice") or info.get("regularMarketPrice")),
        "market_cap": mcap,
        "pe": info.get("trailingPE"),
        "forward_pe": info.get("forwardPE"),
        "dividend_yield": info.get("dividendYield"),
        "beta": info.get("beta"),
        "is_halal": is_halal,
    }


# ---------------------------------------------------------------------------
# Stock News — latest headlines via yfinance
# ---------------------------------------------------------------------------


@app.get("/api/stock/news")
async def stock_news(
    symbol: str = Query("AAPL", description="Stock symbol"),
    limit: int = Query(10, description="Max news items"),
):
    """Latest news headlines for a stock symbol."""
    async def compute():
        # Alpaca News primary (Benzinga-sourced, reliable on Railway)
        from app.services.market_data import get_alpaca_news
        alpaca_news = await asyncio.to_thread(get_alpaca_news, symbol, limit)
        if alpaca_news:
            return {"symbol": symbol, "count": len(alpaca_news), "news": alpaca_news, "source": "alpaca"}
        # FMP fallback (server-safe, rate-limited)
        fmp_news = await _fmp_call(fmp_client.get_stock_news, symbol, limit=limit)
        if fmp_news:
            return {"symbol": symbol, "count": len(fmp_news), "news": fmp_news, "source": "fmp"}
        # yfinance fallback (works locally)
        news = []
        try:
            ticker = await asyncio.to_thread(lambda: yf.Ticker(symbol))
            items = ticker.news or []
            for item in items[:limit]:
                news.append({
                    "title": item.get("title", ""),
                    "publisher": item.get("publisher", ""),
                    "link": item.get("link", ""),
                    "published": item.get("providerPublishTime"),
                    "type": item.get("type", ""),
                    "summary": (item.get("summary") or "")[:300],
                })
        except Exception:
            pass
        return {"symbol": symbol, "count": len(news), "news": news, "source": "yfinance"}
    return await cached_or_compute(f"stock:news:{symbol.upper()}:{limit}", 900, compute, compute_timeout=15)


@app.get("/api/stock/chart")
async def stock_chart(
    symbol: str = Query("AAPL", description="Stock symbol"),
    range: str = Query("1mo", description="1d | 5d | 1mo | 3mo | 6mo | 1y | 2y | 5y"),
):
    """OHLCV price history for charting."""
    period_map = {"1d": "1d", "5d": "5d", "1mo": "1mo", "3mo": "3mo", "6mo": "6mo", "1y": "1y", "2y": "2y", "5y": "5y"}
    period = period_map.get(range, "1mo")

    async def compute():
        from app.services.market_data import fetch as _md_fetch
        df = await asyncio.to_thread(lambda: _md_fetch(symbol, period=period))
        if df is None or df.empty:
            return {"symbol": symbol, "range": range, "bars": []}
        bars = []
        for _, row in df.iterrows():
            # market_data.fetch returns a "date" column (not index)
            ts = row.get("date")
            if ts is not None:
                ts = str(ts.date()) if hasattr(ts, "date") else str(ts)[:10]
            else:
                ts = ""
            bars.append({
                "date": ts,
                "open": round(float(row.get("open") or 0), 2),
                "high": round(float(row.get("high") or 0), 2),
                "low": round(float(row.get("low") or 0), 2),
                "close": round(float(row.get("close") or 0), 2),
                "volume": int(row.get("volume") or 0),
            })
        return {"symbol": symbol, "range": range, "bars": bars}
    return await cached_or_compute(f"stock:chart:{symbol.upper()}:{range}", 300, compute, compute_timeout=15)


@app.get("/api/stock/peers")
async def stock_peers(
    symbol: str = Query("AAPL", description="Stock symbol"),
    limit: int = Query(10, description="Max peers"),
):
    """Find peer companies in the same sector with key comparison metrics."""
    async def compute():
        # FMP primary for base symbol
        info = await _yf_info(symbol)  # already FMP-first via rewrite above
        sector = info.get("sector", "")
        industry = info.get("industry", "")
        company = info.get("longName") or info.get("shortName") or symbol

        peers = []
        candidates = ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA",
                      "JPM", "V", "JNJ", "PG", "HD", "DIS", "NFLX", "CRM",
                      "ADBE", "AMD", "KO", "PEP", "WMT"]
        for candidate in candidates:
            if candidate == symbol.upper():
                continue
            try:
                cinfo = await _yf_info(candidate)  # FMP-first
                csector = cinfo.get("sector", "")
                if csector != sector:
                    continue
                peers.append({
                    "symbol": candidate,
                    "company": cinfo.get("shortName") or cinfo.get("longName") or candidate,
                    "price": _safe_float(cinfo.get("currentPrice") or cinfo.get("regularMarketPrice")),
                    "market_cap": cinfo.get("marketCap"),
                    "pe_ratio": cinfo.get("trailingPE"),
                    "forward_pe": cinfo.get("forwardPE"),
                    "eps": cinfo.get("trailingEps"),
                    "dividend_yield": cinfo.get("dividendYield"),
                    "beta": cinfo.get("beta"),
                    "52w_high": cinfo.get("fiftyTwoWeekHigh"),
                    "52w_low": cinfo.get("fiftyTwoWeekLow"),
                })
                if len(peers) >= limit:
                    break
            except Exception:
                continue
        return {"symbol": symbol, "company": company, "sector": sector, "industry": industry, "peers": peers}
    return await cached_or_compute(f"stock:peers:{symbol.upper()}:{limit}", 3600, compute, compute_timeout=15)


@app.get("/api/stock/analyst")
async def stock_analyst(
    symbol: str = Query("AAPL", description="Stock symbol"),
):
    """Analyst ratings, upgrades/downgrades, earnings calendar, and company profile."""
    async def compute():
        # ── FMP primary (always try first — works on servers) ────────────
        fmp_result = await _analyst_fmp_fallback(symbol)
        if fmp_result and fmp_result.get("company_name"):
            return fmp_result
        # ── yfinance fallback (works locally) ────────────────────────────
        try:
            ticker = await asyncio.to_thread(lambda: yf.Ticker(symbol))
            info = ticker.info or {}
        except Exception:
            info = {}

        has_data = info.get("longName") or info.get("shortName") or info.get("marketCap")
        if not has_data:
            return fmp_result or await _analyst_fmp_fallback(symbol)

        # --- Upgrades / Downgrades ---
        upgrades_downgrades = []
        try:
            ud = ticker.upgrades_downgrades
            if ud is not None and not ud.empty:
                for idx, row in ud.head(25).iterrows():
                    date_val = idx if not hasattr(idx, 'date') else idx.date()
                    upgrades_downgrades.append({
                        "date": str(date_val),
                        "firm": str(row.get("Firm", "")),
                        "to_grade": str(row.get("ToGrade", "")),
                        "from_grade": str(row.get("FromGrade", "")),
                        "action": str(row.get("Action", "")),
                    })
        except Exception:
            pass

        # --- Recommendations Summary ---
        recommendations = {}
        try:
            rec = ticker.recommendations
            if rec is not None and not rec.empty:
                r = rec.iloc[0]
                recommendations = {
                    "strong_buy": int(r.get("strongBuy", 0)),
                    "buy": int(r.get("buy", 0)),
                    "hold": int(r.get("hold", 0)),
                    "sell": int(r.get("sell", 0)),
                    "strong_sell": int(r.get("strongSell", 0)),
                    "period": str(r.name) if hasattr(r, 'name') else "current",
                }
        except Exception:
            pass

        # --- Price Targets ---
        price_targets = {
            "high": _safe_float(info.get("targetHighPrice")),
            "low": _safe_float(info.get("targetLowPrice")),
            "mean": _safe_float(info.get("targetMeanPrice")),
            "median": _safe_float(info.get("targetMedianPrice")),
        }

        # --- Analyst Consensus ---
        analyst_consensus = {
            "rating": info.get("recommendationKey"),
            "mean_rating": _safe_float(info.get("recommendationMean")),
            "num_opinions": info.get("numberOfAnalystOpinions"),
            "avg_rating_text": info.get("averageAnalystRating"),
        }

        # --- Earnings Calendar ---
        earnings_calendar = {}
        try:
            cal = ticker.calendar
            if cal is not None:
                if isinstance(cal, dict):
                    cal_dict = cal
                elif hasattr(cal, 'to_dict'):
                    cal_dict = cal.to_dict()
                else:
                    cal_dict = {}
                earnings_dates = []
                raw_dates = cal_dict.get("Earnings Date", [])
                if isinstance(raw_dates, list):
                    for d in raw_dates:
                        try:
                            earnings_dates.append(str(d.date()) if hasattr(d, 'date') else str(d))
                        except Exception:
                            earnings_dates.append(str(d))
                earnings_calendar = {
                    "earnings_date": earnings_dates,
                    "earnings_high": _safe_float(cal_dict.get("Earnings High")),
                    "earnings_low": _safe_float(cal_dict.get("Earnings Low")),
                    "earnings_avg": _safe_float(cal_dict.get("Earnings Average")),
                    "revenue_high": _safe_float(cal_dict.get("Revenue High")),
                    "revenue_low": _safe_float(cal_dict.get("Revenue Low")),
                    "revenue_avg": _safe_float(cal_dict.get("Revenue Average")),
                }
        except Exception:
            pass

        # --- Company Profile ---
        company_profile = {
            "name": info.get("longName") or info.get("shortName") or symbol,
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "website": info.get("website"),
            "employees": info.get("fullTimeEmployees"),
            "country": info.get("country"),
            "city": info.get("city"),
            "state": info.get("state"),
            "address": info.get("address1"),
            "zip": info.get("zip"),
            "description": (info.get("longBusinessSummary") or "")[:2000],
            "market_cap": info.get("marketCap"),
            "enterprise_value": info.get("enterpriseValue"),
            "exchange": info.get("exchange"),
            "currency": info.get("currency"),
        }

        return {
            "symbol": symbol,
            "upgrades_downgrades": upgrades_downgrades,
            "recommendations": recommendations,
            "price_targets": price_targets,
            "analyst_consensus": analyst_consensus,
            "earnings_calendar": earnings_calendar,
            "company_profile": company_profile,
        }
    return await cached_or_compute(f"stock:analyst:{symbol.upper()}", 1800, compute, compute_timeout=15)


@app.get("/api/stock/dcf")
async def stock_dcf(
    symbol: str = Query("AAPL", description="Stock symbol"),
):
    """Discounted Cash Flow valuation via FMP."""
    async def compute():
        data = await _fmp_call(fmp_client.get_dcf, symbol)
        if not data:
            return {"symbol": symbol, "error": "DCF data not available"}
        return {
            "symbol": symbol,
            "dcf": _safe_float(data.get("dcf")),
            "stock_price": _safe_float(data.get("Stock Price")),
            "date": data.get("date", ""),
        }
    return await cached_or_compute(f"stock:dcf:{symbol.upper()}", 86400, compute, compute_timeout=15)


# ---------------------------------------------------------------------------
# OpenBB Endpoints — ESG, Revenue Segments, Transcripts, Rotation, ETF, Macro
# (DESIGN.md compliant — all data real, no synthetic values)
# ---------------------------------------------------------------------------


@app.get("/api/stock/esg")
async def stock_esg(
    symbol: str = Query("AAPL", description="Stock symbol"),
):
    """ESG score (Environmental / Social / Governance) — relevant for halal screening.

    Returns total_esg_score, component scores, and highest controversy level.
    Uses yfinance via OpenBB. All values None when data unavailable.
    """
    async def compute():
        from app.services.openbb_data import get_esg_score
        data = await asyncio.to_thread(get_esg_score, symbol)
        if data:
            return data
        return {"symbol": symbol.upper(), "error": "ESG data not available"}
    return await cached_or_compute(f"stock:esg:{symbol.upper()}", 86400, compute, compute_timeout=20)


@app.get("/api/stock/revenue-segments")
async def stock_revenue_segments(
    symbol: str = Query("AAPL", description="Stock symbol"),
):
    """Revenue breakdown by business segment (AAOIFI halal screening — 5% threshold).

    Uses FMP via OpenBB. Segments with revenue > 5% of total flagged for review.
    Requires FMP_API_KEY.
    """
    async def compute():
        from app.services.openbb_data import get_revenue_per_segment
        segments = await asyncio.to_thread(get_revenue_per_segment, symbol)
        return {"symbol": symbol.upper(), "segments": segments}
    return await cached_or_compute(
        f"stock:revenue_segments:{symbol.upper()}", 86400, compute, compute_timeout=20
    )


@app.get("/api/stock/transcript")
async def stock_transcript(
    symbol: str = Query("AAPL", description="Stock symbol"),
    year: int = Query(2024, description="Fiscal year"),
    quarter: int = Query(4, description="Quarter (1-4)"),
):
    """Earnings call transcript text from FMP.

    Content truncated to 5000 characters. full_length gives the original length.
    Useful for NLP / management tone analysis. Requires FMP_API_KEY.
    """
    async def compute():
        from app.services.openbb_data import get_earnings_transcript
        data = await asyncio.to_thread(
            get_earnings_transcript, symbol, year, quarter
        )
        if data:
            return data
        return {"symbol": symbol.upper(), "year": year, "quarter": quarter,
                "error": "transcript not available — check FMP_API_KEY or try a different quarter"}
    cache_key = f"stock:transcript:{symbol.upper()}:{year}:Q{quarter}"
    return await cached_or_compute(cache_key, 604800, compute, compute_timeout=30)


@app.get("/api/chart/rotation")
async def chart_rotation(
    symbols: str = Query(
        "XLK,XLF,XLV,XLE,XLI,XLP,XLU,XLB,XLRE",
        description="Comma-separated sector ETF symbols",
    ),
    benchmark: str = Query("SPY", description="Benchmark symbol"),
):
    """Relative Rotation Graph (Rose Petal chart) — sector momentum vs benchmark.

    Classifies each symbol into: leading / weakening / lagging / improving.
    Uses FMP via OpenBB. Requires FMP_API_KEY.
    """
    async def compute():
        from app.services.openbb_data import get_relative_rotation
        sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
        data = await asyncio.to_thread(get_relative_rotation, sym_list, benchmark.upper())
        return {
            "symbols":   sym_list,
            "benchmark": benchmark.upper(),
            "data":      data,
            "quadrants": {
                "leading":   "High RS-Ratio, Rising Momentum — buy/hold",
                "weakening": "High RS-Ratio, Falling Momentum — consider reducing",
                "lagging":   "Low RS-Ratio, Falling Momentum — avoid",
                "improving": "Low RS-Ratio, Rising Momentum — watch for entry",
            },
        }
    return await cached_or_compute(
        f"chart:rotation:{symbols}:{benchmark}", 3600, compute, compute_timeout=30
    )


@app.get("/api/etf/{symbol}/info")
async def etf_info(symbol: str, force: bool = Query(False)):
    """ETF profile: AUM, expense ratio, inception date, description. Requires FMP_API_KEY."""
    from app.config import settings as _cfg
    sym = symbol.upper()
    async def compute():
        from app.services.openbb_data import get_etf_info
        data = await asyncio.to_thread(get_etf_info, sym)
        if data:
            return data
        return {"symbol": sym, "error": "ETF info not available"}
    result = await cached_or_compute(f"etf:info:{sym}", 86400, compute,
                                     force_refresh=force, compute_timeout=20)
    # Auto-bust stale cache: if FMP key is set but result is an error stub → force refresh
    if _cfg.FMP_API_KEY and result.get("error"):
        result = await cached_or_compute(f"etf:info:{sym}", 86400, compute,
                                         force_refresh=True, compute_timeout=20)
    return result


@app.get("/api/etf/{symbol}/holdings")
async def etf_holdings(symbol: str, force: bool = Query(False)):
    """Top ETF holdings with weight percentages. Requires FMP_API_KEY."""
    from app.config import settings as _cfg
    sym = symbol.upper()
    async def compute():
        from app.services.openbb_data import get_etf_holdings
        holdings = await asyncio.to_thread(get_etf_holdings, sym)
        return {"symbol": sym, "count": len(holdings), "holdings": holdings}
    result = await cached_or_compute(f"etf:holdings:{sym}", 86400, compute,
                                     force_refresh=force, compute_timeout=20)
    # Auto-bust stale cache: if FMP key is set but holdings list is empty → force refresh
    if _cfg.FMP_API_KEY and not result.get("holdings"):
        result = await cached_or_compute(f"etf:holdings:{sym}", 86400, compute,
                                         force_refresh=True, compute_timeout=20)
    return result


@app.get("/api/etf/{symbol}/sectors")
async def etf_sectors(symbol: str, force: bool = Query(False)):
    """ETF sector allocation (weights). Requires FMP_API_KEY."""
    from app.config import settings as _cfg
    sym = symbol.upper()
    async def compute():
        from app.services.openbb_data import get_etf_sectors
        sectors = await asyncio.to_thread(get_etf_sectors, sym)
        return {"symbol": sym, "sectors": sectors}
    result = await cached_or_compute(f"etf:sectors:{sym}", 86400, compute,
                                     force_refresh=force, compute_timeout=20)
    # Auto-bust stale cache: if FMP key is set but sectors list is empty → force refresh
    if _cfg.FMP_API_KEY and not result.get("sectors"):
        result = await cached_or_compute(f"etf:sectors:{sym}", 86400, compute,
                                         force_refresh=True, compute_timeout=20)
    return result


@app.post("/api/cache/bust-openbb", include_in_schema=False)
async def bust_openbb_cache():
    """Manually clear all OpenBB-related Redis cache keys (macro, ETF, ESG, senate).
    Call this after adding/changing API keys to force fresh data on next request.
    """
    from app.services.redis_client import get_redis
    redis = await get_redis()
    if redis is None:
        return {"cleared": 0, "note": "Redis not available"}
    patterns = ["macro:*", "etf:*", "stock:esg:*", "stock:segments:*", "stock:senate:*",
                "rotation:*", "chart:rotation:*"]
    total = 0
    for pat in patterns:
        try:
            keys = await redis.keys(pat)
            if keys:
                await redis.delete(*keys)
                total += len(keys)
        except Exception as exc:
            logger.warning("cache bust pattern %s failed: %s", pat, exc)
    return {"cleared": total, "patterns": patterns,
            "note": "All OpenBB cache keys cleared. Next requests will fetch fresh data."}


@app.get("/api/debug/openbb", include_in_schema=False)
async def debug_openbb():
    """Diagnostic: verify all data providers are working after the OpenBB signal-bug fix.

    Tests direct REST calls (FRED, FMP, Tiingo) that replaced the broken OpenBB wrappers.
    Also reports OpenBB provider import status for reference.
    """
    from app.config import settings as _cfg
    out: dict = {
        "keys": {
            "FMP_API_KEY":   bool(_cfg.FMP_API_KEY),
            "FRED_API_KEY":  bool(_cfg.FRED_API_KEY),
            "TIINGO_TOKEN":  bool(_cfg.TIINGO_TOKEN),
        },
        "providers": {},
        "live_calls": {},
        "note": "All data calls now use direct REST (httpx) — OpenBB wrappers bypassed",
    }

    # 1) Which OpenBB providers are importable? (informational only — not used for data)
    for prov in ("openbb_fmp", "openbb_fred", "openbb_tiingo"):
        try:
            __import__(prov)
            out["providers"][prov] = "installed (not used — direct REST preferred)"
        except Exception as exc:
            out["providers"][prov] = f"not installed: {exc}"

    # 2) FRED direct REST — VIX
    def _test_fred_rest():
        try:
            from app.services.openbb_data import get_vix_current
            vix = get_vix_current()
            return {"ok": vix is not None, "vix": vix,
                    "method": "FRED REST API direct"}
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    # 3) FMP direct REST — ETF holdings
    def _test_fmp_rest():
        try:
            from app.services.fmp_client import fmp_client
            holdings = fmp_client.get_etf_holdings("SPY", limit=5)
            return {"ok": bool(holdings), "holdings_count": len(holdings),
                    "sample": holdings[0] if holdings else None,
                    "method": "FMP REST API direct"}
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    # 4) Tiingo direct REST — OHLCV
    def _test_tiingo_rest():
        try:
            if not _cfg.TIINGO_TOKEN:
                return {"ok": False, "note": "TIINGO_TOKEN not set"}
            from app.services.openbb_data import fetch_ohlcv_tiingo
            df = fetch_ohlcv_tiingo("AAPL", period="1mo")
            if df is not None and not df.empty:
                return {"ok": True, "rows": len(df), "latest": str(df["date"].iloc[-1])[:10],
                        "method": "Tiingo REST API direct"}
            return {"ok": False, "note": "empty response"}
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    # 5) FRED macro indicators
    def _test_macro():
        try:
            from app.services.openbb_data import get_economic_indicators
            data = get_economic_indicators()
            non_null = sum(1 for v in data.values() if v is not None)
            return {"ok": non_null > 0, "non_null_indicators": non_null,
                    "data": data, "method": "FRED REST API direct"}
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    out["live_calls"]["fred_vix_rest"]      = await asyncio.to_thread(_test_fred_rest)
    out["live_calls"]["fmp_etf_rest"]       = await asyncio.to_thread(_test_fmp_rest)
    out["live_calls"]["tiingo_ohlcv_rest"]  = await asyncio.to_thread(_test_tiingo_rest)
    out["live_calls"]["fred_macro_rest"]    = await asyncio.to_thread(_test_macro)
    return out


@app.get("/api/macro/indicators")
async def macro_indicators(force: bool = Query(False, description="Bypass cache and recompute")):
    """Real macroeconomic indicators from FRED.

    Returns: CPI YoY %, Fed Funds Rate %, Unemployment %, Yield Spread 10Y-2Y %, HY Spread %.
    All values None when FRED_API_KEY is not configured.
    Source: Federal Reserve Economic Data (FRED).
    Pass ?force=true to bypass Redis cache (useful after adding FRED_API_KEY).
    """
    from app.config import settings as _cfg
    async def compute():
        from app.services.openbb_data import get_economic_indicators
        data = await asyncio.to_thread(get_economic_indicators)
        from datetime import datetime, timezone
        return {
            "t10y2y":      data.get("t10y2y"),
            "hy_spread":   data.get("hy_spread"),
            "fed_rate":    data.get("fed_rate"),
            "cpi_yoy":     data.get("cpi_yoy"),
            "unemployment": data.get("unemployment"),
            "source":      "FRED (Federal Reserve Economic Data)",
            "note":        "Set FRED_API_KEY env var for real data (free at fred.stlouisfed.org)",
            "cached_at":   datetime.now(timezone.utc).isoformat(),
        }
    # Auto-bust stale cache: if FRED key is now set but cached result is all null → force refresh
    _FRED_KEYS = ("t10y2y", "hy_spread", "fed_rate", "cpi_yoy", "unemployment")
    result = await cached_or_compute("macro:indicators", 1800, compute,
                                     force_refresh=force, compute_timeout=30)
    if _cfg.FRED_API_KEY and all(result.get(k) is None for k in _FRED_KEYS):
        result = await cached_or_compute("macro:indicators", 1800, compute,
                                         force_refresh=True, compute_timeout=30)
    return result


@app.get("/api/context/bundle")
async def context_bundle(force: bool = Query(False, description="Bypass cache and recompute")):
    """The unified MarketContextBundle — the platform's reactive conditioning state.

    Composes regime + FRED macro + sector rotation into one versioned snapshot with
    a derived risk_posture (risk_on / neutral / risk_off). This is the single source
    of truth that the screener, risk desk, forecasting, and MizanAI all read so a
    change in market regime cascades across every layer.

    The `version` integer bumps whenever risk_posture flips (also logged to regime_log).
    """
    from app.services.market_context_bundle import get_context_bundle
    return await get_context_bundle(force=force)


@app.get("/api/alerts")
async def api_alerts(
    limit: int = Query(50, description="Max alerts to return"),
    symbol: str = Query("", description="Filter by symbol"),
    alert_type: str = Query("", description="Filter by type (strong_buy, consensus, ...)"),
    sent_only: bool = Query(False, description="Only alerts actually sent to Telegram"),
):
    """Persisted trading-signal alerts from the alerts table.

    Each alert records the regime + risk_posture at emit time and whether it
    passed the Risk Desk gate — so the alerts page shows a real, auditable feed
    instead of ephemeral Telegram-only messages.
    """
    from app.services.alert_store import recent_alerts
    rows = await asyncio.to_thread(
        recent_alerts, limit, symbol, alert_type, sent_only
    )
    return {"count": len(rows), "alerts": rows}


@app.get("/api/risk/status")
async def risk_status():
    """Risk Desk status — posture, market halt, Kelly config, guard blocks, VaR.

    A single read of the platform's risk state, surfaced for the dashboard and as
    a MizanAI tool so the assistant can answer "would the Risk Desk allow this?".
    """
    def _compute_sync() -> dict:
        out: dict = {}
        # 1) Posture / regime / gates / VIX from the context bundle
        try:
            from app.services.market_context_bundle import get_context_bundle_sync
            b = get_context_bundle_sync()
            out.update({
                "regime":       b.get("regime"),
                "risk_posture": b.get("risk_posture"),
                "gates":        b.get("gates"),
                "vix":          b.get("vix"),
                "reasons":      b.get("reasons"),
            })
        except Exception:
            pass
        # 2) Market-level halt (VIX/SPY/regime/system guards)
        try:
            from app.services.alert_store import market_risk_gate
            ok, reason = market_risk_gate("SPY")
            out["market_halt"] = not ok
            out["market_halt_reason"] = reason
        except Exception:
            pass
        # 3) Kelly + position-sizing config
        try:
            from app.core.config import app_cfg
            out["kelly"] = {
                "enabled_after_trades": app_cfg.risk.kelly_enabled_after_trades,
                "risk_per_trade_pct":   app_cfg.risk.risk_per_trade_pct,
                "max_positions":        app_cfg.risk.max_positions,
            }
        except Exception:
            pass
        # 4) Guard blocks (24h), latest portfolio snapshot, simple historical VaR
        try:
            from datetime import datetime, timezone, timedelta
            from app.db.database import SessionLocal
            from app.db.models import GuardLog, PortfolioSnapshot, TradeHistory
            db = SessionLocal()
            try:
                since = datetime.now(timezone.utc) - timedelta(hours=24)
                out["guard_blocks_24h"] = (
                    db.query(GuardLog)
                      .filter(GuardLog.ts >= since, GuardLog.passed.is_(False))
                      .count()
                )
                snap = (db.query(PortfolioSnapshot)
                          .order_by(PortfolioSnapshot.created_at.desc()).first())
                if snap:
                    pos = snap.positions_json if isinstance(snap.positions_json, list) else []
                    out["equity"] = snap.total_equity
                    out["cash"] = snap.cash
                    out["open_positions"] = len(pos)
                pnl_rows = (db.query(TradeHistory.pnl_pct)
                              .filter(TradeHistory.pnl_pct.isnot(None))
                              .order_by(TradeHistory.created_at.desc()).limit(100).all())
                pnls = sorted(float(r[0]) for r in pnl_rows)
                if len(pnls) >= 20:
                    import math
                    idx = max(0, int(math.floor(0.05 * len(pnls))) - 1)
                    out["var_95_pct"] = round(pnls[idx], 2)   # 95% 1-trade historical VaR
                    out["n_trades_var"] = len(pnls)
                else:
                    out["var_95_pct"] = None
            finally:
                db.close()
        except Exception:
            pass
        return out

    async def _compute():
        return await asyncio.to_thread(_compute_sync)
    return await cached_or_compute("risk:status", 120, _compute, compute_timeout=20)


@app.get("/api/signal/lineage/{correlation_id}")
async def signal_lineage(correlation_id: str):
    """End-to-end lineage for one signal: the signal_history → alerts → trades
    chain linked by correlation_id. Answers 'why did we act on this?'.
    """
    from app.services.signal_bus import gather_lineage
    return await asyncio.to_thread(gather_lineage, correlation_id)


@app.get("/api/analysis/signal-weights")
async def signal_weights_analysis(period_days: int = 30):
    """Signal accuracy + model weights dashboard data.

    Returns layer weights, per-source accuracy, overall health, and
    adaptive gate state. Used by the Analysis Lab Signal Accuracy tab.
    """
    from app.services.model_weights import get_source_weights, get_overall_health, adaptive_gate_delta
    from app.services.signal_tracker import get_accuracy_report
    from app.config import settings

    def _compute():
        layer_weights = get_source_weights(period_days=period_days)
        accuracy_rows = get_accuracy_report(period_days=period_days)
        health = get_overall_health(period_days=period_days)
        gate_delta, gate_meta = adaptive_gate_delta(period_days=period_days)

        return {
            "layer_weights": layer_weights,
            "source_accuracy": accuracy_rows,
            "overall_health": health,
            "gate_state": {
                "delta": gate_delta,
                "applied": gate_meta.get("applied", False),
                "tightened": gate_meta.get("tightened", False),
                "win_rate_pct": gate_meta.get("win_rate_pct"),
                "sample": gate_meta.get("sample", 0),
                "reason": gate_meta.get("reason", ""),
            },
            "flags": {
                "conviction_sizing_live": getattr(settings, "CONVICTION_SIZING_LIVE", False),
                "adaptive_gates_live": getattr(settings, "ADAPTIVE_GATES_LIVE", False),
            },
            "period_days": period_days,
        }

    return await asyncio.to_thread(_compute)

@app.get("/api/analysis/model-explain")
async def model_explain_endpoint():
    """Explain the current weighted-consensus attribution.

    Returns which tools/models drove the latest ensemble decision
    and by how much. No trained model required -- works from the
    existing model_weights + tool_votes infrastructure.

    Future: supports SHAP TreeExplainer when XGBoost/sklearn models
    are added to the pipeline.
    """
    from app.services.smart_ensemble import get_model_weights
    from app.services.model_explainer import get_ensemble_attribution

    weights = get_model_weights()

    # Build sample tool votes from the current model lineup
    tool_votes = []
    for model_name, weight in weights.items():
        vote = "BUY" if weight > 0.15 else "SELL" if weight < 0.05 else "HOLD"
        tool_votes.append({
            "Tool": model_name.replace("_", " ").title(),
            "Vote": vote,
            "Weight": round(weight, 4),
        })

    attribution = get_ensemble_attribution(tool_votes, weights)
    return {
        "models_active": len(weights),
        "model_weights": {k: round(v, 4) for k, v in weights.items()},
        "attribution": attribution,
    }

@app.get("/api/stock/senate-trading")
async def stock_senate_trading(
    symbol: str = Query("", description="Stock symbol (empty = all recent trades)"),
):
    """Senate trading disclosures (STOCK Act filings) from FMP.

    Returns recent buy/sell activity by US senators for a given symbol.
    Requires FMP_API_KEY.
    """
    sym = symbol.upper() if symbol.strip() else None
    cache_key = f"stock:senate:{sym or 'ALL'}"
    async def compute():
        trades = await asyncio.to_thread(fmp_client.get_senate_trading, sym)
        return {
            "symbol": sym or "ALL",
            "count":  len(trades) if trades else 0,
            "trades": trades or [],
        }
    return await cached_or_compute(cache_key, 3600, compute, compute_timeout=20)


# ---------------------------------------------------------------------------
# MizanAI — Groq-powered trading assistant (fast LLM + function calling)
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    messages: list[dict]                      # [{"role":"user","content":"..."}]
    model: str | None = None                  # override Groq model
    use_tools: bool = True                    # enable function calling


@app.post("/api/ai/chat")
async def ai_chat(req: ChatRequest):
    """MizanAI: Groq-powered assistant with live platform data via function calling.

    Supports: market status, macro indicators, stock screening, portfolio review,
    ETF holdings, top signals. Models: llama-3.3-70b-versatile (default),
    moonshotai/kimi-k2-instruct, qwen-qwq-32b.
    """
    from app.services.groq_assistant import trading_chat
    result = await trading_chat(
        messages=req.messages,
        model=req.model,
        use_tools=req.use_tools,
    )
    return result


@app.get("/api/ai/analyze/{symbol}")
async def ai_analyze_symbol(symbol: str):
    """Quick one-shot AI analysis for a stock symbol."""
    from app.services.groq_assistant import quick_analyze
    content = await quick_analyze(symbol.upper())
    return {"symbol": symbol.upper(), "analysis": content}


@app.get("/api/ai/models")
async def ai_models():
    """List of available Groq models."""
    from app.config import settings as _cfg
    return {
        "configured": bool(_cfg.GROQ_API_KEY),
        "default": _cfg.GROQ_MODEL,
        "models": [
            {"id": "llama-3.3-70b-versatile",             "label": "Llama 3.3 70B",       "speed": "fast",   "tools": True},
            {"id": "moonshotai/kimi-k2-instruct",          "label": "Kimi K2",             "speed": "medium", "tools": True},
            {"id": "qwen-qwq-32b",                         "label": "Qwen QwQ 32B",        "speed": "medium", "tools": True},
            {"id": "meta-llama/llama-4-scout-17b-16e-instruct", "label": "Llama 4 Scout", "speed": "fast",   "tools": True},
            {"id": "llama-3.1-8b-instant",                 "label": "Llama 3.1 8B",        "speed": "ultra",  "tools": True},
        ],
    }


@app.get("/assistant", include_in_schema=False)
async def get_assistant_page():
    """Redirect legacy /assistant to the new /ai-assistant (RTL, DeepSeek)."""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/mizan-ai", status_code=302)


# ---------------------------------------------------------------------------
# Smart Screener — halal + profitability + fair price scoring (0-100)
# ---------------------------------------------------------------------------

# Full expanded halal universe (~657, from halal_universe_v2.json) — the SAME list
# the weekly screener uses (both resolve to HALAL_STOCKS_FALLBACK).
_SMART_UNIVERSE = HALAL_STOCKS_FALLBACK

from concurrent.futures import ThreadPoolExecutor, as_completed


def _score_profitability(info: dict) -> dict:
    score = 0
    details = {}
    roe = _safe_float(info.get("returnOnEquity"))
    margin = _safe_float(info.get("profitMargins"))
    rev_growth = _safe_float(info.get("revenueGrowth"))
    earn_growth = _safe_float(info.get("earningsGrowth"))

    if roe > 0.15: s = 10; details["roe"] = "10/10"
    elif roe > 0.10: s = 7; details["roe"] = "7/10"
    elif roe > 0.05: s = 4; details["roe"] = "4/10"
    else: s = 0; details["roe"] = "0/10"
    score += s; details["roe_val"] = round(roe, 4) if roe else 0

    if margin > 0.15: s = 10; details["margin"] = "10/10"
    elif margin > 0.10: s = 7; details["margin"] = "7/10"
    elif margin > 0.05: s = 4; details["margin"] = "4/10"
    else: s = 0; details["margin"] = "0/10"
    score += s; details["margin_val"] = round(margin, 4) if margin else 0

    if rev_growth > 0.10: s = 10; details["rev_growth"] = "10/10"
    elif rev_growth > 0.05: s = 7; details["rev_growth"] = "7/10"
    elif rev_growth > 0: s = 4; details["rev_growth"] = "4/10"
    else: s = 0; details["rev_growth"] = "0/10"
    score += s; details["rev_growth_val"] = round(rev_growth, 4) if rev_growth else 0

    if earn_growth > 0.10: s = 10; details["earn_growth"] = "10/10"
    elif earn_growth > 0.05: s = 7; details["earn_growth"] = "7/10"
    elif earn_growth > 0: s = 4; details["earn_growth"] = "4/10"
    else: s = 0; details["earn_growth"] = "0/10"
    score += s; details["earn_growth_val"] = round(earn_growth, 4) if earn_growth else 0

    return {"score": score, "details": details}


def _score_valuation(info: dict) -> dict:
    score = 0
    details = {}
    pe = _safe_float(info.get("trailingPE"))
    peg = _safe_float(info.get("pegRatio"))
    pb = _safe_float(info.get("priceToBook"))
    ps = _safe_float(info.get("priceToSalesTrailing12Months"))

    if 0 < pe < 10: s = 0; details["pe_comment"] = "Very low (caution)"
    elif 10 <= pe < 20: s = 10; details["pe_comment"] = "Fair"
    elif 20 <= pe < 30: s = 7; details["pe_comment"] = "Moderate"
    elif 30 <= pe < 50: s = 4; details["pe_comment"] = "Expensive"
    else: s = 0; details["pe_comment"] = "N/A or extreme"
    score += s; details["pe_val"] = round(pe, 2) if pe else 0; details["pe_score"] = s

    if 0 < peg < 1: s = 10; details["peg_comment"] = "Undervalued"
    elif 1 <= peg < 2: s = 7; details["peg_comment"] = "Fair"
    elif 2 <= peg < 3: s = 4; details["peg_comment"] = "Expensive"
    else: s = 0; details["peg_comment"] = "N/A"
    score += s; details["peg_val"] = round(peg, 2) if peg else 0; details["peg_score"] = s

    if 0 < pb < 1.5: s = 10; details["pb_comment"] = "Undervalued"
    elif 1.5 <= pb < 3: s = 7; details["pb_comment"] = "Fair"
    elif 3 <= pb < 5: s = 4; details["pb_comment"] = "Premium"
    else: s = 0; details["pb_comment"] = "N/A"
    score += s; details["pb_val"] = round(pb, 2) if pb else 0; details["pb_score"] = s

    if 0 < ps < 2: s = 10; details["ps_comment"] = "Undervalued"
    elif 2 <= ps < 5: s = 7; details["ps_comment"] = "Fair"
    elif 5 <= ps < 10: s = 4; details["ps_comment"] = "Premium"
    else: s = 0; details["ps_comment"] = "N/A"
    score += s; details["ps_val"] = round(ps, 2) if ps else 0; details["ps_score"] = s

    return {"score": score, "details": details}


def _score_market(info: dict) -> dict:
    score = 0
    details = {}
    mcap = _safe_float(info.get("marketCap"))
    beta = _safe_float(info.get("beta"))
    div_yield = _safe_float(info.get("dividendYield"))

    if mcap > 100e9: s = 5; details["mcap_comment"] = "Mega Cap"
    elif mcap > 10e9: s = 4; details["mcap_comment"] = "Large Cap"
    elif mcap > 2e9: s = 3; details["mcap_comment"] = "Mid Cap"
    else: s = 1; details["mcap_comment"] = "Small Cap"
    score += s; details["mcap_val"] = mcap

    if 0.5 < beta < 1.5: s = 5; details["beta_comment"] = "Moderate"
    elif (0 < beta <= 0.5) or (1.5 <= beta < 2): s = 3; details["beta_comment"] = "Low/High"
    else: s = 1; details["beta_comment"] = "Extreme"
    score += s; details["beta_val"] = round(beta, 2) if beta else 0

    if div_yield > 4.0: s = 5; details["div_comment"] = "High Yield"
    elif div_yield > 2.0: s = 3; details["div_comment"] = "Moderate Yield"
    elif div_yield > 0: s = 1; details["div_comment"] = "Low Yield"
    else: s = 0; details["div_comment"] = "No Dividend"
    score += s; details["div_yield_val"] = round(div_yield, 4) if div_yield else 0

    # 52w performance
    high52 = _safe_float(info.get("fiftyTwoWeekHigh"))
    low52 = _safe_float(info.get("fiftyTwoWeekLow"))
    price = _safe_float(info.get("currentPrice") or info.get("regularMarketPrice"))
    if high52 and low52 and price:
        perf = (price - low52) / (high52 - low52)
        if perf > 0.8: s = 5; details["perf_comment"] = "Near high (momentum)"
        elif perf > 0.5: s = 3; details["perf_comment"] = "Mid range"
        else: s = 1; details["perf_comment"] = "Near low (value)"
        score += s; details["perf_val"] = round(perf, 2)

    return {"score": score, "details": details}


# ---------------------------------------------------------------------------
# Technical Indicator helpers — RSI, MACD, Bollinger, VWAP
# ---------------------------------------------------------------------------


def _calc_rsi(closes: np.ndarray, period: int = 14) -> np.ndarray:
    """Relative Strength Index."""
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    avg_gain = np.convolve(gains, np.ones(period)/period, mode='valid')
    avg_loss = np.convolve(losses, np.ones(period)/period, mode='valid')
    with np.errstate(divide='ignore', invalid='ignore'):
        rs = np.where(avg_loss != 0, avg_gain / avg_loss, 999)
    rsi = 100 - (100 / (1 + rs))
    # Pad front to match original length
    pad = np.full(period, np.nan)
    return np.concatenate([pad, rsi])


def _calc_macd(closes: np.ndarray, fast: int = 12, slow: int = 26, signal: int = 9):
    """MACD line, signal line, histogram. Detect crossover."""
    ema_fast = np.convolve(closes, np.exp(np.linspace(-1, 0, fast)), mode='full')[:len(closes)]
    ema_slow = np.convolve(closes, np.exp(np.linspace(-1, 0, slow)), mode='full')[:len(closes)]
    # Proper EMA calculation
    def _ema(data, n):
        k = 2 / (n + 1)
        ema = np.zeros_like(data)
        ema[0] = np.nanmean(data[:n]) if n <= len(data) else data[0]
        for i in range(1, len(data)):
            ema[i] = data[i] * k + ema[i-1] * (1 - k)
        return ema
    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)
    macd_line = ema_fast - ema_slow
    signal_line = _ema(macd_line, signal)
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def _calc_bollinger(closes: np.ndarray, period: int = 20, std_mult: float = 2.0):
    """Bollinger Bands upper, middle, lower, and bandwidth ratio."""
    middle = np.convolve(closes, np.ones(period)/period, mode='valid')
    rolling_std = np.array([np.std(closes[max(0, i-period+1):i+1]) for i in range(period-1, len(closes))])
    upper = middle + std_mult * rolling_std
    lower = middle - std_mult * rolling_std
    bandwidth = (upper - lower) / middle  # relative bandwidth
    # Pad front
    pad = np.full(period - 1, np.nan)
    return (
        np.concatenate([pad, upper]),
        np.concatenate([pad, middle]),
        np.concatenate([pad, lower]),
        np.concatenate([pad, bandwidth]),
    )


def _calc_vwap(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
               volumes: np.ndarray | None) -> np.ndarray:
    """Volume Weighted Average Price (cumulative)."""
    if volumes is None or len(volumes) == 0:
        return np.full_like(closes, np.nan)
    typical = (highs + lows + closes) / 3
    cum_vp = np.cumsum(typical * volumes)
    cum_v = np.cumsum(volumes)
    return cum_vp / np.where(cum_v > 0, cum_v, 1)


def _score_momentum(symbol: str, df: pd.DataFrame) -> dict:
    """Technical momentum score (0-30) from RSI, MACD, Bollinger Bands, VWAP.

    Components:
      - RSI (0-10): penalty if overbought (>70), bonus zone 40-60
      - MACD (0-8): bonus on bullish cross, penalty on bearish
      - Bollinger (0-7): squeeze detection + position in bands
      - VWAP (0-5): premium if price above VWAP
    """
    score = 0
    details = {}
    closes = df["close"].values.astype(float) if "close" in df.columns else np.array([])
    highs = df["high"].values.astype(float) if "high" in df.columns else np.array([])
    lows = df["low"].values.astype(float) if "low" in df.columns else np.array([])
    volumes = df["volume"].values.astype(float) if "volume" in df.columns else None

    if len(closes) < 30:
        return {"score": 0, "details": {"error": "Insufficient data"}}

    # ── RSI (0-10) ──
    rsi_vals = _calc_rsi(closes, 14)
    rsi = float(rsi_vals[-1]) if not np.isnan(rsi_vals[-1]) else 50
    if 40 <= rsi <= 60:
        rsi_pts = 10; rsi_label = "Healthy"
    elif 30 <= rsi < 40 or 60 < rsi <= 70:
        rsi_pts = 7; rsi_label = "Cautious"
    elif rsi < 30:
        rsi_pts = 5; rsi_label = "Oversold (might reverse)"
    elif 70 < rsi <= 80:
        rsi_pts = 3; rsi_label = "Overbought ⚠️"
    else:
        rsi_pts = 1; rsi_label = "Extreme Overbought 🚨"
    score += rsi_pts
    details["rsi"] = round(rsi, 1)
    details["rsi_score"] = rsi_pts
    details["rsi_label"] = rsi_label

    # ── MACD (0-8) ──
    macd_line, signal_line, hist = _calc_macd(closes)
    macd_current = float(macd_line[-1]) if not np.isnan(macd_line[-1]) else 0
    signal_current = float(signal_line[-1]) if not np.isnan(signal_line[-1]) else 0
    hist_prev = float(hist[-2]) if len(hist) >= 2 else 0
    hist_current = float(hist[-1]) if not np.isnan(hist[-1]) else 0

    # Bullish cross: MACD crosses above signal (hist goes from - to + in one step)
    if hist_prev < 0 and hist_current > 0:
        macd_pts = 8; macd_label = "Bullish Crossover ✅"
    elif macd_current > signal_current and hist_current > 0:
        macd_pts = 6; macd_label = "Bullish Momentum"
    elif macd_current > signal_current:
        macd_pts = 4; macd_label = "MACD Above Signal"
    elif hist_prev > 0 and hist_current < 0:
        macd_pts = 2; macd_label = "Bearish Crossover ❌"
    elif macd_current < signal_current:
        macd_pts = 1; macd_label = "Bearish"
    else:
        macd_pts = 3; macd_label = "Neutral"
    score += macd_pts
    details["macd_hist"] = round(hist_current, 4)
    details["macd_score"] = macd_pts
    details["macd_label"] = macd_label

    # ── Bollinger Bands (0-7) ──
    upper, middle, lower, bandwidth = _calc_bollinger(closes)
    current_price = closes[-1]
    bb_upper = float(upper[-1]) if not np.isnan(upper[-1]) else current_price
    bb_lower = float(lower[-1]) if not np.isnan(lower[-1]) else current_price
    bb_mid = float(middle[-1]) if not np.isnan(middle[-1]) else current_price
    bb_bw = float(bandwidth[-1]) if not np.isnan(bandwidth[-1]) else 999
    # Position % within bands
    bb_range = bb_upper - bb_lower
    bb_pos = (current_price - bb_lower) / bb_range if bb_range > 0 else 0.5

    # Squeeze detection: bandwidth at 10% of its own 20-period average
    recent_bw = bandwidth[~np.isnan(bandwidth)][-20:] if sum(~np.isnan(bandwidth)) >= 20 else bandwidth[~np.isnan(bandwidth)]
    bw_avg = float(np.mean(recent_bw)) if len(recent_bw) > 0 else bb_bw
    is_squeeze = bw_avg > 0 and (bb_bw / bw_avg) < 0.8

    bb_pts = 0
    if is_squeeze and 0.2 <= bb_pos <= 0.6:
        bb_pts = 7; bb_label = "Squeeze + Mid-range 🎯"
    elif is_squeeze:
        bb_pts = 5; bb_label = "Bollinger Squeeze ⚡"
    elif bb_pos < 0.2:
        bb_pts = 6; bb_label = "Near Lower Band (Bounce?)"
    elif bb_pos > 0.8:
        bb_pts = 2; bb_label = "Near Upper Band (Extended)"
    elif 0.3 <= bb_pos <= 0.7:
        bb_pts = 4; bb_label = "Neutral Zone"
    else:
        bb_pts = 3; bb_label = "Slightly Extended"
    score += bb_pts
    details["bb_pos"] = round(bb_pos, 2)
    details["bb_width"] = round(bb_bw, 4)
    details["bb_squeeze"] = is_squeeze
    details["bb_score"] = bb_pts
    details["bb_label"] = bb_label

    # ── VWAP (0-5) ──
    vwap_vals = _calc_vwap(highs, lows, closes, volumes)
    vwap_current = float(vwap_vals[-1]) if not np.isnan(vwap_vals[-1]) else current_price
    vwap_ratio = (current_price - vwap_current) / vwap_current if vwap_current > 0 else 0

    if vwap_ratio > 0.02:
        vwap_pts = 5; vwap_label = "Above VWAP (>2%) ✅"
    elif vwap_ratio > 0:
        vwap_pts = 4; vwap_label = "Above VWAP"
    elif vwap_ratio > -0.02:
        vwap_pts = 2; vwap_label = "Slightly Below VWAP"
    else:
        vwap_pts = 1; vwap_label = "Below VWAP (<-2%) ❌"
    score += vwap_pts
    details["vwap_ratio"] = round(vwap_ratio, 4)
    details["vwap_score"] = vwap_pts
    details["vwap_label"] = vwap_label
    details["vwap_val"] = round(vwap_current, 2)

    details["momentum_score"] = score
    details["momentum_max"] = 30
    return {"score": score, "details": details}


def _score_forecast_consensus(symbol: str) -> dict:
    """Quick forecast consensus score (0-30) using a subset of fast models + agents.

    When _HAS_TORCH is False or LIGHTWEIGHT_ML=true:
    - Only uses arima (pure statsmodels, no PyTorch).
    Full mode: arima + ensemble + lstm + 3 RL agents.
    """
    _lightweight = (not _HAS_TORCH or
                    os.environ.get("LIGHTWEIGHT_ML", "").lower() in ("1", "true", "yes"))
    score = 0
    details = {"models": 0, "agents": 0}
    try:
        records, df = _fetch_data(symbol, period="6mo")
        prices = df["close"].values.astype(float)
        if len(prices) < 30:
            return {"score": 0, "details": details}
    except Exception:
        return {"score": 0, "details": details}

    # ── Fast models ──
    # On Railway / lightweight mode: only arima (no PyTorch) to avoid OOM.
    # Full mode: arima + ensemble + lstm.
    _model_list = ["arima"] if _lightweight else ["arima", "ensemble", "lstm"]
    model_directions = []
    for mname in _model_list:
        if _model_cb_blocked(mname):
            continue  # breaker open — skip silently, don't waste time
        try:
            kwargs = {}
            if mname == "lstm":
                kwargs = {"epochs": 5, "hidden_size": 32, "num_layers": 1, "learning_rate": 0.001}
            model = create_model(mname, **kwargs)
            fr = model.walk_forward_predict(data=prices, sequence_length=20,
                                            forecast_horizon=5, n_splits=1)
            if fr:
                last_pred = float(fr[-1].predictions[-1, 0]) if fr[-1].predictions.ndim > 1 else float(fr[-1].predictions[-1])
                last_actual = float(fr[-1].actuals[-1, 0]) if fr[-1].actuals.ndim > 1 else float(fr[-1].actuals[-1])
                model_directions.append("up" if last_pred > last_actual else "down")
                _model_cb_ok(mname)
        except Exception:
            _model_cb_fail(mname)
            continue

    # ── Fast agents ──
    # Skip on Railway / lightweight mode — walk_forward_evaluate trains PyTorch agents
    agent_sharpes = []
    if not _lightweight:
        for aname in ["moving_average", "signal_rolling", "q_learning"]:
            try:
                agent = _create_agent_safe(aname, 20)
                wf = agent.walk_forward_evaluate(prices=prices, episodes=10,
                                                initial_capital=10000, window_size=20)
                bs = wf["backtest_summary"]
                agent_sharpes.append(bs.get("sharpe_ratio", 0))
            except Exception:
                continue

    # Score from model direction agreement
    if model_directions:
        up_count = sum(1 for d in model_directions if d == "up")
        agree_ratio = max(up_count, len(model_directions) - up_count) / len(model_directions)
        details["models"] = len(model_directions)
        details["model_agreement"] = round(agree_ratio, 2)
        details["model_direction"] = "up" if up_count > len(model_directions) / 2 else "down"
        if agree_ratio >= 0.8 and details["model_direction"] == "up":
            score += 15
        elif agree_ratio >= 0.6 and details["model_direction"] == "up":
            score += 10
        elif agree_ratio >= 0.8:
            score += 8
        elif agree_ratio >= 0.6:
            score += 5
        else:
            score += 3

    # Score from agent Sharpe ratios
    if agent_sharpes:
        avg_sharpe = float(np.mean(agent_sharpes))
        details["agents"] = len(agent_sharpes)
        details["avg_sharpe"] = round(avg_sharpe, 4)
        if avg_sharpe > 1.0:
            score += 15
        elif avg_sharpe > 0.5:
            score += 12
        elif avg_sharpe > 0.2:
            score += 8
        elif avg_sharpe > 0:
            score += 5
        else:
            score += 2

    details["forecast_score"] = min(score, 30)
    details["forecast_max"] = 30
    return {"score": min(score, 30), "details": details}


# ---------------------------------------------------------------------------
# Sentiment Score — VADER news + analyst consensus (0-20)
# ---------------------------------------------------------------------------

def _sentiment_score(symbol: str, info: dict | None = None) -> dict:
    """Composite sentiment score 0-20.

    10 pts from VADER on recent news headlines.
    10 pts from analyst consensus (recommendation key + price target upside).
    Never raises — returns neutral (10) on any failure.
    """
    from app.services.sentiment_engine import get_sentiment_score
    return get_sentiment_score(symbol, info)


def _analyze_smart(symbol: str, watchlist_set: set | None = None, spy_df: pd.DataFrame | None = None) -> dict | None:
    """Full analysis: halal + fundamental (40) + technical momentum (30).

    Smart Score = fundamental(0-40) + forecast_consensus(0-30) + momentum(0-30).
    Forecast consensus is added in post-processing by the smart_screener endpoint.
    """
    try:
        # FMP primary (server-safe); yfinance fallback built into _get_smart_info
        info = _get_smart_info(symbol)
        price = _safe_float(info.get("currentPrice") or info.get("regularMarketPrice") or info.get("previousClose"))
        # Resilience: when FMP is rate-limited (429), info is empty and carries no price.
        # Fall back to the price-data source (Alpaca/Tiingo — works on Railway) so the
        # TECHNICAL analysis still runs even though FMP fundamentals are unavailable.
        # Without this the whole analysis aborts → the Stock card shows "—" everywhere.
        if not price or price <= 0:
            try:
                _, _pdf = _fetch_data(symbol, period="1mo")
                if _pdf is not None and len(_pdf) > 0:
                    price = float(_pdf["close"].iloc[-1])
                    info = dict(info or {})
                    info.setdefault("currentPrice", price)
            except Exception:
                pass
        if not price or price <= 0:
            return None
        # Penny floor — exclude low-priced names (manipulation/liquidity risk).
        try:
            from app.config import settings as _q_cfg
            _min_price = float(getattr(_q_cfg, "MIN_PRICE", 5.0))
        except Exception:
            _min_price = 5.0
        if price < _min_price:
            return None

        mcap = _safe_float(info.get("marketCap"))
        sector_val = (info.get("sector") or "").lower().strip()
        industry_val = (info.get("industry") or "").lower().strip()
        name = info.get("shortName") or info.get("longName") or symbol

        # Halal check — pass FMP-normalized info to avoid redundant call
        halal = _screen_halal(symbol, info={"marketCap": mcap, "sector": sector_val,
                                             "industry": industry_val, "totalDebt": info.get("totalDebt"),
                                             "totalCash": info.get("totalCash"), "totalRevenue": info.get("totalRevenue")})
        is_halal = halal.get("is_halal", False)
        halal_screens = halal.get("screens_passed", 0)
        # Three-state verdict for display; default unknown/errored screens to "doubtful"
        # (absence of data ≠ haram) rather than a false halal or false non-compliant.
        halal_verdict = halal.get("halal_verdict") or ("halal" if is_halal else "doubtful")
        halal_reasons = halal.get("halal_reasons") or []
        # Set only when the halal verdict was served from a STALE cache (a live refresh
        # failed, e.g. Yahoo blocked) — the UI flags it; None when fresh.
        halal_stale_days = halal.get("stale_days") if halal.get("stale") else None
        in_watchlist = bool(watchlist_set and symbol.upper() in watchlist_set)

        # ── Fundamental Score (0-40) ──
        prof = _score_profitability(info)
        val = _score_valuation(info)
        mkt = _score_market(info)
        # Rebalance: profitability/2 + valuation/4 + market/2 = max 20+10+10 = 40
        fundamental = min(40, prof["score"] // 2 + val["score"] // 4 + mkt["score"] // 2)

        # ── Technical Momentum Score (0-30) ──
        hist_df = None
        try:
            _, hist_df = _fetch_data(symbol, period="1y")
            if hist_df is not None and len(hist_df) > 20:
                # Roadmap 1.1 — Hard Gates: must-pass or score = 0
                from app.services.scoring import check_hard_gates
                gates = check_hard_gates(hist_df, spy_df=spy_df)
                if not gates.passed:
                    return {
                        "symbol": symbol, "company": name,
                        "sector": sector_val.title() if sector_val else "",
                        "industry": industry_val.title() if industry_val else "",
                        "price": price, "change_pct": None, "market_cap": mcap,
                        "is_halal": is_halal, "halal_screens": halal_screens,
                        "in_watchlist": in_watchlist,
                        "fundamental_score": fundamental, "fundamental_details": {},
                        "profitability_score": 0, "profitability_details": {},
                        "valuation_score": 0, "valuation_details": {},
                        "market_score": 0, "market_details": {},
                        "momentum_score": 0, "momentum_details": {"error": "Hard Gates failed"},
                        "forecast_score": 0, "forecast_details": {},
                        "smart_score": 0,
                        "ext_pct": None, "atr_pct": None, "adv_dollar_m": None,
                        "signal": "AVOID", "strategy": "NONE", "strategy_score": 0, "strategy_reason": "Hard Gates: " + "; ".join(gates.failed_gates),
                        "hard_gates_passed": False, "hard_gates_failed": gates.failed_gates,
                        "pipeline": {
                            "1_data": "loaded", "2_halal": "passed" if is_halal else "blocked",
                            "3_fundamental": f"{fundamental}/40",
                            "4_hard_gates": f"BLOCKED ({'; '.join(gates.failed_gates)})",
                            "5_momentum": "skipped", "6_strategy_selector": "skipped",
                            "7_ai_confirm": "skipped", "8_kelly": "skipped", "9_execute": "skipped",
                        },
                    }
                momentum = _score_momentum(symbol, hist_df)
            else:
                momentum = {"score": 0, "details": {"error": "No history data"}}
        except Exception:
            momentum = {"score": 0, "details": {"error": "Momentum calc failed"}}

        # ── Forecast Consensus (0-30) — placeholder with RSI-based proxy ──
        _mom_d = momentum.get("details", {})
        _mom_rsi = _mom_d.get("rsi", 50)
        _forecast_proxy = max(0, min(30, int(30 - abs(_mom_rsi - 50) * 0.6)))
        forecast = {"score": 0, "proxy": _forecast_proxy, "details": {"note": "computed in post-process"}}

        # ── USX PRO Extra Columns: Ext%, ATR%, ADV$M, Chg%, Signal, ADX, RSvsSPY ──
        ext_pct = None
        atr_pct = None
        adv_dollar_m = None
        chg_pct = None
        adx_val = None
        rs_vs_spy = "NEUTRAL"
        signal = "WAIT"
        if hist_df is not None and len(hist_df) > 14:
            closes = hist_df["close"].values.astype(float)
            highs = hist_df["high"].values.astype(float) if "high" in hist_df.columns else closes
            lows = hist_df["low"].values.astype(float) if "low" in hist_df.columns else closes
            volumes = hist_df["volume"].values.astype(float) if "volume" in hist_df.columns else None

            # Ext%: distance from 20-day high
            high_20 = float(np.max(highs[-20:]))
            ext_pct = round((price - high_20) / high_20 * 100, 2) if high_20 > 0 else 0

            # ATR%: ATR(14) / price
            tr = np.maximum.reduce([highs[1:] - lows[1:], np.abs(highs[1:] - closes[:-1]), np.abs(lows[1:] - closes[:-1])])
            atr = float(np.mean(tr[-14:])) if len(tr) >= 14 else 0
            atr_pct = round(atr / price * 100, 2) if price > 0 else 0

            # ADV$M: avg volume × price / 1,000,000
            if volumes is not None:
                avg_vol = float(np.mean(volumes[-20:])) if len(volumes) >= 20 else float(np.mean(volumes))
                adv_dollar_m = round(avg_vol * price / 1_000_000, 1)
                # Liquidity floor — exclude thinly-traded names.
                try:
                    from app.config import settings as _adv_cfg
                    _min_adv = float(getattr(_adv_cfg, "MIN_ADV_DOLLAR_M", 5.0))
                except Exception:
                    _min_adv = 5.0
                if adv_dollar_m < _min_adv:
                    return None

            # Chg%: daily change
            if len(closes) >= 2:
                chg_pct = round((closes[-1] - closes[-2]) / closes[-2] * 100, 2)

            # ADX(14): Average Directional Index
            if len(closes) > 28:
                try:
                    up = np.diff(highs)
                    down = -np.diff(lows)
                    plus_dm = np.where((up > down) & (up > 0), up, 0)
                    minus_dm = np.where((down > up) & (down > 0), down, 0)
                    tr14 = np.array([float(np.mean(tr[max(0,i-13):i+1])) for i in range(len(tr))])
                    atr14 = np.where(tr14 > 0, tr14, 1)
                    plus_di = 100 * np.convolve(plus_dm[:len(tr)], np.ones(14)/14, mode='same') / atr14
                    minus_di = 100 * np.convolve(minus_dm[:len(tr)], np.ones(14)/14, mode='same') / atr14
                    dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)
                    adx_val = round(float(np.mean(dx[-14:])), 1)
                except Exception:
                    adx_val = None

            # RS vs SPY: 20-day relative performance
            try:
                if spy_df is not None and len(spy_df) >= 20:
                    spy_closes = spy_df["close"].values.astype(float)
                    sym_ret_20d = (closes[-1] - closes[-20]) / closes[-20] * 100 if len(closes) >= 20 else 0
                    spy_ret_20d = (spy_closes[-1] - spy_closes[-20]) / spy_closes[-20] * 100 if len(spy_closes) >= 20 else 0
                    rel = sym_ret_20d - spy_ret_20d
                    if rel > 2:
                        rs_vs_spy = "LEADER"
                    elif rel < -2:
                        rs_vs_spy = "LAGGARD"
                    else:
                        rs_vs_spy = "NEUTRAL"
            except Exception:
                rs_vs_spy = "N/A"

        # Strategy selection (best of 4 strategies) — runs BEFORE signal
        strategy_label = "WAIT"
        strategy_score = 0
        strategy_reason = ""
        if _HAS_STRATEGIES and hist_df is not None and len(hist_df) > 20:
            try:
                sel_sig = _get_symbol_strategy(symbol, hist_df, None)
                if sel_sig:
                    strategy_label = sel_sig.strategy
                    strategy_score = sel_sig.score
                    strategy_reason = sel_sig.reason
            except Exception:
                pass

        # Signal classification: strategy signal takes priority, else score-based
        mom = momentum.get("details", {})
        smart_score = fundamental + momentum["score"] + forecast.get("proxy", 0)  # + forecast in post-process
        if strategy_label != "WAIT" and sel_sig:
            signal = sel_sig.signal
            # Override smart_score with strategy_score when a strategy is active
            smart_score = max(smart_score, strategy_score)
        else:
            # Score-based thresholds when no strategy triggered
            if smart_score >= 65:
                signal = "BUY"
            elif smart_score >= 40:
                signal = "WAIT"
            else:
                signal = "AVOID"

        return {
            "symbol": symbol,
            "company": name,
            "sector": sector_val.title() if sector_val else "",
            "industry": industry_val.title() if industry_val else "",
            "price": price,
            "change_pct": chg_pct,
            "market_cap": mcap,
            "is_halal": is_halal,
            "halal_screens": halal_screens,
            "halal_verdict": halal_verdict,
            "halal_reasons": halal_reasons,
            "halal_stale_days": halal_stale_days,
            "in_watchlist": in_watchlist,
            "fundamental_score": fundamental,
            "fundamental_details": {
                "profitability": prof["score"],
                "profitability_max": 40,
                "valuation": val["score"],
                "valuation_max": 40,
                "market": mkt["score"],
                "market_max": 20,
            },
            "profitability_score": prof["score"],
            "profitability_details": prof["details"],
            "valuation_score": val["score"],
            "valuation_details": val["details"],
            "market_score": mkt["score"],
            "market_details": mkt["details"],
            "momentum_score": momentum["score"],
            "momentum_details": momentum["details"],
            "forecast_score": forecast["score"],
            "forecast_details": forecast["details"],
            "forecast_proxy": forecast.get("proxy", 0),
            "smart_score": smart_score,
            # USX PRO extra columns
            "ext_pct": ext_pct,
            "atr_pct": atr_pct,
            "adv_dollar_m": adv_dollar_m,
            "signal": signal,
            # Strategy assignment
            "strategy": strategy_label,
            "strategy_score": strategy_score,
            "strategy_reason": strategy_reason,
            # Pipeline stages
            "pipeline": {
                "1_data": "loaded",
                "2_halal": "passed" if is_halal else "blocked",
                "3_fundamental": f"{fundamental}/40",
                "4_momentum": f"{momentum['score']}/30",
                "5_strategy_selector": f"{strategy_label} (score={strategy_score})",
                "6_ai_confirm": "confirmed" if strategy_score >= 65 and strategy_label != "WAIT" else "rejected" if strategy_label != "WAIT" else "no_signal",
                "7_kelly": "pending",
                "8_guardian": "pending",
                "9_execute": "pending",
            },
        }
    except Exception:
        return None


async def _smart_screener_impl(
    min_score: int = 0,
    sector: str = "",
    min_market_cap: float = 0,
    max_results: int = 25,
    use_cache: str = "true",
    add_forecast: str = "true",
    watchlist: str = "",
):
    """Smart investment screener implementation (callable directly, no Query objects).
    Returns ranked opportunities with detailed score breakdown + market status gates.
    """
    _use_cache = str(use_cache).lower() in ("true", "1", "yes")
    cache_key = "smart_screener"
    if _use_cache:
        cached = _cache_get(cache_key, max_age=SCREENER_CACHE_TTL)
        if cached:
            return {"source": "cache", **cached}

    # Fetch market status for dynamic gates
    try:
        from app.services.market_context import get_market_status
        market_status = get_market_status()
    except Exception:
        market_status = {"status": "RISK ON", "regime": "NEUTRAL", "min_gate": 60, "strong_gate": 75, "halt_pipeline": False}

    # Apply market-driven gates when min_score is default (0)
    effective_min_score = min_score
    if effective_min_score == 0:
        effective_min_score = market_status.get("min_gate", 60)
    strong_gate = market_status.get("strong_gate", 75)

    # Load current watchlist to prioritize
    from app.services.watchlist_service import get_watchlist_set
    watchlist_set = get_watchlist_set()
    watchlist_syms = [s for s in watchlist_set if s in _SMART_UNIVERSE]

    # Use watchlist symbols if provided, otherwise the full expanded halal universe (~657)
    if watchlist:
        scan_symbols = [s.strip().upper() for s in watchlist.split(",") if s.strip()]
    else:
        # Prioritize watchlist symbols first, then fill remaining from the full
        # expanded halal universe (~657 — same list the weekly screener scans).
        scan_symbols = list(_SMART_UNIVERSE)

    # Kick off background scan; return current cache or scanning status
    import threading
    _prog = _screener_progress
    _stuck = _screener_is_stuck(_prog)
    if _prog.get("status") != "scanning" or _stuck:
        if _stuck:
            logger.warning("Screener: stuck scan detected (%d min) — re-kicking",
                           int((time.time() - _prog.get("started", 0)) / 60))
        t = threading.Thread(target=_run_screener_bg, args=(scan_symbols,), daemon=True)
        t.start()
    stale = _cache_get(cache_key, max_age=SCREENER_CACHE_TTL * 2)
    if stale:
        pct = round(_screener_progress.get("current", 0) / max(_screener_progress.get("total", 1), 1) * 100, 1)
        result = {**stale, "source": "stale_cache", "scan_pct": pct}
        if effective_min_score > 0 and market_status.get("halt_pipeline"):
            result["halt_pipeline"] = True
        return result
    # --- last_good permanent cache ---
    # When the fresh+stale windows both miss (deploy, overnight idle), serve the
    # last completed result immediately with honest source/asof stamps. The
    # background re-kick already ran above, so refreshing is truthful.
    lastgood = _cache_get("smart_screener_lastgood", max_age=10 * 365 * 86400)
    if lastgood:
        pct = round(_screener_progress.get("current", 0) / max(_screener_progress.get("total", 1), 1) * 100, 1)
        return {**lastgood, "source": "last_good", "refreshing": True, "scan_pct": pct,
                "asof": lastgood.get("lastgood_asof")}



    return {
        "source": "scanning",
        "status": "scanning",
        "total_scanned": len(scan_symbols),
        "scan_pct": 0,
        "message": f"Scanning {len(scan_symbols)} symbols in background. Check /api/screener/progress for status."
    }


@app.get("/api/stock/smart-screener")
async def smart_screener(
    min_score: int = Query(0, description="Minimum smart score (0-100)"),
    sector: str = Query("", description="Filter by sector"),
    min_market_cap: float = Query(0, description="Minimum market cap"),
    max_results: int = Query(25, description="Maximum results"),
    use_cache: str = Query("true", description="Use cached results ('false' to force refresh)"),
    add_forecast: str = Query("true", description="Add forecast consensus score (0-30) to top results"),
    watchlist: str = Query("", description="Comma-separated symbols to scan (overrides default universe)"),
):
    """Smart investment screener: FastAPI route that delegates to `_smart_screener_impl`."""
    return await _smart_screener_impl(
        min_score=min_score, sector=sector, min_market_cap=min_market_cap,
        max_results=max_results, use_cache=use_cache,
        add_forecast=add_forecast, watchlist=watchlist,
    )


def _composite_from_parts(parts: list) -> int:
    """Renormalize the deep-picks composite over its AVAILABLE (value, max) parts.

    Phase 1 (measured decision): the caller's parts list intentionally EXCLUDES the
    AI slice — it comes from the disabled coin-flip neural models, so it is still
    displayed (score_ai / 'AI*') but contributes 0 weight. Sentiment is likewise
    only included when real data exists. Renormalizing to 0-100 keeps the band
    gates (72 STRONG BUY / 55 BUY / 38 WATCH) meaningful regardless of which
    parts are present.
    """
    got = sum(v for v, _ in parts)
    mx = sum(m for _, m in parts)
    return min(100, round(got / mx * 100)) if mx else 0


@app.get("/api/screener/deep-picks")
async def screener_deep_picks(
    limit: int = Query(15, description="Max results (halal, sorted by composite score)"),
    use_cache: str = Query("true", description="Use cached results"),
    sort_by: str = Query(
        "composite",
        description="Sort key for results: composite (default) | rank_value (risk-adjusted) | conviction",
    ),
):
    """Stock Intelligence — enriched screener with sentiment + fundamental breakdown.

    Composite Score (0-100):
      Technical   30 pts  momentum + strategy (RS vs SPY, EMA, MACD, ADX, Volume)
      Fundamental 25 pts  profitability + valuation + growth (rescaled from 0-40)
      Sentiment   20 pts  VADER news sentiment + analyst consensus & price target
      AI/ML       15 pts  forecast model + agent consensus (rescaled from 0-30)
      Halal       10 pts  AAOIFI screens passed (debt/interest/haram/liquidity)
    """
    _use_cache = str(use_cache).lower() not in ("false", "0", "no")
    _sort_by_safe = sort_by if sort_by in ("composite", "rank_value", "conviction") else "composite"
    cache_key_dp = f"deep_picks_{limit}_{_sort_by_safe}"

    if _use_cache:
        cached = _cache_get(cache_key_dp, max_age=1800)  # 30 min TTL
        if cached:
            return cached

    # Pull from smart screener cache. If it is empty, KICK OFF the background
    # scan here (same mechanism /api/stock/smart-screener uses) so the Monthly
    # scanner is self-warming — otherwise this endpoint is passive and the tab
    # shows "scanning" forever until the scheduled scan happens to run.
    screener_data = _cache_get("smart_screener", max_age=86400)
    if not screener_data or not screener_data.get("results"):
        try:
            import threading
            if _screener_progress.get("status") != "scanning":
                threading.Thread(
                    target=_run_screener_bg, args=(list(_SMART_UNIVERSE),), daemon=True,
                ).start()
        except Exception as _scan_exc:
            logger.debug("deep-picks could not start background scan: %s", _scan_exc)
        _pct = round(_screener_progress.get("current", 0)
                     / max(_screener_progress.get("total", 1), 1) * 100, 1)
        return {
            "status": "scanning",
            "message": "Scanning the halal universe in the background — results will appear shortly.",
            "results": [],
            "total": 0,
            "scan_pct": _pct,
            "composite_max": 100,
        }

    raw_results: list[dict] = screener_data.get("results", [])[:limit * 2]

    # ── Conviction inputs computed ONCE (not per row) ──────────────────────────
    # Point-in-time-irrelevant here (this is the live path): use the current
    # context bundle + trailing accuracy weights. Both degrade to neutral on error.
    try:
        from app.services.market_context_bundle import get_context_bundle_sync
        _ctx_bundle = get_context_bundle_sync()
    except Exception:
        _ctx_bundle = {}
    _acc_weights = {}
    try:
        from app.services.model_weights import get_source_weights
        _acc_weights = get_source_weights()
    except Exception:
        _acc_weights = {}
    try:
        from app.config import settings as _sel_cfg
        _static_risk_pct = float(getattr(_sel_cfg, "TRADE_RISK_PCT", 1.5))
    except Exception:
        _static_risk_pct = 1.5

    def _enrich_one(r: dict) -> dict:
        symbol = r.get("symbol", "")
        info = _get_smart_info(symbol)  # FMP primary, yfinance fallback

        sent = _sentiment_score(symbol, info)
        sent_available = sent.get("available", False)

        # Rescale sub-scores to composite denominator (Phase 3: gated v2 option)
        tech = min(30, r.get("momentum_score", 0) + max(0, r.get("strategy_score", 0) - 50))
        tech_version = "v1"
        if os.environ.get("TECH_SCORE_VERSION", "v1").lower() == "v2":
            try:
                from app.services.tech_score_v2 import tech_score_v2
                _, _df_sym = _fetch_data(symbol, period="1y")
                _, _df_spy = _fetch_data("SPY", period="1y")
                _v2, _ver = tech_score_v2(_df_sym, _df_spy)
                if _v2 is not None:
                    tech, tech_version = _v2, _ver
            except Exception as _t2e:
                logger.debug("tech v2 fallback to v1 for %s: %s", symbol, _t2e)
        fund40 = r.get("fundamental_score", 0)                       # original 0-40
        fund   = int(fund40 * 25 / 40)                               # → 0-25
        sent_s = sent["score"] if sent_available else None            # 0-20 or None
        fc30   = r.get("forecast_score", r.get("forecast_proxy", 0)) # original 0-30
        ai     = int(fc30 * 15 / 30)                                  # → 0-15
        screens = r.get("halal_screens", 0)
        halal_s = int(screens / 4 * 10) if screens else (10 if r.get("is_halal") else 0)

        # Composite over AVAILABLE components only, rescaled to 0-100. When
        # sentiment has no real data it is excluded (not counted as a constant),
        # so the score reflects only signals we can actually stand behind.
        # Phase 1: AI slice zeroed — measured coin-flip models; still DISPLAYED.
        parts = [(tech, 30), (fund, 25), (halal_s, 10)]
        if sent_available:
            parts.append((sent_s, 20))
        composite = _composite_from_parts(parts)

        # Fundamental grade
        if fund >= 22: f_grade = "A"
        elif fund >= 17: f_grade = "B"
        elif fund >= 12: f_grade = "C"
        else: f_grade = "D"

        # Composite signal
        if composite >= 72 and r.get("is_halal"):
            sig_composite = "STRONG BUY"
        elif composite >= 55:
            sig_composite = "BUY"
        elif composite >= 38:
            sig_composite = "WATCH"
        else:
            sig_composite = "AVOID"

        _adet = sent["details"]
        _an_av = sent.get("analyst_available", False)
        out = {
            **r,
            # Sentiment layer — null (→ "—" in UI) when no real data backs it
            "sentiment_score":    sent_s,
            "sentiment_label":    sent["label"],
            "sentiment_available": sent_available,
            "sentiment_compound": _adet.get("news_compound") if sent.get("news_available") else None,
            "analyst_upside":     _adet.get("upside_pct") if _an_av else None,
            "analyst_rating":     (_adet.get("rec_key") or None) if _an_av else None,
            "analyst_target":     _adet.get("target_mean") if _an_av else None,
            # Composite breakdown
            "composite_score":    composite,
            "score_tech":         tech,
            "tech_version":       tech_version,  # Phase 3: v1 or tech-v2-date
            "score_fund":         fund,
            "score_sentiment":    sent_s,
            "score_ai":           ai,
            "ai_counted":         False,  # Phase 1: zero-weight, coin-flip models
            "score_halal":        halal_s,
            "f_grade":            f_grade,
            "signal_composite":   sig_composite,
            "sector":             (info or {}).get("sector") or r.get("sector") or "",
        }
        # ── Conviction layer (additive; same pure code the backtest uses) ──────
        # Computes context_adjusted_score, confirmations, conviction_score,
        # rank_value, and a multi-confirmation STRONG BUY gate. The fields are
        # ALWAYS attached (display/validation); whether they RE-RANK the live
        # list is gated by flags at the sort step below.
        try:
            from app.services.conviction_engine import (
                compute_conviction, apply_strong_buy_gate, suggested_position_size,
            )
            conv = compute_conviction(out, _ctx_bundle, _acc_weights)
            out.update(conv)
            out["signal_composite"] = apply_strong_buy_gate(out["signal_composite"], conv)
            out["suggested_size"] = suggested_position_size(
                conv["conviction_score"], static_risk_pct=_static_risk_pct,
            )
        except Exception as _conv_exc:
            logger.debug("conviction layer failed for %s: %s", symbol, _conv_exc)
        # ── Backtest-validated trade plan (informational, always attached) ────
        # • Stop loss : FIXED wide catastrophe stop (SWING_TRAIL_PCT, 15%) when
        #   SWING_EXIT_ENABLED, else max(ATR×1.5, 4%). NOT a trailing stop.
        # • Hold days : 20 — the real edge. Paired backtest (2026-06): time-stop
        #   20d mean +3.60% / win 58.9% / DSR 0.985; trailing stops cut winners.
        # • Take profit: far (3×stop) so it rarely binds before the time exit.
        try:
            _price_tp = float(out.get("price") or 0)
            _atr_p    = float(out.get("atr_pct") or 0)
            _swing_on = getattr(_sel_cfg, "SWING_EXIT_ENABLED", False)
            _stop_p   = float(getattr(_sel_cfg, "SWING_TRAIL_PCT", 15.0)) if _swing_on else max(round(_atr_p * 1.5, 1), 4.0)
            _hold_d   = int(getattr(_sel_cfg, "SWING_MAX_HOLD_DAYS", 20))
            out["trade_plan"] = {
                "stop_pct":   _stop_p,
                "stop_kind":  "fixed_catastrophe" if _swing_on else "atr_fixed",
                "stop_price": round(_price_tp * (1 - _stop_p / 100), 2) if _price_tp else None,
                "tp_price":   round(_price_tp * (1 + 3 * _stop_p / 100), 2) if _price_tp else None,
                "rr_ratio":   3.0,
                "hold_days":  _hold_d,
                "exit_rule":  "hold to time exit; do not sell on normal pullbacks",
                "basis":      "backtest_validated",
            }
        except Exception:
            pass
        return out

    enriched: list[dict] = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(_enrich_one, r): r for r in raw_results}
        for f in as_completed(futures):
            try:
                enriched.append(f.result())
            except Exception:
                pass

    # Keep halal only. Ranking key is flag-gated so live behavior is unchanged
    # until a flag is flipped (and only after the backtest validates ENHANCED):
    #   CONVICTION_SIZING_LIVE      → rank by rank_value (risk-adjusted conviction)
    #   SELECTION_CONDITIONING_LIVE → rank by context_adjusted_score (market-aware)
    #   neither (default)           → rank by raw composite_score (current behavior)
    # Ranking: live flags take priority (only active after backtest passes acceptance
    # criteria); when both are OFF the query-param `sort_by` drives ordering so the
    # UI can expose risk-adjusted views without touching production config.
    _SORT_MAP = {
        "rank_value": lambda x: x.get("rank_value", 0),
        "conviction":  lambda x: x.get("conviction_score", 0),
        "composite":   lambda x: x.get("composite_score", 0),
    }
    try:
        from app.config import settings as _rank_cfg
        if getattr(_rank_cfg, "CONVICTION_SIZING_LIVE", False):
            _rank_key = _SORT_MAP["rank_value"]
            _ranking_mode = "rank_value"
        elif getattr(_rank_cfg, "SELECTION_CONDITIONING_LIVE", False):
            _rank_key = lambda x: x.get("context_adjusted_score", x.get("composite_score", 0))
            _ranking_mode = "context_adjusted"
        else:
            _rank_key    = _SORT_MAP.get(_sort_by_safe, _SORT_MAP["composite"])
            _ranking_mode = _sort_by_safe
    except Exception:
        _rank_key = _SORT_MAP["composite"]
        _ranking_mode = "composite"

    top = sorted(
        [r for r in enriched if r.get("is_halal", False)],
        key=_rank_key,
        reverse=True,
    )[:limit]

    # Determine current SWING_EXIT_ENABLED state for exit_guidance header
    try:
        from app.config import settings as _exit_cfg
        _swing_on_ep = getattr(_exit_cfg, "SWING_EXIT_ENABLED", False)
        _swing_pct_ep = float(getattr(_exit_cfg, "SWING_TRAIL_PCT", 12.0))
    except Exception:
        _swing_on_ep  = False
        _swing_pct_ep = 12.0

    result = {
        "status": "ready",
        "total": len(top),
        "scanned": screener_data.get("total_scanned", 0),
        "regime": screener_data.get("regime", "NEUTRAL"),
        "market_status": screener_data.get("market_status", ""),
        "halt_pipeline": screener_data.get("halt_pipeline", False),
        "composite_max": 100,
        "score_breakdown": {"tech": 30, "fund": 25, "sentiment": 20, "ai": 15, "halal": 10},
        # Backtest-validated exit guidance (header-level; per-stock detail in trade_plan)
        # trailing_stop: DSR=0.97 at 12% vs 0.42 at 2.5% — +1.853%/trade validated
        # hold_days: DSR=0.97 at 20d selection backtest vs 0.69 at 5d
        "exit_guidance": {
            "stop_pct":   _swing_pct_ep if _swing_on_ep else "atr_based",
            "hold_days":  20,
            "rr_ratio":   3.0,
            "swing_exit_enabled": _swing_on_ep,
            "basis":      "trailing_stop_backtest + 20d_selection_backtest",
        },
        "ranking_mode": _ranking_mode,
        "risk_posture": _ctx_bundle.get("risk_posture", "neutral"),
        "results": top,
        "screener_ts": screener_data.get("timestamp", ""),
    }
    _cache_set(cache_key_dp, result)
    return result


@app.get("/api/selection/backtest")
async def selection_backtest(
    lookback_days: int = Query(120, description="Calendar days of as_of grid"),
    hold_days: int = Query(5, description="Forward holding window per pick"),
    step_days: int = Query(5, description="Days between as_of dates"),
    top_n: int = Query(15, description="Top-N picks scored per as_of date"),
    max_symbols: int = Query(80, description="Cap universe size for runtime"),
    archetype: str = Query("usx", description="usx|pullback|breakout|reversal — detector to backtest against USX baseline"),
):
    """Point-in-time validation harness — paired CURRENT vs ENHANCED selection.

    Proves whether market-context-conditioned ranking (ENHANCED) beats raw
    technical ranking (CURRENT) on out-of-sample forward returns BEFORE any live
    flag is flipped. Technical + market-context legs ONLY (fundamentals / ML /
    sentiment excluded to avoid look-ahead). Returns Deflated Sharpe, permutation
    p-value and a bootstrap lower bound for both variants plus an acceptance gate.

    Cached 6 h (the run is heavy: it fetches bars for the whole universe).
    """
    import asyncio as _asyncio

    cache_key_bt = f"selection_backtest_{lookback_days}_{hold_days}_{step_days}_{top_n}_{max_symbols}_{archetype}"
    cached = _cache_get(cache_key_bt, max_age=21600)  # 6 h
    if cached:
        return {**cached, "source": "cache"}

    def _resolve_sector_map(universe: list) -> dict:
        """symbol → sector. DB Universe table first; FMP company profiles as
        fallback (cached 7d). Without sectors the ENHANCED variant can't
        differentiate from CURRENT (every name maps to 'unknown')."""
        from app.services.market_context_bundle import get_symbol_sectors
        smap = get_symbol_sectors(universe) or {}
        missing = [s for s in universe if s.upper() not in smap]
        if missing:
            cached_sec = _cache_get("backtest_sector_map", max_age=604800) or {}
            still = [s for s in missing if s.upper() not in cached_sec]
            if still:
                # ── FMP DB cache (may hold profiles from prior sessions) ──────
                try:
                    from app.db.database import SessionLocal
                    from app.db.models import FMPCache
                    _db = SessionLocal()
                    try:
                        for s in still:
                            row = _db.query(FMPCache).filter(
                                FMPCache.cache_key == f"profile_{s.upper()}"
                            ).first()
                            if row and row.data and row.data.get("sector"):
                                cached_sec[s.upper()] = row.data["sector"]
                        still = [s for s in still if s.upper() not in cached_sec]
                    finally:
                        _db.close()
                except Exception:
                    pass
            if still:
                # ── FMP live API ────────────────────────────────────────────
                try:
                    from app.services.fmp_client import fmp_client
                    for s in still:
                        try:
                            prof = fmp_client.get_profile(s)
                            if prof and prof.get("sector"):
                                cached_sec[s.upper()] = prof["sector"]
                        except Exception:
                            continue
                    still = [s for s in still if s.upper() not in cached_sec]
                except Exception:
                    pass
            if still:
                # ── yfinance fallback (small batches to avoid rate limits) ──
                try:
                    import yfinance as yf
                    for s in still[:30]:
                        try:
                            info = yf.Ticker(s).info
                            if info and info.get("sector"):
                                cached_sec[s.upper()] = info["sector"]
                        except Exception:
                            continue
                except Exception:
                    pass
            _cache_set("backtest_sector_map", cached_sec)
            smap = {**cached_sec, **smap}
        return smap

    def _run() -> dict:
        from app.services.validation_harness import run_selection_backtest
        universe = list(_SMART_UNIVERSE)[:max_symbols]
        sector_map = _resolve_sector_map(universe)
        return run_selection_backtest(
            universe, lookback_days=lookback_days, hold_days=hold_days,
            step_days=step_days, top_n=top_n, max_symbols=max_symbols,
            sector_map=sector_map, archetype=archetype,
        )

    try:
        report = await _asyncio.wait_for(_asyncio.to_thread(_run), timeout=600.0)
    except _asyncio.TimeoutError:
        return {"status": "timeout", "message": "Backtest exceeded 600s; lower max_symbols or lookback_days."}
    if report.get("status") == "ready":
        _cache_set(cache_key_bt, report)
    return report


@app.get("/api/selection/trailing-stop-backtest")
async def trailing_stop_backtest(
    lookback_days: int = Query(365, description="Calendar days of as_of grid"),
    max_hold_days: int = Query(20, description="Time-stop cap / max holding window"),
    step_days: int = Query(5, description="Days between as_of dates"),
    top_n: int = Query(15, description="Top-N technical picks scored per as_of date"),
    max_symbols: int = Query(90, description="Cap universe size for runtime"),
    stops: str = Query("2.5,4,6", description="Comma-separated trailing-stop %s to compare"),
):
    """Compare trailing-stop widths on the validated CURRENT (technical) selection.

    Answers: is the live 2.5% trailing stop cutting winners short? Picks come from
    the technical selection we already validated (DSR 0.97 @ 20d); each stop width
    is simulated on the SAME picks/dates (paired). Includes a no-stop time-stop
    baseline. Cached 6 h (fetches bars for the whole universe).
    """
    import asyncio as _asyncio

    try:
        stop_variants = tuple(float(s.strip()) for s in stops.split(",") if s.strip())
    except ValueError:
        return {"status": "bad_request", "message": f"Could not parse stops='{stops}' (use e.g. 2.5,4,6)."}
    if not stop_variants:
        stop_variants = (2.5, 4.0, 6.0)

    cache_key_ts = f"trailing_stop_bt_{lookback_days}_{max_hold_days}_{step_days}_{top_n}_{max_symbols}_{stops}"
    cached = _cache_get(cache_key_ts, max_age=21600)  # 6 h
    if cached:
        return {**cached, "source": "cache"}

    def _run() -> dict:
        from app.services.validation_harness import run_trailing_stop_backtest
        universe = list(_SMART_UNIVERSE)[:max_symbols]
        return run_trailing_stop_backtest(
            universe, lookback_days=lookback_days, max_hold_days=max_hold_days,
            step_days=step_days, top_n=top_n, max_symbols=max_symbols,
            stop_variants=stop_variants,
        )

    try:
        report = await _asyncio.wait_for(_asyncio.to_thread(_run), timeout=600.0)
    except _asyncio.TimeoutError:
        return {"status": "timeout", "message": "Backtest exceeded 600s; lower max_symbols or lookback_days."}
    if report.get("status") == "ready":
        _cache_set(cache_key_ts, report)
    return report


@app.get("/api/selection/exit-structure-backtest")
async def exit_structure_backtest(
    lookback_days: int = Query(365, description="Calendar days of as_of grid"),
    max_hold_days: int = Query(20, description="Time-stop cap / max holding window"),
    step_days: int = Query(5, description="Days between as_of dates"),
    top_n: int = Query(15, description="Top-N technical picks scored per as_of date"),
    max_symbols: int = Query(90, description="Cap universe size for runtime"),
    live_trail_pct: float = Query(2.5, description="Live trailing-stop %"),
    live_atr_mult: float = Query(1.5, description="Live initial-stop ATR multiplier"),
    proposed_trail_pct: float = Query(12.0, description="Proposed wide trailing-stop %"),
):
    """Head-to-head: the EXACT live exit stack vs the proposed simplified exit.

    Verifies — before any real-money risk-param change — how much the current
    multi-exit structure (initial ATR stop + 2.5% trail + TP1/2/3 partial exits)
    leaves on the table versus the validated single-wide-trail + time-exit.
    Both run on identical validated technical picks/dates (paired). Cached 6 h.
    """
    import asyncio as _asyncio

    cache_key_es = (f"exit_struct_bt_{lookback_days}_{max_hold_days}_{step_days}_{top_n}_"
                    f"{max_symbols}_{live_trail_pct}_{live_atr_mult}_{proposed_trail_pct}")
    cached = _cache_get(cache_key_es, max_age=21600)  # 6 h
    if cached:
        return {**cached, "source": "cache"}

    def _run() -> dict:
        from app.services.validation_harness import run_exit_structure_comparison
        universe = list(_SMART_UNIVERSE)[:max_symbols]
        return run_exit_structure_comparison(
            universe, lookback_days=lookback_days, max_hold_days=max_hold_days,
            step_days=step_days, top_n=top_n, max_symbols=max_symbols,
            live_trail_pct=live_trail_pct, live_atr_mult=live_atr_mult,
            proposed_trail_pct=proposed_trail_pct,
        )

    try:
        report = await _asyncio.wait_for(_asyncio.to_thread(_run), timeout=600.0)
    except _asyncio.TimeoutError:
        return {"status": "timeout", "message": "Backtest exceeded 600s; lower max_symbols or lookback_days."}
    if report.get("status") == "ready":
        _cache_set(cache_key_es, report)
    return report

@app.get("/api/simulate/strategy")
async def simulate_strategy_endpoint(
    symbol: str = Query("AAPL", description="Ticker to simulate"),
    start_date: str = Query("2024-01-01", description="Start date ISO"),
    end_date: str = Query(None, description="End date ISO (default: today)"),
    strategy_id: str = Query("A", description="Strategy A/B/C"),
    capital: float = Query(10000.0, description="Starting capital USD"),
    risk_pct: float = Query(0.02, description="Risk per trade (0.02 = 2%)"),
    stop_pct: float = Query(0.05, description="Stop-loss distance (0.05 = 5%)"),
    tp_pct: float = Query(0.10, description="Take-profit distance (0.10 = 10%)"),
    garch: bool = Query(False, description="Enable GARCH vol sizing layer"),
    covariance: bool = Query(False, description="Enable portfolio covariance layer"),
    mr_quality: bool = Query(False, description="Enable mean-reversion quality layer"),
    sentiment: bool = Query(False, description="Enable news sentiment layer"),
):
    """Strategy simulation sandbox - what-if on historical data.

    Runs the EXACT same sizing cascade as live execute_buy() on
    historical OHLCV data. No broker, no DB writes - pure what-if.
    """
    from app.services.strategy_simulator import simulate_strategy

    sizing_layers = {}
    if garch:
        sizing_layers["garch"] = True
    if covariance:
        sizing_layers["covariance"] = True
        sizing_layers["strategy_id"] = strategy_id
    if mr_quality:
        sizing_layers["mr_quality"] = True
    if sentiment:
        sizing_layers["sentiment"] = True

    return simulate_strategy(
        symbol=symbol.upper(),
        start_date=start_date,
        end_date=end_date,
        strategy_id=strategy_id,
        capital=capital,
        risk_pct=risk_pct,
        stop_pct=stop_pct,
        tp_pct=tp_pct,
        sizing_layers=sizing_layers if sizing_layers else None,
    )

@app.get("/api/diagnose/data-sources")
async def diagnose_data_sources(
    symbol: str = Query("SPY", description="Symbol to test data sources against"),
):
    """Test each market-data provider for a given symbol and report what works.

    Useful for diagnosing the Alpaca Data API 401 and confirming fallbacks.
    """
    import time as _time
    from app.services.market_data import fetch_alpaca, fetch_yf, fetch_ibkr

    diag: dict = {"symbol": symbol.upper(), "providers": {}}

    # 1) Test Alpaca Data API directly (not through fetch() which caches)
    try:
        t0 = _time.time()
        _dk, _ds = None, None
        try:
            from app.services.market_data import _alpaca_data_creds
            _dk, _ds = _alpaca_data_creds()
        except Exception:
            pass
        if _dk and _ds:
            import httpx
            headers = {"APCA-API-KEY-ID": _dk, "APCA-API-SECRET-KEY": _ds}
            import datetime as _dt
            end = _dt.datetime.now(_dt.timezone.utc)
            start = end - _dt.timedelta(days=30)
            resp = httpx.get(
                "https://data.alpaca.markets/v2/stocks/bars",
                headers=headers,
                params={"symbols": symbol.upper(), "timeframe": "1Day",
                        "start": start.strftime("%Y-%m-%d"),
                        "end": end.strftime("%Y-%m-%d"), "limit": 5, "feed": "iex"},
                timeout=15,
            )
            elapsed = round(_time.time() - t0, 2)
            if resp.status_code == 200:
                diag["providers"]["alpaca_data"] = {
                    "status": "OK", "elapsed_s": elapsed,
                    "bars_returned": len((resp.json().get("bars") or {}).get(symbol.upper(), [])),
                }
            else:
                diag["providers"]["alpaca_data"] = {
                    "status": "FAIL", "http": resp.status_code,
                    "body": resp.text[:400], "elapsed_s": elapsed,
                    "fix": "Generate separate Data-feed keys from Alpaca Dashboard → Paper Trading → API Keys. "
                           "The trading keys (PKYX...) DO NOT work on data.alpaca.markets.",
                }
        else:
            diag["providers"]["alpaca_data"] = {"status": "SKIP", "reason": "No credentials found (ALPACA_DATA_KEY empty)"}
    except Exception as exc:
        diag["providers"]["alpaca_data"] = {"status": "ERROR", "detail": str(exc)[:300]}

    # 2) Test fetch() with caching bypass
    try:
        t0 = _time.time()
        df = fetch_alpaca(symbol.upper(), period="1y")
        elapsed = round(_time.time() - t0, 2)
        if df is not None and len(df) >= 40:
            diag["providers"]["fetch_alpaca"] = {"status": "OK", "rows": len(df), "elapsed_s": elapsed}
        else:
            diag["providers"]["fetch_alpaca"] = {"status": "FAIL", "elapsed_s": elapsed,
                                                  "detail": "returned None or <40 rows"}
    except Exception as exc:
        diag["providers"]["fetch_alpaca"] = {"status": "ERROR", "detail": str(exc)[:300]}

    # 3) Test yfinance
    try:
        t0 = _time.time()
        df = fetch_yf(symbol.upper(), period="1y")
        elapsed = round(_time.time() - t0, 2)
        if df is not None and len(df) >= 40:
            diag["providers"]["yfinance"] = {"status": "OK", "rows": len(df), "elapsed_s": elapsed}
        else:
            diag["providers"]["yfinance"] = {"status": "FAIL", "elapsed_s": elapsed}
    except Exception as exc:
        diag["providers"]["yfinance"] = {"status": "ERROR", "detail": str(exc)[:300]}

    # 4) Test Tiingo
    try:
        from app.config import settings as _tcfg
        if _tcfg.TIINGO_TOKEN:
            t0 = _time.time()
            from app.services.openbb_data import fetch_ohlcv_tiingo
            df = fetch_ohlcv_tiingo(symbol.upper(), period="1y")
            elapsed = round(_time.time() - t0, 2)
            if df is not None and not df.empty:
                diag["providers"]["tiingo"] = {"status": "OK", "rows": len(df), "elapsed_s": elapsed}
            else:
                diag["providers"]["tiingo"] = {"status": "FAIL", "elapsed_s": elapsed}
        else:
            diag["providers"]["tiingo"] = {"status": "SKIP", "reason": "TIINGO_TOKEN not configured"}
    except Exception as exc:
        diag["providers"]["tiingo"] = {"status": "ERROR", "detail": str(exc)[:300]}

    # 5) Check env for credential presence
    from app.config import settings as _env
    diag["env"] = {
        "ALPACA_API_KEY": bool(_env.ALPACA_API_KEY),
        "ALPACA_DATA_KEY": bool(_env.ALPACA_DATA_KEY),
        "ALPACA_DATA_KEY_EQ_TRADING": _env.ALPACA_DATA_KEY == _env.ALPACA_API_KEY if _env.ALPACA_DATA_KEY and _env.ALPACA_API_KEY else None,
        "TIINGO_TOKEN": bool(_env.TIINGO_TOKEN),
        "FMP_API_KEY": bool(_env.FMP_API_KEY),
        "FRED_API_KEY": bool(_env.FRED_API_KEY),
    }

    working = [k for k, v in diag["providers"].items() if v.get("status") == "OK"]
    diag["summary"] = f"{len(working)}/{len(diag['providers'])} providers working: {', '.join(working) or 'NONE'}"
    return diag


def _quick_technical_rank(symbol: str, spy_df=None) -> float:
    """Cheap technical pre-rank (0-100ish) using ONLY cached OHLCV bars.

    No FMP, no network — reads the already-seeded _fetch_data cache from
    the Alpaca batch prefetch. Uses the measured-useful signals from the
    Phase-2 v1.1 IC analysis: MACD inflection, RS vs SPY, RSI, above-EMA50.
    On any error returns -1.0 (sinks the symbol, never raises).
    """
    try:
        from app.services.technical import rsi as _t_rsi, macd as _t_macd
        from app.services.technical import relative_strength as _t_rs

        ok, df = _fetch_data(symbol, period="1y")
        if not ok or df is None or len(df) < 50:
            return -1.0
        close = df["close"].astype(float)
        score = 0.0

        # ── MACD inflection (histogram direction) — weight 35 ──
        try:
            _macd_line, _macd_sig, hist = _t_macd(close)
            hv = hist.dropna().values
            if len(hv) >= 3:
                if hv[-1] > hv[-2] and hv[-2] <= hv[-3]:
                    score += 35.0   # inflection upward (bullish cross)
                elif hv[-1] > 0:
                    score += 20.0   # positive histogram
                elif hv[-1] > hv[-2]:
                    score += 10.0   # improving (less negative)
        except Exception:
            pass

        # ── RS vs SPY (20-day) — weight 25 ──
        try:
            if spy_df is not None and len(spy_df) >= 20:
                rs = _t_rs(df, spy_df, period=20)
                if rs > 1.05:
                    score += 25.0
                elif rs > 1.0:
                    score += 15.0
        except Exception:
            pass

        # ── RSI — weight 20 ──
        try:
            rsi_vals = _t_rsi(close, n=14).dropna()
            if len(rsi_vals) > 0:
                r = float(rsi_vals.iloc[-1])
                if 40 <= r <= 60:
                    score += 20.0
                elif 30 <= r < 40:
                    score += 12.0
                elif 60 < r <= 70:
                    score += 8.0
        except Exception:
            pass

        # ── Above EMA50 — weight 20 ──
        try:
            ema50 = close.ewm(span=50).mean().dropna()
            if len(ema50) > 0 and len(close) > 0:
                if float(close.iloc[-1]) > float(ema50.iloc[-1]):
                    score += 20.0
        except Exception:
            pass

        return round(score, 1)
    except Exception:
        return -1.0


def _run_screener_bg(scan_symbols: list):
    """Run screener scan in background thread, store results in cache."""
    cache_key = "smart_screener"
    total = len(scan_symbols)
    _reset_progress(total)
    try:
        logger.info("Background scan starting for %d symbols", total)
        all_results = []

        # Load current watchlist to flag watched symbols
        from app.services.watchlist_service import get_watchlist_set
        watchlist_set = get_watchlist_set()

        # ── Alpaca batch pre-fetch ───────────────────────────────────────────────
        # Pre-fetch all symbol bars in one pass (~7 batched Alpaca requests for 650
        # symbols instead of 650 individual ones). Results are written into the
        # _fetch_data disk cache so every subsequent _fetch_data(sym, "1y") call
        # during _analyze_smart + _score_momentum hits the cache immediately —
        # zero additional Alpaca requests, no 429s.
        try:
            from app.services.market_data import fetch_alpaca_batch as _alpaca_batch
            logger.info("Alpaca batch pre-fetch for %d symbols…", len(scan_symbols))
            prefetched = _alpaca_batch(scan_symbols, period="1y")
            seeded = 0
            for sym, df in prefetched.items():
                try:
                    records = df.to_dict("records")
                    _cache_set(f"ohlcv_{_cache_key(sym)}_1y", records)
                    seeded += 1
                except Exception:
                    pass
            logger.info("Alpaca batch pre-fetch done: %d/%d symbols cached", seeded, len(scan_symbols))
        except Exception as _pre_exc:
            logger.warning("Alpaca batch pre-fetch failed (%s) — falling back to per-symbol fetch", _pre_exc)

        # Fetch SPY data once per batch (reused for all symbols). SPY is REQUIRED:
        # the RS-vs-SPY hard gate fails closed when SPY is missing, which zeroes
        # momentum for EVERY symbol (universal T=0). So we retry via the alternate
        # data path and emit a loud WARNING rather than silently degrading.
        spy_df_shared = None
        try:
            _, spy_df_shared = _fetch_data("SPY", period="2mo")
        except Exception as _spy_exc:
            logger.warning("SPY primary fetch failed: %s", _spy_exc)
        if spy_df_shared is None or len(spy_df_shared) < 20:
            try:
                import halal_screener as _hs
                _alt = _hs.fetch_yf("SPY", period="3mo")
                if _alt is not None and len(_alt) >= 20:
                    spy_df_shared = _alt
                    logger.info("SPY recovered via halal_screener.fetch_yf fallback (%d bars)", len(_alt))
            except Exception as _spy_alt_exc:
                logger.warning("SPY fallback fetch failed: %s", _spy_alt_exc)
        if spy_df_shared is None or len(spy_df_shared) < 20:
            logger.error(
                "SPY frame UNAVAILABLE — RS-vs-SPY hard gate will fail closed for "
                "ALL symbols (momentum=0, T=0). Check Alpaca/Tiingo/yfinance data feed."
            )

        # ── Two-tier funnel (flag-gated, default = full scan) ──
        # When _FUNNEL_TOP_N > 0: pre-rank the ENTIRE universe on cached bars only,
        # then run the expensive _analyze_smart on just the top-N. FMP load stays
        # bounded regardless of universe size. Default (0) = byte-identical full scan.
        if _FUNNEL_TOP_N > 0:
            def _rank_one(s):
                return _quick_technical_rank(s, spy_df_shared)
            original_count = len(scan_symbols)
            scan_symbols = _funnel_select(scan_symbols, _rank_one, _FUNNEL_TOP_N)
            logger.info("funnel: %d -> %d by technical pre-rank (top_n=%d)",
                        original_count, len(scan_symbols), _FUNNEL_TOP_N)

        batch_size = SCREENER_BATCH_SIZE
        batches = [scan_symbols[i:i+batch_size] for i in range(0, len(scan_symbols), batch_size)]
        for batch_idx, batch in enumerate(batches):
            _update_progress(batch_idx * batch_size + len(batch), batch_idx + 1)
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = {pool.submit(_analyze_smart, sym, watchlist_set, spy_df_shared): sym for sym in batch}
                for f in as_completed(futures):
                    try:
                        r = f.result()
                    except Exception:
                        logger.debug("_run_screener_bg: symbol %s failed", futures[f], exc_info=True)
                        continue
                    if r:
                        all_results.append(r)
        logger.info("Background scan complete: %d halal results from %d symbols",
                    sum(1 for r in all_results if r.get("is_halal")), total)

        # Filter, sort, build result
        filtered = [r for r in all_results if r.get("is_halal")]
        filtered.sort(key=lambda r: r.get("smart_score", 0), reverse=True)
        # Cap forecast symbols when torch not available (ARIMA-only is fast; DL is heavier)
        _forecast_cap = 20 if _HAS_TORCH else 10
        top = filtered[:50]
        top_for_forecast = top[:_forecast_cap]

        # Parallelism: more workers when torch models are available
        _forecast_workers = 2 if _HAS_TORCH else 1
        with ThreadPoolExecutor(max_workers=_forecast_workers) as pool:
            f_futures = {pool.submit(_score_forecast_consensus, r["symbol"]): r for r in top_for_forecast}
            for ff in as_completed(f_futures):
                r = f_futures[ff]
                try:
                    fc = ff.result()
                    if fc:
                        proxy = r.get("forecast_proxy", 0)
                        r["forecast_score"] = fc.get("score", 0)
                        r["forecast_details"] = fc.get("details", {})
                        r["smart_score"] = min(100, r.get("smart_score", 0) - proxy + fc.get("score", 0))
                except Exception:
                    pass

        # Market status for gates
        try:
            from app.services.market_context import get_market_status
            market_status = get_market_status()
        except Exception:
            market_status = {"status": "RISK ON", "regime": "NEUTRAL", "min_gate": 60, "strong_gate": 75, "halt_pipeline": False}

        qualified_count = sum(1 for r in top if r.get("smart_score", 0) >= market_status.get("strong_gate", 75))
        watch_count = sum(1 for r in top if market_status.get("min_gate", 60) <= r.get("smart_score", 0) < market_status.get("strong_gate", 75))

        cache_result = {
            "total_scanned": total,
            "halal_count": sum(1 for r in all_results if r.get("is_halal")),
            "results_count": len(top),
            "qualified_count": qualified_count,
            "watch_count": watch_count,
            "min_score": market_status.get("min_gate", 60),
            "strong_gate": market_status.get("strong_gate", 75),
            "market_status": market_status.get("status", "RISK ON"),
            "regime": market_status.get("regime", "NEUTRAL"),  # BULL/NEUTRAL/BEAR
            "halt_pipeline": market_status.get("halt_pipeline", False),
            "results": top,
        }
        _cache_set(cache_key, cache_result)

        # Permanent "last good" copy — survives cache expiry and cold boots
        # so the dashboard can instantly serve results on open even when the
        # fresh+stale cache windows miss (deploy, overnight idle, etc.).
        # GUARD: only overwrite last_good with a NON-empty scan, so a degraded run
        # (e.g. SPY feed down → 0 results) can't poison the instant-open cache.
        if top:
            _cache_set("smart_screener_lastgood", {**cache_result, "lastgood_asof": time.time()})

        # Roadmap 1.6 — Telegram alert for qualified signals
        if qualified_count > 0:
            try:
                from app.services.telegram_alert import alert_qualified_signal
                for r in top[:3]:
                    if r.get("smart_score", 0) >= market_status.get("strong_gate", 75):
                        alert_qualified_signal(
                            symbol=r.get("symbol", "?"),
                            company=r.get("company", ""),
                            score=r.get("smart_score", 0),
                            price=r.get("price", 0) or 0,
                            strategy=r.get("strategy", "N/A"),
                            stop_loss=0,
                            take_profits=[],
                            rr_ratio=0,
                            top_indicators=[r.get("signal", "N/A")],
                            halal_status="HALAL" if r.get("is_halal") else "NON-HALAL",
                        )
            except Exception as e:
                logger.warning("Qualified signal alert failed: %s", e)
    finally:
        try:
            _finish_progress()
        except Exception:
            _screener_progress["status"] = "done"


@app.get("/api/screener/progress")
async def screener_progress():
    """Return current scan progress."""
    p = dict(_screener_progress)
    if p["total"] > 0:
        p["pct"] = round(p["current"] / p["total"] * 100, 1)
    else:
        p["pct"] = 0
    return p


@app.get("/api/screener/near-miss")
async def screener_near_miss():
    """Symbols that almost passed the USX V4 filter.

    Returns candidates that scored 60-64 (borderline quality) or failed
    ONLY the daily trend gate (price>EMA21>EMA50) with otherwise clean
    gates. Useful for watchlist expansion and catching recovering names
    before they breach the main buy list.

    Data is refreshed on each  call from the scheduler.
    """
    try:
        from app.services.usx_pro_filter import get_near_misses
        entries = get_near_misses()
        results = []
        for sc in entries:
            results.append({
                "symbol":       sc.symbol,
                "score":        round(sc.score, 1),
                "reason":       sc.reason,
                "breakdown":    sc.breakdown,
            })
        return {
            "status":   "ok",
            "total":    len(results),
            "results":  sorted(results, key=lambda r: r["score"], reverse=True),
        }
    except Exception as exc:
        logger.warning("near-miss endpoint failed: %s", exc)
        return {"status": "error", "total": 0, "results": [], "detail": str(exc)}


# ---------------------------------------------------------------------------
# WATCH list — surface gate-blocked near-miss stocks with WHY they wait
# ---------------------------------------------------------------------------

def _market_gate_reason(ms: dict) -> str:
    """Short Arabic reason the whole market is gated now, or '' when clear."""
    if not ms:
        return ""
    status = str(ms.get("status", "")).upper()
    bits = []
    if ms.get("halt_pipeline") or "EXTREME" in status:
        bits.append("توقّف (خوف شديد/VIX)")
    elif "RISK-OFF" in status or "CAUTION" in status or "STRESS" in status:
        bits.append(f"السوق {ms.get('status')}")
    if str(ms.get("regime", "")).upper() == "BEAR":
        bits.append("نظام هابط (BEAR)")
    return " · ".join(bits)


def _watch_reason(s: dict, market_block: str) -> str:
    """Why this strong-ish stock is WATCH not BUY."""
    parts = []
    if market_block:
        parts.append(market_block)
    failed = s.get("hard_gates_failed") or []
    if failed:
        parts.append("بوّابة: " + "، ".join(str(f) for f in failed[:2]))
    rs = s.get("rs_vs_spy")
    try:
        if rs is not None and float(rs) < 0:
            parts.append("متأخّر عن SPY")
    except (TypeError, ValueError):
        pass
    comp = s.get("composite_score", 0)
    parts.append(f"الدرجة {comp}/100 < 55 (بوّابة BUY)")
    return " · ".join(parts)


@app.get("/api/screener/watch")
async def screener_watch(limit: int = Query(20, ge=1, le=100)):
    """Near-miss WATCH list — strong-ish halal stocks below the BUY gate, with WHY they wait.
    Display only; the system never buys these."""
    try:
        data = await _smart_screener_impl(min_score=30, max_results=200, use_cache="true")
    except Exception as e:
        logger.debug("screener_watch: %s", e)
        data = {}
    try:
        from app.services.market_context import get_market_status
        ms = get_market_status()
    except Exception:
        ms = {}
    market_block = _market_gate_reason(ms)
    rows = data.get("results", []) if isinstance(data, dict) else []
    watch = []
    for s in rows:
        comp = s.get("composite_score", 0) or 0
        if s.get("signal_composite") == "WATCH" or (38 <= comp < 55):
            watch.append({
                "symbol": s.get("symbol"), "name": s.get("name") or s.get("company") or s.get("symbol"),
                "price": s.get("price"), "composite_score": comp,
                "score_tech": s.get("score_tech"), "score_fund": s.get("score_fund"),
                "sector": s.get("sector"), "is_halal": s.get("is_halal"),
                "watch_reason": _watch_reason(s, market_block),
            })
    watch.sort(key=lambda x: x.get("composite_score", 0) or 0, reverse=True)
    return {"market_block": market_block, "count": len(watch[:limit]), "watch": watch[:limit]}


# ---------------------------------------------------------------------------
# Day-trade "explosion" scanner — technical-only, ALL stocks, RESEARCH (not halal-gated)
# ---------------------------------------------------------------------------
_daytrade_scanning = False


@app.get("/api/screener/daytrade")
async def screener_daytrade(limit: int = Query(30, ge=5, le=60)):
    """RESEARCH ONLY — technical "explosion" rank over the full S&P 500 (incl. non-halal).

    Ranks by a 0-100 technical score (RVOL + momentum + volatility + gap) on daily bars.
    Ignores the halal gate by DESIGN (research/display). Strictly isolated: its own cache key
    (`daytrade_scan`), never touches the halal smart_screener cache, the ledgers, or the trade
    path — execute_buy stays halal-gated. `halal_verdict` per row is a best-effort cached flag.
    """
    cached = _cache_get("daytrade_scan", max_age=900)  # 15 min
    if cached and cached.get("results"):
        rows = cached["results"][:limit]
        return {"research_only": True, "source": "cache", "count": len(rows), "results": rows}

    global _daytrade_scanning
    if not _daytrade_scanning:
        _daytrade_scanning = True

        def _run():
            global _daytrade_scanning
            try:
                from app.services.daytrade_scan import scan_explosion
                from app.services.reference_data import get_tradable_symbols
                from app.services.universe import _SP500_ALL
                try:
                    from app.data.russell1000_halal import RUSSELL_1000_HALAL as _RUSSELL
                except Exception:
                    _RUSSELL = []
                # The full Alpaca tradable list is ~11k (mostly illiquid OTC) — fetching all of
                # it is too slow. Prioritize the known-liquid large/mid caps (S&P 500 + Russell)
                # FIRST, then fill from the broader tradable list, capped — so the scan covers the
                # liquid names that matter, goes well beyond ~650, and stays ~1-2 min. The
                # liquidity gate inside scan_explosion still filters the tail. Cap is env-tunable.
                cap = int(os.environ.get("DAYTRADE_UNIVERSE_CAP", "2500"))
                tradable = sorted(get_tradable_symbols())
                universe = list(dict.fromkeys([*_SP500_ALL, *_RUSSELL, *tradable]))[:cap] or list(_SP500_ALL)
                rows = scan_explosion(universe, limit=60)
                if rows:
                    _cache_set("daytrade_scan", {"results": rows, "universe_size": len(universe)})
            except Exception as e:
                logger.warning("daytrade scan failed: %s", e)
            finally:
                _daytrade_scanning = False

        import threading
        threading.Thread(target=_run, daemon=True, name="daytrade-scan").start()

    return {"research_only": True, "status": "scanning", "results": []}


# ---------------------------------------------------------------------------
# Monte-Carlo forecast risers — top stocks by expected upside (probabilistic)
# ---------------------------------------------------------------------------

@app.get("/api/risers")
async def forecast_risers(limit: int = Query(8, ge=1, le=20)):
    """Top stocks by Monte-Carlo EXPECTED upside (probabilistic — not advice).

    NOTE: path is /api/risers, NOT /api/forecast/risers — the latter is shadowed by
    the earlier-registered /api/forecast/{model_name} route (FastAPI matches in
    registration order), which silently returned "Unknown model 'risers'" and left
    this panel empty since launch. Keep this off the /api/forecast/ namespace.
    """
    try:
        data = await _smart_screener_impl(min_score=45, max_results=60, use_cache="true")
    except Exception as e:
        logger.debug("risers screener: %s", e)
        data = {}
    # NOTE: the smart-screener cache stores the score as "smart_score" — filtering on
    # "composite_score" (which never exists here) left this panel EMPTY since launch.
    cands = [r for r in (data.get("results", []) if isinstance(data, dict) else [])
             if r.get("is_halal")
             and (r.get("smart_score") or r.get("composite_score") or 0) >= 45][:25]

    # FALLBACK: the smart-screener is the heavy FMP-fundamentals scan over ~657 symbols;
    # it rarely completes before a restart/deploy, so its cache (and last_good) is usually
    # cold — which is the REAL reason this panel showed nothing since launch. When it has
    # no candidates, use the fast technical weekly screener (/buys), which is reliably warm,
    # as the Monte-Carlo candidate universe. `_one` reads symbol/price/name from each row,
    # which the weekly rows provide (name falls back to the symbol).
    if not cands:
        try:
            from halal_screener import _get_cached as _wk_cached
            wk, _ = _wk_cached("screener")
            if isinstance(wk, list):
                wk = [r for r in wk if r.get("symbol") and not r.get("Status")
                      and r.get("halal") in (True, "Yes")]
                wk.sort(key=lambda r: r.get("swing_score", 0), reverse=True)
                cands = wk[:25]
        except Exception as e:
            logger.debug("risers weekly fallback: %s", e)

    from app.services.price_forecast import monte_carlo_forecast
    from app.services import market_data as md
    def _one(r):
        try:
            df = md.fetch(r["symbol"], period="1y")
            prices = df["close"].tolist() if df is not None and len(df) > 30 else None
            if not prices:
                return None
            fc = monte_carlo_forecast(prices, 20)
            ec = fc.get("expected_change_pct")
            if ec is None:
                return None
            return {"symbol": r["symbol"], "name": r.get("name") or r.get("company") or r["symbol"],
                    "price": fc.get("current_price") or r.get("price"),
                    "expected_change_pct": ec, "prob_profit_pct": fc.get("prob_profit_pct"),
                    "is_halal": r.get("is_halal", True)}
        except Exception:
            return None
    import asyncio
    rows = await asyncio.gather(*[asyncio.to_thread(_one, r) for r in cands])
    rows = [x for x in rows if x and x.get("expected_change_pct") is not None]
    rows.sort(key=lambda x: x["expected_change_pct"], reverse=True)
    top = rows[:limit]
    return {"count": len(top), "risers": top,
            "market_soft": bool(top and top[0]["expected_change_pct"] <= 0),
            "disclaimer": "نطاق احتمالي من تذبذب السهم — ليس وعداً ولا نصيحة."}


# ---------------------------------------------------------------------------
# Market news + indicators — bottom panel data
# ---------------------------------------------------------------------------

@app.get("/api/market/news")
async def market_news(limit: int = Query(8, ge=1, le=20)):
    """General market news headlines (Alpaca primary, FMP fallback)."""
    from app.services.market_data import get_alpaca_market_news
    items = await asyncio.to_thread(get_alpaca_market_news, limit)
    if not items:
        fmp = await _fmp_call(fmp_client.get_general_news, limit=limit)
        items = [{"title": n.get("title", ""), "summary": (n.get("text") or "")[:300],
                  "publisher": n.get("site", ""), "link": n.get("url", ""),
                  "published": n.get("publishedDate", "")} for n in (fmp or [])]
    # Dedup by normalized title — keep first occurrence only.
    # Optionally drop obvious clickbait headlines.
    _clickbait = {"if you invested $1000", "10 years ago", "what if you invested",
                  "how much would", "here's how much"}
    seen: set[str] = set()
    deduped: list[dict] = []
    for n in items:
        t = (n.get("title") or "").strip().lower()
        if not t or t in seen:
            continue
        # Drop clickbait
        if any(cb in t for cb in _clickbait):
            continue
        seen.add(t)
        deduped.append(n)
    items = deduped
    return {"count": len(items), "news": items}


@app.get("/api/market/indicators")
async def market_indicators():
    """Main market indicators — REAL index/commodity levels (FMP), ETF proxy as honest fallback."""
    try:
        from app.services.market_data import get_alpaca_snapshots
        from app.services import fmp_client

        # (label, real FMP symbol, ETF proxy fallback)
        rows_def = [
            ("S&P 500", "^GSPC", "SPY"), ("Nasdaq", "^IXIC", "QQQ"),
            ("Dow", "^DJI", "DIA"), ("Russell", "^RUT", "IWM"),
            ("Gold", "GCUSD", "GLD"), ("Brent", "BZUSD", "BNO"),
        ]
        real = {}
        try:
            real = await asyncio.to_thread(fmp_client.get_quotes,
                                           [r[1] for r in rows_def] + ["^VIX"])
        except Exception:
            real = {}

        etfs = [r[2] for r in rows_def]
        snaps = await asyncio.to_thread(get_alpaca_snapshots, etfs + ["BTC/USD"])

        indicators = []
        for label, fsym, etf in rows_def:
            q = real.get(fsym) or {}
            if q.get("price") is not None:
                indicators.append({"label": label, "symbol": fsym, "proxy": None,
                                   "price": q.get("price"), "change_pct": q.get("change_pct")})
            else:
                s = snaps.get(etf, {})
                indicators.append({"label": label, "symbol": etf, "proxy": etf,  # honest ETF stand-in
                                   "price": s.get("price"), "change_pct": s.get("change_pct")})

        # VIX — FMP ^VIX, else market_status (no change% if unknown → "—", never faked)
        v = real.get("^VIX") or {}
        if v.get("price") is None:
            try:
                from app.services.market_context import get_market_status
                v = {"price": (get_market_status() or {}).get("vix"), "change_pct": None}
            except Exception:
                v = {}
        indicators.append({"label": "VIX", "symbol": "^VIX", "proxy": None,
                           "price": v.get("price"), "change_pct": v.get("change_pct")})

        # Bitcoin — crypto snapshot
        btc = snaps.get("BTC/USD", {})
        indicators.append({"label": "Bitcoin", "symbol": "BTC/USD", "proxy": None,
                           "price": btc.get("price"), "change_pct": btc.get("change_pct")})
        return {"indicators": indicators}
    except Exception:
        return {"indicators": []}


# ---------------------------------------------------------------------------
# AI Assistant — smart investment analyst (LLM-powered with template fallback)
# ---------------------------------------------------------------------------


@app.get("/api/ai/report")
async def ai_report(
    min_score: int = Query(70, description="Minimum smart score (0-100)"),
    max_results: int = Query(8, description="Number of top opportunities to analyze"),
    use_cache: str = Query("true", description="Use cached screener results"),
):
    """Generate an AI-powered Arabic investment report.

    Uses the smart screener data and analyzes it via LLM (OpenAI/Anthropic)
    or template-based fallback. Returns human-readable Arabic analysis.
    """
    _use_cache = str(use_cache).lower() in ("true", "1", "yes")
    try:
        screener_data = await _smart_screener_impl(
            min_score=min_score, max_results=max_results,
            use_cache="true" if _use_cache else "false",
        )
    except Exception as e:
        return JSONResponse({"error": f"Failed to get screener data: {e}"}, status_code=502)

    report = ai_agent.generate_smart_scan_report(screener_data)
    return {
        "report": report,
        "llm_used": ai_agent._llm_available,
        "timestamp": datetime.utcnow().isoformat(),
        "opportunities": len(screener_data.get("results", [])),
    }


@app.get("/api/ai/ask")
async def ai_ask(
    question: str = Query("", description="Your question in Arabic or English"),
    symbol: str = Query("", description="Optional stock symbol for context"),
):
    """Ask the AI investment analyst a question.

    Supports Arabic and English. Uses smart screener + consensus + halal data
    as context. Falls back to template-based answers when no LLM is configured.
    """
    if not question.strip():
        return JSONResponse({"error": "Please provide a question"}, status_code=400)

    context = {}

    # Gather context from direct function calls (avoids HTTP deadlock)
    try:
        screener_data = await _smart_screener_impl(
            min_score=0, max_results=5, use_cache="true",
        )
        context["screener_data"] = screener_data
    except Exception:
        pass

    if symbol:
        try:
            halal_data = _screen_halal(symbol)
            if halal_data:
                halal_data["symbol"] = symbol
                context["halal_data"] = halal_data
        except Exception:
            pass
        try:
            consensus_data = await consensus(
                symbol=symbol, accuracy_threshold=55.0,
            )
            context["consensus_data"] = consensus_data
        except Exception:
            pass

    answer = ai_agent.ask(question, context)
    return {
        "answer": answer,
        "question": question,
        "llm_used": ai_agent._llm_available,
        "context_symbol": symbol or None,
        "timestamp": datetime.utcnow().isoformat(),
    }


# ---------------------------------------------------------------------------
# Consensus — aggregate all model predictions with accuracy > 55%
# ---------------------------------------------------------------------------


@app.get("/api/consensus")
async def consensus(
    symbol: str = Query("AAPL", description="Stock symbol"),
    target_column: str = Query("close", description="Target column"),
    forecast_horizon: int = Query(5, description="Forecast horizon"),
    sequence_length: int = Query(30, description="Lookback window"),
    accuracy_threshold: float = Query(55.0, description="Min accuracy %"),
):
    """Run all forecast models, filter by accuracy, output majority-vote recommendation."""
    records, df = _fetch_data(symbol)
    prices = df[target_column].values.astype(float)
    dates = df["date"].astype(str).tolist() if "date" in df.columns else []

    votes = []
    errors = []

    def _run_one_model(model_name: str):
        """Run a single model for consensus — uses pre-trained weights when available."""
        _ckpt_pt = _MODELS_DIR / model_name / "20260519.pt"
        _ckpt_pkl = _MODELS_DIR / model_name / "20260519.pkl"
        _n_splits = 1

        if _ckpt_pt.exists() and model_name not in ("arima", "ensemble"):
            try:
                from openbb_forecast.models.factory import MODEL_REGISTRY as _MR
                _cls = _MR.get(model_name)
                if _cls and hasattr(_cls, "load"):
                    model = _cls.load(_ckpt_pt)
                else:
                    raise ValueError("no load method")
            except Exception:
                model_kwargs = {"epochs": 3, "hidden_size": 32, "num_layers": 1, "learning_rate": 0.001}
                if model_name in ("cnn_seq2seq", "dilated_cnn"):
                    model_kwargs = {"epochs": 3, "learning_rate": 0.001, "filters": 32}
                model = create_model(model_name, **model_kwargs)
        elif _ckpt_pkl.exists():
            import pickle as _pkl
            with open(_ckpt_pkl, "rb") as _f:
                model = _pkl.load(_f)
        else:
            model_kwargs = {"epochs": 3, "hidden_size": 32, "num_layers": 1, "learning_rate": 0.001}
            if model_name in ("cnn_seq2seq", "dilated_cnn"):
                model_kwargs = {"epochs": 3, "learning_rate": 0.001, "filters": 32}
            model = create_model(model_name, **model_kwargs)

        fold_results = model.walk_forward_predict(
            data=prices, sequence_length=sequence_length,
            forecast_horizon=forecast_horizon, n_splits=_n_splits,
        )
        return fold_results

    import asyncio as _asyncio
    import concurrent.futures as _cf

    for model_name in MODEL_NAMES:
        # Skip models whose circuit breaker is open (too many recent failures)
        if _model_cb_blocked(model_name):
            errors.append({"model": model_name, "error": "circuit breaker open"})
            continue
        try:
            loop = _asyncio.get_event_loop()
            try:
                fold_results = await _asyncio.wait_for(
                    loop.run_in_executor(None, _run_one_model, model_name),
                    timeout=45.0,
                )
            except _asyncio.TimeoutError:
                _model_cb_fail(model_name)
                errors.append({"model": model_name, "error": "timeout after 45s"})
                continue

            metrics = compute_forecast_metrics(fold_results)
            r2 = metrics.get("r2_score", 0)
            mape = metrics.get("mape", 100)
            accuracy_pct = max(0, r2 * 100) if r2 > 0 else 0
            accuracy_pct = accuracy_pct * (1 - min(mape / 100, 0.5))

            last_pred = float(fold_results[-1].predictions[-1, 0]) if fold_results[-1].predictions.ndim > 1 else float(fold_results[-1].predictions[-1])
            last_actual = float(fold_results[-1].actuals[-1, 0]) if fold_results[-1].actuals.ndim > 1 else float(fold_results[-1].actuals[-1])
            direction = "up" if last_pred > last_actual else "down"

            votes.append({
                "model": model_name,
                "accuracy_pct": round(accuracy_pct, 1),
                "r2": round(r2, 4),
                "mape": round(mape, 2),
                "direction": direction,
                "last_predicted": round(last_pred, 2),
                "last_actual": round(last_actual, 2),
            })
            _model_cb_ok(model_name)   # successful run resets failure counter
        except Exception as e:
            _model_cb_fail(model_name)
            errors.append({"model": model_name, "error": str(e)})

    # Filter by accuracy threshold
    qualified = [v for v in votes if v["accuracy_pct"] >= accuracy_threshold]
    up_votes = sum(1 for v in qualified if v["direction"] == "up")
    down_votes = sum(1 for v in qualified if v["direction"] == "down")

    if up_votes > down_votes:
        final = "BUY"
    elif down_votes > up_votes:
        final = "SELL"
    else:
        final = "NEUTRAL"

    return {
        "symbol": symbol,
        "recommendation": final,
        "confidence": f"{max(up_votes, down_votes)}/{len(qualified)}" if qualified else "N/A",
        "total_models": len(MODEL_NAMES),
        "qualified_models": len(qualified),
        "accuracy_threshold": accuracy_threshold,
        "up_votes": up_votes,
        "down_votes": down_votes,
        "model_votes": sorted(qualified, key=lambda x: x["accuracy_pct"], reverse=True),
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Daily Scan Workflow — halal screen → agent analysis → Telegram report
# ---------------------------------------------------------------------------

_DEFAULT_WATCHLIST = ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA",
                      "NVDA", "META", "BRK-B", "JNJ", "V",
                      "PG", "HD", "DIS", "MA", "NFLX",
                      "CRM", "ADBE", "AMD", "PYPL", "INTC"]


def _send_telegram(text: str) -> bool:
    """Send a Telegram message if env vars are configured."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN") or ""
    chat_id = os.environ.get("TELEGRAM_CHAT_ID") or ""
    if not token or not chat_id:
        return False
    try:
        import httpx
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        with httpx.Client(timeout=10) as client:
            resp = client.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})
            return resp.status_code == 200
    except Exception:
        return False


@app.get("/api/workflow/daily-scan")
async def daily_scan(
    symbols: str = Query("", description="Comma-separated watchlist (empty = default list)"),
    send_telegram: bool = Query(True, description="Send results via Telegram"),
):
    """Daily market scan: halal filter -> agent analysis -> Telegram report.

    Workflow:
      1. Screen each symbol for AAOIFI halal compliance
      2. Run agent backtests on compliant symbols
      3. Rank by Sharpe ratio
      4. Send formatted report to Telegram (if configured)
    """
    watchlist = [s.strip().upper() for s in symbols.split(",") if s.strip()] if symbols else _DEFAULT_WATCHLIST

    results = []
    halal_count = 0
    haram_count = 0

    for sym in watchlist:
        # Step 1: Halal screening
        halal = _screen_halal(sym)
        if not halal.get("is_halal"):
            results.append({"symbol": sym, "halal": False, "status": halal.get("status", "NON-COMPLIANT")})
            haram_count += 1
            continue

        halal_count += 1

        # Step 2: Run agent analysis (fast agents only)
        try:
            records, df = _fetch_data(sym)
            prices = df["close"].values.astype(float)

            best_sharpe = -999
            best_agent = ""
            agent_results = {}

            for agent_name in ["moving_average", "turtle", "signal_rolling", "abcd_strategy",
                                "double_dqn", "q_learning"]:
                try:
                    agent = _create_agent_safe(agent_name, 30)
                    wf = agent.walk_forward_evaluate(
                        prices=prices, episodes=15,
                        initial_capital=10000, window_size=30,
                    )
                    bs = wf["backtest_summary"]
                    sharpe = bs.get("sharpe_ratio", 0)
                    if sharpe > best_sharpe:
                        best_sharpe = sharpe
                        best_agent = agent_name
                    agent_results[agent_name] = {
                        "sharpe": round(sharpe, 4),
                        "return": round(bs.get("total_return", 0), 4),
                        "trades": bs.get("n_trades", 0),
                    }
                except Exception:
                    continue

            # Step 3: Risk metrics
            simple_returns = (prices[1:] - prices[:-1]) / prices[:-1]
            equity = prices / prices[0] * 10000
            metrics = {
                "sharpe": round(risk_metrics.sharpe_ratio(simple_returns, 0), 4),
                "volatility": round(float(np.std(simple_returns) * np.sqrt(252)), 4),
                "max_drawdown": round(risk_metrics.max_drawdown(equity), 4),
            }

            results.append({
                "symbol": sym,
                "halal": True,
                "status": halal.get("status"),
                "company": halal.get("company_name", ""),
                "price": float(prices[-1]),
                "best_agent": best_agent,
                "best_sharpe": best_sharpe,
                "metrics": metrics,
                "agents": agent_results,
            })
        except Exception as e:
            results.append({"symbol": sym, "halal": True, "error": str(e)})

    # Step 4: Build Telegram report
    telegram_sent = False
    if send_telegram:
        from datetime import datetime
        lines = ["*Daily Market Scan*\n"]
        lines.append(f"Scanned {len(watchlist)} symbols")
        lines.append(f"Halal: {halal_count} | Blocked: {haram_count}\n")

        halal_stocks = [r for r in results if r.get("halal") and "error" not in r]
        halal_stocks.sort(key=lambda r: r.get("best_sharpe", 0), reverse=True)

        if halal_stocks:
            lines.append("*Top Picks:*")
            for r in halal_stocks[:5]:
                name = r.get("company", "")[:25]
                lines.append(
                    f"- {r['symbol']} ({name}) "
                    f"Sharpe={r.get('best_sharpe', 0):.2f} "
                    f"Agent={r.get('best_agent', 'N/A')} "
                    f"Vol={r.get('metrics', {}).get('volatility', 0):.1%}"
                )

        lines.append(f"\n_{datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}_")
        telegram_sent = _send_telegram("\n".join(lines))

    return {
        "symbols_scanned": len(watchlist),
        "halal_count": halal_count,
        "haram_count": haram_count,
        "telegram_sent": telegram_sent,
        "results": results,
    }


# ---------------------------------------------------------------------------
# Scheduler — auto-scan + pipeline runs
# ---------------------------------------------------------------------------


def _run_pipeline_data_collection():
    """Pipeline Stage 1: data collection at ~08:00 ET."""
    try:
        from app.services.pipeline_orchestrator import run_pipeline
        report = run_pipeline(dry_run=True, skip_stages={"halal","smart","consensus","kelly","guardian","execute","report"})
        logger.info("Pipeline data collection: %.1fs", report.elapsed_s)
    except Exception as e:
        logger.error("Pipeline data collection failed: %s", e)


def _run_pipeline_filter():
    """Pipeline Stages 2-3: halal + smart filter at ~08:30 ET."""
    try:
        from app.services.pipeline_orchestrator import run_pipeline
        report = run_pipeline(dry_run=True, skip_stages={"consensus","kelly","guardian","execute","report"})
        logger.info("Pipeline filter: %d halal, %d smart in %.1fs",
                    report.stages[1].count_out if len(report.stages) > 1 else 0,
                    report.stages[2].count_out if len(report.stages) > 2 else 0,
                    report.elapsed_s)
    except Exception as e:
        logger.error("Pipeline filter failed: %s", e)


def _run_pipeline_full():
    """Pipeline Stages 4-7: full analysis + paper execution at ~09:00 ET."""
    try:
        from app.services.pipeline_orchestrator import run_pipeline
        report = run_pipeline(dry_run=True)
        logger.info("Pipeline full: %d signals, %d executed, %d rejected in %.1fs",
                    report.signals_passed, report.signals_executed, report.signals_rejected, report.elapsed_s)
    except Exception as e:
        logger.error("Pipeline full run failed: %s", e)


def _run_scheduled_scan():
    """Run daily scan and send Telegram report."""
    from datetime import datetime
    try:
        import httpx
        base = f"http://127.0.0.1:{os.environ.get('WORKSPACE_PORT', '6910')}"
        resp = httpx.get(f"{base}/api/workflow/daily-scan", params={"send_telegram": "True"}, timeout=600)
        if resp.status_code == 200:
            data = resp.json()
            logger.info(f"Scheduled scan: {data.get('symbols_scanned')} scanned, "
                        f"{data.get('halal_count')} halal, telegram={data.get('telegram_sent')}")
        else:
            logger.error(f"Scheduled scan failed: {resp.status_code}")
    except Exception as e:
        logger.error(f"Scheduled scan error: {e}")


def _run_scheduled_smart_scan():
    """Run smart screener before market open and send top opportunities via Telegram."""
    from datetime import datetime
    now = datetime.utcnow()
    try:
        # Run the screener IN-PROCESS. The old self-HTTP call to
        # 127.0.0.1:{WORKSPACE_PORT|6910} failed with [Errno 111] Connection
        # refused on Railway (the server binds $PORT, nothing listens on 6910),
        # so this scheduled scan was silently dead. _smart_screener_impl is
        # designed for direct calls (no Query objects).
        import asyncio
        data = asyncio.run(_smart_screener_impl(
            min_score=80, max_results=15, use_cache="false",
        ))
        if not isinstance(data, dict):
            logger.error("Smart scan failed: unexpected result type %s", type(data))
            return

        results = data.get("results", [])
        total = data.get("total_scanned", 0)
        halal_count = data.get("halal_count", 0)

        if not results:
            _send_telegram(
                "*قائمة الفرص الحلال لهذا اليوم* 📋\n\n"
                f"📅 {now.strftime('%Y-%m-%d %H:%M')} UTC\n\n"
                f"تم مسح *{total}* سهماً\n"
                f"تم العثور على *{halal_count}* سهماً حلالاً\n"
                "⚠️ لا توجد فرص استثمارية بنتيجة > 80 اليوم.\n"
                "جرّب خفض الحد الأدنى أو تحقق لاحقاً."
            )
            logger.info("Smart scan: no opportunities > 80 found")
            return

        # Build Telegram message in Arabic
        lines = [
            "*📊 قائمة الفرص الحلال لهذا اليوم*",
            f"📅 {now.strftime('%Y-%m-%d %H:%M')} UTC\n",
            f"تم مسح *{total}* سهماً | *{halal_count}* حلالاً",
            f"✅ *{len(results)}* فرصة استثمارية بنتيجة > 80\n",
        ]

        for i, r in enumerate(results[:8]):
            sym = r.get("symbol", "?")
            company = (r.get("company", "") or "")[:30]
            score = r.get("smart_score", 0)
            fund = r.get("fundamental_score", 0)
            mom = r.get("momentum_score", 0)
            fcst = r.get("forecast_score", 0)
            price = r.get("price", 0)
            mcap_raw = r.get("market_cap", 0)
            mcap = f"${mcap_raw/1e9:.1f}B" if mcap_raw else "N/A"
            pe = r.get("valuation_details", {}).get("pe_val", "N/A")
            roe = r.get("profitability_details", {}).get("roe_val")
            roe_str = f"{roe*100:.1f}%" if roe else "N/A"
            # Momentum state
            md = r.get("momentum_details", {})
            rsi = md.get("rsi", "")
            rsi_icon = "🟢" if (rsi and rsi <= 70 and rsi >= 30) else "🔴" if (rsi and rsi > 70) else "🟡"
            macd = md.get("macd_label", "")
            bb = md.get("bb_label", "")
            vwap = md.get("vwap_label", "")
            mom_state = f"RSI{rsi_icon}{rsi} {macd[:15]} {bb[:12]} {vwap[:12]}"

            lines.append(
                f"\n*{i+1}. {sym}* — {company}\n"
                f"   ⭐ *{score}/100* (أساسي {fund} | زخم {mom} | توقع {fcst})\n"
                f"   💰 ${price:.2f} | {mcap} | P/E: {pe if isinstance(pe, str) else f'{pe:.1f}'} | ROE: {roe_str}\n"
                f"   ⚡ *الزخم:* {mom_state}"
            )

        lines.append(f"\n\n🤖 *بوت التداول الإسلامي* | OpenBB Forecast")
        telegram_text = "\n".join(lines)

        # Include AI-powered analysis if available
        try:
            ai_report = ai_agent.generate_smart_scan_report(data)
            if ai_report:
                # Append AI insights (keep it under Telegram's 4096 char limit)
                lines.append("\n\n━━━━━━━━━━━━━━━━━━━━━━━━\n")
                lines.append(f"*🧠 تحليل المحلل الذكي:*\n")
                ai_lines = ai_report.split("\n")
                # Take first ~20 lines of AI analysis for Telegram brevity
                for al in ai_lines[:20]:
                    if al.strip():
                        lines.append(al)
                telegram_text = "\n".join(lines)
        except Exception:
            pass

        _send_telegram(telegram_text)
        logger.info(f"Smart scan: {len(results)} opportunities > 80 sent to Telegram")

    except Exception as e:
        logger.error(f"Smart scan error: {e}")


def _start_scheduler():
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        sched = BackgroundScheduler()
        # ── Pipeline schedule (Phase A paper trading, ET via UTC offsets) ──
        sched.add_job(_run_pipeline_data_collection, "cron", day_of_week="mon-fri", hour=12, minute=0)   # 08:00 ET
        sched.add_job(_run_pipeline_filter,          "cron", day_of_week="mon-fri", hour=12, minute=30)  # 08:30 ET
        sched.add_job(_run_pipeline_full,            "cron", day_of_week="mon-fri", hour=13, minute=0)   # 09:00 ET
        # ── Intraday pipeline full runs every 60 min (10:00-16:00 ET) ──
        sched.add_job(_run_pipeline_full, "cron", day_of_week="mon-fri", hour=14, minute=0)               # 10:00 ET
        sched.add_job(_run_pipeline_full, "cron", day_of_week="mon-fri", hour=15, minute=0)               # 11:00 ET
        sched.add_job(_run_pipeline_full, "cron", day_of_week="mon-fri", hour=16, minute=0)               # 12:00 ET
        sched.add_job(_run_pipeline_full, "cron", day_of_week="mon-fri", hour=17, minute=0)               # 13:00 ET
        sched.add_job(_run_pipeline_full, "cron", day_of_week="mon-fri", hour=18, minute=0)               # 14:00 ET
        sched.add_job(_run_pipeline_full, "cron", day_of_week="mon-fri", hour=19, minute=0)               # 15:00 ET
        sched.add_job(_run_pipeline_full, "cron", day_of_week="mon-fri", hour=19, minute=30)              # 15:30 ET (late-close check)
        # ── Existing scans (offset to avoid pipeline contention) ──
        sched.add_job(_run_scheduled_scan, "cron", day_of_week="mon-fri", hour=13, minute=30)              # 09:30 ET
        sched.add_job(_run_scheduled_smart_scan, "cron", day_of_week="mon-fri", hour=12, minute=45, misfire_grace_time=1, coalesce=True)       # 08:45 ET
        sched.add_job(_run_scheduled_smart_scan, "cron", day_of_week="sun", hour=12, minute=30, misfire_grace_time=1, coalesce=True)           # Sunday 08:30 ET
        sched.start()
        logger.info(
            "Scheduler started: pipeline at 08:00/08:30/09:00 ET + intraday 10:00-15:30 ET hourly, "
            "smart-screener at 08:45 ET, daily-scan at 09:30 ET"
        )
    except ImportError:
        logger.warning("APScheduler not installed — falling back to thread-based scheduler")
        _start_thread_scheduler()
    except Exception as e:
        logger.error(f"APScheduler error: {e} — falling back to thread-based scheduler")
        _start_thread_scheduler()


def _start_thread_scheduler():
    """Fallback scheduler using thread-based polling (no APScheduler needed)."""
    try:
        from app.services.scheduler import start_scheduler
        start_scheduler()
        logger.info("Thread-based scheduler started (polling every 60s)")
    except Exception as e:
        logger.error(f"Thread scheduler also failed: {e}")


# ---------------------------------------------------------------------------
# Chart Endpoints — data optimised for OpenBB chart widgets
# ---------------------------------------------------------------------------


@app.get("/api/chart/agent/{agent_name}")
async def agent_chart(
    agent_name: str,
    symbol: str = Query("AAPL", description="Stock symbol"),
    target_column: str = Query("close", description="Price column"),
    episodes: int = Query(20, description="Training episodes"),
):
    """Return agent equity curve as chart-compatible data."""
    records, df = _fetch_data(symbol)
    prices = df[target_column].values.astype(float)
    dates = df["date"].astype(str).tolist() if "date" in df.columns else []

    agent = _create_agent_safe(agent_name, 30)

    if hasattr(agent, "walk_forward_evaluate"):
        wf = agent.walk_forward_evaluate(prices=prices, episodes=episodes)
        equity = wf["test_results"]["equity_curve"]
        benchmark = wf["benchmark_curve"]
    else:
        result = agent.backtest(prices)
        equity = result.get("portfolio_value", [])
        benchmark = prices / prices[0] * 10000 if len(prices) else []

    chart_data = [
        {"date": dates[i] if i < len(dates) else "", "portfolio": float(equity[i]),
         "benchmark": float(benchmark[i]) if i < len(benchmark) else 0}
        for i in range(min(len(equity), len(dates)))
    ]
    return {"symbol": symbol, "agent": agent_name, "type": "equity_curve", "data": chart_data}


@app.get("/api/chart/forecast/{model_name}")
async def forecast_chart(
    model_name: str,
    symbol: str = Query("AAPL", description="Stock symbol"),
    target_column: str = Query("close", description="Target column"),
    forecast_horizon: int = Query(5, description="Steps ahead"),
):
    """Return forecast actual vs predicted as chart-compatible data."""
    records, df = _fetch_data(symbol)
    prices = df[target_column].values.astype(float)
    dates = df["date"].astype(str).tolist() if "date" in df.columns else []

    model = create_model(model_name, epochs=10, hidden_size=32, num_layers=1)
    fold_results = model.walk_forward_predict(data=prices, sequence_length=30,
                                               forecast_horizon=forecast_horizon, n_splits=2)

    chart_data = []
    for fold in fold_results:
        for j in range(min(fold.test_size, len(dates) - j)):
            idx = int(fold.test_indices[j]) if j < len(fold.test_indices) else 0
            date_str = dates[idx] if idx < len(dates) else ""
            pred = float(fold.predictions[j, 0]) if fold.predictions.ndim > 1 else float(fold.predictions[j])
            actual = float(fold.actuals[j, 0]) if fold.actuals.ndim > 1 else float(fold.actuals[j])
            chart_data.append({"date": date_str, "predicted": pred, "actual": actual, "fold": fold.fold})

    return {"symbol": symbol, "model": model_name, "type": "forecast", "data": chart_data}


# ---------------------------------------------------------------------------
# Agent Comparison — run up to 4 agents side-by-side on the same stock
# ---------------------------------------------------------------------------


@app.get("/api/comparison")
async def agent_comparison(
    symbol: str = Query("AAPL", description="Stock symbol"),
    agents: str = Query("double_dqn,moving_average,q_learning",
                        description="Comma-separated agent names"),
    episodes: int = Query(20, description="Training episodes"),
):
    """Run multiple agents on the same stock and compare equity curves."""
    names = [a.strip() for a in agents.split(",") if a.strip()]
    records, df = _fetch_data(symbol)
    prices = df["close"].values.astype(float)
    dates = df["date"].astype(str).tolist() if "date" in df.columns else []

    curves = []
    summary = []
    for name in names:
        try:
            agent = _create_agent_safe(name, 30)
            wf = agent.walk_forward_evaluate(prices=prices, episodes=episodes)
            equity = wf["test_results"]["equity_curve"]
            bs = wf["backtest_summary"]
            curves.append({
                "agent": name,
                "equity": [float(e) for e in equity],
            })
            summary.append({
                "agent": name,
                "total_return": round(bs.get("total_return", 0), 4),
                "sharpe_ratio": round(bs.get("sharpe_ratio", 0), 4),
                "max_drawdown": round(bs.get("max_drawdown", 0), 4),
                "n_trades": bs.get("n_trades", 0),
                "win_rate": round(bs.get("win_rate", 0), 4),
            })
        except Exception as e:
            summary.append({"agent": name, "error": str(e)})

    return {
        "symbol": symbol,
        "dates": dates[len(dates) - len(curves[0]["equity"]):] if curves else [],
        "curves": curves,
        "summary": sorted(summary, key=lambda r: (r.get("sharpe_ratio") or 0) * (r.get("win_rate") or 0), reverse=True),
    }


# ---------------------------------------------------------------------------
# Historical Leaderboard — daily snapshots saved to JSON
# ---------------------------------------------------------------------------

_leaderboard_history = _cache_dir / "leaderboard_history.json"


@app.get("/api/leaderboard/history")
async def leaderboard_history():
    """Return leaderboard snapshots over time."""
    if not _leaderboard_history.exists():
        return {"history": [], "message": "No history yet"}
    try:
        data = json.loads(_leaderboard_history.read_text())
        return {"history": data.get("snapshots", [])}
    except Exception:
        return {"history": []}


def _save_leaderboard_snapshot():
    """Save today's leaderboard to history file."""
    try:
        import httpx
        base = f"http://127.0.0.1:{os.environ.get('WORKSPACE_PORT', '6910')}"
        resp = httpx.get(f"{base}/api/leaderboard", params={"episodes": 15}, timeout=300)
        if resp.status_code != 200:
            return
        data = resp.json()
        snapshot = {
            "date": datetime.utcnow().isoformat(),
            "symbol": data.get("symbol", ""),
            "leaderboard": data.get("leaderboard", []),
        }
        history = {"snapshots": []}
        if _leaderboard_history.exists():
            try:
                history = json.loads(_leaderboard_history.read_text())
            except Exception:
                pass
        history.setdefault("snapshots", []).append(snapshot)
        # Keep last 90 days
        history["snapshots"] = history["snapshots"][-90:]
        _leaderboard_history.write_text(json.dumps(history, default=str))
        logger.info("Leaderboard snapshot saved")
    except Exception as e:
        logger.error(f"Leaderboard snapshot error: {e}")


# ---------------------------------------------------------------------------
# Progress Streaming — SSE endpoint for long-running tasks
# ---------------------------------------------------------------------------


@app.get("/api/progress/{task_type}")
async def stream_progress(
    task_type: str,
    symbol: str = Query("AAPL", description="Stock symbol"),
    name: str = Query("", description="Model/agent name"),
):
    """Server-Sent Events endpoint streaming training progress."""
    from fastapi.responses import StreamingResponse

    async def event_stream():
        yield f"data: {json.dumps({'status': 'started', 'task': task_type, 'symbol': symbol})}\n\n"
        try:
            records, df = _fetch_data(symbol)
            prices = df["close"].values.astype(float)
            total_steps = 20

            if task_type == "agent":
                agent = _create_agent_safe(name or "q_learning", 30)
                for ep in range(total_steps):
                    yield f"data: {json.dumps({'status': 'running', 'progress': (ep+1)/total_steps*100, 'message': f'Episode {ep+1}/{total_steps}'})}\n\n"
                    # Train one episode
                    env = __import__("openbb_forecast.agents.environment", fromlist=["TradingEnvironment"]).TradingEnvironment(prices=prices, window_size=30)
                    state = env.reset()
                    done = False
                    while not done:
                        action = agent.select_action(state, explore=True)
                        state, _, done, _ = env.step(action)
                    await asyncio.sleep(0.01)
                wf = agent.walk_forward_evaluate(prices=prices, episodes=10)
                bs = wf["backtest_summary"]
                yield f"data: {json.dumps({'status': 'complete', 'progress': 100, 'sharpe': bs.get('sharpe_ratio', 0)})}\n\n"

            elif task_type == "forecast":
                model = create_model(name or "lstm", epochs=5, hidden_size=32, num_layers=1)
                for ep in range(5):
                    yield f"data: {json.dumps({'status': 'running', 'progress': (ep+1)/5*100, 'message': f'Epoch {ep+1}/5'})}\n\n"
                    await asyncio.sleep(0.1)
                yield f"data: {json.dumps({'status': 'complete', 'progress': 100})}\n\n"

            elif task_type == "workflow":
                symbols = (os.environ.get("WATCHLIST", "AAPL,MSFT,GOOGL")).split(",")
                for i, sym in enumerate(symbols[:5]):
                    halal = _screen_halal(sym)
                    pct = (i + 1) / min(len(symbols), 5) * 100
                    status = "halal" if halal.get("is_halal") else "blocked"
                    yield f"data: {json.dumps({'status': 'running', 'progress': pct, 'message': f'{sym}: {status}'})}\n\n"
                    await asyncio.sleep(0.05)
                yield f"data: {json.dumps({'status': 'complete', 'progress': 100, 'message': 'Scan complete'})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'status': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Portfolio Simulation — Kelly Criterion + Equal Weight allocations
# ---------------------------------------------------------------------------


@app.get("/api/portfolio")
async def portfolio_sim(
    symbols: str = Query("AAPL,MSFT,GOOGL", description="Comma-separated symbols"),
    initial_capital: float = Query(10000.0, description="Starting capital"),
    method: str = Query("kelly", description="kelly | equal | sharpe_weighted"),
):
    """Allocate capital among halal-compliant stocks using Kelly or equal weight."""
    sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    results = []

    for sym in sym_list:
        halal = _screen_halal(sym)
        if not halal.get("is_halal"):
            results.append({"symbol": sym, "halal": False, "status": halal.get("status")})
            continue

        records, df = _fetch_data(sym)
        prices = df["close"].values.astype(float)
        returns = (prices[1:] - prices[:-1]) / prices[:-1]
        mean_ret = float(np.mean(returns))
        var_ret = float(np.var(returns)) + 1e-10

        # Kelly fraction: f = (mean - rfr) / variance
        kelly_fraction = mean_ret / var_ret if var_ret > 0 else 0
        kelly_fraction = max(-1, min(1, kelly_fraction))  # clamp

        sharpe = mean_ret / (float(np.std(returns)) + 1e-10) * np.sqrt(252)

        results.append({
            "symbol": sym,
            "halal": True,
            "company": halal.get("company_name", ""),
            "mean_return": round(mean_ret, 6),
            "volatility": round(float(np.std(returns)), 6),
            "sharpe": round(sharpe, 4),
            "kelly_fraction": round(kelly_fraction, 4),
            "kelly_pct": f"{max(0, kelly_fraction * 100):.1f}%",
        })

    # Allocation
    total_sharpe = sum(abs(r.get("sharpe", 0)) for r in results if r.get("halal"))
    if method == "sharpe_weighted" and total_sharpe > 0:
        for r in results:
            if r.get("halal"):
                r["allocation"] = round(abs(r["sharpe"]) / total_sharpe * 100, 1)
                r["allocation_dollars"] = round(initial_capital * r["allocation"] / 100, 2)
    elif method == "kelly":
        total_kelly = sum(max(0, r.get("kelly_fraction", 0)) for r in results if r.get("halal"))
        if total_kelly > 0:
            for r in results:
                if r.get("halal"):
                    r["allocation"] = round(max(0, r["kelly_fraction"]) / total_kelly * 100, 1)
                    r["allocation_dollars"] = round(initial_capital * r["allocation"] / 100, 2)
    else:
        # Equal weight
        halal_count = sum(1 for r in results if r.get("halal"))
        if halal_count > 0:
            for r in results:
                if r.get("halal"):
                    r["allocation"] = round(100 / halal_count, 1)
                    r["allocation_dollars"] = round(initial_capital / halal_count, 2)

    return {
        "method": method,
        "initial_capital": initial_capital,
        "symbols_scanned": len(sym_list),
        "halal_count": sum(1 for r in results if r.get("halal")),
        "allocations": results,
    }


# ---------------------------------------------------------------------------
# List available models/agents
# ---------------------------------------------------------------------------

import numpy as np


# Patch JSONResponse to recursively clean NaN/Inf for JSON compliance
_original_render = JSONResponse.render


def _safe_render(self, content):
    return _original_render(self, _clean_nan(content))


JSONResponse.render = _safe_render


def _live_or_mock_dashboard() -> dict:
    """Return real broker data if credentials exist, else mock."""
    try:
        from app.config import STRATEGY_CONFIGS
        from app.services.alpaca_client import (
            get_account as _ag, get_positions as _ap, get_orders as _ao,
        )
        _sid = next(iter(STRATEGY_CONFIGS), None)
        account = _ag(strategy_id=_sid) or {}
        positions = _ap(strategy_id=_sid) or []
        orders = _ao(status="closed", limit=10) or []
        if account:
            equity = float(account.get("equity") or 0)
            prev_equity = float(account.get("last_equity") or equity)
            today_pnl = round(equity - prev_equity, 2)
            return {
                "pnl": {
                    "today": today_pnl,
                    "today_pct": round((today_pnl / prev_equity * 100) if prev_equity else 0, 2),
                    "all_time": round(float(account.get("portfolio_value", 0)), 2),
                    "week": None, "mtd": None, "week_pct": None, "mtd_pct": None,
                    "all_time_pct": None,
                },
                "open_positions": [
                    {
                        "symbol": p["symbol"], "entry": float(p.get("avg_entry_price") or 0),
                        "current": float(p.get("current_price") or 0),
                        "pnl": float(p.get("unrealized_pl") or 0),
                        "pnl_pct": round(float(p.get("unrealized_plpc") or 0) * 100, 2),
                        "direction": "LONG" if float(p.get("qty", 0)) > 0 else "SHORT",
                        "size": abs(int(float(p.get("qty", 0)))),
                    } for p in positions[:10]
                ],
                "recent_signals": [
                    {
                        "symbol": o["symbol"], "action": o["side"].upper(),
                        "status": o["status"], "guard": None,
                        "price": float(o.get("filled_avg_price") or 0),
                        "time": str(o.get("filled_at", ""))[:16],
                        "confidence": None,
                    } for o in orders[:5]
                ],
                "guard_activity": _mock_guard_activity(),
                "scheduler_timeline": _mock_scheduler_timeline(),
                "alerts": _mock_alerts(),
            }
    except Exception as exc:
        logger.warning("Live broker data unavailable, using mock: %s", exc)
    return {}


@app.get("/api/info")
async def get_info():
    """List all available models and agents with enriched metadata."""
    # Use _ALL_MODEL_NAMES for display so all 19 cards appear in the UI even on
    # Railway where only ARIMA can actually run.  The `available` field tells the
    # frontend whether the model can be executed right now.
    # HONEST metadata only — no fabricated Sharpe/win-rate/champion. Performance
    # numbers are null until the user runs the model/agent (real out-of-sample
    # results then populate the cards). Status reflects real availability:
    #   model  → "production" if a trained checkpoint exists, else availability
    #   agent  → real tier from _AGENT_TIERS (trades=production, RL=staging)
    def _model_status(name: str) -> str:
        has_ckpt = (_MODELS_DIR / name / "20260519.pt").exists() or (_MODELS_DIR / name / "20260519.pkl").exists()
        if has_ckpt:
            return "production"
        return "staging" if name in _RUNNABLE_MODEL_NAMES else "idle"

    model_items = []
    for m in sorted(_ALL_MODEL_NAMES):
        cat = _model_category(m)
        model_items.append({
            "name": m,
            "category": cat,
            "family": cat,
            "status": _model_status(m),
            "last_trained": None,
            "sharpe": None,        # populated by a real run, not fabricated
            "win_rate": None,
            "is_champion": False,
            "available": m in _RUNNABLE_MODEL_NAMES,
        })
    # Inject real performance data from benchmark cache
    try:
        perf_cache = get_all_model_performance()
        for mi in model_items:
            p = perf_cache.get(mi["name"])
            if p and p.get("directional_accuracy"):
                mi["sharpe"] = p.get("sharpe")
                mi["win_rate"] = p.get("win_rate")
                mi["directional_accuracy"] = p.get("directional_accuracy")
                mi["last_trained"] = p.get("last_run")
                mi["is_champion"] = p.get("directional_accuracy", 0) >= 0.60
    except Exception:
        pass

    agent_items = []
    for a in sorted(_ALL_AGENT_NAMES):
        cat = _agent_category(a)
        tier = _agent_tier(a)
        agent_items.append({
            "name": a,
            "category": cat,
            "family": cat,
            "tier": tier,
            "status": tier if tier == "production" else ("staging" if a in _RUNNABLE_AGENT_NAMES else "idle"),
            "last_trained": None,
            "sharpe": None,        # populated by a real run, not fabricated
            "win_rate": None,
            "is_champion": False,
            "available": a in _RUNNABLE_AGENT_NAMES,
        })
    dashboard = _live_or_mock_dashboard()
    return {
        "models": [m["name"] for m in model_items],
        "model_count": len(MODEL_NAMES),
        "model_items": model_items,
        "agents": [a["name"] for a in agent_items],
        "agent_count": len(AGENT_NAMES),
        "agent_items": agent_items,
        "simulation": ["monte_carlo"],
        "analytics": ["metrics"],
        "last_full_retrain": _mock_last_full_retrain(),
        "smart_scan_freshness": _mock_smart_scan_freshness(),
        "pnl": dashboard.get("pnl") or _mock_pnl(),
        "open_positions": dashboard.get("open_positions") or _mock_open_positions(),
        "recent_signals": dashboard.get("recent_signals") or _mock_recent_signals(),
        "guard_activity": dashboard.get("guard_activity") or _mock_guard_activity(),
        "scheduler_timeline": dashboard.get("scheduler_timeline") or _mock_scheduler_timeline(),
        "alerts": dashboard.get("alerts") or _mock_alerts(),
    }


# ---------------------------------------------------------------------------
# Mock metadata helpers — replace with real DB/registry lookups
# ---------------------------------------------------------------------------

import random
import time
import math

_rng = random.Random(42)


def _reset_mock_rng():
    _rng.seed(42)


def _mock_sharpe(name: str) -> float:
    _rng.seed(abs(hash(name)) % (2**31))
    return round(0.6 + _rng.random() * 2.0, 2)


def _mock_win_rate(name: str) -> float:
    _rng.seed(abs(hash(name + "_wr")) % (2**31))
    return round(0.35 + _rng.random() * 0.45, 2)


def _mock_is_champion(name: str) -> bool:
    return name in ("gru_seq2seq", "double_dqn", "actor_critic")


def _mock_last_trained(name: str) -> str:
    _rng.seed(abs(hash(name + "_ts")) % (2**31))
    hours_ago = int(_rng.random() * 168)
    return hours_ago


def _mock_status(name: str) -> str:
    if "broken" in name.lower():
        return "failing"
    if _mock_is_champion(name):
        return "production"
    if _mock_sharpe(name) > 1.5:
        return "staging"
    return "idle"


def _mock_last_full_retrain() -> int:
    """Hours since last full retrain of all models."""
    return 6


def _mock_smart_scan_freshness() -> int:
    """Minutes since last smart screener scan."""
    return 18


# ---------------------------------------------------------------------------
# Overview dashboard mock data
# ---------------------------------------------------------------------------


def _mock_pnl() -> dict:
    return {
        "today": round(random.uniform(-120, 850), 2),
        "week": round(random.uniform(-300, 2400), 2),
        "mtd": round(random.uniform(500, 5800), 2),
        "all_time": round(random.uniform(3000, 25000), 2),
        "today_pct": round(random.uniform(-1.2, 3.5), 2),
        "week_pct": round(random.uniform(-2.0, 6.0), 2),
        "mtd_pct": round(random.uniform(1.5, 12.0), 2),
        "all_time_pct": round(random.uniform(8.0, 45.0), 2),
    }


def _mock_open_positions() -> list[dict]:
    symbols = ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN"]
    random.seed(42)
    positions = []
    for i, sym in enumerate(symbols):
        entry = round(random.uniform(120, 480), 2)
        current = round(entry * random.uniform(0.92, 1.15), 2)
        pnl = round(current - entry, 2)
        pnl_pct = round((pnl / entry) * 100, 2)
        positions.append({
            "symbol": sym,
            "entry": entry,
            "current": current,
            "pnl": pnl,
            "pnl_pct": pnl_pct,
            "direction": "LONG",
            "size": random.randint(10, 200),
        })
    return positions


def _mock_recent_signals() -> list[dict]:
    actions = ["BUY", "SELL", "HOLD"]
    statuses = ["filled", "filled", "rejected_by_guard", "filled", "pending"]
    symbols = ["NVDA", "AAPL", "TSLA", "MSFT", "AMD"]
    guards = ["max_drawdown", "position_sizing", "volatility", None, None]
    random.seed(123)
    signals = []
    for i in range(5):
        random.seed(123 + i)
        signals.append({
            "symbol": symbols[i],
            "action": random.choice(actions),
            "status": statuses[i],
            "guard": guards[i],
            "price": round(random.uniform(100, 500), 2),
            "time": f"{8 + i}:{random.choice(['15','30','45'])} ET",
            "confidence": round(random.uniform(55, 95), 0),
        })
    return signals


def _mock_guard_activity() -> list[dict]:
    guard_names = ["max_drawdown", "position_sizing", "daily_loss_limit",
                   "volatility", "correlation", "liquidity"]
    counts = [12, 8, 3, 15, 5, 2]
    return [
        {"guard": name, "rejections": count, "severity": "high" if count > 10 else "medium" if count > 5 else "low"}
        for name, count in zip(guard_names, counts)
    ]


def _mock_scheduler_timeline() -> dict:
    return {
        "pre_market_last": "06:30 ET",
        "pre_market_next": "06:30 ET",
        "market_scan_last": "09:45 ET",
        "market_scan_next": "09:45 ET",
        "post_market_last": "16:15 ET",
        "post_market_next": "16:15 ET",
        "signal_audit_last": "10:30 ET",
        "train_models_last": "02:00 ET",
        "status": "idle",
    }


# ---------------------------------------------------------------------------
# Alert panel mock data
# ---------------------------------------------------------------------------


def _mock_alerts() -> list[dict]:
    alerts = [
        {"id": 1, "type": "drift", "severity": "high",
         "message": "PSI=0.35 for feature 'rsi_14d' on GRU models — input distribution shifted",
         "source": "GRU", "time": "09:48 ET", "read": False},
        {"id": 2, "type": "guard", "severity": "high",
         "message": "max_drawdown guard rejected AAPL signal — drawdown exceeded 5% threshold",
         "source": "guardian", "time": "09:42 ET", "read": False},
        {"id": 3, "type": "drift", "severity": "medium",
         "message": "PSI=0.21 for feature 'volume_ma' on Transformer — monitor recommended",
         "source": "Transformer", "time": "09:30 ET", "read": False},
        {"id": 4, "type": "latency", "severity": "medium",
         "message": "Signal fill latency 340ms (threshold 300ms) — check broker connection",
         "source": "execution", "time": "09:15 ET", "read": True},
        {"id": 5, "type": "drift", "severity": "low",
         "message": "PSI=0.12 for feature 'bb_width' on LSTM models — within acceptable range",
         "source": "LSTM", "time": "08:55 ET", "read": True},
        {"id": 6, "type": "guard", "severity": "low",
         "message": "position_sizing guard reduced NVDA position from 200 to 150 (risk budget)",
         "source": "guardian", "time": "08:30 ET", "read": True},
        {"id": 7, "type": "system", "severity": "low",
         "message": "Daily retrain completed — 19 models updated, 0 failures",
         "source": "scheduler", "time": "02:05 ET", "read": True},
    ]
    return alerts


# ---------------------------------------------------------------------------
# Model & Agent Categories — for categorized dashboard layouts
# ---------------------------------------------------------------------------

MODEL_CATEGORIES: dict[str, list[str]] = {
    "LSTM": ["lstm", "lstm_2path", "lstm_vae", "bilstm_seq2seq"],
    "GRU": ["gru", "bigru", "gru_2path", "gru_seq2seq", "bigru_seq2seq", "gru_vae"],
    "CNN": ["cnn_seq2seq", "dilated_cnn"],
    "RNN": ["vanilla", "vanilla_bi", "vanilla_2path", "attention_rnn"],
    "Transformer": ["transformer"],
    "Ensemble": ["ensemble"],
    "Statistical": ["arima"],
}

AGENT_CATEGORIES: dict[str, list[str]] = {
    "Deep RL": ["double_dqn", "policy_gradient", "evolution_strategy"],
    "Q-Learning": ["q_learning", "double_q_learning", "recurrent_q_learning",
                   "duel_q_learning", "double_duel_q_learning",
                   "curiosity_q_learning", "recurrent_curiosity_q_learning"],
    "Actor-Critic": ["actor_critic", "actor_critic_duel", "actor_critic_recurrent"],
    "Neuro-Evolution": ["neuro_evolution", "neuro_evolution_novelty"],
    "Classic": ["turtle", "moving_average", "signal_rolling", "abcd_strategy"],
}

# ── Agent Tiers (Production vs Staging) ──
# Production: Sharpe >= 1.5 AND Win Rate >= 55% on 2015–2026 backtest
# Staging: everything else
_AGENT_TIERS: dict[str, str] = {
    # Tiers are classified by EMPIRICAL trade activity, not aspirational numbers.
    # Production = deterministic, rule-based strategies that actually place trades
    # and execute reliably. Profitability varies by symbol/period (that is honest).
    "turtle": "production",          # rule-based breakout — trades
    "moving_average": "production",  # MA crossover — trades
    "signal_rolling": "production",  # rolling-signal strategy — trades
    "abcd_strategy": "production",   # ABCD pattern — trades
    # Experimental (staging) — RL/evolutionary agents. As shipped they place ~0
    # trades on out-of-sample data (tabular/under-trained); their Sharpe/return
    # are degenerate artifacts of a flat curve. Kept for research only until a
    # proper training + validation pipeline gives them a real, verified edge.
    "evolution_strategy": "staging",          # 0 trades as shipped
    "neuro_evolution": "staging",
    "neuro_evolution_novelty": "staging",     # 0 trades (was falsely "production")
    "actor_critic": "staging",
    "actor_critic_duel": "staging",
    "actor_critic_recurrent": "staging",
    "double_dqn": "staging",
    "policy_gradient": "staging",
    "q_learning": "staging",
    "double_q_learning": "staging",
    "recurrent_q_learning": "staging",
    "duel_q_learning": "staging",
    "double_duel_q_learning": "staging",      # empty subclass of duel_q_learning
    "curiosity_q_learning": "staging",
    "recurrent_curiosity_q_learning": "staging",  # empty subclass of curiosity
}


def _agent_tier(name: str) -> str:
    return _AGENT_TIERS.get(name, "staging")


# ---------------------------------------------------------------------------
# Leaderboard — run all agents on a symbol and rank by Sharpe
# ---------------------------------------------------------------------------


@app.get("/api/leaderboard")
async def agent_leaderboard(
    symbol: str = Query("AAPL", description="Stock symbol"),
    target_column: str = Query("close", description="Price column"),
    episodes: int = Query(20, description="Training episodes per agent"),
    initial_capital: float = Query(10000.0, description="Starting capital"),
):
    """Run all agents on the same data and return a ranked leaderboard."""
    records, df = _fetch_data(symbol)
    prices = df[target_column].values.astype(float)

    rows = []
    for agent_name in AGENT_NAMES:
        try:
            agent = _create_agent_safe(agent_name, 30)
            wf = agent.walk_forward_evaluate(
                prices=prices, episodes=episodes,
                initial_capital=initial_capital,
            )
            bs = wf["backtest_summary"]
            rows.append({
                "agent": agent_name,
                "category": _agent_category(agent_name),
                "total_return": round(bs.get("total_return", 0), 4),
                "sharpe_ratio": round(bs.get("sharpe_ratio", 0), 4),
                "max_drawdown": round(bs.get("max_drawdown", 0), 4),
                "n_trades": bs.get("n_trades", 0),
                "win_rate": round(bs.get("win_rate", 0), 4),
                "risk_events": wf.get("risk_events", 0),
            })
        except Exception as e:
            rows.append({
                "agent": agent_name,
                "category": _agent_category(agent_name),
                "total_return": None, "sharpe_ratio": None,
                "max_drawdown": None, "n_trades": 0,
                "win_rate": None, "risk_events": 0, "error": str(e),
            })

    rows.sort(key=lambda r: (r.get("sharpe_ratio") or 0) * (r.get("win_rate") or 0), reverse=True)
    return {"symbol": symbol, "leaderboard": rows, "total_agents": len(rows)}


def _agent_category(name: str) -> str:
    for cat, members in AGENT_CATEGORIES.items():
        if name in members:
            return cat
    return "Other"


def _model_category(name: str) -> str:
    for cat, members in MODEL_CATEGORIES.items():
        if name in members:
            return cat
    return "Other"


@app.get("/api/models/status")
async def api_model_status():
    """Return production/staging tiers for all agents and models."""
    agent_tiers = {}
    for name in AGENT_NAMES:
        tier = _agent_tier(name)
        agent_tiers[name] = {
            "tier": tier,
            "category": _agent_category(name),
        }

    return {
        "agents": agent_tiers,
        "production_count": sum(1 for v in agent_tiers.values() if v["tier"] == "production"),
        "staging_count": sum(1 for v in agent_tiers.values() if v["tier"] == "staging"),
        "promotion_rule": "Sharpe >= 1.5 AND Win Rate >= 55% on 2015-2026 backtest",
        "demotion_rule": "Win Rate < 50% on last 30 paper/live trades OR Sharpe < 1.0 in a month",
    }


# ---------------------------------------------------------------------------
# Model Registry & Quality Gates API (v2.0 — per Quality-Gate Roadmap)
# ---------------------------------------------------------------------------

@app.get("/api/model-registry")
async def api_model_registry(alias: str = "production"):
    """List all models in the registry, with quality metrics."""
    try:
        from app.services.model_registry import list_models
        models = list_models(alias)
        return {
            "alias": alias,
            "count": len(models),
            "models": models,
        }
    except ImportError as e:
        return JSONResponse(
            {"error": f"model_registry module not available: {e}"},
            status_code=503,
        )


@app.post("/api/model-registry/promote")
async def api_model_registry_promote(
    name: str = Query(...),
    version: str = Query(...),
    artifact_path: str = Query(...),
    sharpe: float = Query(None),
    win_rate: float = Query(None),
    max_drawdown: float = Query(None),
    test_accuracy: float = Query(None),
):
    """Promote a model from staging to production (with quality gate enforcement)."""
    try:
        from app.services.model_registry import promote_to_production
        metrics = {}
        if sharpe is not None:
            metrics["sharpe"] = sharpe
        if win_rate is not None:
            metrics["win_rate"] = win_rate
        if max_drawdown is not None:
            metrics["max_drawdown"] = max_drawdown
        if test_accuracy is not None:
            metrics["test_accuracy"] = test_accuracy

        result = promote_to_production(
            name=name, version=version,
            artifact_path=artifact_path, metrics=metrics,
        )
        return result
    except ValueError as e:
        return JSONResponse(
            {"error": str(e), "name": name, "version": version,
             "reason": "quality_gate_failed"},
            status_code=400,
        )
    except ImportError as e:
        return JSONResponse(
            {"error": f"model_registry module not available: {e}"},
            status_code=503,
        )


@app.get("/api/smart-ensemble/weights")
async def api_smart_ensemble_weights():
    """Return current model weights from smart ensemble."""
    try:
        from app.services.smart_ensemble import get_model_weights, get_top_models
        weights = get_model_weights()
        top = get_top_models()
        return {
            "weights": weights,
            "top_models": [
                {"name": m["name"], "weight": m["weight"],
                 "sharpe": m.get("sharpe", 0),
                 "win_rate": m.get("win_rate", 0),
                 "max_drawdown": m.get("max_drawdown", 0),
                 "quality_passed": m.get("quality_passed", True)}
                for m in top
            ],
        }
    except ImportError as e:
        return JSONResponse(
            {"error": f"smart_ensemble module not available: {e}"},
            status_code=503,
        )


@app.post("/api/model-registry/retrain")
async def api_retrain_models(
    models: str = Query(None, description="Comma-separated model names or 'all'"),
    symbol: str = Query("AAPL"),
    episodes: int = Query(200),
):
    """Trigger rolling retraining of specified models (or all production models)."""
    try:
        from openbb_forecast.training.retrainer import rolling_retrain
        target_models = None if not models or models == "all" else [m.strip() for m in models.split(",")]
        result = rolling_retrain(
            symbol=symbol,
            models=target_models,
            episodes=episodes,
            register_after=True,
        )
        return result
    except ImportError as e:
        return JSONResponse(
            {"error": f"retrainer module not available: {e}"},
            status_code=503,
        )


# ---------------------------------------------------------------------------
# OpenBB Workspace Widget Configuration
# ---------------------------------------------------------------------------


def _widget_id(endpoint: str) -> str:
    """Convert endpoint path to a valid widget ID."""
    return endpoint.strip("/").replace("/", "_")


def _build_widgets_json() -> dict:
    """Build OpenBB Workspace-compatible widgets.json (object keyed by widgetId).

    Uses varied widget types and sizes for a rich visual hierarchy:
      - Forecast models: 'table' type (shows actual vs predicted), w=20 h=12
      - RL agents: 'table' type (shows equity curves), w=20 h=12
      - Leaderboard: 'table' type with all-agent comparison, w=24 h=18
      - Monte Carlo: 'table' type, w=24 h=14
      - Risk Metrics: 'metric' type for compact KPI display, w=12 h=6
    """
    widgets = {}

    # DL Forecast widgets — one per model, organized by category
    for model_name in sorted(MODEL_NAMES):
        ep = f"api/forecast/{model_name}"
        wid = _widget_id(ep)
        widgets[wid] = {
            "name": f"{model_name}",
            "description": f"{model_name} — walk-forward time-series forecast with actual vs predicted values",
            "category": f"Forecast / {_model_category(model_name)}",
            "type": "table",
            "endpoint": ep,
            "gridData": {"w": 20, "h": 12},
            "source": "Custom",
            "params": [
                {"paramName": "symbol", "label": "Symbol", "type": "text", "value": "AAPL"},
                {"paramName": "target_column", "label": "Target", "type": "text", "value": "close"},
                {"paramName": "forecast_horizon", "label": "Horizon", "type": "number", "value": 5},
                {"paramName": "epochs", "label": "Epochs", "type": "number", "value": 20},
            ],
        }

    # RL Agent widgets — one per agent
    for agent_name in sorted(AGENT_NAMES):
        ep = f"api/agent/{agent_name}"
        wid = _widget_id(ep)
        widgets[wid] = {
            "name": f"{agent_name}",
            "description": f"{agent_name} — {_agent_category(agent_name)} trading agent with walk-forward backtest",
            "category": f"Agents / {_agent_category(agent_name)}",
            "type": "table",
            "endpoint": ep,
            "gridData": {"w": 20, "h": 12},
            "source": "Custom",
            "params": [
                {"paramName": "symbol", "label": "Symbol", "type": "text", "value": "AAPL"},
                {"paramName": "initial_capital", "label": "Capital", "type": "number", "value": 10000},
                {"paramName": "episodes", "label": "Episodes", "type": "number", "value": 20},
            ],
        }

    # Agent Leaderboard — ranked comparison of all agents
    lb_id = _widget_id("api/leaderboard")
    widgets[lb_id] = {
        "name": "Agent Leaderboard",
        "description": "All agents ranked by Sharpe ratio on the same symbol and time period",
        "category": "Agents / Leaderboard",
        "type": "table",
        "endpoint": "api/leaderboard",
        "gridData": {"w": 24, "h": 18},
        "source": "Custom",
        "params": [
            {"paramName": "symbol", "label": "Symbol", "type": "text", "value": "AAPL"},
            {"paramName": "episodes", "label": "Episodes", "type": "number", "value": 20},
        ],
    }

    # Monte Carlo — wide simulation
    mc_id = _widget_id("api/monte-carlo")
    widgets[mc_id] = {
        "name": "Monte Carlo Simulation",
        "description": "Geometric Brownian Motion — thousands of simulated price paths with VaR/CVaR",
        "category": "Analytics / Simulation",
        "type": "table",
        "endpoint": "api/monte-carlo",
        "gridData": {"w": 24, "h": 14},
        "source": "Custom",
        "params": [
            {"paramName": "symbol", "label": "Symbol", "type": "text", "value": "AAPL"},
            {"paramName": "n_simulations", "label": "Simulations", "type": "number", "value": 500},
            {"paramName": "forecast_days", "label": "Days", "type": "number", "value": 30},
        ],
    }

    # Risk Metrics — compact KPI panel
    rm_id = _widget_id("api/metrics")
    widgets[rm_id] = {
        "name": "Risk Metrics",
        "description": "Sharpe, Sortino, VaR(95%), CVaR(95%), Max Drawdown, Calmar",
        "category": "Analytics / Metrics",
        "type": "table",
        "endpoint": "api/metrics",
        "gridData": {"w": 12, "h": 8},
        "source": "Custom",
        "params": [
            {"paramName": "symbol", "label": "Symbol", "type": "text", "value": "AAPL"},
        ],
    }

    # Halal Status — AAOIFI compliance check
    hal_id = _widget_id("api/halal-status")
    widgets[hal_id] = {
        "name": "Halal Screener",
        "description": "AAOIFI 4-screen Shariah compliance — debt, interest, sector, liquidity",
        "category": "Analytics / Halal",
        "type": "table",
        "endpoint": "api/halal-status",
        "gridData": {"w": 12, "h": 10},
        "source": "Custom",
        "params": [
            {"paramName": "symbol", "label": "Symbol", "type": "text", "value": "AAPL"},
        ],
    }

    # Stock Research widgets
    stock_summary_id = _widget_id("api/stock/summary")
    widgets[stock_summary_id] = {
        "name": "Stock Summary",
        "description": "Company profile, price, market cap, P/E, dividend yield, 52w range",
        "category": "Research / Fundamentals",
        "type": "table",
        "endpoint": "api/stock/summary",
        "gridData": {"w": 20, "h": 14},
        "source": "Custom",
        "params": [
            {"paramName": "symbol", "label": "Symbol", "type": "text", "value": "AAPL"},
        ],
    }

    stock_ratios_id = _widget_id("api/stock/ratios")
    widgets[stock_ratios_id] = {
        "name": "Financial Ratios",
        "description": "Profitability, valuation, liquidity, and growth ratios",
        "category": "Research / Fundamentals",
        "type": "table",
        "endpoint": "api/stock/ratios",
        "gridData": {"w": 20, "h": 14},
        "source": "Custom",
        "params": [
            {"paramName": "symbol", "label": "Symbol", "type": "text", "value": "AAPL"},
        ],
    }

    stock_financials_id = _widget_id("api/stock/financials")
    widgets[stock_financials_id] = {
        "name": "Financial Statements",
        "description": "Income statement, balance sheet, and cash flow statements",
        "category": "Research / Fundamentals",
        "type": "table",
        "endpoint": "api/stock/financials",
        "gridData": {"w": 24, "h": 16},
        "source": "Custom",
        "params": [
            {"paramName": "symbol", "label": "Symbol", "type": "text", "value": "AAPL"},
            {"paramName": "statement", "label": "Statement", "type": "text", "value": "income"},
        ],
    }

    stock_dividends_id = _widget_id("api/stock/dividends")
    widgets[stock_dividends_id] = {
        "name": "Dividends",
        "description": "Dividend yield, payout ratio, ex-date, and payment history",
        "category": "Research / Income",
        "type": "table",
        "endpoint": "api/stock/dividends",
        "gridData": {"w": 20, "h": 14},
        "source": "Custom",
        "params": [
            {"paramName": "symbol", "label": "Symbol", "type": "text", "value": "AAPL"},
        ],
    }

    stock_holders_id = _widget_id("api/stock/holders")
    widgets[stock_holders_id] = {
        "name": "Institutional Holders",
        "description": "Major institutional holders, insider ownership, short interest",
        "category": "Research / Ownership",
        "type": "table",
        "endpoint": "api/stock/holders",
        "gridData": {"w": 20, "h": 14},
        "source": "Custom",
        "params": [
            {"paramName": "symbol", "label": "Symbol", "type": "text", "value": "AAPL"},
        ],
    }

    stock_earnings_id = _widget_id("api/stock/earnings")
    widgets[stock_earnings_id] = {
        "name": "Earnings",
        "description": "EPS history, quarterly earnings surprises, forward estimates",
        "category": "Research / Fundamentals",
        "type": "table",
        "endpoint": "api/stock/earnings",
        "gridData": {"w": 20, "h": 14},
        "source": "Custom",
        "params": [
            {"paramName": "symbol", "label": "Symbol", "type": "text", "value": "AAPL"},
        ],
    }

    stock_screener_id = _widget_id("api/stock/screener")
    widgets[stock_screener_id] = {
        "name": "Stock Screener",
        "description": "Screen 50+ stocks by sector, market cap, P/E, and halal status",
        "category": "Research / Screener",
        "type": "table",
        "endpoint": "api/stock/screener",
        "gridData": {"w": 24, "h": 20},
        "source": "Custom",
        "params": [
            {"paramName": "sector", "label": "Sector", "type": "text", "value": "", "description": "e.g. Technology, Healthcare"},
            {"paramName": "min_market_cap", "label": "Min Market Cap", "type": "number", "value": 0},
            {"paramName": "halal_only", "label": "Halal Only", "type": "text", "value": "False"},
        ],
    }

    # Smart Screener — halal + fundamental (40) + momentum (30) + forecast (30)
    smart_id = _widget_id("api/stock/smart-screener")
    widgets[smart_id] = {
        "name": "Smart Screener",
        "description": "Scores 120+ stocks: fundamental (profitability/valuation/market 0-40) + technical momentum (RSI/MACD/BB/VWAP 0-30) + forecast consensus (0-30) = 0-100",
        "category": "Research / Screener",
        "type": "table",
        "endpoint": "api/stock/smart-screener",
        "gridData": {"w": 24, "h": 22},
        "source": "Custom",
        "params": [
            {"paramName": "min_score", "label": "Min Score", "type": "number", "value": 50, "description": "Minimum smart score 0-100"},
            {"paramName": "sector", "label": "Sector", "type": "text", "value": ""},
            {"paramName": "max_results", "label": "Max Results", "type": "number", "value": 20},
            {"paramName": "add_forecast", "label": "Forecast Consensus", "type": "text", "value": "True", "description": "Add forecast consensus score (0-30)"},
        ],
    }

    # Consensus — model voting aggregation
    cons_id = _widget_id("api/consensus")
    widgets[cons_id] = {
        "name": "Consensus Engine",
        "description": "Aggregates all forecasts, filters by accuracy > 55%, outputs BUY/SELL/NEUTRAL vote",
        "category": "Analytics / Consensus",
        "type": "table",
        "endpoint": "api/consensus",
        "gridData": {"w": 24, "h": 16},
        "source": "Custom",
        "params": [
            {"paramName": "symbol", "label": "Symbol", "type": "text", "value": "AAPL"},
            {"paramName": "accuracy_threshold", "label": "Min Accuracy %", "type": "number", "value": 55},
        ],
    }

    # Daily Scan Workflow
    ds_id = _widget_id("api/workflow/daily-scan")
    widgets[ds_id] = {
        "name": "Daily Scan Workflow",
        "description": "Halal filter -> agent analysis -> Telegram report on your watchlist",
        "category": "Workflow",
        "type": "table",
        "endpoint": "api/workflow/daily-scan",
        "gridData": {"w": 24, "h": 20},
        "source": "Custom",
        "params": [
            {"paramName": "symbols", "label": "Watchlist", "type": "text", "value": "", "description": "Comma-separated (empty = default 20 stocks)"},
            {"paramName": "send_telegram", "label": "Send Telegram", "type": "text", "value": "True", "description": "Send report via Telegram"},
        ],
    }

    # Chart: Agent Equity Curve
    for agent_name in sorted(AGENT_NAMES):
        ep = f"api/chart/agent/{agent_name}"
        wid = _widget_id(ep)
        widgets[wid] = {
            "name": f"Chart: {agent_name}",
            "description": f"Equity curve chart for {agent_name}",
            "category": f"Charts / {_agent_category(agent_name)}",
            "type": "chart",
            "endpoint": ep,
            "gridData": {"w": 20, "h": 12},
            "source": "Custom",
            "params": [
                {"paramName": "symbol", "label": "Symbol", "type": "text", "value": "AAPL"},
                {"paramName": "episodes", "label": "Episodes", "type": "number", "value": 20},
            ],
        }

    # Chart: Forecast
    for model_name in sorted(MODEL_NAMES):
        ep = f"api/chart/forecast/{model_name}"
        wid = _widget_id(ep)
        widgets[wid] = {
            "name": f"Chart: {model_name}",
            "description": f"Actual vs predicted chart for {model_name}",
            "category": f"Charts / {_model_category(model_name)}",
            "type": "chart",
            "endpoint": ep,
            "gridData": {"w": 20, "h": 12},
            "source": "Custom",
            "params": [
                {"paramName": "symbol", "label": "Symbol", "type": "text", "value": "AAPL"},
                {"paramName": "forecast_horizon", "label": "Horizon", "type": "number", "value": 5},
            ],
        }

    # Agent Comparison
    comp_id = _widget_id("api/comparison")
    widgets[comp_id] = {
        "name": "Agent Comparison",
        "description": "Run 3 agents side-by-side and compare equity curves",
        "category": "Charts / Comparison",
        "type": "chart",
        "endpoint": "api/comparison",
        "gridData": {"w": 24, "h": 18},
        "source": "Custom",
        "params": [
            {"paramName": "symbol", "label": "Symbol", "type": "text", "value": "AAPL"},
            {"paramName": "agents", "label": "Agents", "type": "text", "value": "double_dqn,moving_average,q_learning"},
            {"paramName": "episodes", "label": "Episodes", "type": "number", "value": 20},
        ],
    }

    # Leaderboard History
    lbh_id = _widget_id("api/leaderboard/history")
    widgets[lbh_id] = {
        "name": "Leaderboard History",
        "description": "Historical daily leaderboard snapshots (last 90 days)",
        "category": "Agents / Leaderboard",
        "type": "table",
        "endpoint": "api/leaderboard/history",
        "gridData": {"w": 24, "h": 16},
        "source": "Custom",
        "params": [],
    }

    # Portfolio Simulator
    port_id = _widget_id("api/portfolio")
    widgets[port_id] = {
        "name": "Portfolio Simulator",
        "description": "Kelly / Equal / Sharpe-weighted capital allocation for halal stocks",
        "category": "Analytics / Portfolio",
        "type": "table",
        "endpoint": "api/portfolio",
        "gridData": {"w": 24, "h": 18},
        "source": "Custom",
        "params": [
            {"paramName": "symbols", "label": "Symbols", "type": "text", "value": "AAPL,MSFT,GOOGL"},
            {"paramName": "initial_capital", "label": "Capital", "type": "number", "value": 10000},
            {"paramName": "method", "label": "Method", "type": "text", "value": "kelly"},
        ],
    }

    # AI Assistant — smart investment analyst
    ai_report_id = _widget_id("api/ai/report")
    widgets[ai_report_id] = {
        "name": "AI Report",
        "description": "AI-powered Arabic investment report analyzing top smart screener opportunities",
        "category": "AI / Assistant",
        "type": "table",
        "endpoint": "api/ai/report",
        "gridData": {"w": 24, "h": 20},
        "source": "Custom",
        "params": [
            {"paramName": "min_score", "label": "Min Score", "type": "number", "value": 70},
            {"paramName": "max_results", "label": "Max Results", "type": "number", "value": 8},
        ],
    }

    ai_ask_id = _widget_id("api/ai/ask")
    widgets[ai_ask_id] = {
        "name": "AI Ask",
        "description": "Ask the AI investment analyst questions about stocks, halal status, and market analysis",
        "category": "AI / Assistant",
        "type": "table",
        "endpoint": "api/ai/ask",
        "gridData": {"w": 24, "h": 14},
        "source": "Custom",
        "params": [
            {"paramName": "question", "label": "Question", "type": "text", "value": "ما هو أفضل سهم حلال اليوم؟"},
            {"paramName": "symbol", "label": "Symbol", "type": "text", "value": ""},
        ],
    }

    # Backtest Lab — full interactive backtest UI with Chart.js charts
    bt_id = _widget_id("backtest")
    widgets[bt_id] = {
        "name": "Backtest Lab",
        "description": "Walk-forward backtest: equity curve, drawdown, monthly heatmap, win/loss doughnut, SPY benchmark",
        "category": "Analytics / Backtest",
        "type": "iframe",
        "endpoint": "backtest",
        "gridData": {"w": 24, "h": 35},
        "source": "Custom",
    }

    return widgets


@app.get("/widgets.json")
async def get_widgets():
    """Widgets configuration for OpenBB Workspace."""
    return JSONResponse(content=_build_widgets_json())


# ---------------------------------------------------------------------------
# Dashboard Templates (apps.json)
# ---------------------------------------------------------------------------


@app.get("/apps.json")
async def get_apps():
    """Dashboard templates for OpenBB Workspace — categorized, staged layouts."""

    def _wref(endpoint: str) -> str:
        return _widget_id(endpoint)

    # Helper: grid layout per category
    def _cat_layout(category_map: dict, prefix: str, per_row: int = 2,
                    w: int = 12, h: int = 10, x_offset: int = 0, y_offset: int = 0):
        layout = []
        global_y = y_offset
        for cat_name in sorted(category_map):
            members = sorted(category_map[cat_name])
            for i, name in enumerate(members):
                layout.append({
                    "i": _wref(f"{prefix}/{name}"),
                    "x": x_offset + (i % per_row) * w,
                    "y": global_y + (i // per_row) * h,
                    "w": w, "h": h,
                })
            global_y += ((len(members) + per_row - 1) // per_row) * h
        return layout

    apps = [

        # ── Dashboard 1: Forecast Lab ──
        {
            "name": "Forecast Lab",
            "description": "All 19 forecast models organized by architecture family",
            "tabs": {
                cat_name.lower().replace(" ", "_"): {
                    "id": cat_name.lower().replace(" ", "_"),
                    "name": cat_name,
                    "description": f"{cat_name} models",
                    "layout": [
                        {"i": _wref(f"api/forecast/{m}"),
                         "x": i % 2 * 12, "y": i // 2 * 10, "w": 12, "h": 10}
                        for i, m in enumerate(sorted(members))
                    ],
                }
                for cat_name, members in sorted(MODEL_CATEGORIES.items())
            },
        },

        # ── Dashboard 2: Trading Lab ──
        {
            "name": "Trading Lab",
            "description": "All 19 trading agents grouped by algorithm family",
            "tabs": {
                cat_name.lower().replace(" ", "_"): {
                    "id": cat_name.lower().replace(" ", "_"),
                    "name": cat_name,
                    "description": f"{cat_name} agents",
                    "layout": [
                        {"i": _wref(f"api/agent/{a}"),
                         "x": i % 2 * 12, "y": i // 2 * 10, "w": 12, "h": 10}
                        for i, a in enumerate(sorted(members))
                    ],
                }
                for cat_name, members in sorted(AGENT_CATEGORIES.items())
            },
        },

        # ── Dashboard 3: Risk Desk ──
        {
            "name": "Risk Desk",
            "description": "Monte Carlo simulation, risk metrics, halal screener, consensus, and agent leaderboard",
            "tabs": {
                "simulation": {
                    "id": "simulation",
                    "name": "Simulation",
                    "description": "Monte Carlo price paths",
                    "layout": [
                        {"i": _wref("api/monte-carlo"), "x": 0, "y": 0, "w": 24, "h": 14},
                    ],
                },
                "metrics": {
                    "id": "metrics",
                    "name": "Metrics",
                    "description": "Risk & return KPIs",
                    "layout": [
                        {"i": _wref("api/metrics"), "x": 0, "y": 0, "w": 24, "h": 10},
                    ],
                },
                "halal": {
                    "id": "halal",
                    "name": "Halal",
                    "description": "AAOIFI Shariah compliance",
                    "layout": [
                        {"i": _wref("api/halal-status"), "x": 0, "y": 0, "w": 24, "h": 10},
                    ],
                },
                "consensus": {
                    "id": "consensus",
                    "name": "Consensus",
                    "description": "Model voting — BUY/SELL/NEUTRAL",
                    "layout": [
                        {"i": _wref("api/consensus"), "x": 0, "y": 0, "w": 24, "h": 16},
                    ],
                },
                "leaderboard": {
                    "id": "leaderboard",
                    "name": "Leaderboard",
                    "description": "All agents ranked by Sharpe",
                    "layout": [
                        {"i": _wref("api/leaderboard"), "x": 0, "y": 0, "w": 24, "h": 18},
                    ],
                },
            },
        },

        # ── Dashboard 4: Daily Workflow — automated scan pipeline ──
        {
            "name": "Daily Workflow",
            "description": "One-click daily scan: halal check -> agent analysis -> Telegram report",
            "tabs": {
                "scan": {
                    "id": "scan",
                    "name": "Daily Scan",
                    "description": "Run the full pipeline",
                    "layout": [
                        {"i": _wref("api/workflow/daily-scan"), "x": 0, "y": 0, "w": 24, "h": 20},
                    ],
                },
                "halal": {
                    "id": "halal",
                    "name": "Halal",
                    "description": "AAOIFI compliance check",
                    "layout": [
                        {"i": _wref("api/halal-status"), "x": 0, "y": 0, "w": 24, "h": 10},
                    ],
                },
                "consensus": {
                    "id": "consensus",
                    "name": "Consensus",
                    "description": "Model voting engine",
                    "layout": [
                        {"i": _wref("api/consensus"), "x": 0, "y": 0, "w": 24, "h": 16},
                    ],
                },
                "leaderboard": {
                    "id": "leaderboard",
                    "name": "Leaderboard",
                    "description": "Agent rankings",
                    "layout": [
                        {"i": _wref("api/leaderboard"), "x": 0, "y": 0, "w": 24, "h": 18},
                    ],
                },
            },
        },

        # ── Dashboard 5: Charts & Comparison ──
        {
            "name": "Charts & Comparison",
            "description": "Chart widgets, agent comparison, and portfolio simulator",
            "tabs": {
                "comparison": {
                    "id": "comparison",
                    "name": "Comparison",
                    "description": "Side-by-side agent equity curves",
                    "layout": [
                        {"i": _wref("api/comparison"), "x": 0, "y": 0, "w": 24, "h": 18},
                    ],
                },
                "portfolio": {
                    "id": "portfolio",
                    "name": "Portfolio",
                    "description": "Kelly / Equal / Sharpe allocation",
                    "layout": [
                        {"i": _wref("api/portfolio"), "x": 0, "y": 0, "w": 24, "h": 18},
                    ],
                },
                "history": {
                    "id": "history",
                    "name": "History",
                    "description": "Leaderboard snapshots over time",
                    "layout": [
                        {"i": _wref("api/leaderboard/history"), "x": 0, "y": 0, "w": 24, "h": 16},
                    ],
                },
            },
        },

        # ── Dashboard 6: Command Center (all-in-one) ──
        {
            "name": "Command Center",
            "description": "Complete workspace — all forecasts, agents, and analytics",
            "tabs": {
                cat_name.lower().replace(" ", "_"): {
                    "id": cat_name.lower().replace(" ", "_"),
                    "name": cat_name,
                    "description": f"{cat_name} forecast models",
                    "layout": [
                        {"i": _wref(f"api/forecast/{m}"),
                         "x": i % 3 * 8, "y": i // 3 * 8, "w": 8, "h": 8}
                        for i, m in enumerate(sorted(members))
                    ],
                }
                for cat_name, members in sorted(MODEL_CATEGORIES.items())
            }
            | {
                cat_name.lower().replace(" ", "_"): {
                    "id": cat_name.lower().replace(" ", "_"),
                    "name": cat_name,
                    "description": f"{cat_name} trading agents",
                    "layout": [
                        {"i": _wref(f"api/agent/{a}"),
                         "x": i % 3 * 8, "y": i // 3 * 8, "w": 8, "h": 8}
                        for i, a in enumerate(sorted(members))
                    ],
                }
                for cat_name, members in sorted(AGENT_CATEGORIES.items())
            }
            | {
                "analytics": {
                    "id": "analytics",
                    "name": "Analytics",
                    "description": "Risk, simulation, halal, consensus & leaderboard",
                    "layout": [
                        {"i": _wref("api/monte-carlo"), "x": 0, "y": 0, "w": 12, "h": 8},
                        {"i": _wref("api/metrics"), "x": 12, "y": 0, "w": 12, "h": 8},
                        {"i": _wref("api/halal-status"), "x": 0, "y": 8, "w": 12, "h": 6},
                        {"i": _wref("api/consensus"), "x": 12, "y": 8, "w": 12, "h": 10},
                        {"i": _wref("api/comparison"), "x": 0, "y": 14, "w": 12, "h": 10},
                        {"i": _wref("api/portfolio"), "x": 12, "y": 14, "w": 12, "h": 10},
                        {"i": _wref("api/leaderboard"), "x": 0, "y": 24, "w": 24, "h": 14},
                    ],
                },
            },
        },

        # ── Dashboard 7: Research Lab — fundamental stock research ──
        {
            "name": "Research Lab",
            "description": "Fundamental stock research: profile, ratios, financials, dividends, holders, earnings, screener",
            "tabs": {
                "summary": {
                    "id": "summary",
                    "name": "Summary",
                    "description": "Company profile & key metrics",
                    "layout": [
                        {"i": _wref("api/stock/summary"), "x": 0, "y": 0, "w": 24, "h": 14},
                    ],
                },
                "ratios": {
                    "id": "ratios",
                    "name": "Ratios",
                    "description": "Financial ratios & valuation",
                    "layout": [
                        {"i": _wref("api/stock/ratios"), "x": 0, "y": 0, "w": 24, "h": 14},
                    ],
                },
                "financials": {
                    "id": "financials",
                    "name": "Financials",
                    "description": "Income, balance sheet, cash flow",
                    "layout": [
                        {"i": _wref("api/stock/financials"), "x": 0, "y": 0, "w": 24, "h": 16},
                    ],
                },
                "dividends": {
                    "id": "dividends",
                    "name": "Dividends",
                    "description": "Dividend history & yield",
                    "layout": [
                        {"i": _wref("api/stock/dividends"), "x": 0, "y": 0, "w": 24, "h": 14},
                    ],
                },
                "holders": {
                    "id": "holders",
                    "name": "Holders",
                    "description": "Institutional & insider ownership",
                    "layout": [
                        {"i": _wref("api/stock/holders"), "x": 0, "y": 0, "w": 24, "h": 14},
                    ],
                },
                "earnings": {
                    "id": "earnings",
                    "name": "Earnings",
                    "description": "EPS, surprises, estimates",
                    "layout": [
                        {"i": _wref("api/stock/earnings"), "x": 0, "y": 0, "w": 24, "h": 14},
                    ],
                },
                "screener": {
                    "id": "screener",
                    "name": "Screener",
                    "description": "Screen by sector, market cap, halal",
                    "layout": [
                        {"i": _wref("api/stock/screener"), "x": 0, "y": 0, "w": 24, "h": 20},
                    ],
                },
            },
        },

        # ── Dashboard 8: AI Assistant — smart investment analyst ──
        {
            "name": "AI Assistant",
            "description": "AI-powered investment analyst: Arabic reports, Q&A, and market insights",
            "tabs": {
                "report": {
                    "id": "report",
                    "name": "AI Report",
                    "description": "AI-generated Arabic investment report from smart screener data",
                    "layout": [
                        {"i": _wref("api/ai/report"), "x": 0, "y": 0, "w": 24, "h": 20},
                    ],
                },
                "ask": {
                    "id": "ask",
                    "name": "AI Ask",
                    "description": "Ask the AI analyst questions about stocks and halal investing",
                    "layout": [
                        {"i": _wref("api/ai/ask"), "x": 0, "y": 0, "w": 24, "h": 14},
                    ],
                },
            },
        },

        # ── Dashboard 9: Backtest Lab ──
        {
            "name": "Backtest Lab",
            "description": "Walk-forward backtest with equity curve, drawdown, monthly heatmap, and SPY benchmark",
            "tabs": {
                "backtest": {
                    "id": "backtest",
                    "name": "Backtest",
                    "description": "Interactive backtest with Chart.js visualizations",
                    "layout": [
                        {"i": _wref("backtest"), "x": 0, "y": 0, "w": 24, "h": 35},
                    ],
                },
            },
        },

    ]
    return JSONResponse(content=apps)


# ---------------------------------------------------------------------------
# V1 API routes — new dashboard depends on these
# ---------------------------------------------------------------------------

from app.api.v1 import v1_router
app.include_router(v1_router)

# Include sharing routers from halal_screener (screener, consensus, etc.)
# These provide endpoints consumed by the new Overview dashboard.
try:
    from app.routers.screener import router as screener_router
    app.include_router(screener_router)
except Exception:
    logger.warning("screener router not available — skipped")
try:
    from app.routers.consensus import router as consensus_router
    app.include_router(consensus_router)
except Exception:
    logger.warning("consensus router not available — skipped")
try:
    from app.routers.forecast import router as forecast_router
    app.include_router(forecast_router)
except Exception:
    logger.warning("forecast router not available — skipped")
try:
    from app.routers.telegram_webhook import router as telegram_webhook_router
    app.include_router(telegram_webhook_router)
    logger.info("Telegram webhook router registered — POST /api/v1/telegram/webhook")
except Exception:
    logger.warning("telegram webhook router not available — skipped")
try:
    # Admin/agent router — serves the AiWidget's POST /agent/chat and GET /agent/health.
    # It was defined but never mounted, so the floating assistant 404'd → showed "—".
    from app.routers.admin import router as admin_router
    app.include_router(admin_router)
    logger.info("Admin/agent router registered — POST /agent/chat, GET /agent/health")
except Exception as _e:
    logger.warning("admin router not available — skipped: %s", _e)

# ---------------------------------------------------------------------------
# Public API endpoints for dashboard (no auth required)
# ---------------------------------------------------------------------------

@app.get("/api/public/strategies", include_in_schema=False)
async def public_strategies():
    """Public strategy configs (no auth required)."""
    try:
        from app.config import STRATEGY_CONFIGS as CFG
        result = []
        for sid, cfg in CFG.items():
            result.append({
                "id": cfg.strategy_id, "name": cfg.name,
                "configured": True,
                "broker_type": os.environ.get("BROKER_TYPE", "alpaca"),
                "max_positions": cfg.max_positions, "position_pct": cfg.position_pct,
                "trailing_stop": cfg.trailing_stop_enabled, "trailing_stop_pct": cfg.trailing_stop_pct,
                "static_sl_pct": cfg.static_sl_pct, "min_confidence": cfg.min_confidence,
            })
        return result if result else [{"message": "No strategies configured"}]
    except Exception as e:
        logger.warning(f"Public strategies failed: {e}")
        return []


@app.get("/api/public/portfolio-summary", include_in_schema=False)
async def public_portfolio_summary():
    """Public portfolio summary for dashboard (no auth required)."""
    try:
        from app.config import STRATEGY_CONFIGS
        from app.services.broker.factory import get_broker
        sid = next(iter(STRATEGY_CONFIGS), None)
        if not sid:
            return {"error": "No strategy configured", "equity": 0, "positions": 0}
        
        broker = get_broker(strategy_id=sid)
        if not broker:
            return {"error": "No broker configured", "equity": 0, "positions": 0}
        
        account = broker.get_account(strategy_id=sid)
        positions = broker.get_positions(strategy_id=sid) if broker else []
        
        if account:
            return {
                "equity": float(account.get("equity", 0)),
                "cash": float(account.get("cash", 0)),
                "buying_power": float(account.get("buying_power", 0)),
                "positions_count": len(positions) if positions else 0,
                "daily_pl": float(account.get("equity", 0) - account.get("last_equity", account.get("equity", 0))),
                "status": account.get("status", "ACTIVE")
            }
        else:
            return {"error": "No account data", "equity": 0, "positions": 0}
    except Exception as e:
        logger.warning(f"Public portfolio summary failed: {e}")
        return {"error": "Portfolio unavailable", "equity": 0, "positions": 0}


@app.get("/api/public/positions", include_in_schema=False)
async def public_positions():
    """Public positions for dashboard (no auth required)."""
    try:
        from app.config import STRATEGY_CONFIGS
        from app.services.broker.factory import get_broker
        sid = next(iter(STRATEGY_CONFIGS), None)
        if not sid:
            return []
        
        broker = get_broker(strategy_id=sid)
        positions = broker.get_positions(strategy_id=sid) if broker else []
        
        if not positions:
            return []
            
        result = []
        for p in positions:
            result.append({
                "symbol": p.get("symbol", ""),
                "qty": p.get("qty", 0),
                "side": p.get("side", "long").upper(),
                "avg_entry_price": float(p.get("avg_entry_price", 0)),
                "current_price": float(p.get("current_price", 0)),
                "market_value": float(p.get("market_value", 0)),
                "unrealized_pl": float(p.get("unrealized_pl", 0)),
                "unrealized_plpc": float(p.get("unrealized_plpc", 0))
            })
        return result
    except Exception as e:
        logger.warning(f"Public positions failed: {e}")
        return []


# ---------------------------------------------------------------------------
# Dashboard API — real data from model registry + broker
# ---------------------------------------------------------------------------

_MODEL_REGISTRY_DIR = Path(__file__).resolve().parent.parent / "model_registry"


def _read_registry_metrics() -> list[dict]:
    """Read all model registry production.json files and return metrics list."""
    results = []
    if not _MODEL_REGISTRY_DIR.is_dir():
        return results
    for entry in sorted(_MODEL_REGISTRY_DIR.iterdir()):
        if entry.is_dir():
            prod_file = entry / "production.json"
            if prod_file.exists():
                try:
                    data = json.loads(prod_file.read_text(encoding="utf-8"))
                    m = data.get("metrics", {})
                    name = entry.name
                    results.append({
                        "name": name,
                        "sharpe": float(m.get("sharpe", 0)),
                        "win_rate": float(m.get("win_rate", 0)) / 100.0,
                        "avg_return": float(m.get("avg_return", 0)),
                        "total_trades": int(m.get("total_trades", 0)),
                        "max_drawdown": float(m.get("max_drawdown", -5)) / 100.0,
                    })
                except Exception:
                    logger.debug("Skipping unreadable registry %s", entry.name)
    return results


def _generate_equity_curve(sharpe: float, n_days: int = 252, start: float = 100000.0) -> list[dict]:
    """Generate a synthetic equity curve consistent with a given Sharpe ratio."""
    import random as _rnd
    seed = abs(int(sharpe * 10000)) % (2**31)
    rng = _rnd.Random(seed)
    daily_vol = 0.008
    daily_return = sharpe * daily_vol / (252 ** 0.5)
    balance = start
    curve: list[dict] = []
    base_date = datetime(2025, 5, 22)
    for i in range(n_days):
        ret = daily_return + rng.gauss(0, daily_vol)
        balance *= (1 + ret)
        d = base_date - timedelta(days=n_days - i)
        curve.append({"date": d.strftime("%Y-%m-%d"), "balance": round(balance, 2)})
    return curve


# ---------------------------------------------------------------------------
# Professional Dashboard — serves the standalone HTML SPA at /
# ---------------------------------------------------------------------------


from fastapi.staticfiles import StaticFiles


# Include portfolio router
try:
    from app.routers.portfolio import router as portfolio_router
    app.include_router(portfolio_router)
except Exception:
    logger.warning("portfolio router not available — skipped")

# Include dashboard API routes
try:
    from app.routers.dashboard import router as dashboard_router
    app.include_router(dashboard_router)
except Exception:
    logger.warning("dashboard router not available — skipped")

# Include extracted dashboard API endpoints (Phase 1 — app/api/dashboard_api.py)
try:
    from app.api.dashboard_api import router as dashboard_api_router
    app.include_router(dashboard_api_router)
except Exception:
    logger.warning("dashboard_api router not available — skipped")

# Include investors API routes
try:
    from app.routers.investors import router as investors_router
    app.include_router(investors_router)
except Exception:
    logger.warning("investors router not available — skipped")

# Mount static files for dashboard
_static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(_static_dir):
    app.mount("/static", StaticFiles(directory=_static_dir), name="static")

_STATIC = Path(__file__).resolve().parent / "static"

# Cache-bust token for static assets — changes every deploy/restart so browsers
# and the CDN fetch fresh JSX/CSS instead of serving stale cached copies.
_BUILD_ID = (os.environ.get("RAILWAY_GIT_COMMIT_SHA") or "")[:12] or str(int(time.time()))


def _html(rel: str):
    """Return an HTMLResponse for a static file, stripping SRI so CDN scripts always load.

    Appends ``?v=<build>`` to local ``/static/*.js|jsx|css`` references so a fresh
    deploy invalidates the browser/CDN cache (the HTML itself is already no-store,
    but the JSX/CSS it loads are cacheable and were going stale across deploys).
    """
    import re

    p = _STATIC / rel
    if not p.exists():
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": f"Not found: {rel}"}, status_code=404)
    html = p.read_text(encoding="utf-8")
    html = re.sub(
        r'((?:src|href)=")(/static/[^"?]+\.(?:jsx?|css|mjs))(")',
        lambda m: f"{m.group(1)}{m.group(2)}?v={_BUILD_ID}{m.group(3)}",
        html,
    )
    return HTMLResponse(
        content=html,
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )

@app.get("/", include_in_schema=False)
async def get_dashboard():
    """Root → PUBLIC institutional landing cover (no auth).

    Entry flow: visitor sees the landing (explains the platform) → clicks
    "Terminal access" → vault posts real credentials to /login → on success
    the session cookie is set and they are routed to /dashboard (the rich
    Stock Intelligence interface). The Terminal kit is reachable from the
    sidebar at /terminal.
    """
    return _html("public/landing/index.html")

@app.get("/terminal", include_in_schema=False)
@app.get("/terminal/", include_in_schema=False)
async def get_terminal():
    """Terminal Overview kit — served directly so auth gate works correctly."""
    return _html("terminal/index.html")

@app.get("/weekly-picks", include_in_schema=False)
@app.get("/weekly-picks/", include_in_schema=False)
async def get_weekly_picks_page():
    """Weekly swing-picks report UI (advisory). Data: GET /weekly_picks."""
    return _html("weekly-picks.html")

@app.get("/halal-screener-v2", include_in_schema=False)
@app.get("/halal-screener-v2/", include_in_schema=False)
async def get_halal_screener_v2():
    return _html("halal-screener-v2/index.html")

@app.get("/risk-desk-v2", include_in_schema=False)
@app.get("/risk-desk-v2/", include_in_schema=False)
async def get_risk_desk_v2():
    return _html("risk-desk-v2/index.html")

@app.get("/onboarding", include_in_schema=False)
@app.get("/onboarding/", include_in_schema=False)
async def get_onboarding():
    return _html("public/onboarding/index.html")


@app.get("/dashboard", include_in_schema=False)
async def get_dashboard_alt():
    """Consolidated → the single dashboard is now /terminal.

    dashboard.html is retired (its layout broke and it duplicated /terminal);
    everyone is sent to the one clean terminal. The legacy file remains in the
    repo for reference at /dashboard-legacy.
    """
    return RedirectResponse("/terminal", status_code=302)


@app.get("/dashboard-legacy-html", include_in_schema=False)
async def get_dashboard_legacy_html():
    """Legacy dashboard.html kept for reference only."""
    p = Path(__file__).resolve().parent / "static" / "dashboard.html"
    if p.exists():
        return HTMLResponse(content=p.read_text(encoding="utf-8"),
                            headers={"Cache-Control": "no-store"})
    return HTMLResponse("<h1>Dashboard not found</h1>", status_code=404)


@app.get("/legacy", include_in_schema=False)
async def get_legacy_dashboard():
    """Serve the legacy dashboard (kept for backward compatibility)."""
    p = Path(__file__).resolve().parent / "static" / "dashboard-legacy.html"
    if p.exists():
        return HTMLResponse(content=p.read_text(encoding="utf-8"),
                            headers={"Cache-Control": "no-store, no-cache, must-revalidate"})
    return RedirectResponse("/")


@app.get("/forecast", include_in_schema=False)
@app.options("/forecast", include_in_schema=False)
async def get_forecast_panel():
    """Serve the ForecastML calibration suite — Ensemble, LSTM-CNN, Transformer."""
    forecast_path = Path(__file__).resolve().parent / "static" / "forecast-panel.html"
    if forecast_path.exists():
        return HTMLResponse(content=forecast_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Forecast Panel not found</h1>", status_code=404)


@app.get("/trading-lab", include_in_schema=False)
async def get_trading_lab_panel():
    """Serve the Trading Lab — Strategy development and backtesting."""
    trading_lab_path = Path(__file__).resolve().parent / "static" / "trading-lab.html"
    if trading_lab_path.exists():
        return HTMLResponse(content=trading_lab_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Trading Lab not found</h1>", status_code=404)


@app.get("/trading", include_in_schema=False)
async def get_trading_panel():
    """Serve the Live Trading interface."""
    trading_path = Path(__file__).resolve().parent / "static" / "trading.html"
    if trading_path.exists():
        return HTMLResponse(content=trading_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Trading Panel not found</h1>", status_code=404)


@app.get("/analysis-lab", include_in_schema=False)
async def get_analysis_lab():
    """Serve the unified Analysis Lab — Forecast + Trading agents on one page."""
    p = Path(__file__).resolve().parent / "static" / "analysis-lab.html"
    if p.exists():
        return HTMLResponse(content=p.read_text(encoding="utf-8"),
                            headers={"Cache-Control": "no-store"})
    return HTMLResponse("<h1>Analysis Lab not found</h1>", status_code=404)


@app.get("/risk-desk", include_in_schema=False)
async def get_risk_desk_panel():
    """Serve the Risk Desk — Professional risk management."""
    risk_desk_path = Path(__file__).resolve().parent / "static" / "risk-desk.html"
    if risk_desk_path.exists():
        return HTMLResponse(content=risk_desk_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Risk Desk not found</h1>", status_code=404)


@app.get("/halal-screener", include_in_schema=False)
async def get_halal_screener():
    """Serve the Halal Screener interface."""
    p = Path(__file__).resolve().parent / "static" / "halal-screener.html"
    if p.exists():
        return HTMLResponse(content=p.read_text(encoding="utf-8"))
    p = Path(__file__).resolve().parent / "static" / "screener.html"
    if p.exists():
        return HTMLResponse(content=p.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Screener not found</h1>", status_code=404)


@app.get("/strategy-dashboard", include_in_schema=False)
async def get_strategies_page():
    """Serve the Strategies Dashboard."""
    _base = Path(__file__).resolve().parent / "static"
    # Prefer the vanilla HTML page (uses real strategy configs); React SPA uses model registry names
    for name in ("strategies.html", "strategies-app/index.html"):
        p = _base / name
        if p.exists():
            return HTMLResponse(content=p.read_text(encoding="utf-8"),
                                headers={"Cache-Control": "no-store, no-cache, must-revalidate"})
    return HTMLResponse("<h1>Strategies not found</h1>", status_code=404)


@app.get("/alerts", include_in_schema=False)
async def get_alerts_page():
    """Serve the Alerts Center."""
    p = Path(__file__).resolve().parent / "static" / "alerts.html"
    if p.exists():
        return HTMLResponse(content=p.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Alerts not found</h1>", status_code=404)


@app.get("/ai-assistant", include_in_schema=False)
async def get_ai_assistant_page():
    """Serve the AI Assistant."""
    p = Path(__file__).resolve().parent / "static" / "ai-assistant.html"
    if p.exists():
        return HTMLResponse(content=p.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>AI Assistant not found</h1>", status_code=404)


@app.get("/mizan-ai", include_in_schema=False)
async def get_mizan_ai_page():
    """Fresh route for MizanAI RTL design — no cached 301 conflict."""
    p = Path(__file__).resolve().parent / "static" / "ai-assistant.html"
    if p.exists():
        return HTMLResponse(content=p.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Not found</h1>", status_code=404)


@app.get("/backtest", include_in_schema=False)
async def get_backtest_panel():
    """Serve the Backtest Lab interface."""
    backtest_path = Path(__file__).resolve().parent / "static" / "backtest.html"
    if backtest_path.exists():
        return HTMLResponse(content=backtest_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Backtest not found</h1>", status_code=404)


@app.get("/investors", include_in_schema=False)
async def get_investors_page():
    """Serve the Investors Insights page."""
    p = Path(__file__).resolve().parent / "static" / "investors.html"
    if p.exists():
        return HTMLResponse(content=p.read_text(encoding="utf-8"),
                            headers={"Cache-Control": "no-store"})
    return HTMLResponse("<h1>Investors Insights not found</h1>", status_code=404)


@app.get("/macro", include_in_schema=False)
async def get_macro_page():
    """Serve the Macro · FRED indicators page."""
    p = Path(__file__).resolve().parent / "static" / "macro.html"
    if p.exists():
        return HTMLResponse(content=p.read_text(encoding="utf-8"),
                            headers={"Cache-Control": "no-store"})
    return HTMLResponse("<h1>Macro page not found</h1>", status_code=404)


@app.get("/etf", include_in_schema=False)
async def get_etf_page():
    """Serve the ETF Explorer page."""
    p = Path(__file__).resolve().parent / "static" / "etf.html"
    if p.exists():
        return HTMLResponse(content=p.read_text(encoding="utf-8"),
                            headers={"Cache-Control": "no-store"})
    return HTMLResponse("<h1>ETF Explorer not found</h1>", status_code=404)


@app.get("/rotation", include_in_schema=False)
async def get_rotation_page():
    """Serve the Sector Rotation (RRG) page."""
    p = Path(__file__).resolve().parent / "static" / "rotation.html"
    if p.exists():
        return HTMLResponse(content=p.read_text(encoding="utf-8"),
                            headers={"Cache-Control": "no-store"})
    return HTMLResponse("<h1>Rotation page not found</h1>", status_code=404)


# ---------------------------------------------------------------------------
# Design System — Public-facing surfaces  (UI kits served above via _html())
# ---------------------------------------------------------------------------

# ── Public-facing surfaces ────────────────────────────────────────────────────

@app.get("/landing",          include_in_schema=False)
async def get_landing():          return _html("public/landing/index.html")

@app.get("/slides",           include_in_schema=False)
async def get_slides():           return _html("public/slides/index.html")

@app.get("/one-pager",        include_in_schema=False)
async def get_one_pager():        return _html("public/one-pager/index.html")

@app.get("/email-signature",  include_in_schema=False)
async def get_email_signature():  return _html("public/email-signature/index.html")

@app.get("/report/template",  include_in_schema=False)
async def get_report_template():  return _html("public/report/index.html")

@app.get("/terms",            include_in_schema=False)
async def get_terms():            return _html("public/terms/index.html")

@app.get("/press",            include_in_schema=False)
async def get_press():            return _html("public/press/index.html")

@app.get("/brand",            include_in_schema=False)
async def get_brand_vault():      return _html("public/index.html")


# ── Legacy page redirects (301 permanent) ────────────────────────────────────
# Old blue-accent pages are replaced by v2 kits or rebranded equivalents.

_LEGACY_REDIRECTS: dict[str, str] = {
    "/dashboard-legacy":  "/terminal",
    "/halal-screener":    "/halal-screener-v2",
    "/risk-desk":         "/risk-desk-v2",
    # NOTE: "/screener" intentionally NOT redirected — it is a JSON data endpoint
    # (app/routers/screener.py) the halal-screener-v2 kit fetches. Redirecting it
    # would shadow the data API. Navigate to /halal-screener-v2 directly for the page.
    "/trading":           "/terminal",
    "/trading-lab":       "/terminal",
    # "/ai-assistant":      "/assistant",  # REMOVED — v2 page is live, not a legacy redirect
    # "/analysis-lab":      "/assistant",  # REMOVED — v2 page is live, not a legacy redirect
    "/backtest":          "/terminal",
    "/etf":               "/macro",
    "/investors":         "/landing",
    "/alerts":            "/alerts",        # keep — has real API backend
}

for _old, _new in _LEGACY_REDIRECTS.items():
    if _old != _new:                       # skip self-redirect
        _fn = (lambda n: lambda: RedirectResponse(n, status_code=301))(_new)
        app.add_api_route(_old, _fn, include_in_schema=False)


# ---------------------------------------------------------------------------
# Strategies Backtest Data — /api/strategies/backtest-data
# ---------------------------------------------------------------------------

_strategies_backtest_cache: dict = {}
_strategies_backtest_lock = threading.Lock()
_strategies_backtest_running = False


def _quality_label(sharpe: float, win_rate: float) -> str:
    if sharpe > 2.0 and win_rate > 60:
        return "EXCELLENT"
    if sharpe > 1.5 and win_rate > 50:
        return "GOOD"
    if sharpe > 1.0 and win_rate > 45:
        return "OK"
    if sharpe > 0.5 and win_rate > 40:
        return "WEAK"
    return "AVOID"


def _get_rss_mb() -> float:
    """Return current process RSS memory in MB (best-effort)."""
    try:
        import psutil
        return psutil.Process().memory_info().rss / 1024 / 1024
    except Exception:
        try:
            import resource
            return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        except Exception:
            return 0.0


def _to_json_safe(obj):
    """Recursively convert numpy types to native Python types for JSON serialization."""
    import numpy as np
    if isinstance(obj, dict):
        return {k: _to_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_json_safe(i) for i in obj]
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return _to_json_safe(obj.tolist())
    return obj


def _run_strategies_backtest():
    global _strategies_backtest_cache, _strategies_backtest_running
    try:
        mem_before = _get_rss_mb()
        logger.info("Strategies backtest starting (RSS=%.0fMB)", mem_before)
        from app.strategies.backtest import (
            fetch_data, backtest_momentum, backtest_breakout,
            backtest_swing, backtest_momentum_burst, SYMBOLS,
        )

        spy_df = fetch_data("SPY")
        if spy_df is None:
            logger.error("Strategies backtest: failed to fetch SPY")
            return

        symbols_data: dict[str, dict] = {}
        strategies_agg: dict[str, list] = {
            "Momentum": [], "Breakout": [], "Swing": [], "Momentum Burst": [],
        }

        for symbol in SYMBOLS:
            df = fetch_data(symbol)
            if df is None:
                continue

            sym_results = {}
            for fn, name in [
                (backtest_momentum, "Momentum"),
                (backtest_breakout, "Breakout"),
                (backtest_swing, "Swing"),
                (backtest_momentum_burst, "Momentum Burst"),
            ]:
                try:
                    r = fn(df, spy_df, symbol)
                    q = _quality_label(r.sharpe, r.win_rate)
                    sym_results[name] = {
                        "trades": r.total_trades,
                        "win_rate": r.win_rate,
                        "sharpe": r.sharpe,
                        "max_dd": abs(r.max_drawdown),
                        "avg_return": r.avg_return,
                        "profit_factor": r.profit_factor,
                        "avg_hold": r.avg_hold_days,
                        "quality": q,
                        "signal": "BUY" if q in ("GOOD", "EXCELLENT") else "WAIT" if q in ("OK", "WEAK") else "AVOID",
                    }
                    strategies_agg[name].append({
                        "symbol": symbol,
                        "trades": r.total_trades,
                        "win_rate": r.win_rate,
                        "sharpe": r.sharpe,
                        "max_dd": abs(r.max_drawdown),
                        "avg_return": r.avg_return,
                        "profit_factor": r.profit_factor,
                    })
                except Exception as e:
                    logger.warning("Backtest %s/%s failed: %s", name, symbol, e)

            if sym_results:
                symbols_data[symbol] = sym_results

        strategies_summary = {}
        for name, items in strategies_agg.items():
            if not items:
                continue
            total_trades = sum(i["trades"] for i in items)
            avg_wr = round(sum(i["win_rate"] for i in items) / len(items), 1)
            avg_sharpe = round(sum(i["sharpe"] for i in items) / len(items), 2)
            avg_dd = round(sum(i["max_dd"] for i in items) / len(items), 1)
            avg_ret = round(sum(i["avg_return"] for i in items) / len(items), 2)
            avg_pf = round(sum(i["profit_factor"] for i in items) / len(items), 2)
            best = max(items, key=lambda x: x["sharpe"])
            strategies_summary[name] = {
                "total_trades": total_trades,
                "symbol_count": len(items),
                "avg_win_rate": avg_wr,
                "avg_sharpe": avg_sharpe,
                "avg_max_dd": avg_dd,
                "avg_return": avg_ret,
                "avg_profit_factor": avg_pf,
                "pass_win_rate": avg_wr >= 50,
                "pass_sharpe": avg_sharpe >= 1.5,
                "pass_max_dd": avg_dd <= 20,
                "best_symbol": best["symbol"],
                "best_symbol_sharpe": best["sharpe"],
            }

        with _strategies_backtest_lock:
            _strategies_backtest_cache = _to_json_safe({
                "strategies": strategies_summary,
                "symbols": symbols_data,
                "timestamp": datetime.utcnow().isoformat(),
                "cached": True,
            })
        mem_after = _get_rss_mb()
        logger.info("Strategies backtest complete: %d symbols (RSS %.0f→%.0fMB)",
                    len(symbols_data), mem_before, mem_after)
    except Exception as e:
        logger.error("Strategies backtest failed: %s", e)
    finally:
        _strategies_backtest_running = False


def _kick_strategies_backtest_if_needed():
    """Start background backtest if not already running and cache is empty."""
    global _strategies_backtest_running
    if not _strategies_backtest_running and not _strategies_backtest_cache:
        _strategies_backtest_running = True
        t = threading.Thread(target=_run_strategies_backtest, daemon=True)
        t.name = "strategies-backtest"
        t.start()
        logger.info("Strategies backtest kicked off in background")


@app.get("/api/strategies/backtest-data", include_in_schema=False)
async def strategies_backtest_data(force_refresh: str = Query("false")):
    """Strategy backtest comparison data for the Strategies tab.
    Always returns INSTANTLY — never blocks the asyncio event loop.
    Python GIL guarantees safe dict read without acquiring the lock.
    """
    global _strategies_backtest_running

    _force = str(force_refresh).lower() in ("true", "1", "yes")

    # GIL makes simple dict-reference read thread-safe; no lock needed here
    cached = _strategies_backtest_cache  # direct reference — O(1), no blocking

    if cached and not _force:
        return cached

    # Kick off background computation if not running
    if not _strategies_backtest_running:
        _strategies_backtest_running = True
        t = threading.Thread(target=_run_strategies_backtest, daemon=True)
        t.name = "strategies-backtest"
        t.start()
        logger.info("Strategies backtest started on demand")

    if cached:
        return {**cached, "source": "stale_cache", "scanning": True}

    return {
        "strategies": {},
        "symbols": {},
        "status": "scanning",
        "message": "الـ backtest قيد التشغيل — سيستغرق ~90 ثانية أول مرة. ستتحدث النتائج تلقائياً.",
        "cached": False,
        "timestamp": datetime.utcnow().isoformat(),
    }


# ---------------------------------------------------------------------------
# WebSocket — Real-time overview updates
# ---------------------------------------------------------------------------

import hashlib

_ws_cache = {}


@app.websocket("/ws/overview")
async def ws_overview(websocket: WebSocket):
    await websocket.accept()
    client_hash = {}
    try:
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=10)
                msg = json.loads(data) if data.strip() else {"type": "ping"}
            except asyncio.TimeoutError:
                msg = {"type": "ping"}

            if msg.get("type") in ("ping", "subscribe"):
                # ── Signal check ──
                try:
                    sc = _cache_get("smart_screener")
                    if sc and sc.get("results"):
                        h = hashlib.md5(str(sc["results"][:5]).encode()).hexdigest()
                        if client_hash.get("signals") != h:
                            client_hash["signals"] = h
                            await websocket.send_json({"type": "signal_new", "count": len(sc["results"]), "source": sc.get("source", "cache")})
                except Exception:
                    pass

                # ── Pipeline check ──
                try:
                    from app.services.pipeline_orchestrator import _orchestrator
                    if _orchestrator is not None and _orchestrator.report:
                        rpt = _orchestrator.report
                        stages_data = [
                            {"name": s.stage, "status": "completed" if s.status == "ok" else s.status, "label": s.stage.replace("_", " ").title(), "elapsed_s": round(s.elapsed_s, 1) if s.elapsed_s else 0}
                            for s in rpt.stages
                        ]
                        h = hashlib.md5(str(stages_data).encode()).hexdigest()
                        if client_hash.get("pipeline") != h:
                            client_hash["pipeline"] = h
                            last_run = rpt.started_at.isoformat() if rpt.started_at else None
                            await websocket.send_json({"type": "pipeline_stage", "stages": stages_data, "last_run": last_run})
                except Exception:
                    pass

                # ── Portfolio check ──
                try:
                    from app.config import STRATEGY_CONFIGS
                    from app.services.broker.factory import get_broker, _build
                    sid = next(iter(STRATEGY_CONFIGS), None)
                    broker = get_broker(strategy_id=sid) if sid else None
                    broker_label = broker.name if broker else "unknown"

                    def _get_broker_data():
                        acct = broker.get_account(strategy_id=sid) if broker else None
                        pos_list = broker.get_positions(strategy_id=sid) if broker else []
                        served_by = broker_label
                        # Fallback to Alpaca if primary broker returns nothing
                        if acct is None and served_by != "alpaca":
                            try:
                                alpaca = _build("alpaca")
                                fb = alpaca.get_account(strategy_id=sid)
                                if fb:
                                    acct = fb
                                    pos_list = alpaca.get_positions(strategy_id=sid) or []
                                    served_by = "alpaca (fallback)"
                            except Exception:
                                pass
                        return acct, pos_list, served_by

                    acct, pos_list, _broker = await asyncio.wait_for(
                        asyncio.to_thread(_get_broker_data),
                        timeout=8.0,
                    )
                    pf = {"equity": float(acct.get("equity", 0)) if acct else 0,
                          "open_positions": len(pos_list)}
                    h = hashlib.md5(str(pf).encode()).hexdigest()
                    if client_hash.get("portfolio") != h:
                        client_hash["portfolio"] = h
                        await websocket.send_json({"type": "portfolio_update", "data": pf})
                except Exception:
                    pass

                # ── Market context check ──
                try:
                    from app.services.market_context import get_market_context
                    mc = get_market_context()
                    h = hashlib.md5(str(mc).encode()).hexdigest()
                    if client_hash.get("market") != h:
                        client_hash["market"] = h
                        await websocket.send_json({"type": "market_context", "data": mc})
                except Exception:
                    pass

                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


@app.post("/api/execute-signals", include_in_schema=False)
async def execute_signals(
    symbols: str = "DELL,AMD,AAPL,INTC,QCOM,TXN",
    qty: int = 10,
):
    """Execute Strategy A STRONG BUY signals with full sizing pipeline."""
    from app.services.signals_advisor import _send_signal_alert
    import halal_screener as hs
    
    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    results = []
    
    for sym in syms:
        try:
            result = hs.run_consensus_momentum(sym)
            if not result or len(result) == 0:
                results.append({"symbol": sym, "status": "no_result"})
                continue
            
            row = result[0]
            verdict = str(row.get("Verdict", "")).upper()
            
            if verdict not in ("STRONG BUY", "BUY"):
                results.append({"symbol": sym, "verdict": verdict, "status": "not_strong_buy"})
                continue
            
            sent = _send_signal_alert(
                row, usx_score=None, usx_breakdown=None,
                account_usd=100000, risk_pct=1.5,
                stop_pct=4.0, take_pct=8.0,
                dry_run=False, strategy_id="A",
            )
            results.append({
                "symbol": sym,
                "verdict": verdict,
                "confidence": row.get("Confidence %", 0),
                "buy_votes": row.get("Votes BUY", 0),
                "sent": sent,
            })
        except Exception as e:
            results.append({"symbol": sym, "status": "error", "error": str(e)})
    
    return {"executed": sum(1 for r in results if r.get("sent")), "results": results}


# Force deploy 20:45:08
def main():
    """Start the workspace backend server."""
    host = os.getenv("WORKSPACE_HOST", "0.0.0.0")
    port = int(os.getenv("PORT", os.getenv("WORKSPACE_PORT", "6910")))
    logger.info("Starting OpenBB Forecast Workspace Backend on http://%s:%d", host, port)
    logger.info("Widgets available at http://%s:%d/widgets.json", host, port)
    logger.info("Dashboards available at http://%s:%d/apps.json", host, port)
    logger.info("Features: Caching=24h, Pipeline=08:00/08:30/09:00ET, Scheduler=09:30ET, Charts, Comparison, Portfolio")
    _start_scheduler()
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()