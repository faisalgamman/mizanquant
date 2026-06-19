"""Universe — single-source of truth for tradable symbols.

Replaces the old ``HALAL_STOCKS`` / ``RUSSELL_1000_HALAL`` dual lists.
All consumer code should call ``get_universe_symbols()`` instead of
importing from ``halal_screener`` directly.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from app.db.database import SessionLocal
from app.db.models import Universe

logger = logging.getLogger("screener")

# ── Expanded universe loader ──

_UNIVERSE_JSON_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "halal_universe_v2.json"
_EXPANDED_UNIVERSE: list[str] | None = None


def _load_expanded_universe() -> list[str] | None:
    """Load the expanded halal universe from JSON, if available."""
    global _EXPANDED_UNIVERSE
    if _EXPANDED_UNIVERSE is not None:
        return _EXPANDED_UNIVERSE
    try:
        if _UNIVERSE_JSON_PATH.exists():
            data = json.loads(_UNIVERSE_JSON_PATH.read_text())
            symbols = data.get("symbols", [])
            if symbols:
                _EXPANDED_UNIVERSE = symbols
                logger.info("Loaded expanded halal universe from %s (%d symbols)",
                            _UNIVERSE_JSON_PATH, len(symbols))
                return _EXPANDED_UNIVERSE
    except Exception as exc:
        logger.warning("Failed to load expanded universe from %s: %s", _UNIVERSE_JSON_PATH, exc)
    return None

# ── Fallback constants (used when DB is empty / unavailable) ──

_HARAM_EXCLUDE = {
    "BAC", "C", "COF", "CFG", "FITB", "GS", "HBAN", "JPM", "KEY", "MS", "MTB",
    "PNC", "RF", "SCHW", "STT", "TFC", "USB", "WFC", "BK", "BEN", "BLK", "BX",
    "CBOE", "CME", "ICE", "IVZ", "KKR", "MSCI", "NDAQ", "SPGI", "TROW",
    "ACGL", "AFL", "AIG", "AIZ", "ALL", "AJG", "BRO", "CB", "CI", "CINF",
    "CNC", "COR", "EG", "ELV", "ERIE", "GL", "HIG", "HUM", "L", "MET",
    "PFG", "PGR", "PRU", "RJF", "TRV", "UNH", "WRB", "WTW",
    "BF.B", "STZ", "TAP", "MO", "PM", "SAM",
    "WYNN", "LVS", "MGM", "CCL", "NCLH", "RCL",
    "LMT", "NOC", "GD", "RTX", "HII", "LHX", "BA",
    "FOX", "FOXA", "NFLX", "DIS", "WBD", "LYV",
    "AEE", "AEP", "AES", "ATO", "CEG", "CMS", "CNP", "D", "DTE", "DUK", "ED",
    "EIX", "ES", "ETR", "EVRG", "EXC", "FE", "LNT", "NEE", "NI", "NRG", "PCG",
    "PEG", "PPL", "SO", "SRE", "VST", "WEC", "XEL",
    "AMT", "ARE", "AVB", "BXP", "CCI", "CPT", "DLR", "DOC", "EQIX", "EQR",
    "ESS", "EXR", "FRT", "HST", "INVH", "IRM", "KIM", "MAA", "O", "PLD",
    "PSA", "REG", "SBAC", "SPG", "UDR", "VICI", "VTR", "WELL",
    "AXP", "SYF", "CPAY",
}

_SP500_ALL = [
    "A", "AAPL", "ABBV", "ABNB", "ABT", "ACN", "ADBE", "ADI", "ADM", "ADP", "ADSK",
    "AEE", "AEP", "AES", "AKAM", "ALB", "ALGN", "ALLE", "AMAT", "AMCR", "AMD",
    "AME", "AMGN", "AMP", "AMT", "AMZN", "ANET", "AON", "AOS", "APA", "APD", "APH",
    "APO", "APP", "APTV", "ARE", "ARES", "ATO", "AVB", "AVGO", "AVY", "AWK", "AXON",
    "AXP", "AZO", "BALL", "BAX", "BBY", "BDX", "BG", "BIIB", "BKNG", "BKR", "BLDR",
    "BMY", "BR", "BRK.B", "BSX", "BXP", "CAG", "CAH", "CARR", "CAT", "CBRE", "CCI",
    "CDNS", "CDW", "CEG", "CF", "CHD", "CHRW", "CHTR", "CIEN", "CL", "CLX", "CMCSA",
    "CMG", "CMI", "CMS", "CNP", "COHR", "COIN", "COO", "COP", "COST", "CPAY", "CPB",
    "CPRT", "CPT", "CRH", "CRL", "CRM", "CRWD", "CSCO", "CSGP", "CSX", "CTAS",
    "CTRA", "CTSH", "CTVA", "CVNA", "CVS", "CVX", "D", "DAL", "DASH", "DD", "DDOG",
    "DE", "DECK", "DELL", "DG", "DGX", "DHI", "DHR", "DLR", "DLTR", "DOC", "DOV",
    "DOW", "DPZ", "DRI", "DTE", "DUK", "DVA", "DVN", "DXCM", "EA", "EBAY", "ECL",
    "ED", "EFX", "EIX", "EL", "EME", "EMR", "EOG", "EPAM", "EQIX", "EQR", "EQT",
    "ES", "ESS", "ETN", "ETR", "EVRG", "EW", "EXC", "EXE", "EXPD", "EXPE", "EXR",
    "F", "FANG", "FAST", "FCX", "FDS", "FDX", "FE", "FFIV", "FICO", "FIS", "FISV",
    "FIX", "FRT", "FSLR", "FTNT", "FTV", "GDDY", "GE", "GEHC", "GEN", "GEV", "GILD",
    "GIS", "GLW", "GM", "GNRC", "GOOG", "GOOGL", "GPC", "GPN", "GRMN", "GWW", "HAL",
    "HAS", "HCA", "HD", "HLT", "HOLX", "HON", "HOOD", "HPE", "HPQ", "HRL", "HSIC",
    "HST", "HSY", "HUBB", "HWM", "IBKR", "IBM", "IDXX", "IEX", "IFF", "INCY", "INTC",
    "INTU", "INVH", "IP", "IQV", "IR", "IRM", "ISRG", "IT", "ITW", "J", "JBHT", "JBL",
    "JCI", "JKHY", "JNJ", "KDP", "KEYS", "KHC", "KIM", "KLAC", "KMB", "KMI", "KO",
    "KR", "KVUE", "LDOS", "LEN", "LH", "LII", "LIN", "LITE", "LLY", "LNT", "LOW",
    "LRCX", "LULU", "LUV", "LYB", "MA", "MAA", "MAR", "MAS", "MCD", "MCHP", "MCK",
    "MCO", "MDLZ", "MDT", "META", "MKC", "MLM", "MMM", "MNST", "MOS", "MPC", "MPWR",
    "MRK", "MRNA", "MRSH", "MSFT", "MSI", "MTD", "MU", "NEM", "NEE", "NI", "NKE", "NOW",
    "NRG", "NSC", "NTAP", "NTRS", "NUE", "NVDA", "NVR", "NWS", "NWSA", "NXPI", "O",
    "ODFL", "OKE", "OMC", "ON", "ORCL", "ORLY", "OTIS", "OXY", "PANW", "PAYX", "PCAR",
    "PCG", "PEG", "PEP", "PFE", "PG", "PH", "PHM", "PKG", "PLD", "PLTR", "POOL",
    "PODD", "PPG", "PPL", "PSA", "PSKY", "PSX", "PTC", "PWR", "PYPL", "Q", "QCOM",
    "REG", "REGN", "RL", "RMD", "ROK", "ROL", "ROP", "ROST", "RSG", "RVTY", "SAIC",
    "SATS", "SBAC", "SBUX", "SHW", "SJM", "SLB", "SMCI", "SNA", "SNDK", "SNPS", "SO",
    "SOLV", "SPG", "SRE", "STE", "STLD", "STX", "SW", "SWK", "SWKS", "SYF", "SYK",
    "SYY", "T", "TDG", "TDY", "TECH", "TEL", "TER", "TGT", "TJX", "TKO", "TMO",
    "TMUS", "TPL", "TPR", "TRGP", "TRMB", "TSCO", "TSLA", "TSN", "TT", "TTD", "TTWO",
    "TXN", "TXT", "TYL", "UAL", "UBER", "UDR", "UHS", "ULTA", "UNP", "UPS", "URI",
    "V", "VICI", "VLO", "VLTO", "VMC", "VRSK", "VRSN", "VRT", "VRTX", "VST", "VTR",
    "VTRS", "VZ", "WAB", "WAT", "WDAY", "WDC", "WEC", "WELL", "WM", "WMB", "WMT",
    "WSM", "WST", "XEL", "XOM", "XYL", "XYZ", "YUM", "ZBH", "ZBRA", "ZTS",
]

_SP500_DELISTED_HALAL = [
    "ATVI", "DISH", "FRC", "SIVB", "LUMN", "VFC", "ALK", "NCLH",
    "SEE", "NWL", "OGN", "PARA", "SEDG", "ENPH", "BBWI", "CTLT", "AAL",
    "TWTR", "SBNY", "DISCA", "XLNX", "CERN", "FBHS", "RE", "NLSN", "DRE", "VIAC",
]

HALAL_STOCKS_FALLBACK = _load_expanded_universe() or [s for s in _SP500_ALL if s not in _HARAM_EXCLUDE]

# Public export for other modules (e.g. halal_screening.verify_halal)
HARAM_EXCLUDE = _HARAM_EXCLUDE

HALAL_STOCKS_BACKTEST_FALLBACK = [s for s in HALAL_STOCKS_FALLBACK if s not in _HARAM_EXCLUDE] + [
    s for s in _SP500_DELISTED_HALAL if s not in _HARAM_EXCLUDE
]

# ── Public API ──


def get_universe_symbols(db: Session) -> list[str]:
    """Return all active symbols from DB. Falls back to the expanded halal universe
    (halal_universe_v2.json, ~657 symbols) when the DB table is empty — so the
    weekly and monthly scanners run on the SAME universe regardless of DB state."""
    rows = db.query(Universe.symbol).filter(Universe.is_active.is_(True)).all()
    if rows:
        return [r[0] for r in rows]
    logger.warning("Universe table empty — using fallback universe (%d symbols)",
                   len(HALAL_STOCKS_FALLBACK))
    return list(HALAL_STOCKS_FALLBACK)


def get_universe_count(db: Session) -> int:
    """Return number of active symbols in the universe."""
    rows = db.query(Universe.symbol).filter(Universe.is_active.is_(True)).count()
    if rows:
        return rows
    return len(HALAL_STOCKS_FALLBACK)


def seed_from_fallback(db: Session):
    """Seed the Universe table from the fallback constants."""
    for symbol in HALAL_STOCKS_FALLBACK:
        existing = db.query(Universe).filter(Universe.symbol == symbol).first()
        if existing:
            existing.is_active = True
        else:
            db.add(Universe(symbol=symbol, is_active=True))
    db.commit()
    logger.info("Seeded %d symbols into Universe table", len(HALAL_STOCKS_FALLBACK))


# ── Universe expansion (EDGAR-backed, no FMP quota) ──────────────────────────
# The binding constraint on universe size was FMP's fundamentals quota. With SEC
# EDGAR as a free fundamentals source, the nightly warm job can halal-screen a much
# larger candidate pool; the names that PASS are synced into the Universe table so
# BOTH scanners (Weekly via get_universe_symbols, Monthly via a refreshed
# _SMART_UNIVERSE) search the expanded set. The base 657 is always the floor.
HALAL_UNIVERSE_CAP: int = int(os.environ.get("HALAL_UNIVERSE_CAP", "1500"))


def build_halal_candidates(cap: Optional[int] = None) -> list[str]:
    """Candidate pool to halal-screen: the base universe FLOOR + liquid Alpaca-tradable
    US equities, deterministically prioritized and capped. EDGAR screens these for free;
    the scanner's own penny/liquidity gates trim illiquid names at scan time, so no
    separate volume pre-filter is needed here."""
    cap = HALAL_UNIVERSE_CAP if cap is None else cap
    seen: set[str] = set()
    out: list[str] = []

    def _add(syms):
        for s in syms:
            u = str(s).upper().strip()
            if u and u not in seen:
                seen.add(u)
                out.append(u)

    _add(HALAL_STOCKS_FALLBACK)          # base floor — always first
    _add(_SP500_ALL)                     # large-cap liquid
    try:
        from app.services.reference_data import get_tradable_symbols
        _add(sorted(get_tradable_symbols()))
    except Exception as exc:
        logger.warning("build_halal_candidates: tradable fetch failed: %s", exc)
    return out[: max(cap, len(HALAL_STOCKS_FALLBACK))]


def sync_verified_halal_to_universe(cap: Optional[int] = None) -> int:
    """Upsert EDGAR/FMP-verified halal names (current screen version) into the Universe
    table as active, so both scanners search the expanded set. The base floor is always
    kept; this never DEACTIVATES rows (the scanners' own halal gate still filters at scan
    time). Returns the resulting active-halal count."""
    cap = HALAL_UNIVERSE_CAP if cap is None else cap
    try:
        from app.db.models import ScreeningResult
        from app.services.halal_screening import HALAL_SCREEN_VERSION
    except Exception as exc:
        logger.warning("sync_verified_halal: imports failed: %s", exc)
        return 0

    verified: list[str] = []
    try:
        db = SessionLocal()
        try:
            rows = (db.query(ScreeningResult.symbol, ScreeningResult.details)
                      .filter(ScreeningResult.is_halal.is_(True)).all())
            for sym, details in rows:
                if isinstance(details, dict) and details.get("screen_version") == HALAL_SCREEN_VERSION:
                    verified.append(str(sym).upper())
        finally:
            db.close()
    except Exception as exc:
        logger.warning("sync_verified_halal: DB read failed: %s", exc)
        return 0

    # Base floor first, then verified additions, deduped + capped.
    seen: set[str] = set()
    final: list[str] = []
    for s in list(HALAL_STOCKS_FALLBACK) + verified:
        u = str(s).upper()
        if u and u not in seen:
            seen.add(u)
            final.append(u)
    final = final[: max(cap, len(HALAL_STOCKS_FALLBACK))]

    try:
        db = SessionLocal()
        try:
            existing = {r[0].upper() for r in db.query(Universe.symbol).all()}
            for sym in final:
                if sym in existing:
                    db.query(Universe).filter(Universe.symbol == sym).update({"is_active": True})
                else:
                    db.add(Universe(symbol=sym, is_active=True))
            db.commit()
        finally:
            db.close()
    except Exception as exc:
        logger.warning("sync_verified_halal: DB write failed: %s", exc)

    logger.info("sync_verified_halal: %d active halal symbols (%d verified additions over the %d floor)",
                len(final), max(0, len(final) - len(HALAL_STOCKS_FALLBACK)), len(HALAL_STOCKS_FALLBACK))
    return len(final)


def get_symbol_info(db: Session, symbol: str) -> Optional[dict]:
    """Return metadata for a single symbol."""
    row = db.query(Universe).filter(Universe.symbol == symbol).first()
    if row is None:
        return None
    return {
        "symbol": row.symbol,
        "company_name": row.company_name,
        "exchange": row.exchange,
        "sector": row.sector,
        "industry": row.industry,
        "is_active": row.is_active,
    }
