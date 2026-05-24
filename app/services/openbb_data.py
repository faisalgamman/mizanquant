"""OpenBB 4.7.1 data wrappers — FRED (macro) + Tiingo (OHLCV) + ETF + Fundamentals.

Three-tier provider strategy:
  FRED  — free Federal Reserve data: VIX, yield spread, HY spread, CPI, fed rate
  Tiingo — free OHLCV that works on servers (no 401 like yfinance on Railway)
  FMP via OpenBB — ETF holdings/sectors, revenue segments, transcripts

Design: all functions return None / empty list on failure so callers degrade
gracefully. No exceptions propagate to the HTTP layer.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd

logger = logging.getLogger("screener")

# ── Credential init lock ───────────────────────────────────────────────────────
_obb_lock = threading.Lock()
_obb_configured = False


def _get_obb():
    """Lazy import + configure credentials once per process."""
    global _obb_configured
    from openbb import obb
    from app.config import settings

    with _obb_lock:
        if not _obb_configured:
            try:
                if settings.FRED_API_KEY:
                    obb.user.credentials.fred_api_key = settings.FRED_API_KEY
                if settings.TIINGO_TOKEN:
                    obb.user.credentials.tiingo_token = settings.TIINGO_TOKEN
                if settings.FMP_API_KEY:
                    obb.user.credentials.fmp_api_key = settings.FMP_API_KEY
                _obb_configured = True
                logger.info(
                    "OpenBB credentials configured: FRED=%s Tiingo=%s FMP=%s",
                    "yes" if settings.FRED_API_KEY else "no",
                    "yes" if settings.TIINGO_TOKEN else "no",
                    "yes" if settings.FMP_API_KEY else "no",
                )
            except Exception as exc:
                logger.warning("OpenBB credential setup failed: %s", exc)
    return obb


# ── FRED: VIX ─────────────────────────────────────────────────────────────────

def get_vix_current() -> Optional[float]:
    """VIXCLS from FRED — official, free, no rate limit, no 401.

    Returns None when FRED_API_KEY is not set or the request fails,
    so the caller can fall through to FMP / yfinance.
    """
    from app.config import settings
    if not settings.FRED_API_KEY:
        return None
    try:
        obb = _get_obb()
        r = obb.economy.fred_series(symbol="VIXCLS", limit=1, provider="fred")
        if r.results:
            val = r.results[-1].value
            if val is not None:
                v = float(val)
                logger.debug("VIX from FRED: %.2f", v)
                return v
    except Exception as exc:
        logger.debug("FRED VIX current failed: %s", exc)
    return None


def get_vix_series(period_days: int = 365) -> Optional[pd.Series]:
    """VIXCLS historical from FRED as pd.Series(DatetimeIndex → float).

    Returns None on failure; caller falls back to FMP / yfinance.
    """
    from app.config import settings
    if not settings.FRED_API_KEY:
        return None
    try:
        obb = _get_obb()
        start = (datetime.now(timezone.utc).date() - timedelta(days=period_days)).isoformat()
        r = obb.economy.fred_series(symbol="VIXCLS", start_date=start, provider="fred")
        if not r.results:
            return None
        records = [
            (row.date, float(row.value))
            for row in r.results
            if row.value is not None
        ]
        if not records:
            return None
        dates, vals = zip(*records)
        return pd.Series(vals, index=pd.DatetimeIndex(dates)).sort_index()
    except Exception as exc:
        logger.debug("FRED VIX series failed: %s", exc)
    return None


# ── FRED: Economic Indicators ─────────────────────────────────────────────────

def get_economic_indicators() -> dict:
    """Real macroeconomic data from FRED.

    Returns dict with keys: t10y2y, hy_spread, fed_rate, cpi_yoy, unemployment.
    Each value is float | None. All None when FRED_API_KEY is not set.

    Series used:
        T10Y2Y       — 10Y-2Y yield spread (negative = inversion)
        BAMLH0A0HYM2 — ICE BofA US High Yield spread (OAS)
        DFF          — Effective Federal Funds Rate
        CPIAUCSL     — CPI all items → compute YoY %
        UNRATE       — US Unemployment Rate
    """
    from app.config import settings

    result: dict = {
        "t10y2y": None,
        "hy_spread": None,
        "fed_rate": None,
        "cpi_yoy": None,
        "unemployment": None,
    }

    if not settings.FRED_API_KEY:
        return result

    series_map = {
        "t10y2y":      ("T10Y2Y",      1),
        "hy_spread":   ("BAMLH0A0HYM2", 1),
        "fed_rate":    ("DFF",          1),
        "cpi_yoy":     ("CPIAUCSL",    13),   # 13 months to compute YoY
        "unemployment": ("UNRATE",      1),
    }

    try:
        obb = _get_obb()
        for key, (series_id, limit) in series_map.items():
            try:
                r = obb.economy.fred_series(symbol=series_id, limit=limit, provider="fred")
                if not r.results:
                    continue
                if key == "cpi_yoy" and len(r.results) >= 13:
                    latest = float(r.results[-1].value)
                    year_ago = float(r.results[0].value)
                    result[key] = round((latest / year_ago - 1) * 100, 2) if year_ago else None
                else:
                    val = r.results[-1].value
                    result[key] = round(float(val), 4) if val is not None else None
            except Exception as exc:
                logger.debug("FRED %s failed: %s", series_id, exc)
    except Exception as exc:
        logger.debug("FRED indicators batch failed: %s", exc)

    return result


def get_fred_economic_calendar() -> list[dict]:
    """Real economic indicator values from FRED.

    Replaces random.seed(42) synthetic data in dashboard_macro_calendar().
    Returns [] when FRED_API_KEY not set or all calls fail — caller uses fallback.
    """
    indicators = get_economic_indicators()
    events = []

    _rows = [
        ("Consumer Price Index YoY",   "cpi_yoy",     "high",   "%",   "FRED/BLS"),
        ("Unemployment Rate",          "unemployment", "high",   "%",   "FRED/BLS"),
        ("Federal Funds Rate",         "fed_rate",     "high",   "%",   "FRED/Fed"),
        ("Yield Spread 10Y-2Y",        "t10y2y",       "medium", "%",   "FRED/Treasury"),
        ("High Yield Credit Spread",   "hy_spread",    "medium", "%",   "FRED/ICE BofA"),
    ]
    for event, key, impact, unit, source in _rows:
        val = indicators.get(key)
        if val is not None:
            events.append({
                "event":  event,
                "impact": impact,
                "unit":   unit,
                "actual": val,
                "source": source,
            })

    return events


# ── Tiingo: OHLCV ─────────────────────────────────────────────────────────────

def fetch_ohlcv_tiingo(symbol: str, period: str = "1y") -> Optional[pd.DataFrame]:
    """Daily OHLCV from Tiingo — works on servers without 401.

    Returns DataFrame with columns [date, open, high, low, close, volume]
    or None on failure so the caller falls through to yfinance.
    """
    from app.config import settings
    if not settings.TIINGO_TOKEN:
        return None
    try:
        days = {"1mo": 30, "3mo": 90, "6mo": 180, "1y": 365, "2y": 730}.get(period, 365)
        start = (datetime.now(timezone.utc).date() - timedelta(days=days)).isoformat()
        obb = _get_obb()
        r = obb.equity.price.historical(symbol.upper(), start_date=start, provider="tiingo")
        if not r.results:
            return None
        rows = [
            {
                "date":   row.date,
                "open":   row.open,
                "high":   row.high,
                "low":    row.low,
                "close":  row.close,
                "volume": row.volume,
            }
            for row in r.results
        ]
        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index().reset_index()
        logger.debug("Tiingo OHLCV %s: %d rows", symbol, len(df))
        return df
    except Exception as exc:
        logger.debug("Tiingo OHLCV %s failed: %s", symbol, exc)
    return None


# ── FMP via OpenBB: ETF Data ──────────────────────────────────────────────────

def get_etf_info(symbol: str) -> Optional[dict]:
    """ETF profile: AUM, expense ratio, inception date, description."""
    from app.config import settings
    if not settings.FMP_API_KEY:
        return None
    try:
        obb = _get_obb()
        r = obb.etf.info(symbol.upper(), provider="fmp")
        if r.results:
            e = r.results[0]
            return {
                "symbol":        symbol.upper(),
                "name":          getattr(e, "name", "") or "",
                "aum":           getattr(e, "net_assets", None),
                "expense_ratio": getattr(e, "expense_ratio", None),
                "inception_date": str(getattr(e, "inception_date", "") or ""),
                "description":   (getattr(e, "description", "") or "")[:400],
            }
    except Exception as exc:
        logger.debug("ETF info %s failed: %s", symbol, exc)
    return None


def get_etf_holdings(symbol: str, limit: int = 25) -> list[dict]:
    """Top ETF holdings with weights from FMP via OpenBB."""
    from app.config import settings
    if not settings.FMP_API_KEY:
        return []
    try:
        obb = _get_obb()
        r = obb.etf.holdings(symbol.upper(), provider="fmp")
        if not r.results:
            return []
        return [
            {
                "symbol": getattr(h, "symbol", "") or "",
                "name":   getattr(h, "name", "") or "",
                "weight": round(float(h.weight or 0) * 100, 2),
            }
            for h in r.results[:limit]
        ]
    except Exception as exc:
        logger.debug("ETF holdings %s failed: %s", symbol, exc)
    return []


def get_etf_sectors(symbol: str) -> list[dict]:
    """ETF sector allocation (weights) from FMP via OpenBB."""
    from app.config import settings
    if not settings.FMP_API_KEY:
        return []
    try:
        obb = _get_obb()
        r = obb.etf.sectors(symbol.upper(), provider="fmp")
        if not r.results:
            return []
        return [
            {
                "sector": getattr(s, "sector", str(s)),
                "weight": round(float(getattr(s, "weight", 0) or 0) * 100, 2),
            }
            for s in r.results
        ]
    except Exception as exc:
        logger.debug("ETF sectors %s failed: %s", symbol, exc)
    return []


# ── OpenBB Fundamental: ESG + Revenue Segments + Transcripts ─────────────────

def get_esg_score(symbol: str) -> Optional[dict]:
    """ESG score from yfinance via OpenBB — relevant for halal screening.

    Returns total + environmental + social + governance scores and
    highest_controversy level. Returns None if data not available.
    """
    try:
        obb = _get_obb()
        r = obb.equity.fundamental.esg_score(symbol.upper(), provider="yfinance")
        if r.results:
            e = r.results[0]
            return {
                "symbol":        symbol.upper(),
                "total":         getattr(e, "total_esg_score", None),
                "environmental": getattr(e, "environmental_score", None),
                "social":        getattr(e, "social_score", None),
                "governance":    getattr(e, "governance_score", None),
                "controversy":   getattr(e, "highest_controversy", None),
                "source":        "yfinance",
            }
    except Exception as exc:
        logger.debug("ESG score %s failed: %s", symbol, exc)
    return None


def get_revenue_per_segment(symbol: str) -> list[dict]:
    """Revenue breakdown by business segment from FMP via OpenBB.

    Useful for AAOIFI halal screening: identify haram revenue segments.
    Returns [] when FMP_API_KEY not set or data unavailable.
    """
    from app.config import settings
    if not settings.FMP_API_KEY:
        return []
    try:
        obb = _get_obb()
        r = obb.equity.fundamental.revenue_per_segment(symbol.upper(), provider="fmp")
        if not r.results:
            return []
        segments = []
        for s in r.results:
            # Each result row is a period; take the most recent
            data = s.__dict__ if hasattr(s, "__dict__") else {}
            period = str(getattr(s, "period_ending", "") or "")
            for k, v in data.items():
                if k.startswith("_") or k == "period_ending":
                    continue
                if v is not None:
                    segments.append({
                        "name":   k,
                        "value":  v,
                        "period": period,
                    })
            break  # most recent period only
        return segments
    except Exception as exc:
        logger.debug("Revenue segments %s failed: %s", symbol, exc)
    return []


def get_earnings_transcript(symbol: str, year: int, quarter: int) -> Optional[dict]:
    """Earnings call transcript from FMP via OpenBB.

    Returns dict with content (truncated to 5000 chars) + full_length.
    Returns None when FMP_API_KEY not set or transcript unavailable.
    """
    from app.config import settings
    if not settings.FMP_API_KEY:
        return None
    try:
        obb = _get_obb()
        r = obb.equity.fundamental.transcript(
            symbol.upper(), year=year, quarter=quarter, provider="fmp"
        )
        if r.results:
            t = r.results[0]
            content = getattr(t, "content", "") or ""
            return {
                "symbol":      symbol.upper(),
                "year":        year,
                "quarter":     quarter,
                "content":     content[:5000],
                "full_length": len(content),
            }
    except Exception as exc:
        logger.debug("Earnings transcript %s Q%s%d failed: %s", symbol, quarter, year, exc)
    return None


# ── Relative Rotation Graph ────────────────────────────────────────────────────

def _rrg_quadrant(rs_ratio: Optional[float], rs_momentum: Optional[float]) -> str:
    """Classify into RRG quadrant based on RS-Ratio and RS-Momentum."""
    if rs_ratio is None or rs_momentum is None:
        return "unknown"
    if rs_ratio > 100 and rs_momentum > 100:
        return "leading"
    if rs_ratio > 100 and rs_momentum <= 100:
        return "weakening"
    if rs_ratio <= 100 and rs_momentum > 100:
        return "improving"
    return "lagging"


def get_relative_rotation(
    symbols: list[str],
    benchmark: str = "SPY",
    period: int = 21,
) -> list[dict]:
    """Relative Rotation Graph data (Rose Petal chart) from FMP via OpenBB.

    Returns list of {symbol, rs_ratio, rs_momentum, quadrant} dicts.
    Returns [] when FMP_API_KEY not set or data unavailable.
    """
    from app.config import settings
    if not settings.FMP_API_KEY:
        return []
    try:
        obb = _get_obb()
        r = obb.equity.price.relative_rotation(
            symbols=symbols,
            benchmark=benchmark,
            study="price",
            period=period,
            provider="fmp",
        )
        if not r.results:
            return []
        return [
            {
                "symbol":      getattr(p, "symbol", ""),
                "rs_ratio":    getattr(p, "rs_ratio", None),
                "rs_momentum": getattr(p, "rs_momentum", None),
                "quadrant":    _rrg_quadrant(
                    getattr(p, "rs_ratio", None),
                    getattr(p, "rs_momentum", None),
                ),
            }
            for p in r.results
        ]
    except Exception as exc:
        logger.debug("Relative rotation failed: %s", exc)
    return []
