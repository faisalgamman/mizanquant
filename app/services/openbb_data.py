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


# ── FRED REST helper ────────────────────────────────────────────────────────
# NOTE: We call the FRED REST API directly (httpx) rather than via OpenBB.
# OpenBB's sync wrapper runs coroutines through anyio.start_blocking_portal(),
# which raises "signal only works in main thread" on Linux when invoked from a
# worker thread (our asyncio.to_thread endpoints). Direct REST avoids that
# entirely and is faster. See get_economic_indicators / get_vix_* below.

_FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"


def _fred_observations(series_id: str, limit: int | None = None,
                       start_date: str | None = None,
                       sort: str = "desc") -> list[tuple[str, float]]:
    """Fetch observations from FRED REST API.

    Returns list of (date_str, float_value) with missing '.' values dropped.
    Order follows ``sort`` ("desc" = newest first). [] on any failure.
    """
    from app.config import settings
    import httpx

    if not settings.FRED_API_KEY:
        return []

    params: dict = {
        "series_id": series_id,
        "api_key":   settings.FRED_API_KEY,
        "file_type": "json",
        "sort_order": sort,
    }
    if limit:
        params["limit"] = limit
    if start_date:
        params["observation_start"] = start_date

    try:
        with httpx.Client(timeout=10) as c:
            r = c.get(_FRED_BASE, params=params)
            r.raise_for_status()
            obs = r.json().get("observations", [])
        out: list[tuple[str, float]] = []
        for o in obs:
            v = o.get("value")
            if v in (None, ".", ""):
                continue
            try:
                out.append((o.get("date", ""), float(v)))
            except (ValueError, TypeError):
                continue
        return out
    except Exception as exc:
        logger.debug("FRED %s REST failed: %s", series_id, exc)
        return []


# ── FRED: VIX ─────────────────────────────────────────────────────────────────

def get_vix_current() -> Optional[float]:
    """Latest VIXCLS from FRED REST — official, free, no 401.

    Returns None when FRED_API_KEY is not set or the request fails,
    so the caller can fall through to FMP / yfinance.
    """
    obs = _fred_observations("VIXCLS", limit=10, sort="desc")
    if obs:
        v = obs[0][1]
        logger.debug("VIX from FRED: %.2f", v)
        return v
    return None


def get_vix_series(period_days: int = 365) -> Optional[pd.Series]:
    """VIXCLS history from FRED REST as pd.Series(DatetimeIndex → float).

    Returns None on failure; caller falls back to FMP / yfinance.
    """
    start = (datetime.now(timezone.utc).date() - timedelta(days=period_days)).isoformat()
    obs = _fred_observations("VIXCLS", start_date=start, sort="asc")
    if not obs:
        return None
    dates, vals = zip(*obs)
    return pd.Series(vals, index=pd.DatetimeIndex(dates)).sort_index()


# ── FRED: Economic Indicators ─────────────────────────────────────────────────

def get_economic_indicators() -> dict:
    """Real macroeconomic data from FRED REST API.

    Returns dict with keys: t10y2y, hy_spread, fed_rate, cpi_yoy, unemployment.
    Each value is float | None. All None when FRED_API_KEY is not set.

    Series used:
        T10Y2Y       — 10Y-2Y yield spread (negative = inversion)
        BAMLH0A0HYM2 — ICE BofA US High Yield spread (OAS)
        DFF          — Effective Federal Funds Rate
        CPIAUCSL     — CPI all items → compute YoY %
        UNRATE       — US Unemployment Rate
    """
    result: dict = {
        "t10y2y": None,
        "hy_spread": None,
        "fed_rate": None,
        "cpi_yoy": None,
        "unemployment": None,
    }

    # Single-value daily/monthly series — fetch a small buffer, take latest valid.
    for key, series_id in (
        ("t10y2y",      "T10Y2Y"),
        ("hy_spread",   "BAMLH0A0HYM2"),
        ("fed_rate",    "DFF"),
        ("unemployment", "UNRATE"),
    ):
        obs = _fred_observations(series_id, limit=10, sort="desc")
        if obs:
            result[key] = round(obs[0][1], 4)

    # CPI YoY — need 13 monthly points (newest + 12-months-ago).
    cpi = _fred_observations("CPIAUCSL", limit=15, sort="desc")
    if len(cpi) >= 13:
        latest = cpi[0][1]
        year_ago = cpi[12][1]
        if year_ago:
            result["cpi_yoy"] = round((latest / year_ago - 1) * 100, 2)

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
# NOTE: Direct Tiingo REST API (not via OpenBB) to avoid the anyio signal bug
# on Linux worker threads. Endpoint: https://api.tiingo.com/tiingo/daily/{sym}/prices

_TIINGO_BASE = "https://api.tiingo.com/tiingo/daily"


def fetch_ohlcv_tiingo(symbol: str, period: str = "1y") -> Optional[pd.DataFrame]:
    """Daily OHLCV from Tiingo REST API — works on Railway servers without 401.

    Returns DataFrame with columns [date, open, high, low, close, volume]
    or None on failure so the caller falls through to yfinance.
    """
    import httpx
    from app.config import settings
    if not settings.TIINGO_TOKEN:
        return None
    try:
        days = {"1mo": 30, "3mo": 90, "6mo": 180, "1y": 365, "2y": 730}.get(period, 365)
        start = (datetime.now(timezone.utc).date() - timedelta(days=days)).isoformat()
        end   = datetime.now(timezone.utc).date().isoformat()
        url   = f"{_TIINGO_BASE}/{symbol.upper()}/prices"
        headers = {"Authorization": f"Token {settings.TIINGO_TOKEN}"}
        params  = {"startDate": start, "endDate": end, "format": "json"}
        with httpx.Client(timeout=15) as c:
            r = c.get(url, headers=headers, params=params)
            r.raise_for_status()
            data = r.json()
        if not isinstance(data, list) or not data:
            return None
        rows = []
        for row in data:
            rows.append({
                "date":   row.get("date", "")[:10],   # ISO date string
                "open":   row.get("adjOpen")  or row.get("open"),
                "high":   row.get("adjHigh")  or row.get("high"),
                "low":    row.get("adjLow")   or row.get("low"),
                "close":  row.get("adjClose") or row.get("close"),
                "volume": row.get("adjVolume") or row.get("volume"),
            })
        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index().reset_index()
        logger.debug("Tiingo OHLCV %s (REST): %d rows", symbol, len(df))
        return df
    except Exception as exc:
        logger.debug("Tiingo OHLCV %s failed: %s", symbol, exc)
    return None


# ── FMP via OpenBB: ETF Data ──────────────────────────────────────────────────

def get_etf_info(symbol: str) -> Optional[dict]:
    """ETF profile via FMP REST (fmp_client) — avoids OpenBB thread/signal bug."""
    from app.config import settings
    if not settings.FMP_API_KEY:
        return None
    try:
        from app.services.fmp_client import fmp_client
        return fmp_client.get_etf_info(symbol.upper())
    except Exception as exc:
        logger.debug("ETF info %s failed: %s", symbol, exc)
    return None


def get_etf_holdings(symbol: str, limit: int = 25) -> list[dict]:
    """Top ETF holdings via FMP REST (fmp_client) — avoids OpenBB thread/signal bug."""
    from app.config import settings
    if not settings.FMP_API_KEY:
        return []
    try:
        from app.services.fmp_client import fmp_client
        return fmp_client.get_etf_holdings(symbol.upper(), limit=limit)
    except Exception as exc:
        logger.debug("ETF holdings %s failed: %s", symbol, exc)
    return []


def get_etf_sectors(symbol: str) -> list[dict]:
    """ETF sector allocation via FMP REST (fmp_client) — avoids OpenBB thread/signal bug."""
    from app.config import settings
    if not settings.FMP_API_KEY:
        return []
    try:
        from app.services.fmp_client import fmp_client
        return fmp_client.get_etf_sector_weightings(symbol.upper())
    except Exception as exc:
        logger.debug("ETF sectors %s failed: %s", symbol, exc)
    return []


# ── Fundamental: ESG + Revenue Segments + Transcripts ────────────────────────
# NOTE: Direct REST calls (yfinance + FMP REST) replace OpenBB wrappers to
# avoid the anyio "signal only works in main thread" error on Railway Linux.

def get_esg_score(symbol: str) -> Optional[dict]:
    """ESG score from yfinance directly — relevant for halal screening.

    Returns total + environmental + social + governance scores and
    highest_controversy level. Returns None if data not available.
    Uses yfinance.Ticker.sustainability DataFrame (no OpenBB wrapper).
    """
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol.upper())
        sus = ticker.sustainability
        if sus is None or sus.empty:
            return None
        # yfinance sustainability is a DataFrame with columns as metrics
        # Transpose so metrics are rows, then extract values
        if hasattr(sus, "T"):
            df = sus.T if sus.shape[0] == 1 else sus
        else:
            df = sus
        # Try to get values — structure varies by yfinance version
        def _val(keys: list[str]) -> Optional[float]:
            for k in keys:
                try:
                    v = df.loc[k] if k in df.index else df.get(k)
                    if v is not None:
                        fv = float(str(v).replace("nan", "")) if str(v) != "nan" else None
                        if fv is not None:
                            return round(fv, 2)
                except Exception:
                    pass
            return None

        return {
            "symbol":        symbol.upper(),
            "total":         _val(["totalEsg", "totalEsgScore"]),
            "environmental": _val(["environmentScore", "environmentalScore"]),
            "social":        _val(["socialScore"]),
            "governance":    _val(["governanceScore"]),
            "controversy":   _val(["highestControversy"]),
            "source":        "yfinance",
        }
    except Exception as exc:
        logger.debug("ESG score %s failed: %s", symbol, exc)
    return None


def get_revenue_per_segment(symbol: str) -> list[dict]:
    """Revenue breakdown by business segment from FMP REST API.

    Useful for AAOIFI halal screening: identify haram revenue segments.
    Returns [] when FMP_API_KEY not set or data unavailable.
    FMP endpoint: /api/v4/revenue-product-segmentation?symbol=X&period=annual
    """
    import httpx
    from app.config import settings
    if not settings.FMP_API_KEY:
        return []
    try:
        url = "https://financialmodelingprep.com/api/v4/revenue-product-segmentation"
        params = {"symbol": symbol.upper(), "period": "annual",
                  "apikey": settings.FMP_API_KEY}
        with httpx.Client(timeout=10) as c:
            r = c.get(url, params=params)
            r.raise_for_status()
            data = r.json()
        if not isinstance(data, list) or not data:
            return []
        # Most recent period is the first item
        latest = data[0]
        segments = []
        # Each item is a dict like {"date": "2024-09-30", "Cloud Services": 100000, ...}
        period = str(latest.get("date", "") or "")
        for k, v in latest.items():
            if k == "date":
                continue
            if v is not None:
                try:
                    segments.append({"name": k, "value": float(v), "period": period})
                except (ValueError, TypeError):
                    pass
        # Sort by value descending
        segments.sort(key=lambda x: x["value"], reverse=True)
        return segments
    except Exception as exc:
        logger.debug("Revenue segments %s failed: %s", symbol, exc)
    return []


def get_earnings_transcript(symbol: str, year: int, quarter: int) -> Optional[dict]:
    """Earnings call transcript from FMP REST API.

    Returns dict with content (truncated to 5000 chars) + full_length.
    Returns None when FMP_API_KEY not set or transcript unavailable.
    FMP endpoint: /api/v3/earning_call_transcript/{symbol}?year=2024&quarter=4
    """
    import httpx
    from app.config import settings
    if not settings.FMP_API_KEY:
        return None
    try:
        url = f"https://financialmodelingprep.com/api/v3/earning_call_transcript/{symbol.upper()}"
        params = {"year": year, "quarter": quarter, "apikey": settings.FMP_API_KEY}
        with httpx.Client(timeout=15) as c:
            r = c.get(url, params=params)
            r.raise_for_status()
            data = r.json()
        if not isinstance(data, list) or not data:
            return None
        t = data[0]
        content = t.get("content", "") or ""
        return {
            "symbol":      symbol.upper(),
            "year":        year,
            "quarter":     quarter,
            "date":        t.get("date", ""),
            "content":     content[:5000],
            "full_length": len(content),
        }
    except Exception as exc:
        logger.debug("Earnings transcript %s Q%dY%d failed: %s", symbol, quarter, year, exc)
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
        from app.services.fmp_client import fmp_client

        # Need enough history for a rolling normalization window.
        days = max(period * 12, 252)
        bench = fmp_client.get_price_history(benchmark.upper(), period_days=days)
        if bench is None or len(bench) < period * 3:
            return []

        out: list[dict] = []
        for sym in symbols:
            try:
                px = fmp_client.get_price_history(sym.upper(), period_days=days)
                if px is None or len(px) < period * 3:
                    continue
                # Align on common dates
                df = pd.concat([px.rename("sym"), bench.rename("bench")], axis=1).dropna()
                if len(df) < period * 3:
                    continue
                rs = 100.0 * (df["sym"] / df["bench"])
                # RS-Ratio: rolling z-score centered at 100
                win = period
                mean = rs.rolling(win).mean()
                std = rs.rolling(win).std()
                rs_ratio = 100.0 + ((rs - mean) / std).fillna(0)
                # RS-Momentum: rolling z-score of RS-Ratio's rate of change
                roc = rs_ratio.diff(win)
                rmean = roc.rolling(win).mean()
                rstd = roc.rolling(win).std()
                rs_mom = 100.0 + ((roc - rmean) / rstd).fillna(0)

                ratio_val = round(float(rs_ratio.iloc[-1]), 2)
                mom_val = round(float(rs_mom.iloc[-1]), 2)
                out.append({
                    "symbol":      sym.upper(),
                    "rs_ratio":    ratio_val,
                    "rs_momentum": mom_val,
                    "quadrant":    _rrg_quadrant(ratio_val, mom_val),
                })
            except Exception as exc:
                logger.debug("RRG %s failed: %s", sym, exc)
                continue
        return out
    except Exception as exc:
        logger.debug("Relative rotation failed: %s", exc)
    return []
