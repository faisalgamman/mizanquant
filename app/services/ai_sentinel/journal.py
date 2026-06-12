"""Agent journal — retrieval memory (honest, NOT learning).

Records agent decisions, retrieves recent decisions with outcomes, and
matches past recommendations against actual trade P&L or price movement.
This is retrieval memory, NOT learning — the agent does NOT place trades.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger("agent_journal")

_KIND_LABELS = {
    "opportunity": "فرصة",
    "risk": "خطر",
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def record_decision(
    symbol: str,
    kind: str,
    verdict: str,
    confidence: float,
    rationale: str,
    context: Optional[dict] = None,
) -> int:
    """Record an agent decision and return its ID.

    Args:
        symbol: Stock ticker
        kind: "opportunity" or "risk"
        verdict: BUY, STRONG BUY, SELL, etc.
        confidence: 0-1 or 0-100
        rationale: Why the agent made this recommendation
        context: Optional extra snapshot data
    """
    from app.db.database import SessionLocal
    from app.db.models import AgentDecision

    # Normalise confidence: if > 1, treat as 0-100 scale
    if confidence > 1:
        confidence = confidence / 100.0

    snapshot = context or {}
    snapshot["kind"] = kind

    db = SessionLocal()
    try:
        dec = AgentDecision(
            symbol=symbol.upper(),
            verdict=verdict.upper(),
            confidence=confidence,
            rationale=rationale,
            snapshot=snapshot,
        )
        db.add(dec)
        db.commit()
        dec_id = dec.id
        return dec_id
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def recent_decisions(limit: int = 8) -> list[dict]:
    """Return most recent decisions with any outcome info.

    Returns list of dicts with keys: id, symbol, verdict, confidence,
    kind, created_at, outcome_label, outcome_pct, rationale.
    """
    from app.db.database import SessionLocal
    from app.db.models import AgentDecision

    db = SessionLocal()
    try:
        rows = (
            db.query(AgentDecision)
            .order_by(AgentDecision.created_at.desc())
            .limit(limit)
            .all()
        )
        results = []
        for row in rows:
            snap = row.snapshot or {}
            results.append({
                "id": row.id,
                "symbol": row.symbol,
                "verdict": row.verdict,
                "confidence": row.confidence,
                "kind": snap.get("kind", ""),
                "created_at": row.created_at.isoformat() if row.created_at else "",
                "outcome_label": snap.get("outcome_label", ""),
                "outcome_pct": snap.get("outcome_pct"),
                "rationale": (row.rationale or "")[:120],
            })
        return results
    finally:
        db.close()


def match_outcomes(lookback_days: int = 30) -> dict:
    """Match past decisions against trade P&L or price movement.

    For each BUY-ish decision without an outcome in the lookback window:
    1. Check TradeHistory for a closed trade on the same symbol near the
       decision date → label win/loss from pnl_pct.
    2. Fall back: if no matching trade, try market_data.fetch to compute
       the price move since the decision.

    Writes outcome_label + outcome_pct into the decision's snapshot JSON.

    Returns {"matched": N, "still_open": N}.
    """
    from app.db.database import SessionLocal
    from app.db.models import AgentDecision, TradeHistory

    cutoff = _utc_now() - timedelta(days=lookback_days)

    db = SessionLocal()
    try:
        # Decisions without outcomes in the lookback window
        pending = (
            db.query(AgentDecision)
            .filter(AgentDecision.created_at >= cutoff)
            .order_by(AgentDecision.created_at.desc())
            .all()
        )

        # Filter to those without an outcome label in snapshot
        unmatched = [
            d for d in pending
            if not (d.snapshot or {}).get("outcome_label")
        ]

        if not unmatched:
            return {"matched": 0, "still_open": 0}

        matched = 0
        still_open = 0

        for dec in unmatched:
            # Strategy 1: look for a closed TradeHistory on same symbol
            # within 30 days after the decision
            trade = (
                db.query(TradeHistory)
                .filter(
                    TradeHistory.symbol == dec.symbol,
                    TradeHistory.status == "closed",
                    TradeHistory.pnl_pct.isnot(None),
                    TradeHistory.created_at >= dec.created_at,
                    TradeHistory.created_at <= dec.created_at + timedelta(days=30),
                )
                .order_by(TradeHistory.created_at.asc())
                .first()
            )

            if trade is not None:
                pnl = float(trade.pnl_pct)
                label = "win" if pnl > 0 else ("loss" if pnl < 0 else "flat")
                snap = dict(dec.snapshot or {})
                snap["outcome_label"] = label
                snap["outcome_pct"] = pnl
                dec.snapshot = snap
                matched += 1
                continue

            # Strategy 2: fall back to market_data price move
            try:
                from app.services.market_data import fetch as _md_fetch
                df = _md_fetch(dec.symbol, period="1mo")
                if df is not None and len(df) >= 2:
                    entry_price = float(df["close"].iloc[0])
                    exit_price = float(df["close"].iloc[-1])
                    if entry_price > 0:
                        pnl = round((exit_price / entry_price - 1.0) * 100, 2)
                        label = "win" if pnl > 0 else ("loss" if pnl < 0 else "flat")
                        snap = dict(dec.snapshot or {})
                        snap["outcome_label"] = label
                        snap["outcome_pct"] = pnl
                        dec.snapshot = snap
                        matched += 1
                        continue
            except Exception:
                pass

            still_open += 1

        db.commit()
        return {"matched": matched, "still_open": still_open}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
