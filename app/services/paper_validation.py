"""Paper-validation ledgers — accumulate REAL track records for BOTH scanners.

The paper_trade_gate graduation counter only moves on CLOSED trades that carry a
``pnl_pct``. The live broker close-recording path is incomplete, so instead this
keeps TWO ISOLATED simulated ledgers, one per scanner:

  WEEKLY ledger (strategy_id="PV") — the weekly swing scanner, Option-A exit:
    * record_weekly_picks(): take this week's `build_weekly_report` picks and
      insert them as OPEN paper trades (fixed 15% catastrophe stop), one per
      symbol (deduped against still-open trades).
    * mature_open_paper_trades(): for each open trade, replay the symbol's REAL
      price path and apply the validated Option-A exit (15% stop OR 20 trading-day
      time exit) via the already-tested `signal_tracker._simulate_fixed_exit`;
      when it matures, write pnl / pnl_pct / exit_price / closed_at.

  MONTHLY ledger (strategy_id="PVM") — the monthly composite scanner, rebalanced:
    * rebalance_monthly(): pull the live composite (deep-picks) ranking; CLOSE any
      held name that fell out of the top-N at its current price (pnl_pct written),
      OPEN the new entrants at their current price, and KEEP the rest. This is the
      classic cross-sectional rebalance — the "exit" is leaving the top-N, not a
      stop or a clock.

No Alpaca orders, no live-trading flags — simulated ledgers using REAL prices and
the exact policies we run. Two isolated ledgers = an honest A/B: "does the weekly
technical scanner have an edge?" vs "does the monthly fundamental scanner?" The
real money for either half is released ONLY after its ledger graduates.

NOTE: the weekly ledger keeps the existing id "PV" (not "PVW") so the already
deployed history and the paper_trade_gate counter survive without a migration.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from sqlalchemy.exc import SQLAlchemyError

from app.config import settings
from app.db.database import SessionLocal
from app.db.models import TradeHistory

logger = logging.getLogger("screener")

# Isolated paper-validation strategy ids (TradeHistory.strategy_id is <=5 chars).
PV_WEEKLY = "PV"     # weekly swing scanner — Option-A exit (15% stop / 20-day time)
PV_MONTHLY = "PVM"   # monthly composite scanner — rebalanced (hold top-N, drop the rest)
PV_STRATEGY = PV_WEEKLY  # backward-compat alias (older callers / tests use PV_STRATEGY)


def _strategy_for(scanner: str | None) -> str:
    """Map a 'weekly' | 'monthly' scanner label to its ledger strategy id."""
    return PV_MONTHLY if str(scanner or "").lower().startswith("month") else PV_WEEKLY


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
            # C4: Also persist to SignalHistory for the weekly scanner's track record
            try:
                from app.background.cache_manager import record_signal
                record_signal(
                    symbol=sym,
                    signal_type="swing",
                    signal=str(p.get("verdict") or "BUY"),
                    score=float(p.get("confidence") or 0),
                    price=float(p.get("entry") or 0),
                    stop_loss=float(p.get("catastrophe_stop") or 0),
                    take_profit=float(p.get("far_take_profit") or 0),
                    confidence=float(p.get("confidence") or 0),
                    details={"source": "weekly_scanner", "hold_days": p.get("hold_days")},
                    breakdown={k: p.get(k) for k in
                               ("usx_score", "usx_pass", "usx_signals", "usx_version", "usx_shadow", "swing_score")
                               if p.get(k) is not None},
                )
            except Exception:
                logger.debug("weekly pick SignalHistory record skipped", exc_info=True)
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

    hold_days = int(os.environ.get("EXIT_HOLD_DAYS", getattr(settings, "SWING_MAX_HOLD_DAYS", 20)))
    stop_pct = float(os.environ.get("EXIT_STOP_PCT", getattr(settings, "SWING_TRAIL_PCT", 15.0)))

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


def paper_ledger_status(strategy_id: str = PV_WEEKLY) -> dict:
    """Open/closed counts + graduation status for one ledger (weekly PV or monthly PVM)."""
    from app.services.paper_trade_gate import paper_trade_status

    open_n = closed_n = 0
    db = SessionLocal()
    try:
        open_n = db.query(TradeHistory).filter(
            TradeHistory.strategy_id == strategy_id, TradeHistory.pnl_pct.is_(None)).count()
        closed_n = db.query(TradeHistory).filter(
            TradeHistory.strategy_id == strategy_id, TradeHistory.pnl_pct.isnot(None)).count()
    except SQLAlchemyError as e:
        logger.debug("paper_ledger_status count failed: %s", e)
    finally:
        db.close()
    return {
        "scanner": "monthly" if strategy_id == PV_MONTHLY else "weekly",
        "strategy_id": strategy_id,
        "open": open_n,
        "closed": closed_n,
        "graduation": paper_trade_status(strategy_id=strategy_id).as_dict(),
    }


# ── Monthly composite ledger (PVM) — cross-sectional rebalance ─────────────────

def _monthly_row_from_pick(pick: dict, account: float, top_n: int) -> dict:
    """Map a monthly composite pick → TradeHistory kwargs for an OPEN PVM trade.

    Equal-weight sizing: each of the top-N names gets ~account/top_n of capital.
    No stop / take-profit — the monthly exit is *leaving the top-N* at the next
    rebalance, not a price stop (so those columns stay null).
    """
    price = float(pick.get("price") or pick.get("current_price") or 0)
    budget = (float(account) / max(int(top_n), 1)) if account else 0.0
    qty = float(int(budget // price)) if price > 0 else 0.0
    return {
        "strategy_id": PV_MONTHLY,
        "symbol": pick.get("symbol"),
        "side": "buy",
        "qty": qty,
        "entry_price": round(price, 4) if price else 0.0,
        "position_value": round(qty * price, 2) if price else 0.0,
        "confidence": float(pick.get("score") or pick.get("composite_score") or 0),
        "status": "open",
        "signal_details": {
            "source": "paper_validation_monthly",
            "score": pick.get("score") or pick.get("composite_score"),
            "signal": pick.get("signal") or pick.get("signal_composite"),
            "asof": _utc_now().isoformat(),
        },
    }


def _normalize_picks(picks) -> list[dict]:
    """Coerce a picks payload into [{symbol, price, score}, ...] (drop unusable rows)."""
    out: list[dict] = []
    for p in picks or []:
        if not isinstance(p, dict):
            continue
        sym = p.get("symbol")
        price = p.get("price")
        if price is None:
            price = p.get("current_price")
        score = p.get("score")
        if score is None:
            score = p.get("composite_score")
        try:
            price = float(price)
        except (TypeError, ValueError):
            continue
        if not sym or price <= 0:
            continue
        out.append({"symbol": sym, "price": price, "score": float(score or 0)})
    return out


def _default_monthly_picks(top_n: int = 15) -> list[dict]:
    """Best-effort: read the live composite (deep-picks) ranking → [{symbol, price, score}].

    Production default for the scheduled monthly rebalance. Pulls a few times top_n
    so a name that just fell out of the top-N still has a current price to close at.
    Returns [] on any failure (the rebalance then no-ops and logs) — never fabricates.
    """
    import asyncio

    def _call():
        from app.workspace_server import screener_deep_picks
        res = asyncio.run(screener_deep_picks(limit=max(top_n * 3, 45), use_cache="true"))
        return res if isinstance(res, dict) else {}

    try:
        try:
            res = _call()
        except RuntimeError:
            # Already inside a running event loop — run in a worker thread.
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                res = ex.submit(_call).result()
    except Exception as e:
        logger.warning("monthly picks: deep-picks unavailable: %s", e)
        return []
    return _normalize_picks(res.get("results", []))


def _current_price(symbol: str, price_map: dict) -> float | None:
    """Current price for a symbol — from the scan's price map, else a fresh fetch."""
    px = price_map.get(symbol)
    if px and px > 0:
        return float(px)
    try:
        from app.services.market_data import fetch as fetch_market_data
        df = fetch_market_data(symbol, period="5d")
        if df is not None and len(df) and "close" in getattr(df, "columns", []):
            val = float(df["close"].iloc[-1])
            return val if val > 0 else None
    except Exception as e:
        logger.debug("monthly price fetch %s failed: %s", symbol, e)
    return None


def rebalance_monthly(top_n: int = 15, account: float = 10000.0, _picks_fn=None) -> dict:
    """Rebalance the monthly composite ledger (PVM) to the current top-N.

    1. Pull the composite ranking (`_picks_fn` injected in tests; defaults to the
       live deep-picks scan).
    2. CLOSE each held PVM name that fell OUT of the top-N at its current price —
       writes pnl_pct = (cur/entry-1)*100, pnl, exit_price, closed_at, status.
    3. OPEN the new entrants (top-N not already held) at their current price.
    4. KEEP the held names still inside the top-N (left untouched / open).

    Returns {target, opened, closed, held}. Simulated only — no broker orders.
    """
    fn = _picks_fn or _default_monthly_picks
    try:
        picks = _normalize_picks(fn(top_n))
    except Exception as e:
        logger.error("rebalance_monthly: picks fn failed: %s", e)
        return {"error": f"picks_fn failed: {e}"}
    if not picks:
        return {"target": 0, "opened": 0, "closed": 0, "held": 0, "reason": "no picks"}

    ranked = sorted(picks, key=lambda p: p["score"], reverse=True)
    price_map = {p["symbol"]: p["price"] for p in picks}
    target = [p["symbol"] for p in ranked][:top_n]
    target_set = set(target)

    db = SessionLocal()
    try:
        open_trades = db.query(TradeHistory).filter(
            TradeHistory.strategy_id == PV_MONTHLY, TradeHistory.pnl_pct.is_(None)).all()
        held_syms = {t.symbol for t in open_trades}

        closed = held = 0
        for t in open_trades:
            if t.symbol in target_set:
                held += 1                       # still in top-N → keep open
                continue
            cur = _current_price(t.symbol, price_map)
            if cur is None:
                held += 1                       # can't price honestly → leave open
                continue
            entry = float(t.entry_price or 0)
            t.exit_price = round(cur, 4)
            t.pnl_pct = round((cur / entry - 1.0) * 100, 2) if entry > 0 else None
            t.pnl = round((cur - entry) * float(t.qty or 0), 2)
            t.closed_at = _utc_now()
            t.status = "closed"
            closed += 1

        opened = 0
        for sym in target:
            if sym in held_syms:
                continue                        # already held → no double-open
            price = price_map.get(sym, 0)
            if not price or price <= 0:
                continue
            pick = next((p for p in ranked if p["symbol"] == sym), {"symbol": sym, "price": price})
            db.add(TradeHistory(created_at=_utc_now(),
                                **_monthly_row_from_pick(pick, account, top_n)))
            opened += 1

        db.commit()
        return {"target": len(target), "opened": opened, "closed": closed, "held": held}
    except SQLAlchemyError as e:
        db.rollback()
        logger.error("rebalance_monthly failed: %s", e)
        return {"error": str(e)}
    finally:
        db.close()
