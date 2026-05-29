"""Agent reflection loop - post-trade learning + rule extraction.

Hooks:
  store_rationale(trade_id, rationale, snapshot)  - called at trade OPEN
  trigger_reflection(trade_id)                     - called at trade CLOSE
  get_active_rules() -> list[dict]                 - returns active rules for SYSTEM_PROMPT
  scan_and_reflect() -> dict                      - cron-friendly batch processor

Cooldown: 24h minimum before reflecting on a closed trade.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("screener")

_COOLDOWN_HOURS = 24


def store_rationale(trade_id: int, rationale: str, snapshot: dict | None = None) -> bool:
    """Persist agent rationale + snapshot to TradeHistory."""
    try:
        from app.db.database import SessionLocal
        from app.db.models import TradeHistory
        session = SessionLocal()
        try:
            trade = session.query(TradeHistory).filter(TradeHistory.id == trade_id).first()
            if trade:
                trade.rationale = rationale
                if snapshot:
                    trade.snapshot = snapshot
                session.commit()
                return True
        finally:
            session.close()
    except Exception:
        logger.exception("store_rationale failed for trade %d", trade_id)
    return False


def _soft_match_decision(trade_id: int) -> int | None:
    """Link AgentDecision to TradeHistory via symbol + 2h time window."""
    try:
        from app.db.database import SessionLocal
        from app.db.models import TradeHistory, AgentDecision
        session = SessionLocal()
        try:
            trade = session.query(TradeHistory).filter(TradeHistory.id == trade_id).first()
            if not trade or not trade.created_at:
                return None
            window_start = trade.created_at - timedelta(hours=2)
            dec = (
                session.query(AgentDecision)
                .filter(
                    AgentDecision.symbol == trade.symbol,
                    AgentDecision.created_at >= window_start,
                    AgentDecision.created_at <= trade.created_at,
                )
                .order_by(AgentDecision.created_at.desc())
                .first()
            )
            if dec:
                trade.agent_decision_id = dec.id
                session.commit()
                return dec.id
        finally:
            session.close()
    except Exception:
        logger.debug("soft_match_decision failed for trade %d", trade_id)
    return None


def trigger_reflection(trade_id: int) -> dict | None:
    """Post-close agent reflection with 24h cooldown + rule extraction."""
    try:
        from app.db.database import SessionLocal
        from app.db.models import TradeHistory
        session = SessionLocal()
        try:
            trade = session.query(TradeHistory).filter(TradeHistory.id == trade_id).first()
            if not trade:
                return {"status": "not_found", "trade_id": trade_id}
            if trade.closed_at:
                age = datetime.now(timezone.utc) - trade.closed_at.replace(tzinfo=timezone.utc)
                if age < timedelta(hours=_COOLDOWN_HOURS):
                    return {"status": "cooldown", "trade_id": trade_id,
                            "hours_remaining": round(_COOLDOWN_HOURS - age.total_seconds() / 3600, 1)}
            if trade.reflection:
                return {"status": "already_reflected", "trade_id": trade_id}
            if not trade.agent_decision_id:
                _soft_match_decision(trade_id)
            pnl_str = f"${trade.pnl:.2f} ({trade.pnl_pct:.2f}%)" if trade.pnl else "unknown"
            prompt = (
                f"Reflect on closed trade: {trade.symbol} | "
                f"Entry ${trade.entry_price} Exit ${trade.exit_price} | "
                f"PnL {pnl_str} | Rationale: {trade.rationale or 'not recorded'} | "
                f"Snapshot: {trade.snapshot or {}}. "
                f"Extract 1-2 trading rules "
                f"(format: RULE: <text> | CATEGORY: entry|exit|risk|sizing)."
            )
            reflection_text = _call_agent_reflection(trade.symbol, prompt)
            if not reflection_text:
                return {"status": "agent_unavailable", "trade_id": trade_id}
            trade.reflection = reflection_text
            session.commit()
            rules_extracted = _extract_rules(reflection_text, trade_id, trade.agent_decision_id)
            return {"status": "reflected", "trade_id": trade_id, "rules_extracted": rules_extracted}
        finally:
            session.close()
    except Exception:
        logger.exception("trigger_reflection failed for trade %d", trade_id)
        return None


def _call_agent_reflection(symbol: str, prompt: str) -> str | None:
    """Call the AI agent for a reflection response."""
    try:
        from app.ai_agent import AIAgent
        agent = AIAgent()
        return agent.ask(prompt)
    except Exception:
        logger.exception("agent reflection call failed for %s", symbol)
        return None


def _extract_rules(reflection_text: str, trade_id: int, decision_id: int | None) -> int:
    """Parse RULE: lines from reflection and store in TradingRulebook."""
    count = 0
    try:
        from app.db.database import SessionLocal
        from app.db.models import TradingRulebook
        session = SessionLocal()
        try:
            for line in reflection_text.split("\n"):
                line = line.strip()
                if line.upper().startswith("RULE:"):
                    rule_text = line[5:].strip()
                    if not rule_text:
                        continue
                    category = "general"
                    if "CATEGORY:" in line.upper():
                        parts = line.upper().split("CATEGORY:")
                        if len(parts) > 1:
                            cat = parts[1].split("|")[0].strip().lower()
                            if cat in ("entry", "exit", "risk", "sizing"):
                                category = cat
                            rule_text = line[5:].split("|")[0].replace("CATEGORY:", "").strip()
                    rule = TradingRulebook(
                        rule_text=rule_text, category=category,
                        source_trade_id=trade_id, source_decision_id=decision_id, active=True,
                    )
                    session.add(rule)
                    count += 1
            if count > 0:
                session.commit()
        finally:
            session.close()
    except Exception:
        logger.exception("rule extraction failed")
    return count


def get_active_rules(limit: int = 20) -> list[dict]:
    """Return active trading rules for SYSTEM_PROMPT injection."""
    try:
        from app.db.database import SessionLocal
        from app.db.models import TradingRulebook
        session = SessionLocal()
        try:
            rules = (
                session.query(TradingRulebook)
                .filter(TradingRulebook.active == True)
                .order_by(TradingRulebook.confidence.desc().nullslast(),
                          TradingRulebook.created_at.desc())
                .limit(limit)
                .all()
            )
            return [{"id": r.id, "rule_text": r.rule_text, "category": r.category,
                     "confidence": r.confidence, "activations": r.activations,
                     "win_count": r.win_count} for r in rules]
        finally:
            session.close()
    except Exception:
        logger.exception("get_active_rules failed")
        return []


def scan_and_reflect() -> dict:
    """Cron-friendly: scan closed trades >24h old, trigger reflection for each."""
    try:
        from app.db.database import SessionLocal
        from app.db.models import TradeHistory
        cutoff = datetime.now(timezone.utc) - timedelta(hours=_COOLDOWN_HOURS)
        session = SessionLocal()
        try:
            candidates = (
                session.query(TradeHistory)
                .filter(TradeHistory.status == "closed",
                        TradeHistory.closed_at <= cutoff,
                        TradeHistory.reflection == None)
                .all()
            )
        finally:
            session.close()
        results = []
        for trade in candidates:
            r = trigger_reflection(trade.id)
            if r:
                results.append(r)
        return {"scanned": len(candidates), "reflected": len(results), "details": results}
    except Exception:
        logger.exception("scan_and_reflect failed")
        return {"scanned": 0, "reflected": 0, "error": True}
