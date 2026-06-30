"""Auto-paper executor — place the scanner's halal BUY picks as IBKR PAPER bracket orders
and record them to the ledger. Builds a REAL paper track record on IBKR without manual clicks.

Hard safety (every run, in order):
  1. OPT-IN — does nothing unless AUTO_PAPER_TRADE is on (default off).
  2. PAPER-ONLY — aborts unless the broker is on a paper port (get_ibkr_config mode).
     This is the line that guarantees it can NEVER touch real money.
  3. KILL_SWITCH — aborts when armed.
  4. Regime gate (weekly) — no new swing longs while SPY is below its EMA21.
  5. Halal-only — sources from the halal scanners; the server-side broker path re-checks.
  6. Dedupe — skips a symbol already held on IBKR or already auto-placed today.
  7. Daily cap — at most AUTO_PAPER_MAX_NEW new orders per run.

Reuses the same picks the simulated ledgers record (weekly Option-A bracket; monthly top-N
with a wide protective bracket) and the same OrderManager('MANUAL') the manual button uses.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger("screener")

_MANUAL = "MANUAL"


def _on(name: str, default: str = "false") -> bool:
    return os.environ.get(name, default).strip().lower() in ("true", "1", "yes", "on")


def _weekly_bracket_picks(account: float = 10000.0, top: int = 15) -> list[dict]:
    """Weekly swing picks (halal, sized, Option-A bracket levels) — regime-gated: returns
    [] while SPY is below its EMA21 (don't open swing longs into a downtrend)."""
    from app.services.paper_validation import _default_regime
    reg = _default_regime()
    if reg.get("known") and reg.get("spy_bearish"):
        logger.info("auto_paper weekly skipped — SPY bearish (regime gate)")
        return []
    from app.services.weekly_report import build_weekly_report
    rep = build_weekly_report(account, top=top, funnel="swing")
    out: list[dict] = []
    for p in (rep.get("picks") or []):
        sh = int(float(p.get("shares") or 0))
        entry = float(p.get("entry") or 0)
        stop = float(p.get("catastrophe_stop") or 0)
        tp = float(p.get("far_take_profit") or 0)
        if sh > 0 and entry > 0 and stop > 0 and tp > 0:
            out.append({"symbol": p.get("symbol"), "entry": entry, "stop": stop, "tp": tp, "shares": sh})
    return out


def _monthly_bracket_picks(account: float = 10000.0, top_n: int = 15) -> list[dict]:
    """Monthly composite top-N as equal-weight buys with a WIDE protective bracket
    (stop = MONTHLY_CAT_STOP_PCT below entry; TP = 2× that above)."""
    from app.services.paper_validation import _default_monthly_picks
    stop_pct = float(os.environ.get("MONTHLY_CAT_STOP_PCT", "30")) / 100.0
    picks = _default_monthly_picks(top_n) or []
    budget = float(account) / max(int(top_n), 1)
    out: list[dict] = []
    for p in picks[:top_n]:
        price = float(p.get("price") or 0)
        sh = int(budget // price) if price > 0 else 0
        if sh > 0 and price > 0:
            out.append({"symbol": p.get("symbol"), "entry": round(price, 2),
                        "stop": round(price * (1 - stop_pct), 2),
                        "tp": round(price * (1 + 2 * stop_pct), 2), "shares": sh})
    return out


def _default_broker():
    from app.services.broker.factory import get_broker
    return get_broker(strategy_id=_MANUAL)


def _today_manual_symbols() -> set:
    """Symbols already auto-placed today (dedupe across re-runs before fills appear)."""
    from app.db.database import SessionLocal
    from app.db.models import TradeHistory
    db = SessionLocal()
    try:
        rows = db.query(TradeHistory).filter(TradeHistory.strategy_id == _MANUAL)\
                 .order_by(TradeHistory.created_at.desc()).limit(60).all()
        today = datetime.now(timezone.utc).date()
        return {(r.symbol or "").upper() for r in rows if r.created_at and r.created_at.date() == today}
    except Exception:
        return set()
    finally:
        db.close()


def _submit_bracket(p: dict) -> dict:
    from app.services.order_manager import get_order_manager
    mgr = get_order_manager(strategy_id=_MANUAL)
    return mgr.submit_bracket(
        symbol=p["symbol"], side="buy", qty=int(p["shares"]),
        limit_price=float(p["entry"]), stop_loss_price=float(p["stop"]),
        take_profit_price=float(p["tp"]), time_in_force="gtc",
    )


def _record(p: dict, order_id: str, scanner: str) -> None:
    from app.db.database import SessionLocal
    from app.db.models import TradeHistory
    db = SessionLocal()
    try:
        db.add(TradeHistory(
            strategy_id=_MANUAL, symbol=p["symbol"], side="buy", qty=float(p["shares"]),
            entry_price=float(p["entry"]), stop_loss=float(p["stop"]), take_profit=float(p["tp"]),
            position_value=round(float(p["shares"]) * float(p["entry"]), 2), status="submitted",
            signal_details={"source": "auto_paper", "scanner": scanner, "broker": "ibkr",
                            "broker_order_id": order_id, "order_class": "bracket"},
            created_at=datetime.now(timezone.utc),
        ))
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error("auto_paper record failed for %s: %s", p.get("symbol"), e)
    finally:
        db.close()


def run_auto_paper(scanner: str = "weekly", *, _broker=None, _picks_fn=None, _submit_fn=None) -> dict:
    """Place the scanner's halal picks as IBKR PAPER bracket orders, capped & deduped.
    Returns {status, scanner, placed:[...], skipped:int, cap:int}. Never raises."""
    if not _on("AUTO_PAPER_TRADE"):
        return {"status": "disabled"}

    # PAPER-ONLY hard guard — the one line that makes real-money execution impossible.
    from app.services.broker.ibkr_config import get_ibkr_config
    mode = (get_ibkr_config().get("mode") or "")
    if "paper" not in mode.lower():
        logger.error("auto_paper ABORTED — broker is NOT paper (mode=%s)", mode)
        return {"status": "aborted", "reason": "not_paper", "mode": mode}
    if _on("KILL_SWITCH"):
        return {"status": "halted", "reason": "kill_switch"}

    cap = int(os.environ.get("AUTO_PAPER_MAX_NEW", "3"))
    scn = (scanner or "weekly").lower()

    broker = _broker if _broker is not None else _default_broker()
    if broker is None:
        return {"status": "aborted", "reason": "broker_offline"}
    try:
        held = {(pos.get("symbol") or "").upper() for pos in (broker.get_positions(_MANUAL) or [])}
    except Exception:
        held = set()
    held |= _today_manual_symbols()

    # Size to the REAL paper-account equity so trades scale to the actual balance (~$1M),
    # not a fixed default. Falls back to AUTO_PAPER_ACCOUNT (100k) if the read fails.
    try:
        equity = float((broker.get_account(_MANUAL) or {}).get("equity") or 0)
    except Exception:
        equity = 0.0
    if equity <= 0:
        equity = float(os.environ.get("AUTO_PAPER_ACCOUNT", "100000"))

    if _picks_fn is not None:
        picks = _picks_fn(scn)
    elif scn.startswith("month"):
        picks = _monthly_bracket_picks(account=equity)
    else:
        picks = _weekly_bracket_picks(account=equity)

    placed: list[str] = []
    skipped = 0
    for p in (picks or []):
        if len(placed) >= cap:
            break
        sym = (p.get("symbol") or "").upper()
        if not sym or sym in held or not p.get("shares") or not p.get("entry"):
            skipped += 1
            continue
        try:
            res = (_submit_fn or _submit_bracket)(p)
        except Exception as e:
            logger.error("auto_paper submit failed for %s: %s", sym, e)
            skipped += 1
            continue
        if res and res.get("success") and res.get("order_id"):
            _record(p, res["order_id"], scn)
            placed.append(sym)
            held.add(sym)
        else:
            skipped += 1
    logger.info("auto_paper %s: placed=%s skipped=%d cap=%d", scn, placed, skipped, cap)
    return {"status": "ok", "scanner": scn, "placed": placed, "skipped": skipped, "cap": cap}


# ── Smart-exit monitor for the live IBKR-paper positions ─────────────────────
# The broker bracket already arms the catastrophe stop + a fixed take-profit, so
# the monitor only ADDS what the bracket can't do: a trailing stop, a technical-
# weakening exit, and a time backstop. We never act on a "stop" trigger — that's
# the broker bracket's job — only on these.
_SMART_EXIT_ACT = {"trailing", "weakening", "time"}


def _open_manual_entries() -> dict:
    """{symbol: (entry_price, created_at)} for OPEN auto-paper (MANUAL) rows —
    the most recent still-open buy per symbol (pnl_pct IS NULL)."""
    from app.db.database import SessionLocal
    from app.db.models import TradeHistory
    db = SessionLocal()
    out: dict = {}
    try:
        rows = (db.query(TradeHistory)
                  .filter(TradeHistory.strategy_id == _MANUAL,
                          TradeHistory.side == "buy",
                          TradeHistory.pnl_pct.is_(None))
                  .order_by(TradeHistory.created_at.desc()).all())
        for r in rows:
            sym = (r.symbol or "").upper()
            if sym and sym not in out and r.entry_price and float(r.entry_price) > 0:
                out[sym] = (float(r.entry_price), r.created_at)
    except Exception as e:
        logger.debug("auto_paper _open_manual_entries failed: %s", e)
    finally:
        db.close()
    return out


def _flatten_manual(broker, symbol: str) -> bool:
    """Cancel the symbol's open bracket legs, THEN market-close the position.
    Cancel first so no dangling OCA stop/TP can later fill and open a short."""
    try:
        for o in (broker.get_orders(status="open", strategy_id=_MANUAL) or []):
            if (o.get("symbol") or "").upper() == symbol and o.get("id"):
                try:
                    broker.cancel_order(o["id"], strategy_id=_MANUAL)
                except Exception:
                    logger.debug("auto_paper cancel leg failed for %s", symbol, exc_info=True)
        return broker.close_position(symbol, strategy_id=_MANUAL) is not None
    except Exception as e:
        logger.error("auto_paper flatten failed for %s: %s", symbol, e)
        return False


def _mark_manual_closed(symbol: str, exit_price: float, ret_pct: float, reason: str) -> None:
    from app.db.database import SessionLocal
    from app.db.models import TradeHistory
    db = SessionLocal()
    try:
        row = (db.query(TradeHistory)
                 .filter(TradeHistory.strategy_id == _MANUAL, TradeHistory.symbol == symbol,
                         TradeHistory.pnl_pct.is_(None))
                 .order_by(TradeHistory.created_at.desc()).first())
        if row:
            entry = float(row.entry_price or 0)
            row.exit_price = round(float(exit_price), 4)
            row.pnl = round((float(exit_price) - entry) * float(row.qty or 0), 2)
            row.pnl_pct = ret_pct
            row.closed_at = datetime.now(timezone.utc)
            row.status = "closed"
            sd = dict(row.signal_details or {})
            sd["exit_reason"] = reason
            row.signal_details = sd
            db.commit()
    except Exception as e:
        db.rollback()
        logger.error("auto_paper mark closed failed for %s: %s", symbol, e)
    finally:
        db.close()


def run_smart_exit_monitor(*, _broker=None, _entries_fn=None, _bars_fn=None) -> dict:
    """Smart-exit the live IBKR-paper positions (the manager the user asked for).

    For each held auto-paper position, replay its bars through simulate_smart_exit;
    on a trailing / technical-weakening / time trigger, cancel its bracket and
    flatten — locking a winner that's rolling over instead of letting it ride back.
    The broker bracket still owns the hard catastrophe stop + fixed TP. PAPER-ONLY,
    opt-in (AUTO_PAPER_TRADE), kill-switch-respecting. Never raises."""
    if not _on("AUTO_PAPER_TRADE"):
        return {"status": "disabled"}
    from app.services.smart_exit import (
        SMART_EXIT, compute_exit_indicators, simulate_smart_exit, post_entry_bars)
    if not SMART_EXIT:
        return {"status": "smart_exit_off"}

    # PAPER-ONLY hard guard — same line that makes real-money execution impossible.
    from app.services.broker.ibkr_config import get_ibkr_config
    mode = (get_ibkr_config().get("mode") or "")
    if "paper" not in mode.lower():
        logger.error("smart_exit_monitor ABORTED — broker is NOT paper (mode=%s)", mode)
        return {"status": "aborted", "reason": "not_paper", "mode": mode}

    broker = _broker if _broker is not None else _default_broker()
    if broker is None:
        return {"status": "aborted", "reason": "broker_offline"}
    try:
        held = {(pos.get("symbol") or "").upper()
                for pos in (broker.get_positions(_MANUAL) or []) if pos.get("symbol")}
    except Exception:
        held = set()
    if not held:
        return {"status": "ok", "checked": 0, "closed": 0, "exits": []}

    entries = (_entries_fn or _open_manual_entries)()
    bars_fn = _bars_fn
    if bars_fn is None:
        from app.services.market_data import fetch as _fetch
        bars_fn = lambda s: _fetch(s, period="6mo")  # noqa: E731
    hold_days = int(os.environ.get("EXIT_HOLD_DAYS", "20"))
    stop_pct = float(os.environ.get("EXIT_STOP_PCT", "15"))

    exits: list[dict] = []
    for sym in sorted(held):
        meta = entries.get(sym)
        if not meta:
            continue
        entry, created_at = meta
        try:
            df = bars_fn(sym)
            if df is None or len(df) == 0:
                continue
            dfi = compute_exit_indicators(df)
            post = post_entry_bars(dfi, created_at, hold_days + 5)
            sim = simulate_smart_exit(post, entry, stop_pct=stop_pct, hold_days=hold_days)
            if sim is None:
                continue
            ret_pct, exit_price, reason = sim
            if reason not in _SMART_EXIT_ACT:
                continue  # broker bracket owns the catastrophe stop
            if _flatten_manual(broker, sym):
                _mark_manual_closed(sym, exit_price, ret_pct, reason)
                exits.append({"symbol": sym, "reason": reason, "ret_pct": ret_pct})
        except Exception as e:
            logger.debug("smart_exit_monitor %s failed: %s", sym, e)
    logger.info("smart_exit_monitor: checked=%d closed=%d exits=%s", len(held), len(exits), exits)
    return {"status": "ok", "checked": len(held), "closed": len(exits), "exits": exits}
