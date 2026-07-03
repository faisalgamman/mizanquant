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


def cumulative_alpha_series(strategy_ids=("PV", "PVM"), days: int = 365) -> dict:
    """Real cumulative selection-alpha curve from the closed paper ledger."""
    from datetime import datetime, timedelta, timezone
    from app.db.database import SessionLocal
    from app.db.models import TradeHistory
    from app.services.beta_benchmark import _spy_lookup

    spy_at = _spy_lookup()
    if spy_at is None:
        return {"series": [], "error": "no SPY data"}
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    db = SessionLocal()
    try:
        rows = (db.query(TradeHistory)
                  .filter(TradeHistory.strategy_id.in_(list(strategy_ids)),
                          TradeHistory.pnl_pct.isnot(None),
                          TradeHistory.closed_at.isnot(None),
                          TradeHistory.closed_at >= cutoff)
                  .order_by(TradeHistory.closed_at.asc()).all())
        trades = [(float(r.pnl_pct), r.created_at, r.closed_at, r.closed_at.date().isoformat())
                  for r in rows if r.created_at and r.closed_at]
    finally:
        db.close()

    series = cumulative_alpha(trades, spy_at)
    return {"series": series, "n": len(series),
            "final_alpha": series[-1]["cum_alpha"] if series else 0.0,
            "note": "مجموع تراكمي لـ(عائد الصفقة − SPY) على الصفقات المغلقة بالترتيب الزمني."}


__all__ = ["parametric_var", "cumulative_alpha", "portfolio_var", "cumulative_alpha_series"]
