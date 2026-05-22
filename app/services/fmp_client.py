"""Financial Modeling Prep (FMP) API client.

Consolidated client for all FMP API interactions with rate limiting,
circuit breaker, and consistent error handling.

FMP free tier: 250 requests/day. Use sparingly — primarily as fallback
when yfinance data is unavailable.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
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
    import math
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

    def _get(self, endpoint: str, params: dict | None = None) -> Any | None:
        """Get data with in-memory TTL cache + network fallback."""
        ckey = self._cache_key(endpoint, params)
        now = time.time()

        if ckey in self._cache_expiry and now < self._cache_expiry[ckey]:
            return self._cache.get(ckey)

        if not self._is_available():
            return self._cache.get(ckey)

        _fmp_rate_limit()

        url = f"{self.BASE_URL}/{endpoint}"
        request_params = dict(params or {})
        request_params["apikey"] = self.api_key

        try:
            with httpx.Client(timeout=10) as client:
                resp = client.get(url, params=request_params)
                resp.raise_for_status()
                data = resp.json()
                _FMP_BREAKER.record_success()
                ttl = self._cache_ttl_for(endpoint)
                self._cache[ckey] = data
                self._cache_expiry[ckey] = now + ttl
                return data
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                logger.warning("FMP rate limit hit — waiting 60s")
                time.sleep(60)
            elif e.response.status_code == 402:
                logger.debug(f"FMP 402 (premium required) for {endpoint} — skipping")
            elif e.response.status_code == 403:
                logger.error("FMP API key invalid or expired")
            else:
                logger.error(f"FMP HTTP {e.response.status_code} for {endpoint}")
            _FMP_BREAKER.record_failure()
            return self._cache.get(ckey)
        except Exception as e:
            logger.error(f"FMP request failed for {endpoint}: {e}")
            _FMP_BREAKER.record_failure()
            return self._cache.get(ckey)

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
