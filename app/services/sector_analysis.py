"""Sector rotation analysis — 11 sector ETFs performance tracking."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

logger = logging.getLogger("screener")

SECTOR_ETFS = {
    "XLK": "Technology",
    "XLF": "Financials",
    "XLE": "Energy",
    "XLV": "Health Care",
    "XLY": "Consumer Discretionary",
    "XLP": "Consumer Staples",
    "XLI": "Industrials",
    "XLU": "Utilities",
    "XLB": "Materials",
    "XLRE": "Real Estate",
    "XLC": "Communication Services",
}

_sector_cache: dict = {}
_sector_cache_ts: float = 0.0
_SECTOR_CACHE_TTL = 3600
_cache_lock = threading.Lock()


def _cache_valid() -> bool:
    return time.time() - _sector_cache_ts < _SECTOR_CACHE_TTL


def _fetch_etf_data(ticker: str) -> Optional[pd.DataFrame]:
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        hist = t.history(period="1mo")
        if hist is not None and not hist.empty and "Close" in hist.columns:
            return hist
    except Exception:
        pass
    return None


def get_sector_performance(force_refresh: bool = False) -> list[dict]:
    """Return performance for all 11 sectors over 1d, 5d, 20d."""
    global _sector_cache, _sector_cache_ts
    with _cache_lock:
        if not force_refresh and _cache_valid() and _sector_cache:
            return list(_sector_cache.values())

    results = {}
    for ticker, name in SECTOR_ETFS.items():
        hist = _fetch_etf_data(ticker)
        if hist is None or len(hist) < 2:
            results[ticker] = {"ticker": ticker, "name": name, "perf_1d": None, "perf_5d": None, "perf_20d": None, "available": False}
            continue
        close = hist["Close"].astype(float)
        perf_1d = round((float(close.iloc[-1]) / float(close.iloc[-2]) - 1) * 100, 2) if len(close) >= 2 else None
        perf_5d = round((float(close.iloc[-1]) / float(close.iloc[-5]) - 1) * 100, 2) if len(close) >= 5 else None
        perf_20d = round((float(close.iloc[-1]) / float(close.iloc[-21]) - 1) * 100, 2) if len(close) >= 21 else None
        results[ticker] = {"ticker": ticker, "name": name, "perf_1d": perf_1d, "perf_5d": perf_5d, "perf_20d": perf_20d, "available": True}

    # Sort by 20d performance descending
    sorted_results = sorted(results.values(), key=lambda x: (x["perf_20d"] or -999), reverse=True)

    # Classify top/bottom
    total = len(sorted_results)
    for i, r in enumerate(sorted_results):
        if not r["available"]:
            r["classification"] = "unknown"
        elif i < total // 3:
            r["classification"] = "leading"
        elif i >= total * 2 // 3:
            r["classification"] = "lagging"
        else:
            r["classification"] = "neutral"

    with _cache_lock:
        _sector_cache = {r["ticker"]: r for r in sorted_results}
        _sector_cache_ts = time.time()

    return sorted_results


def get_leading_sectors() -> list[str]:
    """Return tickers of leading sectors."""
    sectors = get_sector_performance()
    return [s["ticker"] for s in sectors if s.get("classification") == "leading"]


def is_sector_leading(ticker: str, sector_map: Optional[dict[str, str]] = None) -> bool:
    """Check if a symbol's sector is currently leading."""
    from app.services.universe import get_symbol_info
    from app.db.database import SessionLocal

    if sector_map is None:
        db = SessionLocal()
        try:
            info = get_symbol_info(db, ticker)
            sector = info.get("sector", "") if info else ""
        finally:
            db.close()
    else:
        sector = sector_map.get(ticker, "")

    if not sector:
        return True  # don't block if sector unknown

    leading = get_leading_sectors()
    etf_map = {v: k for k, v in SECTOR_ETFS.items()}
    sector_etf = etf_map.get(sector)
    return sector_etf in leading
