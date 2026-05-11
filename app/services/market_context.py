"""Market context indicators — VIX, SPY Regime, Breadth, HY/IG Credit, Liquidity.

All functions cache results for _CONTEXT_CACHE_TTL seconds (default 5 min).
Call ``get_market_context(force_refresh=True)`` to bypass cache.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd

from app.services.regime import get_regime

logger = logging.getLogger("screener")

# ── Cache ──
_context_cache: dict = {}
_context_cache_ts: float = 0.0
_CONTEXT_CACHE_TTL = 300
_cache_lock = threading.Lock()


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
    """Fetch ^VIX close from yFinance (fallback to regime.vix)."""
    try:
        import yfinance as yf
        ticker = yf.Ticker("^VIX")
        hist = ticker.history(period="1y")
        if hist is not None and not hist.empty and "Close" in hist.columns:
            return float(hist["Close"].iloc[-1])
    except Exception:
        pass
    regime = get_regime()
    return regime.vix if regime.vix != 0.0 else None


def _fetch_vix_series() -> Optional[list[float]]:
    """Fetch 1y ^VIX close history."""
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
    """% of halal symbols with close above EMA 50 (batch yFinance download).

    Uses a representative sample (first 80 symbols) for speed.
    Converts BRK.B → BRK-B for yFinance compatibility.
    """
    from app.services.universe import HALAL_STOCKS_FALLBACK
    all_syms = list(HALAL_STOCKS_FALLBACK)
    # Sample first 80 for speed; sorted by S&P 500 weight it's representative
    symbols = all_syms[:80]
    safe_map = {s: _yf_safe(s) for s in symbols}
    safe_tickers = list(safe_map.values())

    try:
        import yfinance as yf
        data = yf.download(
            tickers=" ".join(safe_tickers),
            period="3mo",
            interval="1d",
            progress=False,
            threads=True,
            group_by="ticker",
        )
    except Exception as e:
        logger.warning("Breadth download failed: %s", e)
        return {"breadth_pct": None, "above": 0, "total": 0, "error": str(e)}

    if data is None or data.empty:
        return {"breadth_pct": None, "above": 0, "total": 0, "error": "no_data"}

    above = 0
    total = 0
    for orig_sym in symbols:
        safe = safe_map[orig_sym]
        try:
            if isinstance(data.columns, pd.MultiIndex):
                closes = data.xs("Close", axis=1, level=1)
                close = closes[safe].dropna()
            else:
                continue
            if len(close) < 50:
                continue
            total += 1
            ema_50 = close.ewm(span=50, adjust=False).mean().iloc[-1]
            if close.iloc[-1] > ema_50:
                above += 1
        except Exception:
            continue

    pct = round(above / total * 100, 1) if total > 0 else 0.0
    return {
        "breadth_pct": pct,
        "above": above,
        "total": total,
        "classification": "broad_rally" if pct >= 60 else ("mixed" if pct >= 40 else "weak"),
    }


# ── HY/IG Credit Ratio ──


def _yf_etf_df(ticker: str, period: str = "1mo") -> Optional[pd.DataFrame]:
    """Fetch ETF data directly via yFinance (bypasses Alpaca)."""
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        hist = t.history(period=period)
        if hist is not None and not hist.empty and "Close" in hist.columns:
            df = pd.DataFrame({
                "close": hist["Close"].astype(float),
                "volume": hist.get("Volume", pd.Series(0, index=hist.index)).astype(float),
            })
            return df
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
    elif vix_val > 50 or (vix_pct > 70 and credit_cls == "stress"):
        status = "CREDIT STRESS"
    elif vix_val > 30 or vix_pct > 50:
        status = "CAUTION"
    else:
        status = "RISK ON"

    # Gate levels tied to market status
    gate_map = {
        "RISK ON":       {"min_gate": 60, "strong_gate": 75},
        "CAUTION":       {"min_gate": 65, "strong_gate": 80},
        "CREDIT STRESS":  {"min_gate": 70, "strong_gate": 85},
        "EXTREME FEAR":   {"min_gate": 99, "strong_gate": 99, "halt": True},
    }
    gates = gate_map.get(status, {"min_gate": 60, "strong_gate": 75})

    result = {
        "status": status,
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


# ── Combined Context ──


def get_market_context(force_refresh: bool = False) -> dict:
    """Return all market context indicators with caching."""
    global _context_cache, _context_cache_ts

    with _cache_lock:
        if not force_refresh and _cache_valid():
            return dict(_context_cache)

    result = {
        "vix": get_vix_context(),
        "spy_regime": get_spy_regime(),
        "breadth": get_market_breadth(),
        "credit": get_credit_ratio(),
        "liquidity": get_liquidity_index(),
        "cached_at": datetime.now(timezone.utc).isoformat(),
    }

    with _cache_lock:
        _context_cache = result
        _context_cache_ts = time.time()

    return result
