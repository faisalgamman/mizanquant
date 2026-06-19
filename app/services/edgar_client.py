"""SEC EDGAR fundamentals client — FREE, unlimited, official US-issuer financials.

Why this exists: the FMP free tier covers only ~83 symbols/day, which is the binding
ceiling on how large a universe we can halal-screen. SEC EDGAR's XBRL ``companyfacts``
API is free, needs no key, and exposes exactly the balance-sheet + income-statement
fields the AAOIFI screen needs (total assets is the price-independent denominator). So
EDGAR lets the nightly warm job screen a much larger universe without touching FMP quota.

``get_fundamentals(symbol)`` returns the SAME ``{profile, bs, income}`` dict shape that
``halal_screening._yf_fallback`` returns, so it drops into ``screen_symbol`` unchanged.

SEC fair-access policy: a descriptive User-Agent is required, and requests are throttled
(SEC allows ~10/s; we stay well under). Parsed results are cached in the shared FMPCache
table (L2, persistent on the /data volume) so a warm universe re-screens from cache.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

logger = logging.getLogger("screener")

# A real contact is required by SEC fair-access policy — override in prod via env.
_USER_AGENT = os.environ.get(
    "EDGAR_USER_AGENT", "mizanquant halal-screener (contact: admin@mizanquant.app)"
)

# SEC permits ~10 req/s; stay conservative.
_MIN_INTERVAL = float(os.environ.get("EDGAR_MIN_INTERVAL", "0.20"))  # ~5 req/s
_LAST_CALL = 0.0
_LOCK = threading.Lock()

_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"

# Parsed-result cache TTL (fundamentals change only on quarterly filings).
_FUND_TTL = int(os.environ.get("EDGAR_FUND_TTL_DAYS", "30")) * 86400
_CIK_TTL = 7 * 86400

_http = httpx.Client(timeout=15, headers={"User-Agent": _USER_AGENT, "Accept-Encoding": "gzip, deflate"})

# Ordered XBRL tag candidates per concept (filers vary). First present wins.
_TAGS_ASSETS = ["Assets"]
_TAGS_DEBT_LT = ["LongTermDebtNoncurrent", "LongTermDebt"]
_TAGS_DEBT_CUR = ["LongTermDebtCurrent", "DebtCurrent", "ShortTermBorrowings"]
_TAGS_DEBT_TOTAL = ["DebtLongtermAndShorttermCombinedAmount"]
_TAGS_CASH = ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsAndShortTermInvestments"]
_TAGS_STI = [
    "ShortTermInvestments",
    "MarketableSecuritiesCurrent",
    "AvailableForSaleSecuritiesCurrent",
    "OtherShortTermInvestments",
]
_TAGS_RECV = ["AccountsReceivableNetCurrent", "ReceivablesNetCurrent"]
_TAGS_REVENUE = [
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
]
_TAGS_INT_INCOME = ["InvestmentIncomeInterest", "InterestAndDividendIncomeOperating", "InterestIncomeOperating"]
_TAGS_INT_EXPENSE = ["InterestExpense", "InterestExpenseDebt"]


def _throttle() -> None:
    global _LAST_CALL
    with _LOCK:
        elapsed = time.time() - _LAST_CALL
        if elapsed < _MIN_INTERVAL:
            time.sleep(_MIN_INTERVAL - elapsed)
        _LAST_CALL = time.time()


def _safe_float(val, default: float = 0.0) -> float:
    try:
        f = float(val)
        return default if (f != f or f in (float("inf"), float("-inf"))) else f
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# L2 cache — reuse the shared FMPCache table (persistent, no new migration)
# ---------------------------------------------------------------------------

def _cache_get(key: str) -> Any | None:
    try:
        from app.db.database import SessionLocal
        from app.db.models import FMPCache
        now = datetime.now(timezone.utc)
        db = SessionLocal()
        try:
            row = db.query(FMPCache).filter(FMPCache.cache_key == key, FMPCache.expires_at > now).first()
            return row.data if row else None
        finally:
            db.close()
    except Exception as exc:
        logger.debug("EDGAR cache read failed: %s", exc)
        return None


def _cache_set(key: str, data: Any, ttl: int) -> None:
    try:
        from app.db.database import SessionLocal
        from app.db.models import FMPCache
        expires = datetime.fromtimestamp(time.time() + ttl, tz=timezone.utc)
        db = SessionLocal()
        try:
            row = db.query(FMPCache).filter(FMPCache.cache_key == key).first()
            if row:
                row.data, row.expires_at = data, expires
            else:
                db.add(FMPCache(cache_key=key, data=data, expires_at=expires))
            db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()
    except Exception as exc:
        logger.debug("EDGAR cache write failed: %s", exc)


def _http_get_json(url: str) -> Any | None:
    _throttle()
    try:
        resp = _http.get(url)
        if resp.status_code == 404:
            return None  # symbol/CIK not an EDGAR filer (ETF, foreign, etc.)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.debug("EDGAR GET failed %s: %s", url, exc)
        return None


# ---------------------------------------------------------------------------
# Ticker -> CIK map (one small download, cached 7 days)
# ---------------------------------------------------------------------------

_cik_map: dict[str, str] | None = None


def get_cik_map() -> dict[str, str]:
    """Return {TICKER: zero-padded-10-digit-CIK}. Cached in memory + FMPCache."""
    global _cik_map
    if _cik_map is not None:
        return _cik_map
    cached = _cache_get("edgar:cik_map")
    if isinstance(cached, dict) and cached:
        _cik_map = cached
        return _cik_map
    data = _http_get_json(_TICKERS_URL)
    out: dict[str, str] = {}
    if isinstance(data, dict):
        for entry in data.values():
            try:
                out[str(entry["ticker"]).upper()] = str(int(entry["cik_str"])).zfill(10)
            except (KeyError, ValueError, TypeError):
                continue
    if out:
        _cik_map = out
        _cache_set("edgar:cik_map", out, _CIK_TTL)
    return _cik_map or {}


def get_cik_for(symbol: str) -> Optional[str]:
    return get_cik_map().get(symbol.upper())


# ---------------------------------------------------------------------------
# XBRL fact extraction
# ---------------------------------------------------------------------------

def _latest_fact(usgaap: dict, tags: list[str], *, annual: bool) -> Optional[float]:
    """Most recent value across the first matching tag.

    annual=True → prefer 10-K/20-F; for duration (income) tags pick ~1-year periods.
    """
    for tag in tags:
        node = usgaap.get(tag)
        if not node:
            continue
        units = node.get("units") or {}
        entries = units.get("USD") or next((v for v in units.values()), [])
        if not entries:
            continue
        cands = []
        for e in entries:
            if e.get("val") is None or not e.get("end"):
                continue
            form = str(e.get("form") or "")
            is_annual_form = form in ("10-K", "10-K/A", "20-F", "20-F/A", "40-F")
            # Income (duration) facts carry a start; keep ~annual spans only.
            if e.get("start"):
                try:
                    span = (datetime.fromisoformat(e["end"]) - datetime.fromisoformat(e["start"])).days
                except ValueError:
                    span = 0
                if annual and span < 300:
                    continue
            if annual and not is_annual_form:
                continue
            cands.append(e)
        if not cands and annual:
            # No clean annual entry — fall back to the most recent of any form.
            cands = [e for e in entries if e.get("val") is not None and e.get("end")]
        if cands:
            best = max(cands, key=lambda e: e["end"])
            return _safe_float(best.get("val"))
    return None


def _sum_debt(usgaap: dict) -> float:
    total = _latest_fact(usgaap, _TAGS_DEBT_TOTAL, annual=True)
    if total is not None and total > 0:
        return total
    lt = _latest_fact(usgaap, _TAGS_DEBT_LT, annual=True) or 0.0
    cur = _latest_fact(usgaap, _TAGS_DEBT_CUR, annual=True) or 0.0
    return lt + cur


def _get_sic(cik: str) -> tuple[str, str]:
    """(sic_code, sic_description) from the submissions endpoint — feeds the sector
    activity classifier (e.g. 'State commercial banks', 'Real estate investment trusts')."""
    cached = _cache_get(f"edgar:sic:{cik}")
    if isinstance(cached, dict):
        return cached.get("sic", ""), cached.get("sicDescription", "")
    data = _http_get_json(_SUBMISSIONS_URL.format(cik=cik))
    if not isinstance(data, dict):
        return "", ""
    sic = str(data.get("sic") or "")
    desc = str(data.get("sicDescription") or "")
    _cache_set(f"edgar:sic:{cik}", {"sic": sic, "sicDescription": desc, "name": data.get("name", "")}, _CIK_TTL)
    return sic, desc


def get_fundamentals(symbol: str) -> Optional[dict]:
    """Fetch AAOIFI-screening fundamentals from SEC EDGAR.

    Returns the SAME shape as ``halal_screening._yf_fallback`` ({profile, bs, income})
    so it plugs into ``screen_symbol`` unchanged, or None if the symbol is not an EDGAR
    XBRL filer (ETF / foreign / no facts). Parsed result is cached 30 days.
    """
    symbol = symbol.upper().strip()
    cached = _cache_get(f"edgar:fund:{symbol}")
    if isinstance(cached, dict):
        return cached

    cik = get_cik_for(symbol)
    if not cik:
        return None
    facts = _http_get_json(_FACTS_URL.format(cik=cik))
    if not isinstance(facts, dict):
        return None
    usgaap = (facts.get("facts") or {}).get("us-gaap") or {}
    dei = (facts.get("facts") or {}).get("dei") or {}
    if not usgaap:
        return None

    total_assets = _latest_fact(usgaap, _TAGS_ASSETS, annual=True) or 0.0
    total_debt = _sum_debt(usgaap)
    cash = _latest_fact(usgaap, _TAGS_CASH, annual=True) or 0.0
    # Short-term investments are tagged inconsistently across filers. Take the MAX disclosed
    # value rather than the first matching tag — avoids double-counting while never
    # UNDER-reporting liquidity (under-reporting could wrongly pass a borderline name).
    short_inv = max([_latest_fact(usgaap, [t], annual=True) or 0.0 for t in _TAGS_STI])
    receivables = _latest_fact(usgaap, _TAGS_RECV, annual=True) or 0.0
    revenue = _latest_fact(usgaap, _TAGS_REVENUE, annual=True) or 0.0
    interest_income = _latest_fact(usgaap, _TAGS_INT_INCOME, annual=True) or 0.0
    interest_expense = _latest_fact(usgaap, _TAGS_INT_EXPENSE, annual=True) or 0.0

    # Sector hint (SIC) for the haram-activity classifier.
    _sic, sic_desc = _get_sic(cik)
    company_name = facts.get("entityName") or symbol

    # marketCap (display only — AAOIFI denominator is total assets) = shares × spot price,
    # best-effort. Absence must NOT block the screen (the gate is relaxed under AAOIFI).
    shares = 0.0
    try:
        sh_node = dei.get("EntityCommonStockSharesOutstanding") or {}
        sh_entries = (sh_node.get("units") or {}).get("shares") or []
        if sh_entries:
            shares = _safe_float(max(sh_entries, key=lambda e: e.get("end", "")).get("val"))
    except Exception:
        shares = 0.0
    price = 0.0
    market_cap = 0.0
    try:
        from app.services import market_data as md
        df = md.fetch(symbol, period="5d")
        if df is not None and len(df) and "close" in df.columns:
            price = float(df["close"].iloc[-1])
            if shares > 0 and price > 0:
                market_cap = shares * price
    except Exception:
        pass

    result = {
        "profile": {
            "marketCap": market_cap,
            "sharesOutstanding": shares,
            "price": price,
            "sector": sic_desc,      # SIC description feeds the keyword activity classifier
            "industry": sic_desc,
            "companyName": company_name,
        },
        "bs": {
            "totalDebt": total_debt,
            "cashAndCashEquivalents": cash,
            "shortTermInvestments": short_inv,
            "cashAndShortTermInvestments": cash + short_inv,
            "netReceivables": receivables,
            "totalAssets": total_assets,   # AAOIFI denominator
        },
        "income": {
            "revenue": revenue,
            "interestIncome": interest_income,
            "interestExpense": interest_expense,
        },
        "_source": "edgar",
    }
    _cache_set(f"edgar:fund:{symbol}", result, _FUND_TTL)
    return result
