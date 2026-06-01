"""Fama-French factor exposure estimation.

Downloads daily factor returns from Kenneth French Data Library and
estimates each stock's exposure (beta) to market, size (SMB), value (HML),
momentum (MOM), and quality (RMW/CMA) factors.

Reference
---------
- Chan (2009), Ch.7 — Factor models
- Jansen (2020), Ch.7 — Linear models / risk factors
- Fama & French (1993, 2015) — Three-factor / Five-factor models
"""

from __future__ import annotations

import logging
import time
from io import StringIO

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Cache: factor returns + timestamp
_factor_cache: dict = {"data": None, "ts": 0.0}
FACTOR_URL: str = (
    "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/"
    "F-F_Research_Data_5_Factors_2x3_daily_CSV.zip"
)
CACHE_TTL: int = 86400  # 24 hours

# Factor names as they appear in the CSV
FACTOR_COLS: list[str] = ["Mkt-RF", "SMB", "HML", "RMW", "CMA", "RF"]


def _fetch_factors() -> pd.DataFrame | None:
    """Download daily Fama-French 5-factor returns. Cached 24h."""
    now = time.time()
    if _factor_cache["data"] is not None and (now - _factor_cache["ts"]) < CACHE_TTL:
        return _factor_cache["data"]

    try:
        df = pd.read_csv(
            FACTOR_URL,
            skiprows=3,
            compression="zip",
            engine="python",
        )
        # CSV has: date, Mkt-RF, SMB, HML, RMW, CMA, RF
        # First row after skiprows is the header
        df.columns = [c.strip() for c in df.columns]
        df = df.rename(columns={df.columns[0]: "date"})
        df["date"] = pd.to_datetime(df["date"], format="%Y%m%d", errors="coerce")
        df = df.dropna(subset=["date"])
        df = df.set_index("date")

        # Keep only factor columns
        cols = [c for c in FACTOR_COLS if c in df.columns]
        df = df[cols].apply(pd.to_numeric, errors="coerce") / 100.0  # % -> decimal

        _factor_cache["data"] = df
        _factor_cache["ts"] = now
        logger.info("factor_exposure: loaded %d days of Fama-French factors", len(df))
        return df

    except Exception:
        logger.debug("factor_exposure: failed to download factors", exc_info=True)
        return None


def estimate_factor_betas(symbol: str, lookback: int = 252) -> dict:
    """Estimate a stock's exposure to Fama-French factors via OLS regression.

    Parameters
    ----------
    symbol : str
        Ticker symbol.
    lookback : int
        Trading days of history to use.

    Returns
    -------
    dict with keys: betas, r_squared, n_days, factors_available
    Returns empty on failure.
    """
    factors = _fetch_factors()
    if factors is None:
        return {"betas": {}, "r_squared": 0.0, "n_days": 0, "factors_available": False}

    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="2y")
        if hist.empty or len(hist) < 60:
            return {"betas": {}, "r_squared": 0.0, "n_days": 0, "factors_available": False}

        # Daily returns
        stock_ret = hist["Close"].pct_change().dropna()
        # Align with factor dates
        common = stock_ret.index.intersection(factors.index)
        if len(common) < 60:
            return {"betas": {}, "r_squared": 0.0, "n_days": 0, "factors_available": False}

        common = common[-lookback:]
        y = stock_ret.loc[common].values
        X = factors.loc[common].values

        # Remove rows with NaN
        mask = ~np.isnan(y) & ~np.isnan(X).any(axis=1)
        y = y[mask]
        X = X[mask]

        if len(y) < 60:
            return {"betas": {}, "r_squared": 0.0, "n_days": 0, "factors_available": False}

        # OLS
        X_with_const = np.column_stack([np.ones(len(y)), X])
        beta, residuals, rank, sv = np.linalg.lstsq(X_with_const, y, rcond=None)
        y_pred = X_with_const @ beta
        ss_res = float(np.sum((y - y_pred) ** 2))
        ss_tot = float(np.sum((y - np.mean(y)) ** 2))
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0

        cols_used = [c for c in FACTOR_COLS if c in factors.columns]
        betas = {}
        for i, name in enumerate(cols_used):
            if i < len(beta) - 1:
                betas[name] = round(float(beta[i + 1]), 6)

        return {
            "betas": betas,
            "alpha_daily": round(float(beta[0]), 6),
            "r_squared": round(r2, 4),
            "n_days": len(y),
            "factors_available": True,
        }

    except Exception:
        logger.debug("factor_exposure: estimation failed for %s", symbol, exc_info=True)
        return {"betas": {}, "r_squared": 0.0, "n_days": 0, "factors_available": False}


def get_factor_multiplier(symbol: str) -> float:
    """Return a position-size multiplier based on factor exposure quality.

    Positive momentum (MOM) exposure + positive quality (RMW) exposure
    = higher signal quality = slightly larger position.

    Returns float in [0.90, 1.10]. Degrades to 1.0 on failure.
    """
    result = estimate_factor_betas(symbol)
    if not result.get("factors_available"):
        return 1.0

    betas = result.get("betas", {})
    score = 0.0
    count = 0

    # Prefer positive exposure to momentum proxy (SMB for small-cap momentum)
    # and quality (RMW)
    for key in ("SMB", "RMW"):
        if key in betas:
            score += max(0.0, betas[key])
            count += 1

    if count == 0 or score == 0:
        return 1.0

    avg_score = score / count
    # Map [0, 0.5] -> [0.90, 1.10]
    mult = 0.90 + min(0.20, avg_score / 2.5)
    return round(mult, 4)
