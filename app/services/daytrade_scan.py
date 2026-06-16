"""Technical-only "explosion" day-trade scanner (RESEARCH — ignores halal).

Ranks symbols by a 0-100 explosion score computed from DAILY bars only:
  relative volume (RVOL) + recent momentum (ROC) + volatility expansion (ATR) + gap.

No FMP, no live halal screen, no composite/fundamental input — a fast, ISOLATED research
view over a broad (incl. non-halal) universe. `halal_verdict` on each row is a best-effort
CACHE-ONLY flag (a single batched DB read; never triggers a screen) so the UI can mark
non-compliant names. Nothing here feeds the halal scanners, ledgers, or the trade path.
"""
from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger("screener")

# Explosion-score weights (sum = 1.0) — env/constant-tunable.
_W_RVOL, _W_MOM, _W_VOL, _W_GAP = 0.35, 0.30, 0.20, 0.15


def _clamp01(x: float) -> float:
    if not np.isfinite(x):
        return 0.0
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def explosion_score(df) -> tuple[float, dict] | None:
    """(score 0-100, fields) for one daily OHLCV df, or None when unusable.

    Pure/technical: reuses app.services.technical indicators only.
    """
    from app.services.technical import atr, gap_detection, roc, volume_ratio

    if df is None or len(df) < 30 or "close" not in df.columns:
        return None
    close = df["close"].astype(float)
    last = float(close.iloc[-1])
    if last <= 0:
        return None

    # Relative volume: today vs 20-day average (1x → 0, 2.5x → 1).
    rvol = float(volume_ratio(df, 20))
    s_rvol = _clamp01((rvol - 1.0) / 1.5)

    # Momentum: 3-day rate-of-change, directional up (+10% over 3d → 1).
    r3 = roc(close, 3)
    mom = float(r3.iloc[-1]) if len(r3) and np.isfinite(r3.iloc[-1]) else 0.0
    s_mom = _clamp01(mom / 10.0)

    # Volatility expansion: current ATR vs its trailing-20 average (+50% → 1).
    a = atr(df, 14)
    atr_now = float(a.iloc[-1]) if len(a) and np.isfinite(a.iloc[-1]) else 0.0
    atr_ref = float(a.iloc[-20:].mean()) if len(a) >= 20 else atr_now
    expansion = (atr_now / atr_ref) if atr_ref > 0 else 1.0
    s_vol = _clamp01((expansion - 1.0) / 0.5)

    # Gap: |open-vs-prev-close| (4% → 1).
    gap_pct = float(gap_detection(df).get("gap_pct", 0.0))
    s_gap = _clamp01(abs(gap_pct) / 4.0)

    score = round(100.0 * (_W_RVOL * s_rvol + _W_MOM * s_mom + _W_VOL * s_vol + _W_GAP * s_gap), 1)
    chg = float(roc(close, 1).iloc[-1]) if len(close) > 1 and np.isfinite(roc(close, 1).iloc[-1]) else 0.0
    return score, {
        "price": round(last, 2),
        "change_pct": round(chg, 2),
        "rvol": round(rvol, 2),
        "momentum_pct": round(mom, 2),
        "vol_expansion": round(expansion, 2),
        "gap_pct": round(gap_pct, 2),
        "components": {
            "rvol": round(100 * s_rvol),
            "momentum": round(100 * s_mom),
            "volatility": round(100 * s_vol),
            "gap": round(100 * s_gap),
        },
    }


def _cached_verdicts(symbols: list[str]) -> dict[str, str]:
    """Best-effort CACHE-ONLY halal verdicts (single batched DB read; NO screening).

    Returns {SYMBOL: "halal"|"doubtful"|"non_compliant"} for symbols already screened;
    absent for the rest (UI shows "—"). Never triggers a live screen.
    """
    out: dict[str, str] = {}
    try:
        from app.db.database import SessionLocal
        from app.db.models import ScreeningResult
        db = SessionLocal()
        try:
            ups = [s.upper() for s in symbols if s]
            rows = (db.query(ScreeningResult.symbol, ScreeningResult.is_halal, ScreeningResult.details)
                      .filter(ScreeningResult.symbol.in_(ups)).all())
            for sym, is_halal, details in rows:
                v = (details or {}).get("halal_verdict") if isinstance(details, dict) else None
                out[sym.upper()] = v or ("halal" if is_halal else "non_compliant")
        finally:
            db.close()
    except Exception as e:
        logger.debug("daytrade: cached verdicts failed: %s", e)
    return out


def scan_explosion(symbols: list[str], limit: int = 60) -> list[dict]:
    """Rank `symbols` by the technical explosion score (daily bars). Ranked desc, top `limit`."""
    from app.services.market_data import fetch_alpaca_batch

    try:
        prefetched = fetch_alpaca_batch(list(symbols), period="1y") or {}
    except Exception as e:
        logger.warning("daytrade: batch prefetch failed: %s", e)
        prefetched = {}

    verdicts = _cached_verdicts(list(prefetched.keys()) or list(symbols))
    rows: list[dict] = []
    for sym, df in prefetched.items():
        try:
            res = explosion_score(df)
            if res is None:
                continue
            score, fields = res
            rows.append({"symbol": sym, "explosion_score": score,
                         "halal_verdict": verdicts.get(sym.upper()), **fields})
        except Exception:
            continue
    rows.sort(key=lambda r: r["explosion_score"], reverse=True)
    logger.info("daytrade: scored %d/%d symbols", len(rows), len(prefetched))
    return rows[:limit]
