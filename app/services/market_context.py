"""Market context indicators — VIX, SPY Regime, Breadth, HY/IG Credit, Liquidity.

All functions cache results for _CONTEXT_CACHE_TTL seconds (default 5 min).
Call ``get_market_context(force_refresh=True)`` to bypass cache.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from datetime import datetime, timezone
from typing import Callable, Optional

import numpy as np
import pandas as pd

from app.services.regime import get_regime

logger = logging.getLogger("screener")

# ── Cache ──
_context_cache: dict = {}
_context_cache_ts: float = 0.0
_CONTEXT_CACHE_TTL = 900  # 15 min — yfinance is slow & rate-limited
_cache_lock = threading.Lock()
_refresh_in_flight = threading.Lock()

# Bounded executor for slow external calls so a stalled fetch can't pin the event loop.
_yf_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="mc-ctx")


def _run_with_timeout(fn: Callable, timeout: float, fallback):
    """Run fn() in a worker thread; return fallback on timeout or exception."""
    future = _yf_executor.submit(fn)
    try:
        return future.result(timeout=timeout)
    except FuturesTimeout:
        logger.warning("yfinance call %s timed out after %.1fs", getattr(fn, "__name__", "?"), timeout)
        future.cancel()
        return fallback
    except Exception as e:
        logger.warning("yfinance call %s failed: %s", getattr(fn, "__name__", "?"), e)
        return fallback


def _cache_valid() -> bool:
    return time.time() - _context_cache_ts < _CONTEXT_CACHE_TTL


# ── VIX ──


def _classify_vix(vix: float) -> str:
    if vix < 15:
        return "low_fear"
    if vix < 20:
        return "normal"
    if vix < 30:
        return "elevated"
    return "extreme_fear"


def _fetch_vix_close() -> Optional[float]:
    """Fetch ^VIX close — FMP primary (works on servers), regime.vix fallback."""
    try:
        from app.services.fmp_client import fmp_client
        vix = fmp_client.get_vix()
        if vix is not None and vix > 0:
            return vix
    except Exception:
        pass
    # Local fallback: yfinance (works without server restrictions)
    try:
        import yfinance as yf
        ticker = yf.Ticker("^VIX")
        hist = ticker.history(period="5d")
        if hist is not None and not hist.empty and "Close" in hist.columns:
            return float(hist["Close"].iloc[-1])
    except Exception:
        pass
    regime = get_regime()
    return regime.vix if regime.vix != 0.0 else None


def _fetch_vix_series() -> Optional[list[float]]:
    """Fetch 1y ^VIX close history — FMP primary."""
    try:
        from app.services.fmp_client import fmp_client
        series = fmp_client.get_vix_history(365)
        if series is not None and len(series) > 1:
            return [float(v) for v in series.values]
    except Exception:
        pass
    # Local fallback: yfinance
    try:
        import yfinance as yf
        ticker = yf.Ticker("^VIX")
        hist = ticker.history(period="1y")
        if hist is not None and not hist.empty and "Close" in hist.columns:
            return [float(v) for v in hist["Close"].dropna().values]
    except Exception:
        pass
    return None


def get_vix_context() -> dict:
    """VIX raw value + percentile + classification."""
    vix = _fetch_vix_close()
    if vix is None:
        return {"vix": None, "vix_pctile": None, "classification": "unknown"}

    series = _fetch_vix_series()
    if series and len(series) > 1:
        vix_pctile = float(np.sum(np.array(series) <= vix) / len(series) * 100.0)
    else:
        vix_pctile = get_regime().vix_pctile

    return {
        "vix": round(vix, 2),
        "vix_pctile": round(vix_pctile, 1),
        "classification": _classify_vix(vix),
    }


# ── SPY Regime (EMA-based) ──


def _ema_series(close: np.ndarray, span: int) -> np.ndarray:
    alpha = 2.0 / (span + 1.0)
    out = np.empty_like(close)
    out[0] = close[0]
    for i in range(1, len(close)):
        out[i] = alpha * close[i] + (1.0 - alpha) * out[i - 1]
    return out


def get_spy_regime() -> dict:
    """SPY regime: BULL / BEAR / NEUTRAL via EMA 20/50/200."""
    df = _yf_etf_df("SPY", period="2y")
    if df is None or len(df) < 200:
        return {"regime": "unknown", "price": None, "ema_20": None, "ema_50": None, "ema_200": None}

    close = df["close"].astype(float).values
    price = float(close[-1])
    ema_20 = float(_ema_series(close, 20)[-1])
    ema_50 = float(_ema_series(close, 50)[-1])
    ema_200 = float(_ema_series(close, 200)[-1])

    if price > ema_200 and ema_20 > ema_50:
        regime = "bull"
    elif price < ema_200:
        regime = "bear"
    else:
        regime = "neutral"

    return {
        "regime": regime,
        "price": round(price, 2),
        "ema_20": round(ema_20, 2),
        "ema_50": round(ema_50, 2),
        "ema_200": round(ema_200, 2),
        "price_vs_ema200_pct": round((price / ema_200 - 1) * 100, 2),
    }


# ── Market Breadth ──


def _yf_safe(symbol: str) -> str:
    """Convert ticker to yFinance-safe format (BRK.B → BRK-B)."""
    return symbol.replace(".", "-")


def get_market_breadth() -> dict:
    """% of halal symbols with close above EMA 50 — individual market_data.fetch() calls."""
    from app.services.universe import HALAL_STOCKS_FALLBACK
    from app.services.market_data import fetch as _md_fetch
    from concurrent.futures import ThreadPoolExecutor, as_completed as _as_completed

    symbols = list(HALAL_STOCKS_FALLBACK)[:30]

    def _fetch_close(sym: str):
        try:
            df = _md_fetch(sym, period="3mo")
            if df is not None and not df.empty and "close" in df.columns:
                return sym, df["close"].astype(float)
        except Exception:
            pass
        return sym, None

    above = 0
    total = 0
    with ThreadPoolExecutor(max_workers=6, thread_name_prefix="breadth") as pool:
        futures = {pool.submit(_fetch_close, sym): sym for sym in symbols}
        for f in _as_completed(futures):
            _, close = f.result()
            if close is None or len(close) < 50:
                continue
            total += 1
            ema_50 = close.ewm(span=50, adjust=False).mean().iloc[-1]
            if close.iloc[-1] > ema_50:
                above += 1

    if total == 0:
        return {"breadth_pct": None, "above": 0, "total": 0, "error": "no_data"}

    pct = round(above / total * 100, 1)
    return {
        "breadth_pct": pct,
        "above": above,
        "total": total,
        "classification": "broad_rally" if pct >= 60 else ("mixed" if pct >= 40 else "weak"),
    }


# ── HY/IG Credit Ratio ──


def _yf_etf_df(ticker: str, period: str = "1mo") -> Optional[pd.DataFrame]:
    """Fetch ETF/stock OHLCV via market_data.fetch() (Alpaca-first, yfinance fallback)."""
    try:
        from app.services.market_data import fetch as _md_fetch
        df = _md_fetch(ticker, period=period)
        if df is not None and not df.empty and "close" in df.columns:
            return df  # already has lowercase close + volume columns
    except Exception:
        pass
    # Local fallback: yfinance (for indices etc. Alpaca may not cover)
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        hist = t.history(period=period)
        if hist is not None and not hist.empty and "Close" in hist.columns:
            return pd.DataFrame({
                "close": hist["Close"].astype(float),
                "volume": hist.get("Volume", pd.Series(0, index=hist.index)).astype(float),
            })
    except Exception:
        pass
    return None


def get_credit_ratio() -> dict:
    """HYG/LQD ratio — rising = credit OK, falling = credit stress."""
    hyg = _yf_etf_df("HYG", period="1mo")
    lqd = _yf_etf_df("LQD", period="1mo")

    if hyg is None or lqd is None or len(hyg) < 2 or len(lqd) < 2:
        return {"ratio": None, "daily_change_pct": None, "classification": "unknown"}

    hyg_close = hyg["close"].astype(float)
    lqd_close = lqd["close"].astype(float)

    ratio = float(hyg_close.iloc[-1] / lqd_close.iloc[-1])
    prev_ratio = float(hyg_close.iloc[-2] / lqd_close.iloc[-2])
    daily_change = (ratio / prev_ratio - 1) * 100

    if daily_change < -0.5:
        classification = "stress"
    elif daily_change < 0:
        classification = "slight_stress"
    else:
        classification = "ok"

    return {
        "ratio": round(ratio, 4),
        "daily_change_pct": round(daily_change, 2),
        "classification": classification,
        "hyg_price": round(float(hyg_close.iloc[-1]), 2),
        "lqd_price": round(float(lqd_close.iloc[-1]), 2),
    }


# ── Liquidity Index ──


def get_liquidity_index() -> dict:
    """Volume comparison using SPY as proxy."""
    df = _yf_etf_df("SPY", period="2mo")
    if df is None or len(df) < 20:
        return {"liquidity_pct": None, "classification": "unknown"}

    volume = df["volume"].astype(float)
    current_vol = float(volume.iloc[-1])
    avg_vol = float(volume.iloc[-20:].mean())
    ratio = current_vol / avg_vol if avg_vol > 0 else 1.0

    if ratio < 0.7:
        classification = "low"
    elif ratio < 0.9:
        classification = "reduced"
    else:
        classification = "normal"

    return {
        "liquidity_pct": round(ratio * 100, 1),
        "classification": classification,
        "current_volume": int(current_vol),
        "avg_volume_20d": int(avg_vol),
    }


# ── Market Status Classification (USX PRO V4.1 style) ──


_MARKET_STATUS_CACHE: dict = {}
_MARKET_STATUS_TS: float = 0.0


def get_market_status(force_refresh: bool = False) -> dict:
    """USX PRO V4.1 Market Status: RISK ON / CAUTION / CREDIT STRESS / EXTREME FEAR.

    Combines VIX percentile + HY/IG credit trend to classify overall market.
    Returns status label, VIX%, HY/IG ratio, and score gate levels.
    """
    global _MARKET_STATUS_CACHE, _MARKET_STATUS_TS
    now = time.time()
    if not force_refresh and now - _MARKET_STATUS_TS < _CONTEXT_CACHE_TTL:
        return dict(_MARKET_STATUS_CACHE)

    vix_ctx = get_vix_context()
    credit = get_credit_ratio()
    vix_val = vix_ctx.get("vix") or 0
    vix_pct = vix_ctx.get("vix_pctile") or 0
    credit_cls = credit.get("classification", "ok")
    credit_change = credit.get("daily_change_pct") or 0

    # Classification rules
    if vix_val > 70:
        status = "EXTREME FEAR"
    elif vix_val > 50 or (vix_pct > 85 and credit_cls == "stress"):
        # P-Debug.1 — raised pctile threshold 70→85: VIX=17 + pctile=85 = relatively calm
        status = "CREDIT STRESS"
    elif vix_val > 30 or vix_pct > 65:
        # Raised CAUTION threshold 50→65: avoid false CAUTION in normal markets
        status = "CAUTION"
    else:
        status = "RISK ON"

    logger.info(
        "Market status classified: %s (VIX=%.1f, pctile=%.0f%%, credit=%s)",
        status, vix_val, vix_pct, credit_cls,
    )

    # ── Regime-aware gate calibration ───────────────────────────────────────
    # Base gates come from market stress (VIX + credit).
    # A regime delta is then applied so that:
    #   BULL  → thresholds drop  (-5 pts)  — market trend supports more signals
    #   NEUTRAL → no change      (±0)
    #   BEAR  → thresholds rise  (+7 pts)  — only high-conviction signals pass
    # This creates a 4 × 3 matrix (stress × trend) without touching any other code.
    # ─────────────────────────────────────────────────────────────────────────
    _base_gates = {
        "RISK ON":       {"min_gate": 50, "strong_gate": 65},
        "CAUTION":       {"min_gate": 55, "strong_gate": 70},
        "CREDIT STRESS": {"min_gate": 60, "strong_gate": 75},
        "EXTREME FEAR":  {"min_gate": 99, "strong_gate": 99, "halt": True},
    }
    _regime_delta = {
        "BULL":    (-5, -5),   # lower gates — trend in our favour
        "NEUTRAL": ( 0,  0),   # unchanged
        "BEAR":    (+7, +7),   # raise gates — demand higher conviction
    }
    gates = dict(_base_gates.get(status, {"min_gate": 60, "strong_gate": 75}))

    # Only apply regime delta when not halted
    if not gates.get("halt"):
        try:
            regime_state = get_regime().state          # "BULL" | "NEUTRAL" | "BEAR"
            d_min, d_str = _regime_delta.get(regime_state, (0, 0))
            gates["min_gate"]    = int(max(0, min(99, gates["min_gate"]    + d_min)))
            gates["strong_gate"] = int(max(0, min(99, gates["strong_gate"] + d_str)))
        except Exception as _re:
            regime_state = "NEUTRAL"
            logger.warning("Regime lookup failed in get_market_status: %s", _re)
    else:
        regime_state = "NEUTRAL"

    logger.info(
        "Regime-calibrated gates: status=%s regime=%s → min_gate=%d strong_gate=%d",
        status, regime_state, gates["min_gate"], gates["strong_gate"],
    )

    result = {
        "status": status,
        "regime": regime_state,
        "vix": round(vix_val, 2),
        "vix_pctile": round(vix_pct, 1),
        "credit_ratio": credit.get("ratio"),
        "credit_change_pct": credit_change,
        "hyg_price": credit.get("hyg_price"),
        "lqd_price": credit.get("lqd_price"),
        "min_gate": gates["min_gate"],
        "strong_gate": gates["strong_gate"],
        "halt_pipeline": gates.get("halt", False),
        "cached_at": datetime.now(timezone.utc).isoformat(),
    }
    _MARKET_STATUS_CACHE = result
    _MARKET_STATUS_TS = now
    return result


# ── QQQ / IWM Snapshots ──

def _etf_snapshot(symbol: str, period: str = "5d") -> dict:
    """Return {price, change_pct} for a single ETF ticker."""
    try:
        from app.services.market_data import fetch as _md_fetch
        df = _md_fetch(symbol, period=period)
        if df is not None and not df.empty and len(df) >= 2:
            closes = df["close"].astype(float).values
            price = float(closes[-1])
            prev = float(closes[-2])
            change_pct = round((price / prev - 1) * 100, 2)
            return {"price": round(price, 2), "change_pct": change_pct}
        if df is not None and not df.empty:
            price = float(df["close"].astype(float).values[-1])
            return {"price": round(price, 2), "change_pct": None}
    except Exception:
        pass
    # Local fallback: yfinance
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period=period, interval="1d", auto_adjust=True)
        if hist is not None and not hist.empty and len(hist) >= 2:
            closes = hist["Close"].astype(float).values
            return {"price": round(float(closes[-1]), 2), "change_pct": round((float(closes[-1]) / float(closes[-2]) - 1) * 100, 2)}
    except Exception:
        pass
    return {"price": None, "change_pct": None}


def get_qqq_snapshot() -> dict:
    """QQQ current price + daily change percent."""
    return _etf_snapshot("QQQ")


def get_iwm_snapshot() -> dict:
    """IWM current price + daily change percent."""
    return _etf_snapshot("IWM")


# ── Combined Context ──


def _compute_market_context() -> dict:
    qqq = get_qqq_snapshot()
    iwm = get_iwm_snapshot()
    return {
        "vix": get_vix_context(),
        "spy_regime": get_spy_regime(),
        "breadth": get_market_breadth(),
        "credit": get_credit_ratio(),
        "liquidity": get_liquidity_index(),
        "qqq_price": qqq.get("price"),
        "qqq_change_pct": qqq.get("change_pct"),
        "iwm_price": iwm.get("price"),
        "iwm_change_pct": iwm.get("change_pct"),
        "cached_at": datetime.now(timezone.utc).isoformat(),
    }


def _background_refresh() -> None:
    if not _refresh_in_flight.acquire(blocking=False):
        return
    try:
        result = _compute_market_context()
        with _cache_lock:
            global _context_cache, _context_cache_ts
            _context_cache = result
            _context_cache_ts = time.time()
    except Exception as e:
        logger.warning("Background market-context refresh failed: %s", e)
    finally:
        _refresh_in_flight.release()


def get_market_context(force_refresh: bool = False) -> dict:
    """Return all market context indicators with stale-while-revalidate caching.

    - Fresh cache → return immediately.
    - Stale cache → return stale value, kick off background refresh.
    - No cache → compute synchronously (first call only).
    """
    global _context_cache, _context_cache_ts

    with _cache_lock:
        have_cache = bool(_context_cache)
        is_fresh = _cache_valid()
        cached_copy = dict(_context_cache) if have_cache else None

    if not force_refresh and is_fresh:
        return cached_copy

    if have_cache and not force_refresh:
        threading.Thread(target=_background_refresh, name="mc-refresh", daemon=True).start()
        cached_copy["stale"] = True
        return cached_copy

    result = _compute_market_context()
    with _cache_lock:
        _context_cache = result
        _context_cache_ts = time.time()
    return result
