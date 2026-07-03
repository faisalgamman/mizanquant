"""① Alpha capture — the biggest multiplier, and the least glamorous: DATA.

We only recorded factors for the ~5 names we traded each week. Quant desks snapshot the
WHOLE universe every day and label it later. This captures one point-in-time row per
universe name per day (RS, RSI, trend, vol, 12-1 momentum), then fills the forward return
once it matures. Result: ~universe-fold more observations, so IC/attribution reach
significance in weeks, not months. Read-only measurement — never touches a trade.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger("screener")


def _symbol_factors(df, spy_closes) -> dict | None:
    """Point-in-time factors from a price frame's LAST bar (same math as the live weekly
    signals) + 12-1 momentum. None if too short."""
    try:
        from app.services.smart_exit import compute_exit_indicators
        from app.services.backtest_engine import factor_rs_vs_spy, factor_momentum_12_1
        if df is None or len(df) < 70:
            return None
        closes = df["close"].astype(float).values
        last = compute_exit_indicators(df).iloc[-1]
        c = float(closes[-1])
        ema20 = float(last.get("_ema20") or 0)
        rs = factor_rs_vs_spy(closes, spy_closes, 63) if spy_closes is not None else None
        mom = factor_momentum_12_1(closes)
        return {
            "rs": round(float(rs) * 100, 3) if rs is not None else None,
            "rsi": round(float(last.get("_rsi") or 0), 1),
            "above_ema20": 1 if (ema20 and c > ema20) else 0,
            "atr_pct": round(float(last.get("_atr_pct") or 0), 2),
            "dist_ema20_pct": round((c / ema20 - 1) * 100, 2) if ema20 else None,
            "mom_12_1": round(float(mom) * 100, 2) if mom is not None else None,
            "price": round(c, 2),
        }
    except Exception:
        return None


def capture_snapshot(symbols=None, cap: int | None = None) -> dict:
    """Store today's PIT factor row for every universe name (deduped by date+symbol).

    Bounded to ``cap`` symbols (env ALPHA_CAPTURE_CAP, default 120) — build_halal_candidates'
    own cap arg is NOT honoured (returns the full ~1500), so we slice explicitly to keep the
    daily fetch load (and Alpaca 429 pressure) sane while still giving a wide cross-section."""
    import os as _os
    from app.db.database import SessionLocal
    from app.db.models import FactorSnapshot
    from app.services.market_data import fetch as _f

    if cap is None:
        try:
            cap = int(_os.environ.get("ALPHA_CAPTURE_CAP", "120"))
        except (TypeError, ValueError):
            cap = 120
    if not symbols:
        try:
            from app.services.universe import build_halal_candidates
            symbols = list(build_halal_candidates() or [])[:cap]   # slice — cap arg is ignored upstream
        except Exception as e:
            logger.debug("alpha_capture universe load failed: %s", e)
            return {"error": "no universe"}
    symbols = list(dict.fromkeys(symbols))

    spy = _f("SPY", period="1y")
    spy_closes = spy["close"].astype(float).values if spy is not None and len(spy) >= 70 else None
    snap_date = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    # ① market regime today (from the HMM) — stamped on each row so regime_conditional_ic
    # can later measure which factor works in which regime. Market-wide, computed once.
    regime = None
    try:
        from app.services.regime_hmm import regime_probabilities
        rp = regime_probabilities(spy_closes) if spy_closes is not None else None
        regime = rp.get("dominant") if rp else None
    except Exception:
        regime = None

    db = SessionLocal()
    stored = skipped = 0
    try:
        existing = {r[0] for r in db.query(FactorSnapshot.symbol).filter(
            FactorSnapshot.snap_date == snap_date).all()}
        for sym in symbols:
            if sym in existing:
                skipped += 1
                continue
            try:
                fac = _symbol_factors(_f(sym, period="1y"), spy_closes)
                if not fac:
                    continue
                fac["regime"] = regime          # market regime stamp (① regime-conditional IC)
                db.add(FactorSnapshot(snap_date=snap_date, symbol=sym,
                                      price=fac.get("price"), factors=fac, fwd_ret=None))
                stored += 1
            except Exception as e:
                logger.debug("snapshot %s failed: %s", sym, e)
        db.commit()
        return {"snap_date": snap_date.date().isoformat(), "stored": stored, "skipped": skipped}
    except Exception as e:
        db.rollback()
        logger.error("capture_snapshot failed: %s", e)
        return {"error": str(e)}
    finally:
        db.close()


def label_snapshots(horizon_days: int = 10) -> dict:
    """Fill fwd_ret[horizon] for snapshots ≥ horizon trading days old (idempotent)."""
    from app.db.database import SessionLocal
    from app.db.models import FactorSnapshot
    from app.services.market_data import fetch as _f
    from app.services.smart_exit import post_entry_bars

    h = int(horizon_days)
    key = str(h)
    db = SessionLocal()
    checked = labeled = 0
    try:
        # Load all rows and skip those already carrying THIS horizon in Python — robust to
        # how the JSON column stores nulls (a stored Python None can round-trip as JSON null,
        # so a SQL is_(None) filter is unreliable here).
        rows = [r for r in db.query(FactorSnapshot).all()
                if not (isinstance(r.fwd_ret, dict) and key in r.fwd_ret)]
        # group by symbol to reuse one fetch per symbol
        by_sym: dict = {}
        for r in rows:
            by_sym.setdefault(r.symbol, []).append(r)
        for sym, snaps in by_sym.items():
            df = None
            for r in snaps:
                entry = float(r.price or 0)
                if entry <= 0:
                    continue
                checked += 1
                try:
                    if df is None:
                        df = _f(sym, period="1y")
                    if df is None or len(df) == 0:
                        continue
                    post = post_entry_bars(df, r.snap_date, h + 5)
                    if post is None or len(post) < h:
                        continue
                    px = float(post["close"].iloc[h - 1])
                    if px > 0:
                        r.fwd_ret = {**(r.fwd_ret or {}), key: round((px / entry - 1.0) * 100.0, 3)}
                        labeled += 1
                except Exception as e:
                    logger.debug("label snapshot %s failed: %s", sym, e)
        db.commit()
        return {"horizon_days": h, "checked": checked, "labeled": labeled}
    except Exception as e:
        db.rollback()
        logger.error("label_snapshots failed: %s", e)
        return {"error": str(e)}
    finally:
        db.close()


_FACTORS = ("rs", "rsi", "above_ema20", "atr_pct", "dist_ema20_pct", "mom_12_1")


def snapshot_attribution(horizon_days: int = 10, sector_neutral: bool = False) -> dict:
    """Per-factor Information Coefficient across the captured panel: for each snap_date rank
    names by each factor and Spearman-correlate with the labelled forward return; average
    the per-date ICs (mean IC + IR ≈ t + % positive). ⑥ optional sector-neutral z-scoring
    of the factor first. This is the payoff of the capture base — power per DAY."""
    import numpy as np
    from app.db.database import SessionLocal
    from app.db.models import FactorSnapshot
    from app.services.signal_calibration import _spearman

    h = str(int(horizon_days))
    db = SessionLocal()
    try:
        rows = db.query(FactorSnapshot.snap_date, FactorSnapshot.symbol,
                        FactorSnapshot.sector, FactorSnapshot.factors, FactorSnapshot.fwd_ret).all()
    finally:
        db.close()

    by_date: dict = {}
    for sd, sym, sec, fac, fwd in rows:
        if not (isinstance(fac, dict) and isinstance(fwd, dict) and h in fwd):
            continue
        by_date.setdefault(sd, []).append((sym, sec, fac, float(fwd[h])))

    out = {}
    n_dates_total = 0
    for f in _FACTORS:
        ics = []
        for sd, items in by_date.items():
            vals = {sym: fac.get(f) for sym, sec, fac, _ in items if isinstance(fac.get(f), (int, float))}
            if len(vals) < 5:
                continue
            if sector_neutral:
                from app.services.sector_neutral import sector_neutral_zscores
                secmap = {sym: sec for sym, sec, _, _ in items}
                vals = sector_neutral_zscores(vals, secmap)
            fwds = {sym: fr for sym, sec, fac, fr in items if sym in vals}
            syms = [s for s in vals if s in fwds]
            if len(syms) < 5:
                continue
            ic = _spearman([vals[s] for s in syms], [fwds[s] for s in syms])
            if ic is not None:
                ics.append(ic)
        if ics:
            a = np.asarray(ics, dtype=float)
            n = len(a)
            sd_ = float(a.std(ddof=1)) if n > 1 else 0.0
            out[f] = {"n_dates": n, "mean_ic": round(float(a.mean()), 4),
                      "ic_ir": round(float(a.mean() / (sd_ / np.sqrt(n))), 2) if sd_ > 0 else None,
                      "pct_positive": round(100.0 * float((a > 0).mean()), 1)}
            n_dates_total = max(n_dates_total, n)
        else:
            out[f] = {"n_dates": 0, "status": "accumulating"}

    return {"horizon_days": int(horizon_days), "sector_neutral": sector_neutral,
            "panel_rows": len(rows), "labelled_dates": len(by_date),
            "factors": out,
            "note": "Cross-sectional IC from the daily whole-universe capture — power accrues per day."}


def regime_conditional_ic(horizon_days: int = 10) -> dict:
    """① × ④ — the sharp one: each factor's IC measured WITHIN each market regime. Momentum
    tends to earn in a calm-bull tape and bleed in a choppy one; this quantifies it from the
    capture panel (each snapshot carries its regime stamp), so the gate can eventually switch
    criteria by climate instead of using one threshold for all weather."""
    import numpy as np
    from app.db.database import SessionLocal
    from app.db.models import FactorSnapshot
    from app.services.signal_calibration import _spearman

    h = str(int(horizon_days))
    db = SessionLocal()
    try:
        rows = db.query(FactorSnapshot.snap_date, FactorSnapshot.factors, FactorSnapshot.fwd_ret).all()
    finally:
        db.close()

    # per (regime, date) cross-section
    by_key: dict = {}
    for sd, fac, fwd in rows:
        if not (isinstance(fac, dict) and isinstance(fwd, dict) and h in fwd):
            continue
        reg = fac.get("regime") or "unknown"
        by_key.setdefault((reg, sd), []).append((fac, float(fwd[h])))

    regimes = sorted({k[0] for k in by_key})
    matrix: dict = {}
    for f in _FACTORS:
        matrix[f] = {}
        for reg in regimes:
            ics = []
            for (r, sd), items in by_key.items():
                if r != reg:
                    continue
                pairs = [(fa.get(f), fr) for fa, fr in items if isinstance(fa.get(f), (int, float))]
                if len(pairs) >= 5:
                    ic = _spearman([p[0] for p in pairs], [p[1] for p in pairs])
                    if ic is not None:
                        ics.append(ic)
            if ics:
                matrix[f][reg] = {"n_dates": len(ics), "mean_ic": round(float(np.mean(ics)), 4)}
            else:
                matrix[f][reg] = {"n_dates": 0, "mean_ic": None}

    return {"horizon_days": int(horizon_days), "regimes": regimes,
            "ic_by_regime": matrix,
            "note": "Per-factor IC WITHIN each market regime (HMM). Positive in one climate + "
                    "negative in another ⇒ make the factor regime-conditional."}


def capture_status() -> dict:
    """Row/label counts for the capture base (for the dashboard)."""
    from app.db.database import SessionLocal
    from app.db.models import FactorSnapshot
    db = SessionLocal()
    try:
        total = db.query(FactorSnapshot).count()
        # count in Python — a stored None can round-trip as JSON null, so isnot(None) lies
        labelled = sum(1 for (f,) in db.query(FactorSnapshot.fwd_ret).all() if isinstance(f, dict) and f)
        dates = db.query(FactorSnapshot.snap_date).distinct().count()
        return {"rows": total, "labelled": labelled, "snap_dates": dates}
    finally:
        db.close()


__all__ = ["capture_snapshot", "label_snapshots", "snapshot_attribution",
           "regime_conditional_ic", "capture_status"]
