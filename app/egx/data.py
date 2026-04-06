"""EGX data loading and storage — CSV upload with Arabic column support.

Handles:
- Arabic CSV column mapping (الرمز, فتح, أعلى, etc.)
- English CSV column mapping (fallback)
- Data validation and cleaning
- Persistent storage to DB
"""

import io
import logging
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd
from sqlalchemy import text

from app.db.database import SessionLocal
from app.egx.models import EgxStockData, EgxWatchlist

logger = logging.getLogger("egx")

# Arabic → English column mapping
ARABIC_COL_MAP = {
    "الرمز": "symbol",
    "نطاق البيانات": "date",
    "فتح": "open",
    "أعلى": "high",
    "الأدنى": "low",
    "مغلق": "close",
    "التغير%": "change_pct",
    "تغيير": "change",
    "إقفال سابق": "prev_close",
    "حجم التداول": "volume",
    "قيمة التداول": "value",
}

# English fallback mapping (for standard OHLCV CSVs)
ENGLISH_COL_MAP = {
    "symbol": "symbol",
    "ticker": "symbol",
    "date": "date",
    "datetime": "date",
    "open": "open",
    "high": "high",
    "low": "low",
    "close": "close",
    "volume": "volume",
    "value": "value",
}

REQUIRED_COLS = {"date", "open", "high", "low", "close", "volume"}


def load_csv(file_content: bytes, filename: str = "") -> pd.DataFrame:
    """Parse a CSV file (Arabic or English columns) into a standardized DataFrame.

    Returns DataFrame with columns: [symbol, date, open, high, low, close, volume, ...]
    """
    # Try multiple encodings
    df = None
    for encoding in ["utf-8", "utf-8-sig", "cp1256", "iso-8859-6", "latin-1"]:
        try:
            df = pd.read_csv(io.BytesIO(file_content), encoding=encoding)
            break
        except (UnicodeDecodeError, pd.errors.ParserError):
            continue

    if df is None or df.empty:
        raise ValueError("Could not parse CSV file with any supported encoding")

    # Try Arabic mapping first, then English
    original_cols = set(df.columns)
    arabic_match = sum(1 for c in original_cols if c in ARABIC_COL_MAP)
    english_match = sum(1 for c in original_cols if c.lower() in ENGLISH_COL_MAP)

    if arabic_match >= 3:
        df.rename(columns=ARABIC_COL_MAP, inplace=True)
    elif english_match >= 3:
        df.rename(columns={c: ENGLISH_COL_MAP.get(c.lower(), c) for c in df.columns}, inplace=True)

    # Normalize column names to lowercase
    df.columns = [c.lower().strip() for c in df.columns]

    # Validate required columns
    missing = REQUIRED_COLS - set(df.columns)
    if missing:
        raise ValueError(f"Missing required columns: {missing}. Found: {list(df.columns)}")

    # Parse dates
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df.dropna(subset=["date"], inplace=True)

    # Convert numeric columns
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    for col in ["value", "change_pct", "prev_close"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Extract symbol from data or filename
    if "symbol" not in df.columns or df["symbol"].isna().all():
        # Try to extract from filename (e.g., "COMI.csv" → "COMI")
        if filename:
            sym = filename.rsplit(".", 1)[0].upper().strip()
            df["symbol"] = sym
        else:
            df["symbol"] = "UNKNOWN"
    else:
        df["symbol"] = df["symbol"].astype(str).str.strip().str.upper()

    # Sort by date
    df.sort_values("date", inplace=True)
    df.reset_index(drop=True, inplace=True)

    # Drop rows with NaN in OHLCV
    df.dropna(subset=["open", "high", "low", "close", "volume"], inplace=True)

    return df


def save_to_db(df: pd.DataFrame) -> dict:
    """Save parsed EGX data to database. Returns summary stats."""
    db = SessionLocal()
    try:
        symbols = df["symbol"].unique()
        inserted = 0
        updated = 0

        for symbol in symbols:
            sym_df = df[df["symbol"] == symbol]

            # Add to watchlist if not exists
            existing = db.query(EgxWatchlist).filter_by(symbol=symbol).first()
            if not existing:
                db.add(EgxWatchlist(symbol=symbol))

            for _, row in sym_df.iterrows():
                existing_row = (
                    db.query(EgxStockData)
                    .filter_by(symbol=symbol, date=row["date"])
                    .first()
                )
                if existing_row:
                    existing_row.open = row["open"]
                    existing_row.high = row["high"]
                    existing_row.low = row["low"]
                    existing_row.close = row["close"]
                    existing_row.volume = row["volume"]
                    existing_row.value = row.get("value")
                    existing_row.change_pct = row.get("change_pct")
                    existing_row.prev_close = row.get("prev_close")
                    updated += 1
                else:
                    db.add(EgxStockData(
                        symbol=symbol,
                        date=row["date"],
                        open=row["open"],
                        high=row["high"],
                        low=row["low"],
                        close=row["close"],
                        volume=row["volume"],
                        value=row.get("value"),
                        change_pct=row.get("change_pct"),
                        prev_close=row.get("prev_close"),
                    ))
                    inserted += 1

        db.commit()
        return {
            "symbols": list(symbols),
            "rows_inserted": inserted,
            "rows_updated": updated,
            "total_rows": len(df),
        }
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()


def get_stock_data(symbol: str, min_rows: int = 5) -> Optional[pd.DataFrame]:
    """Load EGX stock data from database for a given symbol."""
    db = SessionLocal()
    try:
        rows = (
            db.query(EgxStockData)
            .filter_by(symbol=symbol.upper())
            .order_by(EgxStockData.date.asc())
            .all()
        )
        if not rows or len(rows) < min_rows:
            return None

        data = [{
            "date": r.date,
            "open": r.open,
            "high": r.high,
            "low": r.low,
            "close": r.close,
            "volume": r.volume,
            "value": r.value,
            "symbol": r.symbol,
        } for r in rows]

        return pd.DataFrame(data)
    finally:
        db.close()


def get_watchlist() -> list:
    """Return all EGX symbols in the watchlist."""
    db = SessionLocal()
    try:
        items = db.query(EgxWatchlist).filter_by(is_active=True).all()
        return [{
            "symbol": w.symbol,
            "name_ar": w.name_ar,
            "sector": w.sector,
            "added_at": w.added_at.isoformat() if w.added_at else None,
            "last_analyzed": w.last_analyzed.isoformat() if w.last_analyzed else None,
        } for w in items]
    finally:
        db.close()


def get_all_symbols() -> list:
    """Return list of all EGX symbols with data in the DB."""
    db = SessionLocal()
    try:
        results = db.query(EgxStockData.symbol).distinct().all()
        return [r[0] for r in results]
    finally:
        db.close()
