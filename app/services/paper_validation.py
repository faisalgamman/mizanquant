"""Paper-validation ledger — accumulate a real track record for the weekly picks.

The paper_trade_gate graduation counter only moves on CLOSED trades that carry a
``pnl_pct``. The live broker close-recording path is incomplete, so instead this
keeps an ISOLATED simulated ledger (strategy_id="PV"):

  * record_weekly_picks(): take this week's `build_weekly_report` picks and insert
    them into TradeHistory as OPEN paper trades (Option-A: fixed 15% catastrophe
    stop), one per symbol (deduped against still-open PV trades).
  * mature_open_paper_trades(): for each open PV trade, replay the symbol's REAL
    price path and apply the validated Option-A exit (15% stop OR 20 trading-day
    time exit) via the already-tested `signal_tracker._simulate_fixed_exit`; when
    it matures, write pnl / pnl_pct / exit_price / closed_at → the gate counts it.

No Alpaca orders, no live-trading flags — a simulated ledger using real prices and
the exact exit policy we validate. It answers "do the weekly picks have an edge?"
honestly and accumulates the evidence over time.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.exc import SQLAlchemyError

from app.config import settings
from app.db.database import SessionLocal
from app.db.models import TradeHistory

logger = logging.getLogger("screener")

PV_STRATEGY = "PV"  # isolated paper-validation strategy id (TradeHistory.strategy_id, <=5 chars)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _paper_row_from_pick(pick: dict) -> dict:
    """Map a weekly-report pick to TradeHistory column kwargs (pure, testable)."""
    return {
        "strategy_id": PV_STRATEGY,
        "symbol": pick.get("symbol"),
        "side": "buy",
        "qty": float(pick.get("shares") or 0),
        "entry_price": float(pick.get("entry") or 0),
        "stop_loss": pick.get("catastrophe_stop"),
        "take_profit": pick.get("far_take_profit"),
        "position_value": pick.get("position_value"),
        "risk_amount": pick.get("risk_amount"),
        "risk_pct": pick.get("risk_pct_realized"),
        "confidence": float(pick.get("confidence") or 0),
        "status": "open",
        "signal_details": {
            "source": "paper_validation",
            "verdict": pick.get("verdict"),
            "hold_days": pick.get("hold_days"),
            "stop_pct": pick.get("stop_pct"),
            "time_exit_date": pick.get("time_exit_date"),
            "votes": pick.get("votes"),
        },
    }


def record_weekly_picks(account: float = 10000.0, top: int = 15,
                        min_confidence: float = 45.0, funnel: str = "pipeline") -> dict:
    """Record this week's picks as OPEN paper trades (one per symbol, deduped)."""
    from app.services.weekly_report import build_weekly_report

    report = build_weekly_report(account, top=top, min_confidence=min_confidence, funnel=funnel)
    picks = report.get("picks", []) if isinstance(report, dict) else []
    if not picks:
        return {"recorded": 0, "skipped": 0, "reason": "no picks"}

    db = SessionLocal()
    try:
        open_syms = {
            r[0] for r in db.query(TradeHistory.symbol).filter(
                TradeHistory.strategy_id == PV_STRATEGY,
                TradeHistory.pnl_pct.is_(None),
            ).all()
        }
        recorded = skipped = 0
        for p in picks:
            sym = p.get("symbol")
            if not sym or float(p.get("shares") or 0) <= 0 or sym in open_syms:
                skipped += 1
                continue
            db.add(TradeHistory(created_at=_utc_now(), **_paper_row_from_pick(p)))
            open_syms.add(sym)
            recorded += 1
        db.commit()
        return {"recorded": recorded, "skipped": skipped}
    except SQLAlchemyError as e:
        db.rollback()
        logger.error("paper_validation record failed: %s", e)
        return {"error": str(e)}
    finally:
        db.close()


def mature_open_paper_trades() -> dict:
    """Close any open PV trade whose Option-A exit (15% stop / 20-day time) has fired."""
    from app.services.market_data import fetch as fetch_market_data
    from app.services.signal_tracker import _simulate_fixed_exit

    hold_days = int(getattr(settings, "SWING_MAX_HOLD_DAYS", 20))
    stop_pct = float(getattr(settings, "SWING_TRAIL_PCT", 15.0))

    db = SessionLocal()
    try:
        open_trades = db.query(TradeHistory).filter(
            TradeHistory.strategy_id == PV_STRATEGY,
            TradeHistory.pnl_pct.is_(None),
        ).all()
        checked = closed = 0
        for t in open_trades:
            checked += 1
            try:
                entry = float(t.entry_price or 0)
                if entry <= 0:
                    continue
                df = fetch_market_data(t.symbol, period="6mo")
                if df is None or len(df) == 0:
                    continue
                try:
                    post = df[df.index > t.created_at]
                except Exception:
                    post = df.tail(hold_days + 5)
                sim = _simulate_fixed_exit(post, entry, hold_days, stop_pct, is_sell=False)
                if sim is None:
                    continue  # not matured yet
                ret_pct, exit_price = sim
                t.exit_price = exit_price
                t.pnl = round((exit_price - entry) * float(t.qty or 0), 2)
                t.pnl_pct = ret_pct
                t.closed_at = _utc_now()
                t.status = "closed"
                closed += 1
            except Exception as e:  # one bad symbol must not abort the batch
                logger.debug("paper_validation mature %s failed: %s", t.symbol, e)
        db.commit()
        return {"checked": checked, "closed": closed}
    except SQLAlchemyError as e:
        db.rollback()
        logger.error("paper_validation mature failed: %s", e)
        return {"error": str(e)}
    finally:
        db.close()


def paper_ledger_status() -> dict:
    """Open/closed PV counts + the graduation status for the PV ledger."""
    from app.services.paper_trade_gate import paper_trade_status

    open_n = closed_n = 0
    db = SessionLocal()
    try:
        open_n = db.query(TradeHistory).filter(
            TradeHistory.strategy_id == PV_STRATEGY, TradeHistory.pnl_pct.is_(None)).count()
        closed_n = db.query(TradeHistory).filter(
            TradeHistory.strategy_id == PV_STRATEGY, TradeHistory.pnl_pct.isnot(None)).count()
    except SQLAlchemyError as e:
        logger.debug("paper_ledger_status count failed: %s", e)
    finally:
        db.close()
    return {
        "open": open_n,
        "closed": closed_n,
        "graduation": paper_trade_status(strategy_id=PV_STRATEGY).as_dict(),
    }
