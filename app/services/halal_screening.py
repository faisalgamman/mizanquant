"""DJIM-standard Halal compliance screening.

Implements the Dow Jones Islamic Market (DJIM) 3-ratio financial
screens using fundamental data from Financial Modeling Prep (FMP)
free API (via consolidated FMPClient):

1. Debt Screen:       Total Debt / avg_mcap_24m < 33%
2. Interest Screen:   Interest Income / Total Revenue < 5%
3. Haram Revenue:     Sector/industry disqualification (incl. REITs/real estate)
4. Liquidity Screen:  (Cash + Interest-Bearing Securities) / avg_mcap_24m < 33%
5. Receivables Screen: Net Receivables / avg_mcap_24m < 33%

Free tier: 250 requests/day. Each symbol needs 3 API calls
(balance sheet + income statement + profile), so ~83 symbols/day.
"""

import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.exc import SQLAlchemyError

from app.config import settings
from app.db.database import SessionLocal
from app.db.models import ScreeningResult
from app.services.fmp_client import fmp_client

logger = logging.getLogger("screener")

# ── yfinance fundamentals circuit breaker ────────────────────────────────────
# FMP free tier covers only ~83 symbols/day, so a 657-symbol screen leans heavily
# on the yfinance fundamentals fallback. When Yahoo throttles/blocks the server IP
# every call burns its full timeout — observed: 365 × 30–45s timeouts crawling one
# monthly scan into oblivion (then the stall-detector re-kicks it from scratch).
# This breaker fails fast once yfinance is clearly down: after N consecutive misses
# we skip yfinance for a cooldown so the bulk scan COMPLETES (degraded coverage)
# instead of hanging. It self-heals — any success resets it, and it reopens to
# yfinance after the cooldown. No effect when yfinance is healthy (just a tighter
# per-call timeout). All knobs are env-overridable.
_YF_TIMEOUT: float = float(os.environ.get("HALAL_YF_TIMEOUT", "8"))
_YF_CB_THRESHOLD: int = int(os.environ.get("HALAL_YF_CB_THRESHOLD", "12"))
_YF_CB_COOLDOWN: float = float(os.environ.get("HALAL_YF_CB_COOLDOWN", "300"))
_YF_CB_LOCK = threading.Lock()
_YF_CB = {"fails": 0, "until": 0.0}


def _yf_cb_is_open() -> bool:
    with _YF_CB_LOCK:
        return time.time() < _YF_CB["until"]


def _yf_cb_record(success: bool) -> None:
    with _YF_CB_LOCK:
        if success:
            _YF_CB["fails"] = 0
            return
        _YF_CB["fails"] += 1
        if _YF_CB["fails"] >= _YF_CB_THRESHOLD:
            _YF_CB["until"] = time.time() + _YF_CB_COOLDOWN
            _YF_CB["fails"] = 0
            logger.warning(
                "halal: yfinance fundamentals circuit OPEN (%d consecutive misses) — "
                "skipping yfinance for %.0fs so the scan completes",
                _YF_CB_THRESHOLD, _YF_CB_COOLDOWN,
            )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_float(val, default: float = 0.0) -> float:
    """Convert value to float, treating None/NaN/Inf as default.

    yfinance often returns NaN for missing data. float('nan') is truthy
    so the common ``val or 0`` pattern silently passes NaN through,
    which later crashes FastAPI's JSON serializer (NaN is invalid JSON).
    """
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
# Haram sectors/industries — excluded by default (AAOIFI standard)
# ---------------------------------------------------------------------------
HARAM_SECTORS = {
    "financial services",
    "financial",
    "banks",
    "insurance",
    "real estate",          # REITs and real estate companies
}

HARAM_INDUSTRIES = {
    # Alcohol
    "beverages—brewers", "beverages—wineries & distilleries",
    # Tobacco
    "tobacco",
    # Gambling
    "gambling", "casinos & gaming", "resorts & casinos",
    # Interest-based financial institutions
    "banks—regional", "banks—diversified", "credit services",
    "insurance—diversified", "insurance—life", "insurance—property & casualty",
    "insurance—specialty", "insurance—reinsurance",
    "mortgage finance", "capital markets",
    # Real Estate / REITs
    "reit—diversified", "reit—healthcare facilities", "reit—hotel & motel",
    "reit—industrial", "reit—mortgage", "reit—office", "reit—residential",
    "reit—retail", "reit—specialty", "real estate—development",
    "real estate—diversified", "real estate services",
    "mortgage real estate investment trusts (reits)",
}

# Industries that require manual review per AAOIFI — not auto-disqualified.
# packaged_foods: may contain pork products (needs label check)
# entertainment: may contain adult content (needs content check)
_REVIEW_INDUSTRIES = {
    "packaged foods",
    "entertainment",
}

def _yf_fallback(symbol: str) -> Optional[dict]:
    """Fallback: fetch fundamental data from yfinance when FMP fails (premium block).

    Guarded by a circuit breaker: when yfinance is systematically blocked we skip it
    immediately (return None) rather than paying the per-call timeout on every symbol.
    """
    if _yf_cb_is_open():
        return None
    from app.services.market_context import _run_with_timeout

    def _do_yf():
        import yfinance as yf
        try:
            from app.services.market_data import _configure_yfinance_cache
            _configure_yfinance_cache(yf)
        except Exception:
            pass
        ticker = yf.Ticker(symbol)
        info = ticker.info or {}

        market_cap = _safe_float(info.get("marketCap"))
        if market_cap <= 0:
            logger.warning(f"yfinance: no market cap for {symbol}")
            return None

        total_debt = _safe_float(info.get("totalDebt"))
        cash_eq = _safe_float(info.get("totalCash"))
        short_inv = 0.0
        net_receivables = 0.0

        try:
            bs = ticker.balance_sheet
            if bs is not None and not bs.empty:
                latest = bs.iloc[:, 0]
                total_debt = _safe_float(latest.get("Total Debt"), total_debt)
                cash_eq = _safe_float(latest.get("Cash And Cash Equivalents"), cash_eq)
                short_inv = _safe_float(latest.get("Other Short Term Investments"))
                net_receivables = _safe_float(latest.get("Receivables")) or _safe_float(latest.get("Accounts Receivable"))
        except Exception as e:
            logger.error(f"{symbol}: balance_sheet fallback failed, using info-based values: {e}")

        revenue = _safe_float(info.get("totalRevenue"))
        interest_income = 0.0
        interest_expense = 0.0

        try:
            inc = ticker.income_stmt
            if inc is not None and not inc.empty:
                latest_inc = inc.iloc[:, 0]
                revenue = _safe_float(latest_inc.get("Total Revenue"), revenue)
                interest_income = _safe_float(latest_inc.get("Interest Income"))
                interest_expense = _safe_float(latest_inc.get("Interest Expense"))
        except Exception as e:
            logger.error(f"{symbol}: income_stmt fallback failed, using info-based values: {e}")

        logger.info(f"yfinance fallback OK for {symbol}: mcap={market_cap}, debt={total_debt}")
        return {
            "profile": {
                "marketCap": market_cap,
                "sharesOutstanding": _safe_float(info.get("sharesOutstanding")),
                "price": _safe_float(info.get("currentPrice")) or _safe_float(info.get("regularMarketPrice")),
                "sector": info.get("sector", ""),
                "industry": info.get("industry", ""),
                "companyName": info.get("shortName", symbol),
            },
            "bs": {
                "totalDebt": total_debt,
                "cashAndCashEquivalents": cash_eq,
                "shortTermInvestments": short_inv,
                "cashAndShortTermInvestments": cash_eq + short_inv,
                "netReceivables": net_receivables,  # extracted from yfinance balance sheet
            },
            "income": {
                "revenue": revenue,
                "interestIncome": interest_income,
                "interestExpense": interest_expense,
            },
        }

    result = _run_with_timeout(_do_yf, timeout=_YF_TIMEOUT, fallback=None)
    _yf_cb_record(result is not None)
    return result


def _avg_market_cap_24m(symbol: str, shares: float, spot_mcap: float) -> tuple[float, str]:
    """Compute trailing 24-month average market cap for DJIM ratio denominator.

    Returns (avg_mcap, basis) where basis is 'avg_24m' or 'spot_fallback'.
    Uses market_data.fetch(period='2y') to get 2 years of daily closes,
    multiplies by shares_outstanding (assumed constant — labelled approximation).
    Falls back to spot market cap if price history unavailable.
    """
    if shares <= 0 or spot_mcap <= 0:
        return spot_mcap, "spot_fallback"
    try:
        from app.services import market_data as md
        df = md.fetch(symbol, period="2y")
        if df is None or df.empty or "close" not in df.columns:
            return spot_mcap, "spot_fallback"
        avg_close = float(df["close"].mean())
        avg_mcap = avg_close * shares
        if avg_mcap <= 0:
            return spot_mcap, "spot_fallback"
        return avg_mcap, "avg_24m"
    except Exception as e:
        logger.debug("_avg_market_cap_24m %s: %s — using spot", symbol, e)
        return spot_mcap, "spot_fallback"


def screen_symbol(symbol: str) -> Optional[dict]:
    """Run DJIM-standard Halal screening on a single symbol.

    Implements the Dow Jones Islamic Market 3-ratio financial screens
    (debt, liquidity, receivables) each < 33% of trailing 24-month
    average market cap, plus non-permissible income < 5% of revenue
    and sector/industry exclusion (expanded: REITs/real estate added).

    Returns a dict with screening results, or None if data unavailable.
    Uses FMP API with yfinance fallback for premium-blocked symbols.
    """
    # 1. Fetch profile (market cap + sector/industry)
    profile = fmp_client.get_profile(symbol)
    if not profile:
        yf_data = _yf_fallback(symbol)
        if not yf_data:
            return None
        profile = yf_data["profile"]
        bs = yf_data["bs"]
        income = yf_data["income"]
        used_fallback = True
    else:
        used_fallback = False

    market_cap = _safe_float(profile.get("marketCap")) or _safe_float(profile.get("mktCap"))
    # Shares outstanding for 24m avg mcap calculation
    shares_outstanding = _safe_float(profile.get("sharesOutstanding"))
    if shares_outstanding <= 0 and market_cap > 0:
        price = _safe_float(profile.get("price"))
        if price > 0:
            shares_outstanding = market_cap / price
    sector = (profile.get("sector") or "").lower().strip()
    industry = (profile.get("industry") or "").lower().strip()
    company_name = profile.get("companyName", symbol)

    if market_cap <= 0:
        logger.warning(f"No market cap for {symbol}")
        return None

    # DJIM: use trailing 24-month average market cap as ratio denominator
    avg_mcap, mcap_basis = _avg_market_cap_24m(symbol, shares_outstanding, market_cap)
    djim_denom = avg_mcap  # all 3 ratios use this denominator

    # 2. Haram sector/industry check
    haram_sector_flag = sector in HARAM_SECTORS
    haram_industry_flag = industry in HARAM_INDUSTRIES
    needs_manual_review = industry in _REVIEW_INDUSTRIES

    # 3. Fetch balance sheet (skip if already from yfinance)
    if not used_fallback:
        bs_data = fmp_client.get_balance_sheet(symbol, limit=1)
        if not bs_data:
            yf_data = _yf_fallback(symbol)
            if not yf_data:
                return None
            bs = yf_data["bs"]
            income = yf_data["income"]
            used_fallback = True
        else:
            bs = bs_data[0]

    total_debt = _safe_float(bs.get("totalDebt"))
    cash_and_equivalents = _safe_float(bs.get("cashAndCashEquivalents"))
    short_term_investments = _safe_float(bs.get("shortTermInvestments"))
    cash_and_short_term = _safe_float(bs.get("cashAndShortTermInvestments"))
    net_receivables = _safe_float(bs.get("netReceivables"))

    # 4. Fetch income statement (skip if already from yfinance)
    if not used_fallback:
        is_data = fmp_client.get_income_statement(symbol, limit=1)
        if not is_data:
            yf_data = _yf_fallback(symbol)
            if not yf_data:
                return None
            income = yf_data["income"]
        else:
            income = is_data[0]

    revenue = _safe_float(income.get("revenue"))
    interest_income = _safe_float(income.get("interestIncome"))
    interest_expense = _safe_float(income.get("interestExpense"))

    # ---------------------------------------------------------------------------
    # AAOIFI Screens
    # ---------------------------------------------------------------------------

    # ── DJIM Financial Screens (denominator = trailing 24-month avg market cap) ──

    # Screen 1 (DJIM): Total Debt / avg_mcap_24m < 33%
    debt_ratio = (total_debt / djim_denom * 100) if djim_denom > 0 else 999.0
    debt_pass = debt_ratio < 33.0

    # Screen 2 (DJIM): Non-permissible income — Interest / Revenue < 5%
    interest_ratio = (abs(interest_income) / revenue * 100) if revenue > 0 else 0.0
    interest_pass = interest_ratio < 5.0

    # Screen 3 (DJIM): Haram sector/industry (expanded: REITs/real-estate added)
    haram_pass = not (haram_sector_flag or haram_industry_flag)

    # Screen 4 (DJIM): Liquidity — (Cash + Interest-Bearing Securities) / avg_mcap_24m < 33%
    liquid_assets = cash_and_short_term if cash_and_short_term > 0 else (cash_and_equivalents + short_term_investments)
    liquidity_ratio = (liquid_assets / djim_denom * 100) if djim_denom > 0 else 999.0
    liquidity_pass = liquidity_ratio < 33.0

    # Screen 5 (DJIM): Net Receivables / avg_mcap_24m < 33%
    receivable_ratio = (net_receivables / djim_denom * 100) if djim_denom > 0 else 0.0
    receivable_pass = receivable_ratio < 33.0

    # Final DJIM verdict — all 5 screens must pass
    is_halal = debt_pass and interest_pass and haram_pass and liquidity_pass and receivable_pass

    # ── Data-confidence label (honesty layer — NO threshold change) ──
    _low = []
    if mcap_basis == "spot_fallback":
        _low.append("no 24-month average (spot mcap used)")
    if total_debt == 0 and liquid_assets == 0:
        _low.append("balance-sheet data missing/zero")
    if revenue <= 0:
        _low.append("revenue unavailable")
    halal_confidence = "high" if not _low else "partial"

    result = {
        "symbol": symbol,
        "company_name": company_name,
        "sector": profile.get("sector", ""),
        "industry": profile.get("industry", ""),
        "market_cap": market_cap,
        "is_halal": is_halal,
        "needs_manual_review": needs_manual_review,
        # Ratios
        "debt_ratio": round(debt_ratio, 2),
        "debt_pass": debt_pass,
        "interest_ratio": round(interest_ratio, 2),
        "interest_pass": interest_pass,
        "haram_revenue": not haram_pass,
        "haram_pass": haram_pass,
        "liquidity_ratio": round(liquidity_ratio, 2),
        "liquidity_pass": liquidity_pass,
        # Raw data for audit
        "total_debt": total_debt,
        "revenue": revenue,
        "interest_income": interest_income,
        "cash_and_short_term": liquid_assets,
        # DJIM additions
        "standard": "DJIM",
        "avg_market_cap": round(avg_mcap, 0),
        "mcap_basis": mcap_basis,
        "net_receivables": net_receivables,
        "receivable_ratio": round(receivable_ratio, 2),
        "receivable_pass": receivable_pass,
        "disclaimer": "Quantitative DJIM screen only — no named Sharia supervisory board.",
        # Metadata
        "screens_passed": sum([debt_pass, interest_pass, haram_pass, liquidity_pass, receivable_pass]),
        "screens_total": 5,
        # ── Reliability v2 (additive display — no threshold/logic change) ──
        "halal_confidence": halal_confidence,
        "confidence_reasons": _low,
        "purification_pct": round(interest_ratio, 2),
        "purification_note": "طهّر هذه النسبة من أرباح هذا السهم (تقدير DJIM من دخل الفائدة/الإيراد)",
    }

    return result


def screen_and_store(symbol: str) -> Optional[dict]:
    """Screen a symbol and persist results to database."""
    result = screen_symbol(symbol)
    if result is None:
        return None

    try:
        db = SessionLocal()
        try:
            existing = db.query(ScreeningResult).filter(
                ScreeningResult.symbol == symbol
            ).first()

            if existing:
                existing.debt_ratio = result["debt_ratio"]
                existing.interest_ratio = result["interest_ratio"]
                existing.haram_revenue_ratio = 100.0 if result["haram_revenue"] else 0.0
                existing.liquidity_ratio = result["liquidity_ratio"]
                existing.is_halal = result["is_halal"]
                existing.sector = result.get("sector", "")
                existing.last_screened = _utc_now()
                existing.details = result
            else:
                row = ScreeningResult(
                    symbol=symbol,
                    debt_ratio=result["debt_ratio"],
                    interest_ratio=result["interest_ratio"],
                    haram_revenue_ratio=100.0 if result["haram_revenue"] else 0.0,
                    liquidity_ratio=result["liquidity_ratio"],
                    is_halal=result["is_halal"],
                    sector=result.get("sector", ""),
                    last_screened=_utc_now(),
                    details=result,
                )
                db.add(row)

            db.commit()
        finally:
            db.close()
    except SQLAlchemyError as e:
        logger.error(f"DB error storing screening for {symbol}: {e}")

    return result


def get_halal_status(symbol: str) -> Optional[dict]:
    """Get cached Halal status from DB, or screen live if stale/missing."""
    try:
        db = SessionLocal()
        try:
            row = db.query(ScreeningResult).filter(
                ScreeningResult.symbol == symbol
            ).first()

            if row and row.last_screened:
                age_days = (_utc_now() - row.last_screened).days
                if age_days < 7:  # Cache for 7 days (data is quarterly)
                    return row.details if row.details else {
                        "symbol": row.symbol,
                        "is_halal": row.is_halal,
                        "debt_ratio": row.debt_ratio,
                        "interest_ratio": row.interest_ratio,
                        "liquidity_ratio": row.liquidity_ratio,
                        "sector": row.sector,
                        "cached": True,
                        "last_screened": row.last_screened.isoformat(),
                    }
        finally:
            db.close()
    except SQLAlchemyError as e:
        logger.error(f"DB read error for {symbol} screening: {e}")

    # Not cached or stale — screen live
    if settings.FMP_API_KEY:
        return screen_and_store(symbol)

    return None


def get_screening_report() -> list[dict]:
    """Get all screening results from the database."""
    try:
        db = SessionLocal()
        try:
            rows = db.query(ScreeningResult).order_by(
                ScreeningResult.is_halal.desc(),
                ScreeningResult.symbol.asc(),
            ).all()

            results = []
            for row in rows:
                results.append({
                    "Symbol": row.symbol,
                    "Halal": "YES" if row.is_halal else "NO",
                    "Debt %": row.debt_ratio,
                    "Interest %": row.interest_ratio,
                    "Liquidity %": row.liquidity_ratio,
                    "Sector": row.sector or "",
                    "Last Screened": row.last_screened.strftime("%Y-%m-%d") if row.last_screened else "",
                })
            return results
        finally:
            db.close()
    except SQLAlchemyError as e:
        logger.error(f"DB error reading screening report: {e}")
        return []


# ---------------------------------------------------------------------------
# verify_halal — runtime halal check with session cache (moved from halal_screener)
# ---------------------------------------------------------------------------

_VERIFIED_HARAM: set = set()
_VERIFIED_HALAL: set = set()
_CURATED_SET = None


def _get_curated_set():
    global _CURATED_SET
    if _CURATED_SET is None:
        from app.services.universe import HALAL_STOCKS_FALLBACK
        _CURATED_SET = set(HALAL_STOCKS_FALLBACK)
    return _CURATED_SET


def verify_halal(symbol: str) -> tuple[bool, str]:
    """Check if a symbol is verified halal. Returns (is_halal, reason).

    Priority:
    1. If in HARAM_EXCLUDE → blocked (sector-level exclusion)
    2. If already verified this session → use cached result
    3. If in curated HALAL_STOCKS list → allowed (already sector-filtered)
    4. If FMP data available → run live AAOIFI screening
    5. If not in curated list AND FMP unavailable → blocked
    """
    from app.services.universe import HARAM_EXCLUDE

    sym = symbol.upper().strip()

    # 1. Sector-level exclusion (instant reject)
    if sym in HARAM_EXCLUDE:
        return False, "Excluded sector (banks/insurance/alcohol/gambling/weapons/utilities/REITs)"

    # 2. Session cache — fast path
    if sym in _VERIFIED_HALAL:
        return True, "Verified halal"
    if sym in _VERIFIED_HARAM:
        return False, "Verified haram (AAOIFI screening failed)"

    # 3. Curated list — stocks already passed sector exclusion
    curated = _get_curated_set()
    if sym in curated:
        _VERIFIED_HALAL.add(sym)
        return True, "Halal (curated S&P 500 list, sector-verified)"

    # 4. Not in curated list — must verify via FMP AAOIFI screening
    try:
        result = get_halal_status(sym)
        if result is not None:
            if result.get("is_halal"):
                _VERIFIED_HALAL.add(sym)
                return True, "Verified halal (AAOIFI)"
            else:
                _VERIFIED_HARAM.add(sym)
                reasons = []
                if not result.get("debt_pass", True):
                    reasons.append(f"debt {result.get('debt_ratio', '?')}% > 33%")
                if not result.get("interest_pass", True):
                    reasons.append(f"interest {result.get('interest_ratio', '?')}% > 5%")
                if not result.get("haram_pass", True):
                    reasons.append("haram sector/industry")
                if not result.get("liquidity_pass", True):
                    reasons.append(f"liquidity {result.get('liquidity_ratio', '?')}% > 33%")
                return False, f"Haram: {', '.join(reasons)}" if reasons else "Verified haram"
        else:
            logger.warning(f"Halal gate: {sym} BLOCKED — not in curated list, no FMP data")
            return False, "Cannot verify — not in curated list and no financial data"
    except Exception as e:
        logger.error(f"Halal verification error for {sym}: {e}")
        return False, f"Verification error: {e}"


def batch_screen(symbols: list[str], max_per_run: int = 80) -> dict:
    """Screen multiple symbols in batch. Respects FMP daily limit.

    Args:
        symbols: List of stock symbols to screen.
        max_per_run: Max symbols per batch (default 80 = 240 API calls, under 250 limit).

    Returns:
        Dict with counts: screened, halal, haram, errors, skipped.
    """
    if not settings.FMP_API_KEY:
        return {"error": "FMP_API_KEY not configured"}

    stats = {"screened": 0, "halal": 0, "haram": 0, "errors": 0, "skipped": 0}

    # Skip symbols already screened within 7 days
    fresh_cutoff = _utc_now() - timedelta(days=7)
    try:
        db = SessionLocal()
        try:
            recent = db.query(ScreeningResult.symbol).filter(
                ScreeningResult.last_screened >= fresh_cutoff
            ).all()
            recent_symbols = {r.symbol for r in recent}
        finally:
            db.close()
    except SQLAlchemyError:
        recent_symbols = set()

    to_screen = [s for s in symbols if s not in recent_symbols]
    stats["skipped"] = len(symbols) - len(to_screen)

    # Limit to max_per_run
    to_screen = to_screen[:max_per_run]

    for symbol in to_screen:
        try:
            result = screen_and_store(symbol)
            if result:
                stats["screened"] += 1
                if result["is_halal"]:
                    stats["halal"] += 1
                else:
                    stats["haram"] += 1
            else:
                stats["errors"] += 1
        except Exception as e:
            logger.error(f"Screening error for {symbol}: {e}")
            stats["errors"] += 1

    logger.info(f"Batch screening complete: {stats}")
    return stats
