"""Persistent alert store + Risk-Desk gate for advisory alerts.

Two responsibilities:
  1. record_alert(): persist every emitted alert to the `alerts` DB table,
     enriched with the current regime / risk_posture and the guard result.
  2. market_risk_gate(): the "Risk Desk threshold" check for advisory alerts —
     runs only the MARKET-LEVEL guards (VIX halt, SPY bear halt, regime freeze,
     market conditions, system block) which need no account/position state. This
     lets the intraday advisor suppress alerts during a market halt without
     building a full order-level GuardContext.

The automated pipeline path still runs the full 17-guard `evaluate()` before
execution; this module is the lighter gate for the manual-execution advisory feed.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger("screener")

# Market-level guards that depend only on regime/market state (no account needed).
_MARKET_GUARD_NAMES = {
    "block_system", "market_conditions", "spy_bear_halt", "vix_halt", "regime_bear_freeze",
}


def market_risk_gate(symbol: str, price: float = 0.0, stop_loss: float = 0.0) -> tuple[bool, str]:
    """Run the market-level Risk Desk guards. Returns (passed, reason).

    Fail-open: if the gate itself errors (e.g. regime unavailable), returns
    (True, "") so advisories are not silently lost on infrastructure hiccups.
    A blocking market guard (VIX/SPY/regime/system halt) returns (False, reason).
    """
    try:
        from app.services.guards import _GUARDS
        from app.services.guards.base import GuardContext
        from app.services.regime import get_regime

        ctx = GuardContext(
            strategy_id=None, symbol=symbol.upper(), side="buy",
            price=float(price or 0.0), stop_loss=float(stop_loss or 0.0), qty=1,
            account={}, positions=[], regime=get_regime(),
        )
        failed: list[str] = []
        for g in _GUARDS:
            try:
                r = g(ctx)
            except Exception:
                continue  # account-dependent guard on empty ctx — skip
            if r.name in _MARKET_GUARD_NAMES and r.blocking and not r.passed:
                failed.append(f"{r.name}: {r.reason}")
        if failed:
            return False, "; ".join(failed)
        return True, ""
    except Exception as exc:
        logger.debug("market_risk_gate error for %s: %s", symbol, exc)
        return True, ""


def record_alert(
    symbol: str,
    alert_type: str,
    *,
    signal: Optional[str] = None,
    score: Optional[float] = None,
    price: Optional[float] = None,
    stop_loss: Optional[float] = None,
    take_profit: Optional[float] = None,
    confidence: Optional[float] = None,
    guard_passed: bool = True,
    guard_reason: Optional[str] = None,
    sent: bool = False,
    correlation_id: Optional[str] = None,
    payload: Optional[dict] = None,
) -> Optional[int]:
    """Persist one alert to the `alerts` table. Returns row id or None on failure.

    Enriches the row with the current regime + risk_posture from the context
    bundle so every alert is auditable against the market state at emit time.
    """
    regime = posture = None
    try:
        from app.services.market_context_bundle import get_context_bundle_sync
        ctx = get_context_bundle_sync()
        regime = ctx.get("regime")
        posture = ctx.get("risk_posture")
    except Exception:
        pass

    try:
        from app.db.database import SessionLocal
        from app.db.models import Alert
        db = SessionLocal()
        try:
            row = Alert(
                symbol=(symbol or "").upper(), alert_type=alert_type, signal=signal,
                score=score, price=price, stop_loss=stop_loss, take_profit=take_profit,
                confidence=confidence, regime=regime, risk_posture=posture,
                guard_passed=guard_passed, guard_reason=guard_reason, sent=sent,
                correlation_id=correlation_id, payload=payload,
            )
            db.add(row)
            db.commit()
            return row.id
        finally:
            db.close()
    except Exception as exc:
        logger.debug("record_alert failed for %s: %s", symbol, exc)
        return None


def recent_alerts(limit: int = 50, symbol: str = "", alert_type: str = "",
                  sent_only: bool = False) -> list[dict]:
    """Read recent alerts from the DB for the /api/alerts endpoint."""
    try:
        from app.db.database import SessionLocal
        from app.db.models import Alert
        db = SessionLocal()
        try:
            q = db.query(Alert).order_by(Alert.ts.desc())
            if symbol:
                q = q.filter(Alert.symbol == symbol.upper())
            if alert_type:
                q = q.filter(Alert.alert_type == alert_type)
            if sent_only:
                q = q.filter(Alert.sent.is_(True))
            rows = q.limit(min(max(limit, 1), 500)).all()
            return [{
                "id":            r.id,
                "ts":            r.ts.isoformat() if r.ts else None,
                "symbol":        r.symbol,
                "alert_type":    r.alert_type,
                "signal":        r.signal,
                "score":         r.score,
                "price":         r.price,
                "stop_loss":     r.stop_loss,
                "take_profit":   r.take_profit,
                "confidence":    r.confidence,
                "regime":        r.regime,
                "risk_posture":  r.risk_posture,
                "guard_passed":  r.guard_passed,
                "guard_reason":  r.guard_reason,
                "sent":          r.sent,
                "correlation_id": r.correlation_id,
            } for r in rows]
        finally:
            db.close()
    except Exception as exc:
        logger.debug("recent_alerts failed: %s", exc)
        return []
