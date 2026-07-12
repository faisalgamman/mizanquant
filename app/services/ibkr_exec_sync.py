"""Daily IBKR-PAPER execution sync — READ-ONLY.

The IB API only exposes the CURRENT session's fills, so this job runs each trading day, reads
the paper gateway's executions, and stores them (deduped by execId) into the ibkr_executions
table. A real execution history then accumulates: actual fill prices, slippage, commissions, and
realized PnL on closing trades. It NEVER places, modifies, or cancels an order — it only reads.

Note: the paper account may also contain positions NOT placed by this platform (e.g. IBKR's
seeded demo book, or manual TWS trades). This sync records whatever the gateway reports; the
summary flags that provenance is unverified.
"""

import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("ibkr_exec_sync")

_IB_SENTINEL = 1.7e308  # IB sends ~1.8e308 for "not applicable" (realizedPNL / commission)


def _clean(v):
    """Float or None — drops NaN and IB's not-applicable sentinel."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or abs(f) >= _IB_SENTINEL:
        return None
    return f


def _parse_exec_time(raw):
    """ib_insync returns a tz-aware datetime; fall back to the 'YYYYMMDD  HH:MM:SS' string form."""
    if isinstance(raw, datetime):
        return raw.astimezone(timezone.utc) if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    try:
        s = " ".join(str(raw).split())  # collapse the double space IB uses
        return datetime.strptime(s, "%Y%m%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except Exception:
        return None


def sync_ibkr_executions(_fills=None) -> dict:
    """Read the paper gateway's fills and upsert them (deduped by execId). Fail-safe — never raises."""
    from app.db.database import SessionLocal
    from app.db.models import IbkrExecution

    fills = _fills
    if fills is None:
        try:
            from app.services.broker.ibkr_adapter import _connect, _call_ib
            ib = _connect("MANUAL")
            if ib is None:
                return {"status": "gateway_offline", "inserted": 0}
            # reqExecutions() re-requests the session's executions (robust on a fresh connect);
            # fall back to the cached fills() list. Both yield ib_insync Fill objects.
            fills = _call_ib(ib, "reqExecutions", timeout=25) or _call_ib(ib, "fills") or []
        except Exception as e:
            logger.error("ibkr exec sync: gateway read failed: %s", e)
            return {"status": "error", "message": str(e)[:200], "inserted": 0}

    db = SessionLocal()
    inserted = 0
    seen = 0
    try:
        for f in fills:
            try:
                ex = getattr(f, "execution", None)
                exec_id = getattr(ex, "execId", None) if ex else None
                if not exec_id:
                    continue
                seen += 1
                if db.query(IbkrExecution.id).filter(IbkrExecution.exec_id == str(exec_id)).first():
                    continue
                cr = getattr(f, "commissionReport", None)
                db.add(IbkrExecution(
                    exec_id=str(exec_id),
                    account=str(getattr(ex, "acctNumber", "") or ""),
                    symbol=str(getattr(getattr(f, "contract", None), "symbol", "") or ""),
                    side=str(getattr(ex, "side", "") or ""),
                    qty=_clean(getattr(ex, "shares", None)),
                    price=_clean(getattr(ex, "price", None)),
                    avg_price=_clean(getattr(ex, "avgPrice", None)),
                    commission=_clean(getattr(cr, "commission", None)) if cr else None,
                    realized_pnl=_clean(getattr(cr, "realizedPNL", None)) if cr else None,
                    exec_time=_parse_exec_time(getattr(ex, "time", None)),
                    perm_id=str(getattr(ex, "permId", "") or ""),
                    synced_at=datetime.now(timezone.utc),
                ))
                inserted += 1
            except Exception as e:
                logger.debug("ibkr exec sync: skipped a fill: %s", e)
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error("ibkr exec sync commit failed: %s", e)
        return {"status": "error", "message": str(e)[:200], "inserted": inserted}
    finally:
        db.close()
    logger.info("ibkr exec sync: seen=%d inserted=%d", seen, inserted)
    return {"status": "ok", "seen": seen, "inserted": inserted}


def executions_summary(days: int = 365, limit: int = 800) -> dict:
    """Aggregate the stored IBKR-paper executions into a compact, honest summary."""
    from app.db.database import SessionLocal
    from app.db.models import IbkrExecution

    cutoff = datetime.now(timezone.utc) - timedelta(days=max(int(days or 365), 1))
    db = SessionLocal()
    try:
        rows = (db.query(IbkrExecution)
                  .filter(IbkrExecution.exec_time >= cutoff)
                  .order_by(IbkrExecution.exec_time.desc()).limit(int(limit)).all())
    finally:
        db.close()

    if not rows:
        return {"status": "empty", "count": 0,
                "message": ("No IBKR-paper executions stored yet. The daily sync runs after each "
                            "market close (~16:45 ET); the real fill history accumulates from the "
                            "next trading day onward.")}

    buys = [e for e in rows if (e.side or "").upper().startswith("B")]
    sells = [e for e in rows if (e.side or "").upper().startswith("S")]
    realized = [e.realized_pnl for e in rows if e.realized_pnl is not None]
    wins = [r for r in realized if r > 0]
    losses = [r for r in realized if r <= 0]
    total_comm = round(sum(e.commission for e in rows if e.commission is not None), 2)

    per_sym: dict = {}
    for e in rows:
        d = per_sym.setdefault(e.symbol, {"fills": 0, "realized": 0.0})
        d["fills"] += 1
        if e.realized_pnl is not None:
            d["realized"] += e.realized_pnl
    ranked = sorted(per_sym.items(), key=lambda kv: kv[1]["realized"], reverse=True)

    return {
        "status": "ok",
        "window_days": days,
        "count": len(rows),
        "first_fill": (rows[-1].exec_time.isoformat() if rows[-1].exec_time else None),
        "last_fill": (rows[0].exec_time.isoformat() if rows[0].exec_time else None),
        "buys": len(buys),
        "sells": len(sells),
        "closed_trades_with_pnl": len(realized),
        "total_realized_pnl": round(sum(realized), 2) if realized else None,
        "realized_win_rate_pct": round(100 * len(wins) / len(realized), 1) if realized else None,
        "avg_win": round(sum(wins) / len(wins), 2) if wins else None,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else None,
        "total_commission": total_comm,
        "top_symbols_by_realized": [
            {"symbol": s, "realized_pnl": round(d["realized"], 2), "fills": d["fills"]}
            for s, d in ranked[:8]],
        "worst_symbols_by_realized": [
            {"symbol": s, "realized_pnl": round(d["realized"], 2), "fills": d["fills"]}
            for s, d in reversed(ranked[-5:]) if d["realized"] < 0],
        "recent_fills": [
            {"symbol": e.symbol, "side": e.side, "qty": e.qty, "price": e.price,
             "realized_pnl": e.realized_pnl,
             "time": e.exec_time.isoformat() if e.exec_time else None}
            for e in rows[:15]],
        "note": ("REAL IBKR-PAPER executions synced read-only from the gateway. realized_pnl is set "
                 "by IB on CLOSING fills only (open-side buys have none), so realized stats cover "
                 "round-trips, not still-open positions. Paper account — NO real money. Provenance of "
                 "these positions is unverified: the paper account may include IBKR's seeded demo book "
                 "or manual TWS trades, not only platform orders."),
    }


__all__ = ["sync_ibkr_executions", "executions_summary"]
