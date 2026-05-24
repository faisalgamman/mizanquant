"""Financial Modeling Prep (FMP) API client.

Consolidated client for all FMP API interactions with rate limiting,
circuit breaker, and consistent error handling.

FMP free tier: 250 requests/day. Use sparingly — primarily as fallback
when yfinance data is unavailable.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from app.config import settings

logger = logging.getLogger("screener")

# ---------------------------------------------------------------------------
# Rate limiting — FMP free tier is 250 req/day, be conservative
# ---------------------------------------------------------------------------
_FMP_LAST_CALL = 0.0
_FMP_MIN_INTERVAL = 0.6  # seconds between calls (~100/min, well under 250/day)
_FMP_LOCK = threading.Lock()


def _fmp_rate_limit():
    global _FMP_LAST_CALL
    with _FMP_LOCK:
        elapsed = time.time() - _FMP_LAST_CALL
        if elapsed < _FMP_MIN_INTERVAL:
            time.sleep(_FMP_MIN_INTERVAL - elapsed)
        _FMP_LAST_CALL = time.time()


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------


class _CircuitBreaker:
    def __init__(self, name: str, threshold: int = 5, reset: float = 120.0):
        self.name = name
        self.threshold = threshold
        self.reset = reset
        self.failures = 0
        self.last_failure = 0.0
        self.state = "closed"
        self._lock = threading.Lock()

    def record_failure(self):
        with self._lock:
            self.failures += 1
            self.last_failure = time.time()
            if self.failures >= self.threshold:
                self.state = "open"

    def record_success(self):
        with self._lock:
            self.failures = 0
            self.state = "closed"

    def is_open(self) -> bool:
        with self._lock:
            if self.state == "open":
                if time.time() - self.last_failure > self.reset:
                    self.state = "half-open"
                    return False
                return True
            return False


_FMP_BREAKER = _CircuitBreaker("fmp", threshold=5, reset=120.0)


# ---------------------------------------------------------------------------
# Safe float helper
# ---------------------------------------------------------------------------


def _safe_float(val, default: float = 0.0) -> float:
    if val is None:
        return default
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# FMP Client
# ---------------------------------------------------------------------------


class FMPClient:
    """Financial Modeling Prep API client.

    Usage:
        client = FMPClient()
        profile = client.get_profile("AAPL")
        financials = client.get_income_statement("AAPL")
    """

    BASE_URL = "https://financialmodelingprep.com/stable"

    # In-memory TTL cache (key -> (data, expiry))
    _cache: dict[str, Any] = {}
    _cache_expiry: dict[str, float] = {}

    # TTL per endpoint pattern (seconds); fundamental data changes quarterly at most
    _CACHE_TTL: dict[str, int] = {
        "profile": 604800,                # 7 days
        "income-statement": 604800,       # 7 days
        "balance-sheet-statement": 604800,
        "cash-flow-statement": 604800,
        "key-metrics": 604800,
        "financial-ratios": 604800,
        "dcf": 604800,
        "historical-dividends": 604800,
        "price-target-consensus": 86400,  # 1 day
        "price-target": 86400,
        "analyst-estimates": 86400,
        "stock-news": 3600,               # 1 hour
        "general-news": 3600,
        "earning-calendar": 3600,         # 1 hour
    }

    def __init__(self):
        self.api_key = settings.FMP_API_KEY or ""
        # Persistent HTTP client — reused across requests (connection pooling)
        self._http = httpx.Client(timeout=10)

    def __del__(self):
        try:
            self._http.close()
        except Exception:
            pass

    def _is_available(self) -> bool:
        return bool(self.api_key) and not _FMP_BREAKER.is_open()

    @staticmethod
    def _cache_key(endpoint: str, params: dict | None = None) -> str:
        if params:
            sorted_parts = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
            return f"{endpoint}?{sorted_parts}"
        return endpoint

    def _cache_ttl_for(self, endpoint: str) -> int:
        for pattern, ttl in self._CACHE_TTL.items():
            if pattern in endpoint:
                return ttl
        return 3600  # default 1 hour

    # ------------------------------------------------------------------
    # Database cache helpers (L2 — persistent across restarts)
    # ------------------------------------------------------------------

    def _db_cache_get(self, key: str, now: datetime) -> Any | None:
        """Read from persistent DB cache. Returns None on miss/error."""
        try:
            from app.db.database import SessionLocal
            from app.db.models import FMPCache
            db = SessionLocal()
            try:
                row = db.query(FMPCache).filter(
                    FMPCache.cache_key == key,
                    FMPCache.expires_at > now,
                ).first()
                if row:
                    logger.debug("FMP cache hit (db): %s", key[:80])
                    return row.data
            finally:
                db.close()
        except Exception as exc:
            logger.debug("FMP db cache read failed: %s", exc)
        return None

    def _db_cache_set(self, key: str, data: Any, expires: datetime) -> None:
        """Write to persistent DB cache. Silent on error."""
        try:
            from app.db.database import SessionLocal
            from app.db.models import FMPCache
            db = SessionLocal()
            try:
                row = db.query(FMPCache).filter(FMPCache.cache_key == key).first()
                if row:
                    row.data = data
                    row.expires_at = expires
                else:
                    db.add(FMPCache(cache_key=key, data=data, expires_at=expires))
                db.commit()
            except Exception:
                db.rollback()
            finally:
                db.close()
        except Exception as exc:
            logger.debug("FMP db cache write failed: %s", exc)

    # ------------------------------------------------------------------
    # Core HTTP helper
    # ------------------------------------------------------------------

    def _http_get(self, url: str, params: dict) -> Any | None:
        """Single HTTP GET with retry after 429. Returns parsed JSON or None."""
        try:
            resp = self._http.get(url, params=params)
            resp.raise_for_status()
            _FMP_BREAKER.record_success()
            return resp.json()
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            if status == 429:
                wait = 65  # 65s ensures the rate-limit window resets
                logger.warning("FMP 429 rate limit — waiting %ds then retrying", wait)
                time.sleep(wait)
                try:
                    resp2 = self._http.get(url, params=params)
                    resp2.raise_for_status()
                    _FMP_BREAKER.record_success()
                    return resp2.json()
                except Exception as retry_exc:
                    logger.error("FMP retry after 429 also failed: %s", retry_exc)
            elif status == 402:
                logger.debug("FMP 402 (premium endpoint) for %s — skipping", url)
            elif status == 403:
                logger.error("FMP 403 — API key invalid or expired")
            else:
                logger.error("FMP HTTP %d for %s", status, url)
            _FMP_BREAKER.record_failure()
        except Exception as exc:
            logger.error("FMP request failed: %s", exc)
            _FMP_BREAKER.record_failure()
        return None

    def _get(self, endpoint: str, params: dict | None = None) -> Any | None:
        """Two-tier cache: L1 in-memory → L2 database → L3 HTTP."""
        ckey = self._cache_key(endpoint, params)
        now = time.time()
        now_dt = datetime.fromtimestamp(now, tz=timezone.utc)

        # L1: in-memory (sub-millisecond for hot paths)
        if ckey in self._cache_expiry and now < self._cache_expiry[ckey]:
            return self._cache.get(ckey)

        # L2: database (survives container restarts)
        db_data = self._db_cache_get(ckey, now_dt)
        if db_data is not None:
            self._cache[ckey] = db_data           # warm L1
            self._cache_expiry[ckey] = now + 60   # keep warm 60s in-memory
            return db_data

        # Circuit breaker / no API key — return stale in-memory value if any
        if not self._is_available():
            return self._cache.get(ckey)

        _fmp_rate_limit()

        url = f"{self.BASE_URL}/{endpoint}"
        request_params = dict(params or {})
        request_params["apikey"] = self.api_key

        data = self._http_get(url, request_params)
        if data is not None:
            ttl = self._cache_ttl_for(endpoint)
            expires_dt = datetime.fromtimestamp(now + ttl, tz=timezone.utc)
            self._db_cache_set(ckey, data, expires_dt)   # persist (L2)
            self._cache[ckey] = data                      # warm (L1)
            self._cache_expiry[ckey] = now + ttl
        return data if data is not None else self._cache.get(ckey)

    # ------------------------------------------------------------------
    # Company Profile / Info
    # ------------------------------------------------------------------

    def get_profile(self, symbol: str) -> dict | None:
        """Company profile: name, sector, industry, description, mcap, etc."""
        data = self._get("profile", {"symbol": symbol.upper()})
        if isinstance(data, list) and data:
            return data[0]
        return None

    # ------------------------------------------------------------------
    # Financial Statements
    # ------------------------------------------------------------------

    def get_income_statement(self, symbol: str, limit: int = 4) -> list[dict] | None:
        """Annual income statements, most recent first."""
        data = self._get("income-statement", {"symbol": symbol.upper(), "period": "annual", "limit": limit})
        if isinstance(data, list):
            return data
        return None

    def get_balance_sheet(self, symbol: str, limit: int = 4) -> list[dict] | None:
        """Annual balance sheets, most recent first."""
        data = self._get("balance-sheet-statement", {"symbol": symbol.upper(), "period": "annual", "limit": limit})
        if isinstance(data, list):
            return data
        return None

    def get_cash_flow(self, symbol: str, limit: int = 4) -> list[dict] | None:
        """Annual cash flow statements, most recent first."""
        data = self._get("cash-flow-statement", {"symbol": symbol.upper(), "period": "annual", "limit": limit})
        if isinstance(data, list):
            return data
        return None

    def get_key_metrics(self, symbol: str, limit: int = 1) -> list[dict] | None:
        """Key financial metrics (ratios, margins, ROE, etc.)."""
        data = self._get("key-metrics", {"symbol": symbol.upper(), "period": "annual", "limit": limit})
        if isinstance(data, list):
            return data
        return None

    def get_financial_ratios(self, symbol: str, limit: int = 1) -> list[dict] | None:
        """Financial ratios (PE, PB, debt/equity, current ratio, etc.)."""
        data = self._get("financial-ratios", {"symbol": symbol.upper(), "period": "annual", "limit": limit})
        if isinstance(data, list):
            return data
        return None

    # ------------------------------------------------------------------
    # Earnings
    # ------------------------------------------------------------------

    def get_earnings_calendar(self, symbols: list[str] | None = None) -> dict[str, Any]:
        """Upcoming earnings dates for the next 30 days."""
        from datetime import datetime, timedelta, timezone
        today = datetime.now(timezone.utc).date()
        params = {
            "from": today.isoformat(),
            "to": (today + timedelta(days=30)).isoformat(),
        }
        data = self._get("earning-calendar", params)
        if not isinstance(data, list):
            return {}

        symbol_set = {s.upper() for s in (symbols or [])}
        result: dict[str, str] = {}
        for item in data:
            sym = str(item.get("symbol", "")).upper()
            if symbol_set and sym not in symbol_set:
                continue
            dt = item.get("date") or item.get("earningDate")
            if sym and dt:
                result[sym] = str(dt)[:10]
        return {"earnings": result}

    # ------------------------------------------------------------------
    # Analyst Data
    # ------------------------------------------------------------------

    def get_analyst_estimates(self, symbol: str, limit: int = 4) -> list[dict] | None:
        """Analyst revenue/EBITDA/earnings estimates by quarter."""
        data = self._get("analyst-estimates", {"symbol": symbol.upper(), "limit": limit})
        if isinstance(data, list):
            return data
        return None

    def get_price_target(self, symbol: str) -> dict | None:
        """Price target consensus (high, low, mean, median)."""
        data = self._get("price-target-consensus", {"symbol": symbol.upper()})
        if isinstance(data, list) and data:
            return data[0]
        if isinstance(data, dict):
            return data
        return None

    def get_price_target_summary(self, symbol: str) -> list[dict] | None:
        """Historical price targets."""
        data = self._get("price-target", {"symbol": symbol.upper()})
        if isinstance(data, list):
            return data[:25]
        return None

    def get_analyst_recommendations(self, symbol: str) -> list[dict] | None:
        """Analyst stock recommendations (Buy/Sell/Hold ratings)."""
        data = self._get(f"v3/analyst-stock-recommendations/{symbol.upper()}")
        if isinstance(data, list):
            return data
        return None

    def get_institutional_holders(self, symbol: str) -> list[dict] | None:
        """Institutional stock holders."""
        data = self._get("institutional-holder", {"symbol": symbol.upper()})
        if isinstance(data, list):
            return data[:20]
        return None

    # ------------------------------------------------------------------
    # ETF data (holdings / sector weightings / fund info)
    # ------------------------------------------------------------------

    def get_etf_holdings(self, symbol: str, limit: int = 25) -> list[dict]:
        """Top ETF holdings with weight %. Returns [] on failure."""
        data = self._get("etf/holdings", {"symbol": symbol.upper()})
        if not isinstance(data, list):
            return []
        out = []
        for h in data[:limit]:
            w = h.get("weightPercentage", h.get("weight"))
            try:
                w = float(str(w).replace("%", "")) if w is not None else None
            except (ValueError, TypeError):
                w = None
            out.append({
                "symbol": h.get("asset") or h.get("symbol") or "",
                "name":   h.get("name") or "",
                "weight": round(w, 2) if w is not None else None,
            })
        return out

    def get_etf_sector_weightings(self, symbol: str) -> list[dict]:
        """ETF sector allocation. Returns [] on failure."""
        data = self._get("etf/sector-weightings", {"symbol": symbol.upper()})
        if not isinstance(data, list):
            return []
        out = []
        for s in data:
            w = s.get("weightPercentage", s.get("weight"))
            try:
                w = float(str(w).replace("%", "")) if w is not None else None
            except (ValueError, TypeError):
                w = None
            out.append({
                "sector": s.get("sector") or "",
                "weight": round(w, 2) if w is not None else None,
            })
        return out

    def get_etf_info(self, symbol: str) -> dict | None:
        """ETF profile: AUM, expense ratio, inception. Returns None on failure."""
        data = self._get("etf/info", {"symbol": symbol.upper()})
        rec = None
        if isinstance(data, list) and data:
            rec = data[0]
        elif isinstance(data, dict):
            rec = data
        if not rec:
            return None
        return {
            "symbol":         symbol.upper(),
            "name":           rec.get("name") or "",
            "aum":            rec.get("assetsUnderManagement") or rec.get("aum"),
            "expense_ratio":  rec.get("expenseRatio"),
            "inception_date": str(rec.get("inceptionDate") or rec.get("inception_date") or ""),
            "description":    (rec.get("description") or "")[:400],
        }

    # ------------------------------------------------------------------
    # VIX (index — not available via Alpaca)
    # ------------------------------------------------------------------

    def get_vix(self) -> float | None:
        """Current ^VIX value (CBOE Volatility Index)."""
        try:
            data = self._get("quote", {"symbol": "^VIX"})
            if isinstance(data, list) and data:
                return float(data[0].get("price", 0)) or None
            if isinstance(data, dict) and "price" in data:
                return float(data["price"]) or None
        except Exception:
            pass
        return None

    def get_price_history(self, symbol: str, period_days: int = 365) -> "pd.Series | None":
        """Daily close history for any symbol as pd.Series indexed by date."""
        import pandas as pd
        from datetime import datetime, timedelta, timezone
        try:
            today = datetime.now(timezone.utc).date()
            start = (today - timedelta(days=period_days)).isoformat()
            data = self._get(
                "historical-price-eod/full",
                {"symbol": symbol.upper(), "from": start, "to": today.isoformat()},
            )
            records = None
            if isinstance(data, list):
                records = data
            elif isinstance(data, dict):
                records = data.get("historical") or data.get("historicalStockList", [{}])[0].get("historicalStockList")
            if not records:
                return None
            df = pd.DataFrame(records)
            if "date" not in df.columns or "close" not in df.columns:
                return None
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").set_index("date")
            series = df["close"].astype(float).dropna()
            return series if len(series) > 0 else None
        except Exception as exc:
            logger.debug("FMP price history %s failed: %s", symbol, exc)
            return None

    def get_vix_history(self, period_days: int = 365) -> "pd.Series | None":
        """^VIX daily close history as a pd.Series indexed by date."""
        import pandas as pd
        from datetime import datetime, timedelta, timezone
        try:
            today = datetime.now(timezone.utc).date()
            start = (today - timedelta(days=period_days)).isoformat()
            data = self._get(
                "historical-price-eod/full",
                {"symbol": "^VIX", "from": start, "to": today.isoformat()},
            )
            records = None
            if isinstance(data, list):
                records = data
            elif isinstance(data, dict):
                records = data.get("historical") or data.get("historicalStockList", [{}])[0].get("historicalStockList")
            if not records:
                return None
            df = pd.DataFrame(records)
            if "date" not in df.columns or "close" not in df.columns:
                return None
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").set_index("date")
            series = df["close"].astype(float).dropna()
            return series if len(series) > 0 else None
        except Exception as exc:
            logger.debug("FMP VIX history failed: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Investors Insights (G.O.A.T / Institutional / Senate)
    # ------------------------------------------------------------------

    def get_senate_trading(self, symbol: str | None = None) -> list[dict] | None:
        """Recent senate trading disclosures."""
        params = {"symbol": symbol.upper()} if symbol else {}
        data = self._get("v4/senate-trading", params)
        if isinstance(data, list):
            return data
        return None

    def get_institutional_portfolio(self, cik: str, date: str | None = None) -> list[dict] | None:
        """Institutional portfolio by CIK (e.g., 13F filings)."""
        params = {"cik": cik}
        if date:
            params["date"] = date
        data = self._get("v4/institutional-ownership/portfolio", params)
        if isinstance(data, list):
            return data
        return None

    # ------------------------------------------------------------------
    # Stock News
    # ------------------------------------------------------------------

    def get_stock_news(self, symbol: str, limit: int = 10) -> list[dict] | None:
        """Latest news for a stock."""
        data = self._get("stock-news", {"symbol": symbol.upper(), "limit": limit})
        if isinstance(data, list):
            return [
                {
                    "title": item.get("title", ""),
                    "text": (item.get("text") or "")[:300],
                    "url": item.get("url", ""),
                    "publishedDate": item.get("publishedDate", ""),
                    "site": item.get("site", ""),
                }
                for item in data
            ]
        return None

    def get_general_news(self, limit: int = 20) -> list[dict] | None:
        """General financial news."""
        data = self._get("general-news", {"limit": limit})
        if isinstance(data, list):
            return data
        return None

    # ------------------------------------------------------------------
    # Dividends
    # ------------------------------------------------------------------

    def get_historical_dividends(self, symbol: str, limit: int = 24) -> list[dict] | None:
        """Historical dividend payments."""
        data = self._get("historical-dividends", {"symbol": symbol.upper(), "limit": limit})
        if isinstance(data, list):
            return data
        return None

    # ------------------------------------------------------------------
    # Discounted Cash Flow
    # ------------------------------------------------------------------

    def get_dcf(self, symbol: str) -> dict | None:
        """Discounted cash flow valuation."""
        data = self._get("dcf", {"symbol": symbol.upper()})
        if isinstance(data, list) and data:
            return data[0]
        if isinstance(data, dict):
            return data
        return None

    # ------------------------------------------------------------------
    # Bulk convenience — multiple data points from one call
    # ------------------------------------------------------------------

    def get_company_data(self, symbol: str) -> dict | None:
        """Fetch company profile + key metrics in one go. Returns None if profile fails."""
        profile = self.get_profile(symbol)
        if not profile:
            return None
        metrics = self.get_key_metrics(symbol)
        latest_metrics = metrics[0] if metrics else {}
        return {
            "profile": profile,
            "metrics": latest_metrics,
        }


# Singleton
fmp_client = FMPClient()
