"""Effective Number of Bets (Meucci) — the concentration guard.

Five names that all move like NVDA are ONE bet, not five. We decorrelate the open book via
PCA and measure the entropy of the variance spread across the principal portfolios: ENB = N
means fully diversified; ENB → 1 means a single hidden bet. If the book's ENB collapses,
adding another correlated name buys risk, not diversification.

Pure NumPy core (testable) + a live reader over the open paper positions.
"""

from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger("screener")


def effective_number_of_bets(cov, weights=None) -> float | None:
    """ENB via the Meucci PCA-entropy method on a covariance (or correlation) matrix.

    Decorrelate into principal portfolios, take each one's variance contribution p_i, then
    ENB = exp(−Σ p_i ln p_i). Returns None on a degenerate/empty matrix.
    """
    S = np.asarray(cov, dtype=float)
    if S.ndim != 2 or S.shape[0] != S.shape[1] or S.shape[0] == 0:
        return None
    n = S.shape[0]
    w = np.ones(n) / n if weights is None else np.asarray(weights, dtype=float)
    if w.shape[0] != n:
        return None
    try:
        vals, vecs = np.linalg.eigh(S)
        vals = np.clip(vals, 1e-12, None)
        w_tilde = vecs.T @ w                      # weights in the principal basis
        contrib = (w_tilde ** 2) * vals           # variance contribution per principal bet
        total = float(contrib.sum())
        if total <= 0:
            return None
        p = contrib / total
        p = p[p > 1e-12]
        return round(float(np.exp(-np.sum(p * np.log(p)))), 2)
    except Exception as e:
        logger.debug("ENB failed: %s", e)
        return None


def _corr_from_returns(rets: dict) -> tuple:
    """Align per-symbol return series → (symbols, correlation matrix). Drops symbols with
    too few overlapping points."""
    import pandas as pd
    df = pd.DataFrame(rets).dropna()
    if df.shape[0] < 20 or df.shape[1] < 2:
        return [], None
    C = np.corrcoef(df.values, rowvar=False)
    return list(df.columns), C


def open_positions_enb(symbols=None, lookback: str = "6mo") -> dict:
    """Concentration of the OPEN weekly paper book: ENB over the position correlation
    matrix. Returns {n, enb, enb_ratio, concentration}. enb_ratio = ENB / N (1 = perfectly
    diversified). Best-effort; needs ≥2 positions with price history."""
    from app.services.market_data import fetch as _f

    if symbols is None:
        try:
            from app.db.database import SessionLocal
            from app.db.models import TradeHistory
            db = SessionLocal()
            try:
                symbols = [r[0] for r in db.query(TradeHistory.symbol).filter(
                    TradeHistory.strategy_id == "PV", TradeHistory.pnl_pct.is_(None)).distinct().all()]
            finally:
                db.close()
        except Exception:
            symbols = []
    symbols = list(dict.fromkeys(symbols or []))
    if len(symbols) < 2:
        return {"n": len(symbols), "enb": None, "note": "need ≥2 open positions"}

    rets = {}
    for s in symbols:
        try:
            df = _f(s, period=lookback)
            if df is not None and len(df) > 25:
                c = df["close"].astype(float).values
                rets[s] = np.diff(np.log(c))[-120:]
        except Exception:
            continue
    syms, C = _corr_from_returns({s: r for s, r in rets.items()})
    if C is None:
        return {"n": len(symbols), "enb": None, "note": "insufficient overlapping history"}

    enb = effective_number_of_bets(C)
    n = len(syms)
    ratio = round(enb / n, 2) if (enb and n) else None
    conc = "high" if (ratio is not None and ratio < 0.5) else ("medium" if (ratio is not None and ratio < 0.75) else "low")
    avg_corr = round(float((C.sum() - n) / (n * (n - 1))), 3) if n > 1 else None
    return {"n": n, "enb": enb, "enb_ratio": ratio, "avg_pairwise_corr": avg_corr,
            "concentration": conc, "symbols": syms,
            "note": "ENB = effective independent bets (Meucci). enb_ratio<0.5 ⇒ crowded book."}


__all__ = ["effective_number_of_bets", "open_positions_enb"]
