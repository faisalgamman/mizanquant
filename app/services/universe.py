"""Universe — single-source of truth for tradable symbols.

Replaces the old ``HALAL_STOCKS`` / ``RUSSELL_1000_HALAL`` dual lists.
All consumer code should call ``get_universe_symbols()`` instead of
importing from ``halal_screener`` directly.
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.orm import Session

from app.db.database import SessionLocal
from app.db.models import Universe

logger = logging.getLogger("screener")

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

HALAL_STOCKS_FALLBACK = [s for s in _SP500_ALL if s not in _HARAM_EXCLUDE]

# Public export for other modules (e.g. halal_screening.verify_halal)
HARAM_EXCLUDE = _HARAM_EXCLUDE

HALAL_STOCKS_BACKTEST_FALLBACK = HALAL_STOCKS_FALLBACK + [
    s for s in _SP500_DELISTED_HALAL if s not in _HARAM_EXCLUDE
]

# ── Public API ──


def get_universe_symbols(db: Session) -> list[str]:
    """Return all active symbols from DB.  Falls back to old constants if empty."""
    rows = db.query(Universe.symbol).filter(Universe.is_active.is_(True)).all()
    if rows:
        return [r[0] for r in rows]
    logger.warning("Universe table empty — using fallback HALAL_STOCKS (~240 symbols)")
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
