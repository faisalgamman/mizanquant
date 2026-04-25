"""Paper-trade graduation gate (Phase 8 — deployment safety).

Chan's epilogue ("Going Live") makes one rule blunt: do not commit live
capital to a strategy until paper trading has produced *measured*
positive expectancy across enough trades, enough symbols, and enough
calendar time to be statistically meaningful. The hardening pass below
turns that rule into code.

A strategy is considered **graduated** — i.e. eligible to receive live
capital — only when ALL of the following hold:

1. **n_trades ≥ min_trades** — the sample is large enough that DSR and
   p-values are not pinned to a handful of lucky exits. Default 30.
2. **calendar_days ≥ min_calendar_days** — the trades span enough
   distinct days that we are not measuring one good week. Default 14.
3. **unique_symbols ≥ min_unique_symbols** — the edge does not depend
   on a single ticker. Default 8.
4. **DSR ≥ min_dsr** — Deflated Sharpe (Phase 1) is above the
   conviction threshold; the apparent edge survives multiple-testing
   adjustment. Default 0.60.
5. **Mean return > 0** — basic sanity: the strategy is not net-losing
   on the paper sample.

The function is split into a pure evaluator (`evaluate_graduation`)
that takes the raw arrays and a thin DB adapter (`paper_trade_status`)
that pulls the arrays from `TradeHistory`. The pure form is what
tests pin down; the adapter is what `risk_manager` calls before
allowing any live order to flow.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Sequence

import numpy as np

from app.services.backtest_qc import deflated_sharpe


@dataclass
class GraduationStatus:
    graduated: bool
    reason: str
    n_trades: int
    calendar_days: float
    unique_symbols: int
    mean_return: float
    sharpe: float
    dsr: float

    def as_dict(self) -> dict:
        return {
            "graduated": self.graduated,
            "reason": self.reason,
            "n_trades": self.n_trades,
            "calendar_days": round(self.calendar_days, 1),
            "unique_symbols": self.unique_symbols,
            "mean_return_pct": round(self.mean_return * 100, 4),
            "sharpe": round(self.sharpe, 3),
            "dsr": round(self.dsr, 3),
        }


def evaluate_graduation(
    pnls_pct: Sequence[float],
    timestamps: Sequence[datetime],
    symbols: Sequence[str],
    min_trades: int = 30,
    min_calendar_days: int = 14,
    min_unique_symbols: int = 8,
    min_dsr: float = 0.60,
    n_trials_announced: int = 1,
) -> GraduationStatus:
    """Pure graduation evaluator.

    Parameters
    ----------
    pnls_pct
        Per-trade returns as fractions (0.05 = +5%).
    timestamps
        Trade close (or open) times; only the spread between min and max
        matters for the calendar-days check.
    symbols
        One ticker per trade. Used for the unique-symbols check.
    n_trials_announced
        Pass through to deflated_sharpe — the number of strategies / param
        sets searched. Set conservatively to honour multiple-testing
        adjustment (defaulting to 1 if the strategy has not been tuned).

    Returns
    -------
    GraduationStatus with the verdict and every diagnostic that fed it.
    """
    rets = np.asarray(list(pnls_pct), dtype=float)
    rets = rets[np.isfinite(rets)]
    n = len(rets)

    times = list(timestamps)
    syms = list(symbols)

    failures: list[str] = []

    if n < min_trades:
        failures.append(f"n_trades={n}<{min_trades}")

    cal_days = 0.0
    if times:
        cal_days = (max(times) - min(times)).total_seconds() / 86400.0
    if cal_days < min_calendar_days:
        failures.append(f"calendar_days={cal_days:.1f}<{min_calendar_days}")

    uniq = len({s for s in syms if s})
    if uniq < min_unique_symbols:
        failures.append(f"unique_symbols={uniq}<{min_unique_symbols}")

    if n == 0:
        mu = 0.0
        sharpe = 0.0
        dsr = 0.0
    else:
        mu = float(rets.mean())
        sd = float(rets.std(ddof=1)) if n > 1 else 0.0
        sharpe = float(mu / sd * np.sqrt(252)) if sd > 0 else 0.0
        dsr = deflated_sharpe(rets, n_trials=n_trials_announced)

    if mu <= 0:
        failures.append(f"mean_return_pct={mu*100:.3f}<=0")
    if dsr < min_dsr:
        failures.append(f"DSR={dsr:.2f}<{min_dsr}")

    graduated = len(failures) == 0
    reason = "graduated — eligible for live capital" if graduated else "; ".join(failures)

    return GraduationStatus(
        graduated=graduated,
        reason=reason,
        n_trades=n,
        calendar_days=cal_days,
        unique_symbols=uniq,
        mean_return=mu,
        sharpe=sharpe,
        dsr=dsr,
    )


def paper_trade_status(
    strategy_id: str | None = None,
    lookback_trades: int = 200,
    **thresholds,
) -> GraduationStatus:
    """DB-backed wrapper: pull recent paper trades for the given strategy
    and evaluate graduation. Returns a not-graduated status with reason
    `"DB unavailable"` when the database cannot be reached, so callers
    can fail closed (no live capital on a degraded backend)."""
    try:
        from app.db.database import SessionLocal
        from app.db.models import TradeHistory

        db = SessionLocal()
        try:
            query = db.query(TradeHistory).filter(TradeHistory.pnl_pct.isnot(None))
            if strategy_id:
                query = query.filter(TradeHistory.strategy_id == strategy_id)
            trades = (
                query.order_by(TradeHistory.created_at.desc())
                .limit(lookback_trades)
                .all()
            )
        finally:
            db.close()
    except Exception:
        return GraduationStatus(
            graduated=False,
            reason="DB unavailable",
            n_trades=0,
            calendar_days=0.0,
            unique_symbols=0,
            mean_return=0.0,
            sharpe=0.0,
            dsr=0.0,
        )

    pnls: list[float] = []
    times: list[datetime] = []
    syms: list[str] = []
    for t in trades:
        if t.pnl_pct is None:
            continue
        try:
            pnls.append(float(t.pnl_pct) / 100.0)
        except (TypeError, ValueError):
            continue
        times.append(t.created_at)
        syms.append(t.symbol or "")

    return evaluate_graduation(pnls, times, syms, **thresholds)


__all__ = ["GraduationStatus", "evaluate_graduation", "paper_trade_status"]
