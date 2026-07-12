"""Portfolio risk + cumulative-alpha series — the two panels that were placeholders.

VaR is a parametric 1-day estimate (equity × market vol × z) using SPY realised vol as a
beta proxy for a long-only equity book. The cumulative-alpha series walks the CLOSED paper
trades in time order and accumulates each trade's return minus SPY over its own window — a
real equity-curve of selection alpha (look-ahead-safe: uses realised dates). Read-only.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("screener")

_Z = {95: 1.645, 99: 2.326}


def parametric_var(equity, daily_vol, z: float = 1.645):
    """1-day parametric VaR = equity × daily_vol × z (pure). None on bad inputs."""
    try:
        e, v = float(equity), float(daily_vol)
    except (TypeError, ValueError):
        return None
    if e <= 0 or v <= 0:
        return None
    return round(e * v * float(z), 2)


def cumulative_alpha(trades, spy_close_at) -> list:
    """Cumulative selection alpha over CLOSED trades in time order (pure/testable).

    ``trades``: iterable of ``(ret_pct, entry_ts, exit_ts, date_label)`` already sorted by
    exit. Returns ``[{date, cum_alpha}]`` — the running sum of (trade − SPY) per trade.
    """
    cum, out = 0.0, []
    for tr, ent, ex, lab in trades:
        se = spy_close_at(ent)
        sx = spy_close_at(ex)
        if se and sx and se > 0:
            cum += float(tr) - (sx / se - 1.0) * 100.0
            out.append({"date": lab, "cum_alpha": round(cum, 3)})
    return out


def _spy_daily_vol(win: int = 20):
    """SPY realised daily vol (std of last ``win`` log returns). None on no data."""
    try:
        import numpy as np
        from app.services.market_data import fetch
        spy = fetch("SPY", period="3mo")
        if spy is None or len(spy) < win + 2:
            return None
        c = spy["close"].astype(float).values
        rets = np.diff(np.log(c))[-win:]
        v = float(np.std(rets))
        return v if v > 0 else None
    except Exception as e:
        logger.debug("spy vol failed: %s", e)
        return None


def portfolio_var(equity=None) -> dict:
    """Parametric 1-day VaR for the book. ``equity`` from the caller (dashboard passes the
    live portfolio value); daily vol from SPY (market-beta proxy)."""
    dv = _spy_daily_vol()
    v95 = parametric_var(equity, dv, _Z[95]) if (equity and dv) else None
    v99 = parametric_var(equity, dv, _Z[99]) if (equity and dv) else None
    return {
        "equity": float(equity) if equity else None,
        "daily_vol_pct": round(dv * 100, 2) if dv else None,
        "ann_vol_pct": round(dv * (252 ** 0.5) * 100, 1) if dv else None,
        "var_95": -v95 if v95 is not None else None,   # negative = loss
        "var_99": -v99 if v99 is not None else None,
        "posture": "منخفض" if (dv and dv < 0.008) else "متوسط" if (dv and dv < 0.014) else "مرتفع" if dv else "—",
        "note": "VaR بارامتري يومي = القيمة × تقلّب SPY × z (وكيل بيتا لمحفظة شراء-فقط).",
    }


def _bench_lookup(symbol: str = "SPY", period: str = "2y"):
    """Return a ``ts -> <symbol> close on/nearest-before that date`` function, or None.
    Generalises the SPY lookup to ANY benchmark (e.g. the halal ETFs SPUS / HLAL)."""
    try:
        import numpy as np
        import pandas as pd
        from app.services.market_data import fetch
        df = fetch(symbol, period=period)
        if df is None or len(df) == 0 or "date" not in df.columns:
            return None
        d = pd.to_datetime(df["date"], utc=True)
        closes = df["close"].astype(float).values
        order = np.argsort(d.values)
        d_sorted = d.values[order]
        c_sorted = closes[order]

        def _at(ts):
            try:
                t = pd.Timestamp(ts)
                if t.tz is None:
                    t = t.tz_localize("UTC")
                idx = np.searchsorted(d_sorted, np.datetime64(t), side="right") - 1
                return float(c_sorted[idx]) if idx >= 0 else None
            except Exception:
                return None
        return _at
    except Exception as exc:
        logger.debug("bench lookup build failed for %s: %s", symbol, exc)
        return None


def cumulative_alpha_series(strategy_ids=("PV", "PVM"), days: int = 365,
                            benchmark: str = "SPY") -> dict:
    """Real cumulative selection-alpha curve from the closed paper ledger vs ``benchmark``.

    Applies each ledger's inception cutoff (ledger_inception) — the SAME cutoff the weekly
    ledger status + graduation gate already use — so a known-corrupt pre-inception batch (e.g.
    the 31 weekly picks recorded right before the 2026-06 drop, all closed at the catastrophe
    stop) does not poison the curve. Those trades stay in the DB; they are just not reported here.
    ``benchmark`` defaults to SPY (broad market); pass SPUS / HLAL for the halal-ETF alternative.
    """
    from datetime import datetime, timedelta, timezone
    from app.db.database import SessionLocal
    from app.db.models import TradeHistory
    from app.services.paper_validation import ledger_inception

    spy_at = _bench_lookup(benchmark)
    if spy_at is None:
        return {"series": [], "error": f"no {benchmark} data"}
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    inceptions = {s: ledger_inception(s) for s in strategy_ids}  # per-strategy, None = full history
    db = SessionLocal()
    excluded = 0
    try:
        rows = (db.query(TradeHistory)
                  .filter(TradeHistory.strategy_id.in_(list(strategy_ids)),
                          TradeHistory.pnl_pct.isnot(None),
                          TradeHistory.closed_at.isnot(None),
                          TradeHistory.closed_at >= cutoff)
                  .order_by(TradeHistory.closed_at.asc()).all())
        trades = []
        for r in rows:
            if not (r.created_at and r.closed_at):
                continue
            inc = inceptions.get(r.strategy_id)
            if inc is not None:
                ca = r.created_at.replace(tzinfo=None) if r.created_at.tzinfo else r.created_at
                if ca < inc:                     # pre-inception corrupt batch — skip (as elsewhere)
                    excluded += 1
                    continue
            trades.append((float(r.pnl_pct), r.created_at, r.closed_at, r.closed_at.date().isoformat()))
    finally:
        db.close()

    series = cumulative_alpha(trades, spy_at)
    return {"series": series, "n": len(series),
            "final_alpha": series[-1]["cum_alpha"] if series else 0.0,
            "excluded_pre_inception": excluded,
            "note": "مجموع تراكمي لـ(عائد الصفقة − SPY) على الصفقات المغلقة بالترتيب الزمني، بعد "
                    "استبعاد الدفعات قبل تأسيس كلّ دفتر (نفس قطع حالة الدفتر والتخرّج)."}


def benchmark_comparison(strategy_ids=("PV", "PVM"), days: int = 365,
                         benchmarks=("SPY", "SPUS", "HLAL")) -> dict:
    """Cumulative selection alpha of the closed paper ledger vs EACH benchmark.

    SPY = the broad market; SPUS (SP Funds S&P 500 Sharia) and HLAL (Wahed FTSE USA Shariah) =
    the passive HALAL alternative a Muslim investor could simply buy. Beating SPY can be mostly
    the halal tilt (tech-heavy, no banks); beating SPUS/HLAL is the honest test of whether our
    stock SELECTION adds value over just owning a halal index. Same inception cutoff as the
    curve. Read-only measurement — never trades.
    """
    labels = {"SPY": "S&P 500 (broad market)",
              "SPUS": "SP Funds S&P 500 Sharia (halal ETF)",
              "HLAL": "Wahed FTSE USA Shariah (halal ETF)"}
    out: dict = {}
    n = None
    for b in benchmarks:
        r = cumulative_alpha_series(strategy_ids=strategy_ids, days=days, benchmark=b)
        out[b] = {"label": labels.get(b, b), "cum_alpha": r.get("final_alpha"),
                  "n": r.get("n"), "halal": b in ("SPUS", "HLAL"), "error": r.get("error")}
        if n is None and r.get("n"):
            n = r.get("n")
    beats_halal = None
    try:
        halal_alphas = [out[b]["cum_alpha"] for b in ("SPUS", "HLAL")
                        if out.get(b) and out[b].get("cum_alpha") is not None]
        if halal_alphas:
            beats_halal = all(a > 0 for a in halal_alphas)
    except Exception:
        pass
    return {"benchmarks": out, "n": n, "beats_halal_benchmarks": beats_halal,
            "note": ("Cumulative selection alpha (Σ trade − benchmark) vs each index. SPUS/HLAL are "
                     "halal ETFs a Muslim investor could buy directly — beating THEM (not just SPY) "
                     "is the honest test of halal stock-selection skill. If alpha vs SPUS/HLAL is "
                     "≤0, the honest answer is 'just buy the halal ETF'. Paper measurement only.")}


__all__ = ["parametric_var", "cumulative_alpha", "portfolio_var", "cumulative_alpha_series",
           "benchmark_comparison"]
