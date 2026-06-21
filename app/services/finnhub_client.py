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

    def get_insider_transactions(self, symbol: str) -> list | None:
        """SEC Form 4 insider transactions: rows with {name, change, transactionDate,
        transactionCode ('S'=sale, 'P'=purchase, 'A'=award, 'F'=tax), transactionPrice}."""
        d = self._get("stock/insider-transactions", {"symbol": symbol.upper()})
        if isinstance(d, dict) and isinstance(d.get("data"), list):
            return d["data"]
        return None


finnhub_client = FinnhubClient()
