"""Alpha-vs-beta scorecard — the honest question: does the SELECTION beat just
holding the market?

For every CLOSED trade we compare its realized return to SPY's return over the
SAME [entry, exit] window. If the picks don't beat SPY on average, the profit is
market **beta**, not selection **alpha**. Look-ahead-safe (it uses the trade's own
realized dates), and it reports a t-stat so a near-zero alpha isn't mistaken for
edge on a small sample.
"""

from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger("screener")


def compute_alpha(trades, spy_close_at) -> dict:
    """Core (testable) math.

    Parameters
    ----------
    trades : iterable of ``(trade_ret_pct, entry_ts, exit_ts)``.
    spy_close_at : callable ``ts -> float | None`` — SPY close on/nearest a date.

    Returns the scorecard: sample size, mean trade vs mean SPY return over the same
    windows, mean alpha, % of trades that beat SPY, and the alpha t-stat.
    """
    als, trs, spys = [], [], []
    for tr, ent, ex in trades:
        se = spy_close_at(ent)
        sx = spy_close_at(ex)
        if se and sx and se > 0:
            spy_ret = (sx / se - 1.0) * 100.0
            trs.append(float(tr))
            spys.append(spy_ret)
            als.append(float(tr) - spy_ret)
    n = len(als)
    if n == 0:
        return {"n": 0}
    mean_a = float(np.mean(als))
    sd = float(np.std(als, ddof=1)) if n > 1 else 0.0
    t = round(mean_a / (sd / np.sqrt(n)), 2) if (n > 1 and sd > 0) else None
    return {
        "n": n,
        "mean_trade_ret": round(float(np.mean(trs)), 3),
        "mean_spy_ret": round(float(np.mean(spys)), 3),
        "mean_alpha": round(mean_a, 3),
        "pct_beat_spy": round(100.0 * float(np.mean([a > 0 for a in als])), 1),
        "alpha_t": t,   # |t| >~ 2 → alpha distinguishable from zero
    }


def _spy_lookup(period: str = "2y"):
    """Return a ``ts -> SPY close on/nearest-before that date`` function, or None."""
    try:
        import pandas as pd
        from app.services.market_data import fetch
        spy = fetch("SPY", period=period)
        if spy is None or len(spy) == 0 or "date" not in spy.columns:
            return None
        d = pd.to_datetime(spy["date"], utc=True)
        closes = spy["close"].astype(float).values
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
        logger.debug("beta_benchmark: SPY lookup build failed: %s", exc)
        return None


def alpha_vs_spy(strategy_ids=("PVM", "PV", "PVP"), days: int = 180) -> dict:
    """Honest scorecard over CLOSED trades in the last ``days``: do the picks beat
    SPY over their own holding windows? Returns compute_alpha()'s dict (+ scope)."""
    from datetime import datetime, timedelta, timezone
    from app.db.database import SessionLocal
    from app.db.models import TradeHistory

    spy_at = _spy_lookup()
    if spy_at is None:
        return {"n": 0, "error": "no SPY data"}

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    db = SessionLocal()
    try:
        rows = (db.query(TradeHistory)
                  .filter(TradeHistory.strategy_id.in_(list(strategy_ids)),
                          TradeHistory.pnl_pct.isnot(None),
                          TradeHistory.closed_at.isnot(None),
                          TradeHistory.closed_at >= cutoff)
                  .all())
        trades = [(float(r.pnl_pct), r.created_at, r.closed_at)
                  for r in rows if r.created_at and r.closed_at]
    finally:
        db.close()

    rep = compute_alpha(trades, spy_at)
    rep["strategy_ids"] = list(strategy_ids)
    rep["days"] = days
    return rep


__all__ = ["compute_alpha", "alpha_vs_spy"]
