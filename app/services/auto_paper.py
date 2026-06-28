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
