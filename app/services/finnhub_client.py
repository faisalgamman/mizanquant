"""Finnhub API client — analyst recommendation trends + insider transactions.

Used to surface two signals a technical scan misses, in the Analyze card:
  • analyst consensus  → /stock/recommendation        (FREE tier)
  • insider trades      → /stock/insider-transactions  (FREE tier, SEC Form 4)
The price-target endpoint is PREMIUM on the free tier (403), so it is NOT used —
the recommendation consensus carries the same caution.

Reads FINNHUB_API_KEY from the environment (set as a Fly secret). No key ⇒ every
call returns None and the card simply omits the chips (graceful degradation).
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx

logger = logging.getLogger("screener")

_BASE = "https://finnhub.io/api/v1"


class FinnhubClient:
    """Minimal cached Finnhub client. Free tier is 60 req/min; we cache 6h since
    analyst/insider data updates slowly, so on-demand Analyze opens stay well under."""

    _cache: dict[str, Any] = {}
    _expiry: dict[str, float] = {}
    _TTL = 21600  # 6 hours

    def __init__(self) -> None:
        self._http = httpx.Client(timeout=8)

    @property
    def api_key(self) -> str:
        # Read live each call so a secret set after boot is picked up without a restart.
        return os.environ.get("FINNHUB_API_KEY", "") or ""

    def _get(self, path: str, params: dict) -> Any | None:
        key = self.api_key
        if not key:
            return None
        ckey = path + "?" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        now = time.time()
        if ckey in self._expiry and now < self._expiry[ckey]:
            return self._cache.get(ckey)
        try:
            p = dict(params)
            p["token"] = key
            r = self._http.get(f"{_BASE}/{path}", params=p)
            r.raise_for_status()
            data = r.json()
            self._cache[ckey] = data
            self._expiry[ckey] = now + self._TTL
            return data
        except Exception as exc:
            logger.debug("finnhub %s failed: %s", path, exc)
            return None

    def get_recommendation(self, symbol: str) -> list | None:
        """Monthly analyst recommendation trends (latest period first):
        [{symbol, period, strongBuy, buy, hold, sell, strongSell}, ...]."""
        d = self._get("stock/recommendation", {"symbol": symbol.upper()})
        return d if isinstance(d, list) else None

    def get_next_earnings(self, symbol: str) -> dict | None:
        """Next scheduled earnings within ~120d: {date, hour ('amc'/'bmo'/'dmh'),
        epsEstimate, ...} or None. Finnhub earnings calendar is on the FREE tier
        (FMP's earnings endpoint is premium on this plan)."""
        from datetime import date, timedelta
        today = date.today()
        d = self._get("calendar/earnings", {
            "symbol": symbol.upper(),
            "from": today.isoformat(),
            "to": (today + timedelta(days=120)).isoformat(),
        })
        if isinstance(d, dict):
            rows = [r for r in (d.get("earningsCalendar") or []) if r.get("date")]
            if rows:
                return sorted(rows, key=lambda r: r["date"])[0]  # soonest upcoming
        return None

    def get_basic_financials(self, symbol: str) -> dict | None:
        """Basic financial metrics (FREE tier /stock/metric): revenue & EPS growth,
        margins, cash-flow/share, ROE/ROA, leverage. Returns the flat 'metric' dict, or
        None. This is the working free fundamentals source — FMP's are legacy/403 and
        yfinance is IP-blocked on the server, so this repairs the degraded fundamentals."""
        d = self._get("stock/metric", {"symbol": symbol.upper(), "metric": "all"})
        if isinstance(d, dict) and isinstance(d.get("metric"), dict):
            return d["metric"]
        return None

    def get_recent_earnings_surprise(self, symbol: str) -> dict | None:
        """The most recent REPORTED earnings surprise → {period_end, days_since, surprise_pct} or
        None. Uses /stock/earnings (FREE tier: last 4 quarters with actual/estimate/surprisePercent).
        The raw material for the post-earnings-announcement-drift (PEAD) factor. NOTE: 'days_since'
        counts from the fiscal PERIOD END (Finnhub gives no announcement date on the free tier); the
        actual announcement lags the period end by ~2-6 weeks, so the PEAD window is widened to suit."""
        from datetime import date, datetime as _dt
        d = self._get("stock/earnings", {"symbol": symbol.upper()})
        if not isinstance(d, list) or not d:
            return None
        cand = [r for r in d if r.get("actual") is not None
                and r.get("surprisePercent") is not None and r.get("period")]
        if not cand:
            return None
        r = sorted(cand, key=lambda x: x["period"], reverse=True)[0]   # most recent reported quarter
        try:
            pend = _dt.strptime(r["period"], "%Y-%m-%d").date()
            ds = (date.today() - pend).days
            return {"period_end": r["period"], "days_since": int(ds), "surprise_pct": round(float(r["surprisePercent"]), 2)}
        except (TypeError, ValueError):
            return None

    def get_insider_transactions(self, symbol: str) -> list | None:
        """SEC Form 4 insider transactions: rows with {name, change, transactionDate,
        transactionCode ('S'=sale, 'P'=purchase, 'A'=award, 'F'=tax), transactionPrice}."""
        d = self._get("stock/insider-transactions", {"symbol": symbol.upper()})
        if isinstance(d, dict) and isinstance(d.get("data"), list):
            return d["data"]
        return None


finnhub_client = FinnhubClient()
