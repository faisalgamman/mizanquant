"""Decision journal + outcome matching (honest retrieval memory).

⚠️  DESIGN NOTE — this is retrieval memory for self-review, NOT learning;
it does not improve predictive accuracy. The journal records decisions and
re-injects them as context for future sentinel cycles so the AI can reference
its own past reasoning and outcomes. LLMs do not learn from use; the journal
provides honest attribution, not model improvement.
"""

import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("screener")


def record_decision(
    symbol: str,
    kind: str,  # "opportunity" | "risk_question"
    verdict: str,
    confidence: float,
    rationale: str,
    context: dict | None = None,
    _session_factory=None,
) -> int | None:
    """Persist a sentinel decision to the AgentDecision table.

    Args:
        symbol: Ticker or topic identifier.
        kind:  "opportunity" or "risk_question".
        verdict: Short headline / verdict text.
        confidence: 0.0–1.0 (or 0 for questions).
        rationale: LLM reasoning string.
        context: Arbitrary extra data stored in the ``snapshot`` JSON column.
        _session_factory: Optional injection point for tests.

    Returns:
        The new row ``id``, or None on failure.
    """
    factory = _session_factory
    if factory is None:
        from app.db.database import SessionLocal
        factory = SessionLocal
    session = factory()
    try:
        from app.db.models import AgentDecision

        snap = (context or {}).copy()
        snap["kind"] = kind

        dec = AgentDecision(
            symbol=symbol.upper() if kind == "opportunity" else symbol,
            verdict=verdict,
            confidence=confidence,
            rationale=rationale,
            snapshot=snap,
        )
        session.add(dec)
        session.commit()
        return dec.id
    except Exception:
        logger.exception("record_decision failed for %s (%s)", symbol, kind)
        session.rollback()
        return None
    finally:
        session.close()


def recent_decisions(
    limit: int = 10,
    _session_factory=None,
) -> list[dict]:
    """Return the most-recent sentinel decisions with any matched outcome.

    Feeds the sentinel prompt so it can reference prior calls.
    """
    factory = _session_factory
    if factory is None:
        from app.db.database import SessionLocal
        factory = SessionLocal
    session = factory()
    try:
        from app.db.models import AgentDecision
        rows = (
            session.query(AgentDecision)
            .order_by(AgentDecision.created_at.desc())
            .limit(limit)
            .all()
        )
        results: list[dict] = []
        for r in reversed(rows):  # oldest→newest for the prompt
            d = {
                "id": r.id,
                "symbol": r.symbol,
                "verdict": r.verdict,
                "confidence": r.confidence,
                "rationale": r.rationale,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "kind": (r.snapshot or {}).get("kind", "opportunity"),
            }
            if r.snapshot:
                outcome = r.snapshot.get("outcome_pct")
                outcome_label = r.snapshot.get("outcome_label")
                if outcome is not None or outcome_label:
                    d["outcome"] = {
                        "pct": outcome,
                        "label": outcome_label,
                    }
            results.append(d)
        return results
    except Exception:
        logger.exception("recent_decisions failed")
        return []
    finally:
        session.close()


def match_outcomes(
    lookback_days: int = 30,
    _session_factory=None,
) -> dict:
    """Link open opportunity decisions to realised paper P&L or price moves.

    For each "opportunity"-kind ``AgentDecision`` lacking an outcome:
      1. Prefer a CLOSED ``TradeHistory`` row for the same symbol whose
         ``created_at`` is after the decision date — use ``pnl_pct``.
      2. Else compute price move via ``market_data.fetch(symbol)`` from
         decision date → now.

    The outcome (``outcome_pct`` + ``outcome_label``) is written into the
    ``snapshot`` JSON column.

    Returns: ``{"matched": int, "still_open": int}``.
    """
    factory = _session_factory
    if factory is None:
        from app.db.database import SessionLocal
        factory = SessionLocal
    session = factory()
    matched = 0
    still_open = 0
    try:
        from app.db.models import AgentDecision, TradeHistory
        from app.services.market_data import fetch as md_fetch

        cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        rows = (
            session.query(AgentDecision)
            .filter(AgentDecision.created_at >= cutoff)
            .order_by(AgentDecision.created_at)
            .all()
        )
        opportunity_rows = [
            r for r in rows
            if (r.snapshot or {}).get("kind") == "opportunity"
            and not (r.snapshot or {}).get("outcome_label")
        ]
        for r in opportunity_rows:
            outcome_pct = None
            outcome_label = "open"

            # Prefer a closed trade for the same symbol after decision date.
            trade = (
                session.query(TradeHistory)
                .filter(
                    TradeHistory.symbol == r.symbol,
                    TradeHistory.status == "closed",
                    TradeHistory.created_at >= r.created_at,
                    TradeHistory.pnl_pct is not None,
                )
                .order_by(TradeHistory.created_at)
                .first()
            )
            if trade is not None:
                outcome_pct = float(trade.pnl_pct)
                outcome_label = (
                    "win" if outcome_pct > 0 else "loss" if outcome_pct < 0 else "flat"
                )
            else:
                # Fall back to price move since decision date.
                try:
                    df = md_fetch(r.symbol, period="2y",
                                  start=r.created_at, end=datetime.now(timezone.utc))
                    if df is not None and len(df) >= 2:
                        entry = float(df["close"].iloc[0] if "close" in df.columns
                                      else df.iloc[0]["close"])
                        exit_ = float(df["close"].iloc[-1] if "close" in df.columns
                                      else df.iloc[-1]["close"])
                        if entry and entry > 0:
                            outcome_pct = round((exit_ - entry) / entry * 100, 2)
                            outcome_label = (
                                "win" if outcome_pct > 0
                                else "loss" if outcome_pct < 0
                                else "flat"
                            )
                except Exception:
                    logger.debug("price-move fallback failed for %s", r.symbol)

            if outcome_label != "open":
                matched += 1
            else:
                still_open += 1

            # Persist outcome into the snapshot JSON column.
            snap = dict(r.snapshot or {})
            snap["outcome_pct"] = outcome_pct
            snap["outcome_label"] = outcome_label
            r.snapshot = snap

        session.commit()
        logger.info("match_outcomes: matched=%d still_open=%d", matched, still_open)
        return {"matched": matched, "still_open": still_open}
    except Exception:
        logger.exception("match_outcomes failed")
        session.rollback()
        return {"matched": 0, "still_open": 0}
    finally:
        session.close()
