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


def _clean_factors(d: dict) -> dict:
    """Replace non-finite floats (NaN/inf) with None — a stored NaN serialises to the literal
    `NaN`, which the Postgres JSON type rejects ("Token NaN is invalid"). Deeper history (older
    bars / gappy symbols) occasionally yields NaN in a factor, so sanitise every value on write."""
    import math
    return {k: (None if (isinstance(v, float) and not math.isfinite(v)) else v) for k, v in (d or {}).items()}


def _panel_rows(universe: str = "halal"):
    """(snap_date, factors, fwd_ret) panel rows, filtered to the requested slice.

    universe="halal" (default — preserves every existing reading): keeps rows whose factors carry
    halal∈{1, missing} — legacy rows predate the tag and were all halal-universe. universe="all":
    the full research panel including the non-halal expansion (DISCOVERY reading only; nothing
    graduates without passing the halal slice)."""
    from app.db.database import SessionLocal
    from app.db.models import FactorSnapshot
    db = SessionLocal()
    try:
        rows = db.query(FactorSnapshot.snap_date, FactorSnapshot.factors,
                        FactorSnapshot.fwd_ret,).all()
    finally:
        db.close()
    if universe == "all":
        return rows
    return [r for r in rows if not (isinstance(r[1], dict) and r[1].get("halal") == 0)]


def _price_factors(closes, spy_closes=None) -> dict:
    """Extra CLOSE-ONLY factors (so both the live capture AND the historical backfill compute
    the SAME set → one measurable panel). Each factor is guarded independently; a short window
    just omits that factor. Price/volume-derivable-from-closes only — no fundamentals/lookahead.
    Factor factory (DATA_ENGINE_PLAN.md ph2): 52w-high proximity, residual & risk-adjusted
    momentum, momentum consistency, downside vol, beta, max-drawdown, range position, 5d reversal."""
    import numpy as np
    out: dict = {}
    try:
        c = np.asarray(closes, dtype=float)
        c = c[np.isfinite(c) & (c > 0)]
        if len(c) < 40:
            return out
        px = float(c[-1])
        # 52-week-high proximity (Grinblatt-Han) — c / trailing-252 max
        win = c[-252:] if len(c) >= 252 else c
        hi = float(win.max())
        if hi > 0:
            out["hi52_prox"] = round(px / hi, 3)
        # 6-month max drawdown %
        w6 = c[-126:] if len(c) >= 126 else c
        peak = np.maximum.accumulate(w6)
        out["maxdd_6m"] = round(float((w6 / peak - 1.0).min()) * 100, 2)
        # position within the last 20-day range (0..1)
        if len(c) >= 20:
            w20 = c[-20:]; lo, hh = float(w20.min()), float(w20.max())
            out["range_pos_20"] = round((px - lo) / (hh - lo), 3) if hh > lo else 0.5
        # 5-day short-term reversal (negative of recent return — reversal is a known effect)
        if len(c) >= 6 and c[-6] > 0:
            out["rev_5d"] = round(-(px / float(c[-6]) - 1.0) * 100, 2)
        # daily returns → vol / downside vol / risk-adjusted & consistency of momentum
        r = np.diff(c) / c[:-1]
        r = r[np.isfinite(r)]
        if len(r) >= 60:
            vol = float(r.std() * np.sqrt(252))
            neg = r[r < 0]
            if len(neg) > 5:
                out["downside_vol"] = round(float(neg.std() * np.sqrt(252)) * 100, 2)
            if len(c) >= 253 and vol > 1e-6:
                mom121 = float(c[-21] / c[-252] - 1.0)
                out["sharpe_mom"] = round(mom121 / vol, 3)
            if len(c) >= 252:
                seq = c[-252:]
                blocks = [seq[k + 21] / seq[k] - 1.0 for k in range(0, 231, 21)]
                if blocks:
                    out["mom_consistency"] = round(sum(1 for x in blocks if x > 0) / len(blocks), 2)
        # beta + residual (idiosyncratic) 12-1 momentum vs SPY
        if spy_closes is not None:
            s = np.asarray(spy_closes, dtype=float); s = s[np.isfinite(s) & (s > 0)]
            n = min(len(c), len(s), 120)
            if n >= 60:
                rc = np.diff(c[-n:]) / c[-n:][:-1]; rsp = np.diff(s[-n:]) / s[-n:][:-1]
                m = min(len(rc), len(rsp)); rc, rsp = rc[-m:], rsp[-m:]
                var = float(rsp.var())
                if var > 1e-12:
                    beta = float(np.cov(rc, rsp)[0, 1] / var)
                    out["beta"] = round(beta, 2)
                    if len(c) >= 253 and len(s) >= 253:
                        ms = float(c[-21] / c[-252] - 1.0); mspy = float(s[-21] / s[-252] - 1.0)
                        out["resid_mom"] = round((ms - beta * mspy) * 100, 2)
    except Exception:
        pass
    return out


# factor labels for the factor table (extends _FACTORS below)
_NEW_FACTORS = ("hi52_prox", "resid_mom", "sharpe_mom", "mom_consistency", "downside_vol", "beta", "maxdd_6m", "range_pos_20", "rev_5d")


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
        base = {
            "rs": round(float(rs) * 100, 3) if rs is not None else None,
            "rsi": round(float(last.get("_rsi") or 0), 1),
            "above_ema20": 1 if (ema20 and c > ema20) else 0,
            "atr_pct": round(float(last.get("_atr_pct") or 0), 2),
            "dist_ema20_pct": round((c / ema20 - 1) * 100, 2) if ema20 else None,
            "mom_12_1": round(float(mom) * 100, 2) if mom is not None else None,
            "price": round(c, 2),
        }
        base.update(_price_factors(closes, spy_closes))   # + the factor-factory (close-only) factors
        return base
    except Exception:
        return None


_PEAD_WINDOW = 140.0   # calendar days from fiscal period-end over which the drift stays "fresh"
                       # (period-end + ~3-6wk announcement lag + ~60-trading-day drift window)


def _pead_factor(symbol: str) -> "float | None":
    """Post-Earnings-Announcement-Drift signal — the first NON-price factor. The most recent
    reported earnings surprise, decayed by how long ago it was (drift fades ~1 quarter after the
    announcement). Positive = a recent beat (expected continued upward drift). None when no recent
    earnings or no Finnhub key. Fail-safe. Cheap (Finnhub cached 6h)."""
    try:
        from app.services.finnhub_client import finnhub_client
        e = finnhub_client.get_recent_earnings_surprise(symbol)
        if not e:
            return None
        ds = e.get("days_since"); sp = e.get("surprise_pct")
        if not isinstance(ds, (int, float)) or not isinstance(sp, (int, float)):
            return None
        ds = max(0.0, float(ds))                    # a just-ended quarter is freshest (weight ~1)
        if ds > _PEAD_WINDOW:
            return None                             # older than a quarter → drift has faded
        w = 1.0 - ds / _PEAD_WINDOW                 # linear recency decay
        sp = max(-200.0, min(200.0, float(sp)))     # clamp tiny-estimate blowups
        return round(sp * w, 3)
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
    halal_set: set = set()
    if not symbols:
        try:
            # Research universe: halal candidates first (panel continuity) + liquid expansion,
            # composition-capped internally (RESEARCH_HALAL_N/RESEARCH_EXPANSION_N) — the ``cap``
            # arg only bounds the legacy fallback path.
            from app.services.research_universe import get_research_universe
            symbols = get_research_universe()
        except Exception as e:
            logger.debug("alpha_capture research universe failed: %s", e)
        if not symbols:
            try:
                from app.services.universe import build_halal_candidates
                symbols = list(build_halal_candidates() or [])[:cap]
            except Exception as e:
                logger.debug("alpha_capture universe load failed: %s", e)
                return {"error": "no universe"}
    symbols = list(dict.fromkeys(symbols))
    try:
        from app.services.research_universe import get_halal_set
        halal_set = get_halal_set()
    except Exception:
        halal_set = set()

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
                # halal-slice tag (two-tier research): 1 = in the AAOIFI basket today, 0 = research-
                # only expansion name. Legacy rows lack the tag and are treated as halal (they were).
                fac["halal"] = 1 if (halal_set and sym.upper() in halal_set) else (1 if not halal_set else 0)
                # PEAD (non-price factor) — compute only for the HALAL (buyable) names to keep the
                # Finnhub call budget bounded; expansion names are denominator-only and don't need it.
                if fac["halal"] == 1:
                    p = _pead_factor(sym)
                    if p is not None:
                        fac["pead"] = p
                db.add(FactorSnapshot(snap_date=snap_date, symbol=sym,
                                      price=fac.get("price"), factors=_clean_factors(fac), fwd_ret=None))
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


_FACTORS = ("rs", "rsi", "above_ema20", "atr_pct", "dist_ema20_pct", "mom_12_1") + _NEW_FACTORS + ("pead",)


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
    # halal slice only (the verdict that counts) — expansion rows are discovery-only
    rows = [r for r in rows if not (isinstance(r[3], dict) and r[3].get("halal") == 0)]

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


def _rsi_series(closes, period: int = 14):
    """Causal Wilder RSI for every bar (value at i uses only bars ≤ i)."""
    import numpy as np
    import pandas as pd
    c = pd.Series(np.asarray(closes, dtype=float))
    delta = c.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    dn = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rsi = 100.0 - 100.0 / (1.0 + up / dn.replace(0, np.nan))
    rsi = rsi.where(dn != 0, 100.0)          # no losses → RSI 100 (not NaN)
    rsi = rsi.where(~((dn == 0) & (up == 0)), 50.0)   # perfectly flat → 50
    return rsi.values


def _pit_regime_labels(spy_closes, win: int = 20):
    """Point-in-time regime label per bar (calm_bull / choppy / crisis) from SPY only — a
    cheap look-ahead-safe proxy for the HMM used in the historical backfill: trend sign +
    realized vol vs its own EXPANDING median up to that bar. No future data."""
    import numpy as np
    c = np.asarray(spy_closes, dtype=float)
    rets = np.diff(np.log(c))
    out = [None] * len(c)
    vols = []
    for i in range(1, len(c)):
        w = rets[max(0, i - win):i]
        v = float(np.std(w)) if len(w) >= 5 else float("nan")
        vols.append(v)
        if not np.isfinite(v) or i < win + 5:
            continue
        med = float(np.median([x for x in vols if np.isfinite(x)]))
        trend = c[i] / c[max(0, i - win)] - 1.0
        if v > 1.5 * med:
            out[i] = "crisis"
        elif trend > 0 and v <= med:
            out[i] = "calm_bull"
        else:
            out[i] = "choppy"
    return out


def backfill_snapshots(symbols=None, *, period: str = "2y", rebalance_days: int = 5,
                       horizons=(5, 10, 20), warmup: int = 120, cap: int = 120) -> dict:
    """① Historical backfill of the capture base — the real multiplier. Reconstruct each
    name's factors AS OF every rebalance date over ``period`` (look-ahead-safe, price-only:
    same causal EMA/RSI/RS/momentum as live) and label with the KNOWN forward returns at
    every horizon (③). Fills IC/attribution/meta with hundreds of dates TODAY instead of
    waiting months. Idempotent (dedup by snap_date+symbol). Heavy — run in the background."""
    import numpy as np
    import pandas as pd
    from app.db.database import SessionLocal
    from app.db.models import FactorSnapshot
    from app.services.backtest_engine import _aligned_closes, factor_rs_vs_spy, factor_momentum_12_1

    if not symbols:
        try:
            from app.services.research_universe import get_research_universe
            symbols = get_research_universe()
        except Exception as e:
            logger.debug("backfill research universe failed: %s", e)
        if not symbols:
            try:
                from app.services.universe import build_halal_candidates
                symbols = list(build_halal_candidates() or [])[:cap]
            except Exception as e:
                return {"error": f"no universe: {e}"}
    symbols = list(dict.fromkeys(symbols))
    try:
        from app.services.research_universe import get_halal_set
        _hset = get_halal_set()
    except Exception:
        _hset = set()
    mat = _aligned_closes(list(symbols) + ["SPY"], period)
    if mat is None or "SPY" not in mat.columns:
        return {"error": "no data"}
    dates = mat.index
    max_h = max(horizons)
    if len(dates) < warmup + max_h + rebalance_days:
        return {"error": "insufficient history", "rows": len(dates)}

    reg = _pit_regime_labels(mat["SPY"].values)
    universe = [s for s in symbols if s in mat.columns and s != "SPY"]
    rebal = list(range(warmup, len(dates) - max_h, rebalance_days))

    db = SessionLocal()
    stored = skipped = 0
    try:
        # existing (snap_date, symbol) keys to stay idempotent
        existing = {(d, s) for d, s in db.query(FactorSnapshot.snap_date, FactorSnapshot.symbol).all()}
        for s in universe:
            col = mat[s]
            c = col.values
            ema20 = col.ewm(span=20, adjust=False).mean().values
            rsi = _rsi_series(c, 14)
            logret = np.diff(np.log(np.where(c > 0, c, np.nan)))
            for i in rebal:
                # store NAIVE UTC (the column is naive) so the idempotency key round-trips
                d = dates[i].to_pydatetime().replace(tzinfo=None) if hasattr(dates[i], "to_pydatetime") else dates[i]
                if (d, s) in existing:
                    skipped += 1
                    continue
                csl = col.iloc[:i + 1].dropna().values
                if len(csl) < warmup or not np.isfinite(c[i]) or c[i] <= 0:
                    continue
                spysl = mat["SPY"].iloc[:i + 1].dropna().values
                rs = factor_rs_vs_spy(csl, spysl, 63)
                if rs is None:
                    continue
                mom = factor_momentum_12_1(csl)
                e = float(ema20[i]) if np.isfinite(ema20[i]) else 0.0
                atr_pct = float(np.std(logret[max(0, i - 14):i]) * 100) if i > 15 else None
                fwd = {}
                for h in horizons:
                    j = i + h
                    if j < len(c) and np.isfinite(c[j]) and c[i] > 0:
                        fwd[str(h)] = round(float(c[j] / c[i] - 1.0) * 100, 3)
                if not fwd:
                    continue
                fac = {
                    "rs": round(float(rs) * 100, 3),
                    "rsi": round(float(rsi[i]), 1) if np.isfinite(rsi[i]) else None,
                    "above_ema20": 1 if (e and c[i] > e) else 0,
                    "dist_ema20_pct": round((c[i] / e - 1) * 100, 2) if e else None,
                    "mom_12_1": round(float(mom) * 100, 2) if mom is not None else None,
                    "atr_pct": round(atr_pct, 2) if atr_pct is not None else None,
                    "regime": reg[i], "price": round(float(c[i]), 2), "backfill": 1,
                    # halal tag AS OF TODAY (membership history starts with the daily basket
                    # archive; historical membership is unknowable — documented limitation)
                    "halal": 1 if (_hset and s.upper() in _hset) else (1 if not _hset else 0),
                }
                fac.update(_price_factors(csl, spysl))   # + factor-factory (close-only), AS-OF date i
                db.add(FactorSnapshot(snap_date=d, symbol=s, price=fac["price"],
                                      factors=_clean_factors(fac), fwd_ret=_clean_factors(fwd)))
                existing.add((d, s))
                stored += 1
            if stored and stored % 2000 == 0:
                db.commit()
        db.commit()
        return {"universe": len(universe), "rebalance_dates": len(rebal),
                "horizons": list(horizons), "stored": stored, "skipped": skipped}
    except Exception as e:
        db.rollback()
        logger.error("backfill_snapshots failed: %s", e)
        return {"error": str(e)}
    finally:
        db.close()


_BACKFILL = {"running": False, "result": None}


def run_backfill_bg(period=None, cap=None, warmup=None, rebalance_days=5) -> dict:
    """Single-flight background backfill so the request path never blocks on the reconstruction.
    Params (or env BACKFILL_PERIOD / BACKFILL_CAP / BACKFILL_WARMUP) let a BIG run (e.g. 3y ×
    the whole halal universe, weekly) multiply the panel — the real lever for statistical power.
    Heavy on shared-cpu-1x → single-flight bg thread, chunked commits, idempotent."""
    import os as _os, threading
    if _BACKFILL["running"]:
        return {"status": "already_running"}
    per = str(period or _os.environ.get("BACKFILL_PERIOD", "5y"))   # 5y reaches 2021 (incl. 2022 bear); "3y" returns only ~2y from the source
    try:
        cp = int(cap if cap is not None else _os.environ.get("BACKFILL_CAP", "120"))
    except (TypeError, ValueError):
        cp = 120
    try:
        wu = int(warmup if warmup is not None else _os.environ.get("BACKFILL_WARMUP", "120"))
    except (TypeError, ValueError):
        wu = 120

    def _run():
        _BACKFILL["running"] = True
        try:
            _BACKFILL["result"] = backfill_snapshots(period=per, cap=cp, warmup=wu, rebalance_days=rebalance_days)
        except Exception as e:
            _BACKFILL["result"] = {"error": str(e)}
        finally:
            _BACKFILL["running"] = False
    threading.Thread(target=_run, daemon=True, name="alpha-backfill").start()
    return {"status": "started", "period": per, "cap": cp, "warmup": wu}


def backfill_status() -> dict:
    return {"running": _BACKFILL["running"], "last_result": _BACKFILL["result"]}


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
    rows = _panel_rows()

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


def gate_ema20_ab(horizon_days: int = 20, min_rs=None) -> dict:
    """SHADOW test of the standing hypothesis that the weekly gate's 'above EMA20'
    REQUIREMENT may hurt (backfill IC of above_ema20 was −0.024 at 10d, −0.033 at 20d).

    On the capture panel, hold the RS gate fixed and, per date, compare the mean forward
    return of the with-trend set (above_ema20==1) vs the counter-trend set (above_ema20==0).
    Paired across dates. If counter-trend ≥ with-trend, requiring above-EMA20 adds no value
    (confirms the hypothesis). Rich immediate answer from the backfill; the live PVSH shadow
    (weekly_gate_forward_eval require_ema20=False) confirms it forward as it accrues."""
    import numpy as np
    from app.db.database import SessionLocal
    from app.db.models import FactorSnapshot

    if min_rs is None:
        try:
            from app.services.gate_config import get_min_rs
            min_rs = get_min_rs()
        except Exception:
            min_rs = -2.0
    h = str(int(horizon_days))
    rows = _panel_rows()

    by_date: dict = {}
    for sd, fac, fwd in rows:
        if not (isinstance(fac, dict) and isinstance(fwd, dict) and h in fwd):
            continue
        rs, ab = fac.get("rs"), fac.get("above_ema20")
        if not isinstance(rs, (int, float)) or ab not in (0, 1) or float(rs) < min_rs:
            continue
        by_date.setdefault(sd, []).append((int(ab), float(fwd[h])))

    with_m, counter_m, diffs = [], [], []
    for _sd, items in by_date.items():
        w = [r for a, r in items if a == 1]
        c = [r for a, r in items if a == 0]
        if len(w) >= 3 and len(c) >= 3:
            mw, mc = float(np.mean(w)), float(np.mean(c))
            with_m.append(mw)
            counter_m.append(mc)
            diffs.append(mc - mw)

    n = len(diffs)
    if n < 10:
        return {"status": "insufficient", "n_dates": n, "min_rs": min_rs}
    a = np.asarray(diffs, dtype=float)
    md = float(a.mean())
    sd_ = float(a.std(ddof=1)) if n > 1 else 0.0
    t = round(float(md / (sd_ / np.sqrt(n))), 2) if sd_ > 0 else None   # plain float (JSON-safe)
    confirms = bool(md > 0 and t is not None and t >= 1.5)              # plain bool (np.bool_ isn't JSON-serializable)
    return {
        "horizon_days": int(horizon_days), "min_rs": min_rs, "n_dates": n,
        "with_ema20_mean_ret": round(float(np.mean(with_m)), 3),
        "counter_trend_mean_ret": round(float(np.mean(counter_m)), 3),
        "delta_counter_minus_with": round(md, 3), "paired_t": t,
        "hypothesis_confirmed": confirms,
        "verdict": ("requiring above-EMA20 HURTS — counter-trend names do as well or better"
                    if confirms else
                    "above-EMA20 requirement helps or is neutral — keep it"),
        "note": "Paired A/B on the capture panel (with-trend vs counter-trend), RS gate held fixed.",
    }


_VERDICTS = {
    "mom_12_1": {"pos_strong": "الأقوى — ثابت في كل الأنظمة"},
    "above_ema20": {"neg_strong": "سلبي — شراء الممتدّ يضعف قريباً"},
}


def _factor_verdict(factor: str, ir):
    """Direction arrow + plain-language verdict from the primary-horizon IR."""
    sp = _VERDICTS.get(factor, {})
    if ir is None:
        return "—", "يتراكم"
    if ir >= 1.5:
        return "↑↑↑", sp.get("pos_strong", "قويّ موجب")
    if ir >= 0.3:
        return "↑", "موجب ضعيف"
    if ir > -0.3:
        return "→", "محايد"
    if ir > -1.5:
        return "↓", "ميل ارتدادي (ضعيف)"
    return "↓↓", sp.get("neg_strong", "سلبي قويّ")


_MULTI_CACHE = {"at": 0.0, "key": None, "data": None}
_CAND_CACHE = {"at": 0.0, "key": None, "data": None}


def candidate_composites_ic(horizons=(5, 10, 20), universe: str = "halal") -> dict:
    """Forward IC of CANDIDATE technical composites vs the plain-momentum baseline, measured on
    the snapshot panel (per-date cross-sectional z-score → Spearman IC vs forward return). This
    is a SHADOW factor race — research/measurement only, it NEVER touches the live composite or
    any order. Cached ~10 min; only changes as the daily capture/labeling adds data.

    Candidates come from the on-panel measurement: plain 12-1 momentum is the robust anchor; the
    mean-reversion tweaks help short-horizon at best. Surfacing them lets the edge earn its way
    in by evidence over time before any weighting decision (which stays the user's call)."""
    import time as _t
    key = (tuple(int(h) for h in horizons), universe)
    now = _t.time()
    if _CAND_CACHE["data"] is not None and _CAND_CACHE["key"] == key and (now - _CAND_CACHE["at"]) < 600:
        return _CAND_CACHE["data"]
    import numpy as np
    from app.services.signal_calibration import _spearman

    hs = [str(int(h)) for h in horizons]
    rows = _panel_rows(universe)
    by_date: dict = {}
    for sd, fac, fwd in rows:
        if isinstance(fac, dict) and isinstance(fwd, dict):
            by_date.setdefault(sd, []).append((fac, fwd))

    def _z(vals):
        a = np.asarray(vals, dtype=float)
        s = a.std()
        return (a - a.mean()) / s if s > 1e-9 else a * 0.0

    NEED = ("mom_12_1", "above_ema20", "rsi", "dist_ema20_pct")

    def _adaptive(Z, rg):
        # HAND-SPECIFIED regime weights (direction-only, from the regime-conditional IC finding —
        # NOT magnitudes fit to this panel, so it stays honest/OOS-style): in a calm uptrend lean
        # on the trend filter + light momentum; in choppy/crisis lean on momentum and PENALISE
        # being over-extended above EMA20 (falling-knife / stretched). This is the whole thesis:
        # the composite should ADAPT to the HMM regime instead of using fixed weights.
        if rg == "calm_bull":
            return Z["above_ema20"] + 0.5 * Z["mom_12_1"]
        if rg == "crisis":
            return Z["mom_12_1"] - Z["above_ema20"]
        return Z["mom_12_1"] - 0.5 * Z["above_ema20"]     # choppy / unknown

    CANDS = {
        "mom": lambda Z, rg: Z["mom_12_1"],
        "adaptive": _adaptive,
        "fresh_ema": lambda Z, rg: Z["mom_12_1"] - Z["above_ema20"],
        "combo": lambda Z, rg: Z["mom_12_1"] - Z["above_ema20"] - 0.5 * Z["rsi"],
        "dip": lambda Z, rg: Z["mom_12_1"] - Z["rsi"],
        "fresh_dist": lambda Z, rg: Z["mom_12_1"] - Z["dist_ema20_pct"],
    }
    LABELS = {"mom": "الزخم الخام (أساس)", "adaptive": "★ مشروط بالنظام (HMM)", "fresh_ema": "زخم − فوق EMA20",
              "combo": "مركّب (زخم − EMA20 − ½·RSI)", "dip": "زخم − RSI", "fresh_dist": "زخم − امتداد"}
    acc = {c: {h: [] for h in hs} for c in CANDS}
    exc = {c: {h: [] for h in hs} for c in CANDS}   # top-quintile forward EXCESS (long-only alpha proxy)
    for _sd, items in by_date.items():
        good = [(fa, fw) for fa, fw in items if all(isinstance(fa.get(k), (int, float)) for k in NEED)]
        if len(good) < 6:
            continue
        Z = {k: _z([fa.get(k) for fa, fw in good]) for k in NEED}
        rg = good[0][0].get("regime")   # market regime is a per-date stamp (same for all names)
        for cname, fn in CANDS.items():
            score = fn(Z, rg)
            for h in hs:
                ys = [(float(score[i]), float(good[i][1].get(h)))
                      for i in range(len(good)) if isinstance(good[i][1].get(h), (int, float))]
                if len(ys) >= 6:
                    ic = _spearman([p[0] for p in ys], [p[1] for p in ys])
                    if ic is not None:
                        acc[cname][h].append(ic)
                # Long-only reality: only the TOP picks are bought — measure their excess vs the
                # equal-weight universe (IC ranks the whole cross-section; a long-only picker cares
                # only about the top bucket, and the two can DISAGREE).
                if len(ys) >= 8:
                    ss = sorted(ys, key=lambda p: -p[0])
                    ktop = max(3, int(round(len(ys) * 0.2)))
                    um = sum(p[1] for p in ys) / len(ys)
                    tm = sum(p[1] for p in ss[:ktop]) / ktop
                    exc[cname][h].append(tm - um)

    out = {}
    max_dates = 0
    for cname in CANDS:
        per_h = {}
        for h in hs:
            a = np.asarray(acc[cname][h], dtype=float)
            e = np.asarray(exc[cname][h], dtype=float)
            n = len(a)
            if n:
                sd_ = float(a.std(ddof=1)) if n > 1 else 0.0
                cell = {"mean_ic": round(float(a.mean()), 4),
                        "t": round(float(a.mean() / (sd_ / np.sqrt(n))), 2) if sd_ > 0 else None,
                        "n_dates": n}
                if len(e):
                    se = float(e.std(ddof=1)) if len(e) > 1 else 0.0
                    cell["top_excess"] = round(float(e.mean()), 3)     # % excess of top-20% vs universe
                    cell["top_t"] = round(float(e.mean() / (se / np.sqrt(len(e)))), 2) if se > 0 else None
                    cell["top_win"] = int((e > 0).mean() * 100)
                per_h[h] = cell
                max_dates = max(max_dates, n)
            else:
                per_h[h] = {"mean_ic": None, "t": None, "n_dates": 0}
        out[cname] = {"label": LABELS[cname], "h": per_h}
    res = {"horizons": [int(h) for h in horizons], "labelled_dates": max_dates, "candidates": out,
           "universe": universe,
           "note": "Shadow candidate composites — forward IC vs plain-momentum. Research only; never live scoring."}
    _CAND_CACHE.update(at=now, key=key, data=res)
    return res


_MKTREL_CACHE = {"at": 0.0, "key": None, "data": None}


def market_relative_race(horizons=(5, 10, 20)) -> dict:
    """Two-tier hypothesis test (from the 2026-07 breadth expansion): does z-scoring a MULTI-factor
    composite over the FULL research universe (halal + non-halal expansion — a ~3x wider normalisation
    denominator) pick BETTER halal names than z-scoring over halal-only? Single-factor rank is invariant
    to scaling, so this only moves multi-factor composites (the wider denominator changes each factor's
    relative scale → different composite ranking of the SAME halal names). We rank over the chosen
    universe but ONLY ever buy/measure the HALAL top-bucket (long-only halal). 'market' beating 'halal'
    = the wider denominator improves selection. Meaningful only once expansion history is backfilled.
    SHADOW/research — never touches live scoring or any order."""
    import time as _t
    key = tuple(int(h) for h in horizons)
    now = _t.time()
    if _MKTREL_CACHE["data"] is not None and _MKTREL_CACHE["key"] == key and (now - _MKTREL_CACHE["at"]) < 600:
        return _MKTREL_CACHE["data"]
    import numpy as np
    hs = [str(int(h)) for h in horizons]
    rows = _panel_rows("all")
    by_date: dict = {}
    for sd, fac, fwd in rows:
        if isinstance(fac, dict) and isinstance(fwd, dict):
            by_date.setdefault(sd, []).append((fac, fwd))

    def _z(vals):
        a = np.asarray(vals, dtype=float); s = a.std()
        return (a - a.mean()) / s if s > 1e-9 else a * 0.0

    def _is_halal(fac):
        return fac.get("halal") != 0                      # 1 or missing(legacy) = halal; 0 = expansion

    NEED = ("mom_12_1", "above_ema20", "dist_ema20_pct")

    def _adaptive(Z, rg):
        if rg == "calm_bull":
            return Z["above_ema20"] + 0.5 * Z["mom_12_1"]
        if rg == "crisis":
            return Z["mom_12_1"] - Z["above_ema20"]
        return Z["mom_12_1"] - 0.5 * Z["above_ema20"]
    RECIPES = {"fresh_dist": lambda Z, rg: Z["mom_12_1"] - Z["dist_ema20_pct"], "adaptive": _adaptive}
    LABELS = {"fresh_dist": "زخم − امتداد", "adaptive": "★ مشروط بالنظام (HMM)"}

    exc = {r: {m: {h: [] for h in hs} for m in ("market", "halal")} for r in RECIPES}
    dates_used = dates_with_exp = 0
    for _sd, items in by_date.items():
        allg = [(fa, fw) for fa, fw in items if all(isinstance(fa.get(k), (int, float)) for k in NEED)]
        hal = [(fa, fw) for fa, fw in allg if _is_halal(fa)]
        if len(hal) < 8 or len(allg) < 12:
            continue
        dates_used += 1
        if any(not _is_halal(fa) for fa, fw in allg):
            dates_with_exp += 1
        rg = allg[0][0].get("regime")
        Zfull = {k: _z([fa.get(k) for fa, fw in allg]) for k in NEED}     # over FULL universe
        Zhal = {k: _z([fa.get(k) for fa, fw in hal]) for k in NEED}       # over halal-only
        halal_idx = [i for i, (fa, fw) in enumerate(allg) if _is_halal(fa)]
        for rname, fn in RECIPES.items():
            sfull = fn(Zfull, rg); shal = fn(Zhal, rg)
            for h in hs:
                ym = [(float(sfull[i]), float(allg[i][1].get(h))) for i in halal_idx
                      if isinstance(allg[i][1].get(h), (int, float))]
                yh = [(float(shal[j]), float(hal[j][1].get(h))) for j in range(len(hal))
                      if isinstance(hal[j][1].get(h), (int, float))]
                for tag, ys in (("market", ym), ("halal", yh)):
                    if len(ys) >= 8:
                        ss = sorted(ys, key=lambda p: -p[0])
                        ktop = max(3, int(round(len(ys) * 0.2)))
                        um = sum(p[1] for p in ys) / len(ys)
                        tm = sum(p[1] for p in ss[:ktop]) / ktop
                        exc[rname][tag][h].append(tm - um)

    out = {}
    for rname in RECIPES:
        per_h = {}
        for h in hs:
            cell = {}
            for tag in ("market", "halal"):
                e = np.asarray(exc[rname][tag][h], dtype=float)
                if len(e):
                    se = float(e.std(ddof=1)) if len(e) > 1 else 0.0
                    cell[tag] = {"top_excess": round(float(e.mean()), 3),
                                 "t": round(float(e.mean() / (se / np.sqrt(len(e)))), 2) if se > 0 else None,
                                 "n": len(e)}
                else:
                    cell[tag] = {"top_excess": None, "t": None, "n": 0}
            per_h[h] = cell
        out[rname] = {"label": LABELS[rname], "h": per_h}
    res = {"horizons": [int(h) for h in horizons], "recipes": out,
           "dates_used": dates_used, "dates_with_expansion": dates_with_exp,
           "note": "Two-tier test: rank a multi-factor composite over the FULL universe (market) vs "
                   "halal-only (halal); buy/measure HALAL top-bucket only. 'market' top_excess > 'halal' "
                   "means the wider denominator improves halal selection. Grows meaningful as expansion "
                   "history backfills (see dates_with_expansion). Shadow only; never live scoring."}
    _MKTREL_CACHE.update(at=now, key=key, data=res)
    return res


_SIM_CACHE = {"at": 0.0, "key": None, "data": None}


def walk_forward_sim(top_k: int = 5, hold: str = "5", cost_bps: float = 15.0) -> dict:
    """⏱️ 'Time machine' — walk-forward simulation of each shadow composite on the 4-year snapshot
    panel: each ~weekly rebalance date, rank cross-sectionally, BUY the top_k equal-weight, realise
    the fwd_ret[hold] minus round-trip costs; chain into an equity curve vs the equal-weight halal
    universe (the honest 'did selection add value' benchmark — NOT literally SPY). Reports total/
    CAGR/max-drawdown/win/PF + a 2022-only slice (the adverse test). Research only; never trades.

    HONEST caveats baked in: survivorship (today's halal set) inflates ABSOLUTE returns, so read the
    strategy-vs-universe SPREAD and the 2022 slice, not the headline number; costs are applied to the
    strategy only (conservative)."""
    import time as _t
    key = (int(top_k), str(hold), float(cost_bps))
    now = _t.time()
    if _SIM_CACHE["data"] is not None and _SIM_CACHE["key"] == key and (now - _SIM_CACHE["at"]) < 900:
        return _SIM_CACHE["data"]
    import numpy as np, math
    from app.db.database import SessionLocal
    from app.db.models import FactorSnapshot
    rows = _panel_rows()
    by_date: dict = {}
    for sd, fac, fwd in rows:
        if isinstance(fac, dict) and isinstance(fwd, dict) and isinstance(fwd.get(hold), (int, float)):
            by_date.setdefault(sd, []).append((fac, fwd))
    all_dates = sorted(by_date.keys())
    # non-overlapping ~weekly rebalance dates (hold=5 trading days ≈ 7 calendar) to avoid double-count
    gap = max(1, int(hold)) + 1
    rebal, last = [], None
    for d in all_dates:
        if last is None or (d - last).days >= gap:
            rebal.append(d); last = d

    def _z(v):
        a = np.asarray(v, float); s = a.std()
        return (a - a.mean()) / s if s > 1e-9 else a * 0.0

    def _adaptive(Z, rg):
        if rg == "calm_bull":
            return Z["above_ema20"] + 0.5 * Z["mom_12_1"]
        if rg == "crisis":
            return Z["mom_12_1"] - Z["above_ema20"]
        return Z["mom_12_1"] - 0.5 * Z["above_ema20"]
    NEED = ("mom_12_1", "above_ema20", "rsi", "dist_ema20_pct")
    CANDS = {"adaptive": _adaptive, "mom": lambda Z, rg: Z["mom_12_1"],
             "fresh_dist": lambda Z, rg: Z["mom_12_1"] - Z["dist_ema20_pct"]}
    LABELS = {"adaptive": "★ مشروط بالنظام (HMM)", "mom": "الزخم الخام", "fresh_dist": "زخم − امتداد"}
    cost = cost_bps / 1e4

    series = {c: [] for c in CANDS}      # (date, period_ret) for the strategy
    uni = []                              # equal-weight universe period returns (benchmark)
    for d in rebal:
        good = [(fa, fw) for fa, fw in by_date[d] if all(isinstance(fa.get(k), (int, float)) for k in NEED)]
        if len(good) < 10:
            continue
        y = np.array([float(fw.get(hold)) / 100.0 for fa, fw in good])
        uni.append((d, float(y.mean())))
        Z = {k: _z([fa.get(k) for fa, fw in good]) for k in NEED}
        rg = good[0][0].get("regime")
        kk = min(top_k, max(1, len(good) // 3))
        for c, fn in CANDS.items():
            s = np.asarray(fn(Z, rg), float)
            top = np.argsort(-s)[:kk]
            series[c].append((d, float(y[top].mean()) - cost))

    def _metrics(pairs):
        if len(pairs) < 4:
            return None
        rets = np.array([r for _, r in pairs])
        eq = np.cumprod(1.0 + rets)
        yrs = max(0.1, len(rets) / 52.0)
        cagr = float(eq[-1] ** (1.0 / yrs) - 1.0)
        peak = np.maximum.accumulate(eq); mdd = float((eq / peak - 1.0).min())
        gains = rets[rets > 0].sum(); losses = -rets[rets < 0].sum()
        pf = float(gains / losses) if losses > 1e-9 else None
        r22 = np.array([r for d, r in pairs if d.year == 2022])
        ret22 = float(np.prod(1.0 + r22) - 1.0) if len(r22) else None
        curve = [{"date": str(pairs[i][0])[:10], "eq": round(float(eq[i]), 3)}
                 for i in range(0, len(eq), max(1, len(eq) // 80))]
        return {"total_return": round(float(eq[-1] - 1.0) * 100, 1), "cagr": round(cagr * 100, 1),
                "max_drawdown": round(mdd * 100, 1), "win_rate": int((rets > 0).mean() * 100),
                "pf": round(pf, 2) if pf else None, "periods": len(rets),
                "ret_2022": round(ret22 * 100, 1) if ret22 is not None else None, "curve": curve}

    umet = _metrics(uni)
    out = {}
    for c in CANDS:
        m = _metrics(series[c])
        if m and umet:
            m["label"] = LABELS[c]
            m["alpha_cagr"] = round(m["cagr"] - umet["cagr"], 1)                       # vs universe
            m["alpha_2022"] = (round(m["ret_2022"] - umet["ret_2022"], 1)
                               if (m["ret_2022"] is not None and umet["ret_2022"] is not None) else None)
        out[c] = m
    res = {"top_k": top_k, "hold_days": int(hold), "cost_bps": cost_bps,
           "rebalances": len(uni), "span": [str(all_dates[0])[:10], str(all_dates[-1])[:10]] if all_dates else None,
           "benchmark": umet, "strategies": out,
           "note": "Walk-forward on the snapshot panel vs the equal-weight halal universe. Survivorship inflates ABSOLUTE returns — read the SPREAD vs benchmark + the 2022 slice. Costs on the strategy only. Research only; never trades."}
    _SIM_CACHE.update(at=now, key=key, data=res)
    return res


_CORE_CACHE = {"at": 0.0, "key": None, "data": None}


def core_overlay_sim(hold: str = "5", target_vol: float = 0.15, cost_bps: float = 15.0) -> dict:
    """⏱️ Core+Overlay A/B (CORE_OVERLAY_PLAN.md) — the data said selection LOSES but the signals
    are DEFENSIVE, so test the professional inversion: OWN the equal-weight halal universe (the
    'core'), then let the HMM regime + vol-target manage EXPOSURE (the overlay). Compares, on the
    4y panel: core (100% always) vs core×regime-dial vs core×vol-target vs both. Look-ahead-safe
    (regime/vol are point-in-time). The thesis wins if an overlay keeps most of the CAGR while
    cutting drawdown a lot ⇒ a better CAGR/DD ratio (a risk-tolerant sizer can then lever up).
    Research only; never trades."""
    import time as _t
    key = (str(hold), float(target_vol), float(cost_bps))
    now = _t.time()
    if _CORE_CACHE["data"] is not None and _CORE_CACHE["key"] == key and (now - _CORE_CACHE["at"]) < 900:
        return _CORE_CACHE["data"]
    import numpy as np
    from app.db.database import SessionLocal
    from app.db.models import FactorSnapshot
    rows = _panel_rows()
    by_date: dict = {}
    for sd, fac, fwd in rows:
        if isinstance(fac, dict) and isinstance(fwd, dict) and isinstance(fwd.get(hold), (int, float)):
            by_date.setdefault(sd, []).append((fac, fwd))
    all_dates = sorted(by_date.keys())
    gap = max(1, int(hold)) + 1
    seq, last = [], None
    for d in all_dates:
        if last is not None and (d - last).days < gap:
            continue
        items = by_date[d]
        if len(items) < 10:
            continue
        ur = float(np.mean([float(fw.get(hold)) / 100.0 for fa, fw in items]))
        rg = items[0][0].get("regime")
        seq.append((d, ur, rg)); last = d

    urs = [x[1] for x in seq]
    EXP = {"calm_bull": 1.0, "choppy": 0.75, "crisis": 0.45}
    cost = cost_bps / 1e4

    def _vt(i):
        if i < 12:
            return 1.0
        v = float(np.std(urs[i - 12:i]) * np.sqrt(52))
        return float(min(1.5, target_vol / v)) if v > 1e-6 else 1.0

    def _run(exp_fn):
        rets = []; prev = 1.0
        for i, (d, ur, rg) in enumerate(seq):
            e = exp_fn(i, rg)
            c = cost if abs(e - prev) > 0.05 else 0.0
            rets.append((d, e * ur - c)); prev = e
        return rets

    strategies = {
        "core": _run(lambda i, rg: 1.0),
        "dial": _run(lambda i, rg: EXP.get(rg, 0.75)),
        "voltarget": _run(lambda i, rg: _vt(i)),
        "both": _run(lambda i, rg: min(1.5, EXP.get(rg, 0.75) * _vt(i))),
    }
    LABELS = {"core": "النواة (الكون بالتساوي)", "dial": "النواة + قرص النظام (HMM)",
              "voltarget": "النواة + استهداف التقلّب", "both": "النواة + الاثنان معاً"}

    def _metrics(pairs):
        rets = np.array([r for _, r in pairs]); n = len(rets)
        if n < 4:
            return None
        eq = np.cumprod(1.0 + rets); yrs = max(0.1, n / 52.0)
        cagr = float(eq[-1] ** (1.0 / yrs) - 1.0)
        peak = np.maximum.accumulate(eq); mdd = float((eq / peak - 1.0).min())
        r22 = np.array([r for d, r in pairs if d.year == 2022])
        curve = [{"date": str(pairs[i][0])[:10], "eq": round(float(eq[i]), 3)} for i in range(0, n, max(1, n // 80))]
        return {"total_return": round(float(eq[-1] - 1.0) * 100, 1), "cagr": round(cagr * 100, 1),
                "max_drawdown": round(mdd * 100, 1), "cagr_dd": round(cagr / abs(mdd), 2) if mdd < -1e-6 else None,
                "win_rate": int((rets > 0).mean() * 100), "ret_2022": round(float(np.prod(1.0 + r22) - 1.0) * 100, 1) if len(r22) else None,
                "curve": curve}

    core_m = _metrics(strategies["core"])
    out = {}
    for k, pairs in strategies.items():
        m = _metrics(pairs)
        if m:
            m["label"] = LABELS[k]
            if core_m:
                m["dd_improve_pct"] = round((abs(core_m["max_drawdown"]) - abs(m["max_drawdown"])) / abs(core_m["max_drawdown"]) * 100, 0) if core_m["max_drawdown"] else None
                m["cagr_dd_improve_pct"] = round((m["cagr_dd"] - core_m["cagr_dd"]) / abs(core_m["cagr_dd"]) * 100, 0) if (m.get("cagr_dd") and core_m.get("cagr_dd")) else None
        out[k] = m
    res = {"hold_days": int(hold), "target_vol": target_vol, "cost_bps": cost_bps, "rebalances": len(seq),
           "span": [str(all_dates[0])[:10], str(all_dates[-1])[:10]] if all_dates else None, "strategies": out,
           "note": "Core+Overlay on the panel (regime/vol are point-in-time, look-ahead-safe). The overlay wins if it cuts drawdown a lot while keeping most CAGR → higher CAGR/DD ratio. Survivorship inflates absolute returns — read the CAGR/DD ratio + 2022. Research only; never trades."}
    _CORE_CACHE.update(at=now, key=key, data=res)
    return res


_VALID_CACHE = {"at": 0.0, "n": None, "data": None}


def candidate_forward_validation(recent_n: int = 60) -> dict:
    """GRADUATION GATE (auto-propose, NEVER auto-apply). Measures each shadow composite on the
    MOST-RECENT `recent_n` snapshot dates — a rolling window that becomes genuinely out-of-sample
    as new snapshots accrue — and compares to the full panel. A candidate is 'ready' only if it
    ALSO holds up recently (t≥2 recent AND t≥1.5 full, same sign) — the guard against in-sample
    luck (we watched beta look significant then evaporate). Cached 10 min."""
    import time as _t
    now = _t.time()
    if _VALID_CACHE["data"] is not None and _VALID_CACHE["n"] == recent_n and (now - _VALID_CACHE["at"]) < 600:
        return _VALID_CACHE["data"]
    import numpy as np, math
    from app.db.database import SessionLocal
    from app.db.models import FactorSnapshot
    full = candidate_composites_ic()
    rows = _panel_rows()
    by_date: dict = {}
    for sd, fac, fwd in rows:
        if isinstance(fac, dict) and isinstance(fwd, dict):
            by_date.setdefault(sd, []).append((fac, fwd))
    recent = sorted(by_date.keys())[-recent_n:]

    def _z(v):
        a = np.asarray(v, float); s = a.std()
        return (a - a.mean()) / s if s > 1e-9 else a * 0.0

    def _adaptive(Z, rg):
        if rg == "calm_bull":
            return Z["above_ema20"] + 0.5 * Z["mom_12_1"]
        if rg == "crisis":
            return Z["mom_12_1"] - Z["above_ema20"]
        return Z["mom_12_1"] - 0.5 * Z["above_ema20"]
    NEED = ("mom_12_1", "above_ema20", "rsi", "dist_ema20_pct")
    CANDS = {"mom": lambda Z, rg: Z["mom_12_1"], "adaptive": _adaptive,
             "fresh_ema": lambda Z, rg: Z["mom_12_1"] - Z["above_ema20"],
             "combo": lambda Z, rg: Z["mom_12_1"] - Z["above_ema20"] - 0.5 * Z["rsi"],
             "dip": lambda Z, rg: Z["mom_12_1"] - Z["rsi"],
             "fresh_dist": lambda Z, rg: Z["mom_12_1"] - Z["dist_ema20_pct"]}
    H = "10"
    exc = {c: [] for c in CANDS}
    used = 0
    for dt in recent:
        good = [(fa, fw) for fa, fw in by_date[dt]
                if all(isinstance(fa.get(k), (int, float)) for k in NEED) and isinstance(fw.get(H), (int, float))]
        if len(good) < 8:
            continue
        used += 1
        Z = {k: _z([fa.get(k) for fa, fw in good]) for k in NEED}
        rg = good[0][0].get("regime")
        y = [float(fw.get(H)) for fa, fw in good]
        um = sum(y) / len(y)
        ktop = max(3, int(round(len(good) * 0.2)))
        for c, fn in CANDS.items():
            s = fn(Z, rg)
            order = sorted(range(len(good)), key=lambda i: -float(s[i]))
            exc[c].append(sum(y[i] for i in order[:ktop]) / ktop - um)

    fullc = full.get("candidates", {})
    out = {}
    for c in CANDS:
        e = np.asarray(exc[c], float); n = len(e)
        re = rt = None
        if n >= 5:
            se = float(e.std(ddof=1)) if n > 1 else 0.0
            re = round(float(e.mean()), 3)
            rt = round(float(e.mean() / (se / math.sqrt(n))), 2) if se > 0 else None
        ft = ((fullc.get(c) or {}).get("h", {}).get("10") or {}).get("top_t")
        ready = (rt is not None and ft is not None and rt >= 2.0 and ft >= 1.5)
        status = "ready" if ready else ("watching" if (rt is not None and rt > 0.5) else "weak")
        out[c] = {"label": (fullc.get(c) or {}).get("label", c), "full_t": ft, "recent_t": rt,
                  "recent_excess": re, "recent_dates": n, "ready": bool(ready), "status": status}
    res = {"recent_n": recent_n, "recent_dates_used": used, "candidates": out,
           "any_ready": any(v["ready"] for v in out.values()),
           "note": "Graduation gate — 'ready' only if the candidate holds on the recent window too. Auto-PROPOSE only; never auto-applies to live scoring."}
    _VALID_CACHE.update(at=now, n=recent_n, data=res)
    return res


def snapshot_attribution_multi(horizons=(5, 10, 20), universe: str = "halal") -> dict:
    """Per-factor Information Coefficient at MULTIPLE horizons in ONE pass (for the factor
    table) + a direction arrow and plain-language verdict from the primary (10d) horizon.
    Cached ~10 min — it scans the whole snapshot panel (thousands of rows) and only changes
    when the daily capture/backfill adds data."""
    import time as _t
    key = (tuple(int(h) for h in horizons), universe)
    now = _t.time()
    if _MULTI_CACHE["data"] is not None and _MULTI_CACHE["key"] == key and (now - _MULTI_CACHE["at"]) < 600:
        return _MULTI_CACHE["data"]
    import numpy as np
    from app.services.signal_calibration import _spearman

    hs = [str(int(h)) for h in horizons]
    rows = _panel_rows(universe)

    by_date: dict = {}
    for sd, fac, fwd in rows:
        if isinstance(fac, dict) and isinstance(fwd, dict):
            by_date.setdefault(sd, []).append((fac, fwd))

    out = {}
    max_dates = 0
    for f in _FACTORS:
        per_h = {}
        for h in hs:
            ics = []
            for _sd, items in by_date.items():
                pairs = [(fa.get(f), float(fw[h])) for fa, fw in items
                         if isinstance(fa.get(f), (int, float)) and isinstance(fw.get(h), (int, float))]
                if len(pairs) >= 5:
                    ic = _spearman([p[0] for p in pairs], [p[1] for p in pairs])
                    if ic is not None:
                        ics.append(ic)
            if ics:
                a = np.asarray(ics, dtype=float)
                n = len(a)
                sd_ = float(a.std(ddof=1)) if n > 1 else 0.0
                per_h[h] = {"mean_ic": round(float(a.mean()), 4),
                            "ir": round(float(a.mean() / (sd_ / np.sqrt(n))), 2) if sd_ > 0 else None,
                            "n_dates": n}
                max_dates = max(max_dates, n)
            else:
                per_h[h] = {"mean_ic": None, "ir": None, "n_dates": 0}
        prim = per_h.get("10") or per_h.get(hs[0], {})
        direction, verdict = _factor_verdict(f, prim.get("ir"))
        out[f] = {"h": per_h, "direction": direction, "verdict": verdict}

    res = {"horizons": [int(h) for h in horizons], "labelled_dates": max_dates,
           "factors": out,
           "note": "Cross-sectional IC per factor at 5/10/20-day horizons — power accrues per day."}
    _MULTI_CACHE.update(at=now, key=key, data=res)
    return res


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
           "regime_conditional_ic", "gate_ema20_ab", "snapshot_attribution_multi",
           "capture_status", "backfill_snapshots", "run_backfill_bg", "backfill_status"]
