"""AAOIFI-standard Halal compliance screening.

Implements the four AAOIFI screens using fundamental data from
Financial Modeling Prep (FMP) free API:

1. Debt Screen:     Total Debt / Market Cap < 33%
2. Interest Screen: Interest Income / Total Revenue < 5%
3. Haram Revenue:   Sector/industry disqualification (SIC codes)
4. Liquidity Screen: (Cash + Interest-Bearing Securities) / Market Cap < 33%

Free tier: 250 requests/day. Each symbol needs 3 API calls
(balance sheet + income statement + profile), so ~83 symbols/day.
"""

import logging
import time
from datetime import datetime, timedelta
from typing import Optional

import httpx
from sqlalchemy.exc import SQLAlchemyError

from app.config import settings
from app.db.database import SessionLocal
from app.db.models import ScreeningResult

logger = logging.getLogger("screener")

# ---------------------------------------------------------------------------
# Haram sectors/industries — excluded by default (AAOIFI standard)
# ---------------------------------------------------------------------------
HARAM_SECTORS = {
    "financial services",
    "financial",
    "banks",
    "insurance",
}

HARAM_INDUSTRIES = {
    # Alcohol
    "beverages—brewers", "beverages—wineries & distilleries",
    # Tobacco
    "tobacco",
    # Gambling
    "gambling", "casinos & gaming", "resorts & casinos",
    # Weapons (conventional arms dealers — defense contractors evaluated case-by-case)
    # Pork processing
    "packaged foods",  # flagged for manual review, not auto-disqualified
    # Adult entertainment
    "entertainment",  # flagged, not auto-disqualified
    # Interest-based financial institutions
    "banks—regional", "banks—diversified", "credit services",
    "insurance—diversified", "insurance—life", "insurance—property & casualty",
    "insurance—specialty", "insurance—reinsurance",
    "mortgage finance", "capital markets",
}

# Rate limiting for FMP API (250/day = ~10/min to be safe)
_fmp_last_call = 0.0
_FMP_MIN_INTERVAL = 0.5  # seconds between calls


def _fmp_rate_limit():
    """Simple rate limiter for FMP API calls."""
    global _fmp_last_call
    elapsed = time.time() - _fmp_last_call
    if elapsed < _FMP_MIN_INTERVAL:
        time.sleep(_FMP_MIN_INTERVAL - elapsed)
    _fmp_last_call = time.time()


def _fmp_get(endpoint: str, symbol: str) -> Optional[dict | list]:
    """Make a rate-limited GET request to FMP stable API."""
    if not settings.FMP_API_KEY:
        return None

    _fmp_rate_limit()

    # FMP migrated to /stable/ endpoints (Aug 2025)
    url = f"https://financialmodelingprep.com/stable/{endpoint}"
    params = {"symbol": symbol, "apikey": settings.FMP_API_KEY}

    # Add period=annual for financial statements
    if endpoint in ("balance-sheet-statement", "income-statement"):
        params["period"] = "annual"
        params["limit"] = 1  # latest only

    try:
        with httpx.Client(timeout=8) as client:
            resp = client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            return data
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 429:
            logger.warning("FMP rate limit hit — waiting 60s")
            time.sleep(60)
        elif e.response.status_code == 402:
            logger.debug(f"FMP 402 (premium required) for {symbol}/{endpoint} — skipping")
        elif e.response.status_code == 403:
            logger.error("FMP API key invalid or expired")
        else:
            logger.error(f"FMP HTTP {e.response.status_code} for {symbol}/{endpoint}")
        return None
    except Exception as e:
        logger.error(f"FMP request failed for {symbol}/{endpoint}: {e}")
        return None


def _yf_fallback(symbol: str) -> Optional[dict]:
    """Fallback: fetch fundamental data from yfinance when FMP fails (premium block)."""
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        info = ticker.info or {}

        market_cap = info.get("marketCap", 0) or 0
        if market_cap <= 0:
            logger.warning(f"yfinance: no market cap for {symbol}")
            return None

        # Balance sheet fields
        total_debt = float(info.get("totalDebt", 0) or 0)
        cash_eq = float(info.get("totalCash", 0) or 0)
        short_inv = 0

        # Try balance_sheet DataFrame for more detail
        try:
            bs = ticker.balance_sheet
            if bs is not None and not bs.empty:
                latest = bs.iloc[:, 0]
                total_debt = float(latest.get("Total Debt", total_debt) or total_debt)
                cash_eq = float(latest.get("Cash And Cash Equivalents", cash_eq) or cash_eq)
                short_inv = float(latest.get("Other Short Term Investments", 0) or 0)
        except Exception:
            pass  # use info-based values

        # Income statement fields
        revenue = float(info.get("totalRevenue", 0) or 0)
        interest_income = 0
        interest_expense = 0

        try:
            inc = ticker.income_stmt
            if inc is not None and not inc.empty:
                latest_inc = inc.iloc[:, 0]
                revenue = float(latest_inc.get("Total Revenue", revenue) or revenue)
                interest_income = float(latest_inc.get("Interest Income", 0) or 0)
                interest_expense = float(latest_inc.get("Interest Expense", 0) or 0)
        except Exception:
            pass  # use info-based values

        logger.info(f"yfinance fallback OK for {symbol}: mcap={market_cap}, debt={total_debt}")
        return {
            "profile": {
                "marketCap": market_cap,
                "sector": info.get("sector", ""),
                "industry": info.get("industry", ""),
                "companyName": info.get("shortName", symbol),
            },
            "bs": {
                "totalDebt": total_debt,
                "cashAndCashEquivalents": cash_eq,
                "shortTermInvestments": short_inv,
                "cashAndShortTermInvestments": cash_eq + short_inv,
            },
            "income": {
                "revenue": revenue,
                "interestIncome": interest_income,
                "interestExpense": interest_expense,
            },
        }
    except Exception as e:
        logger.error(f"yfinance fallback failed for {symbol}: {e}")
        return None


def screen_symbol(symbol: str) -> Optional[dict]:
    """Run AAOIFI Halal screening on a single symbol.

    Returns a dict with screening results, or None if data unavailable.
    Uses FMP API with yfinance fallback for premium-blocked symbols.
    """
    # 1. Fetch profile (market cap + sector/industry)
    profile_data = _fmp_get("profile", symbol)
    if not profile_data or not isinstance(profile_data, list) or len(profile_data) == 0:
        # Try yfinance fallback
        yf_data = _yf_fallback(symbol)
        if not yf_data:
            return None
        profile = yf_data["profile"]
        bs = yf_data["bs"]
        income = yf_data["income"]
        used_fallback = True
    else:
        profile = profile_data[0]
        used_fallback = False

    market_cap = profile.get("marketCap") or profile.get("mktCap") or 0
    sector = (profile.get("sector") or "").lower().strip()
    industry = (profile.get("industry") or "").lower().strip()
    company_name = profile.get("companyName", symbol)

    if market_cap <= 0:
        logger.warning(f"No market cap for {symbol}")
        return None

    # 2. Haram sector check (instant disqualification)
    haram_sector_flag = sector in HARAM_SECTORS
    haram_industry_flag = industry in HARAM_INDUSTRIES

    # 3. Fetch balance sheet (skip if already from yfinance)
    if not used_fallback:
        bs_data = _fmp_get("balance-sheet-statement", symbol)
        if not bs_data or not isinstance(bs_data, list) or len(bs_data) == 0:
            # FMP premium block — try yfinance
            yf_data = _yf_fallback(symbol)
            if not yf_data:
                return None
            bs = yf_data["bs"]
            income = yf_data["income"]
            used_fallback = True
        else:
            bs = bs_data[0]

    total_debt = bs.get("totalDebt", 0) or 0
    cash_and_equivalents = bs.get("cashAndCashEquivalents", 0) or 0
    short_term_investments = bs.get("shortTermInvestments", 0) or 0
    cash_and_short_term = bs.get("cashAndShortTermInvestments", 0) or 0

    # 4. Fetch income statement (skip if already from yfinance)
    if not used_fallback:
        is_data = _fmp_get("income-statement", symbol)
        if not is_data or not isinstance(is_data, list) or len(is_data) == 0:
            yf_data = _yf_fallback(symbol)
            if not yf_data:
                return None
            income = yf_data["income"]
        else:
            income = is_data[0]

    revenue = income.get("revenue", 0) or 0
    interest_income = income.get("interestIncome", 0) or 0
    interest_expense = income.get("interestExpense", 0) or 0

    # ---------------------------------------------------------------------------
    # AAOIFI Screens
    # ---------------------------------------------------------------------------

    # Screen 1: Debt ratio — Total Debt / Market Cap < 33%
    debt_ratio = (total_debt / market_cap * 100) if market_cap > 0 else 999
    debt_pass = debt_ratio < 33.0

    # Screen 2: Interest income — Interest Income / Revenue < 5%
    # Use absolute value since interest_income can be reported differently
    interest_ratio = (abs(interest_income) / revenue * 100) if revenue > 0 else 0
    interest_pass = interest_ratio < 5.0

    # Screen 3: Haram revenue — sector/industry based
    haram_pass = not (haram_sector_flag or haram_industry_flag)

    # Screen 4: Liquidity — (Cash + Interest-Bearing Securities) / Market Cap < 33%
    # Interest-bearing securities ≈ short-term investments
    liquid_assets = cash_and_short_term if cash_and_short_term > 0 else (cash_and_equivalents + short_term_investments)
    liquidity_ratio = (liquid_assets / market_cap * 100) if market_cap > 0 else 999
    liquidity_pass = liquidity_ratio < 33.0

    # Final verdict
    is_halal = debt_pass and interest_pass and haram_pass and liquidity_pass

    result = {
        "symbol": symbol,
        "company_name": company_name,
        "sector": profile.get("sector", ""),
        "industry": profile.get("industry", ""),
        "market_cap": market_cap,
        "is_halal": is_halal,
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
        # Metadata
        "screens_passed": sum([debt_pass, interest_pass, haram_pass, liquidity_pass]),
        "screens_total": 4,
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
                existing.last_screened = datetime.utcnow()
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
                    last_screened=datetime.utcnow(),
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
                age_days = (datetime.utcnow() - row.last_screened).days
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
    fresh_cutoff = datetime.utcnow() - timedelta(days=7)
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
