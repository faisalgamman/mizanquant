"""AI Sentinel — periodic judgment cycle using the journal as context.

Reads the market state, recent journal entries, and makes a cautious
judgment call: opportunities (with honesty disclaimer) and risk questions.
The sentinel NEVER auto-trades — it only sends advisory messages.
"""

import json
import logging
from typing import Callable, Optional

logger = logging.getLogger("ai_sentinel")


# ── helpers ──────────────────────────────────────────────────────────

def _parse_json_lenient(text: Optional[str]) -> dict:
    """Parse JSON from LLM output, handling markdown fences and malformed input."""
    if not text or not isinstance(text, str):
        return {}
    text = text.strip()
    # Remove markdown code fences
    for fence in ("```json", "```"):
        if text.startswith(fence):
            text = text[len(fence):].strip()
        if text.endswith("```"):
            text = text[:-3].strip()
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return {}


def _conf_num(level: str) -> float:
    """Map confidence string to a numeric value."""
    mapping = {"low": 0.3, "medium": 0.6, "high": 0.85}
    return mapping.get((level or "").lower(), 0.5)


def _format_opportunity(item: dict) -> str:
    """Format an opportunity message with honesty disclaimer."""
    symbol = item.get("symbol", "???")
    headline = item.get("headline", "")
    reasoning = item.get("reasoning", "")
    confidence = item.get("confidence", "medium")
    uncertainty = item.get("uncertainty", "")

    lines = [
        f"📊 فرصة: {symbol}",
        f"العنوان: {headline}",
        f"التحليل: {reasoning}",
        f"الثقة: {confidence}",
    ]
    if uncertainty:
        lines.append(f"عدم اليقين: {uncertainty}")
    lines.extend([
        "",
        "⚠️ DISCLAIMER: Signals have coin-flip base accuracy (~51%). This is not advice.",
        "The paper ledger not graduated — no real capital should be allocated.",
    ])
    return "\n".join(lines)


def _format_risk_question(item: dict) -> str:
    """Format a risk question for the trader."""
    topic = item.get("topic", "")
    question = item.get("question", "")
    reasoning = item.get("reasoning", "")

    lines = [
        f"⚡ سؤال مخاطرة: {topic}",
        f"السؤال: {question}",
    ]
    if reasoning:
        lines.append(f"المنطق: {reasoning}")
    return "\n".join(lines)


def build_prompt(context: dict) -> tuple[str, str]:
    """Build system + user prompts for the sentinel LLM call.

    Args:
        context: {"market": {...}, "opportunities": [...], "recent_journal": [...]}

    Returns:
        (system_prompt, user_prompt) tuple.
    """
    system = """أنت حارس تداول حذر (cautious trading sentinel). دورك:
1. مراجعة حالة السوق والفرص الأخيرة.
2. اقتراح فرص تداول محتملة (مع ذكر عدم اليقين).
3. طرح أسئلة مخاطر للمتداول.

STRICT JSON output format — no markdown, no text outside JSON:
{
  "summary": "ملخص قصير بالعربية",
  "opportunities": [
    {
      "symbol": "AAPL",
      "headline": "عنوان الفرصة",
      "reasoning": "المنطق",
      "confidence": "low|medium|high",
      "uncertainty": "مصدر عدم اليقين"
    }
  ],
  "risk_questions": [
    {
      "topic": "موضوع المخاطرة",
      "reasoning": "المنطق",
      "question": "السؤال للمتداول"
    }
  ]
}"""

    user = "Context:\n" + json.dumps(context, default=str, ensure_ascii=False, indent=2)
    return system, user


def run_sentinel_cycle(
    _llm: Optional[Callable] = None,
    _send: Optional[Callable] = None,
) -> dict:
    """Run one sentinel judgment cycle.

    Gathers context (market state, recent journal), calls the LLM,
    formats opportunities and risk questions, sends them to the user,
    and records decisions in the journal.

    Args:
        _llm: Injectable LLM function (system, user) -> JSON string.
        _send: Injectable send function (text, dedup_key="") -> bool.

    Returns:
        {"opportunities_sent": N, "questions_sent": N, "summary": str}
    """
    from app.services.ai_sentinel.journal import recent_decisions, record_decision

    # Gather context
    journal = recent_decisions(limit=8)
    market = {}
    try:
        from app.services.market_panels import get_market_panels
        pan = get_market_panels()
        if pan:
            market = pan
    except Exception:
        pass

    context = {
        "market": market,
        "opportunities": [],
        "recent_journal": journal,
    }

    # Call LLM (or injected mock)
    if _llm is None:
        # In production, this would call the real LLM.
        # For now, return empty — sentinel is on-demand only.
        return {"opportunities_sent": 0, "questions_sent": 0, "summary": "Sentinel dormant (no LLM configured)"}

    system_prompt, user_prompt = build_prompt(context)
    raw = _llm(system_prompt, user_prompt)
    data = _parse_json_lenient(raw)

    if not data:
        # Malformed — send fallback
        if _send:
            _send("⚠️ تعذر تحليل مخرجات الحارس — يرجى المراجعة اليدوية.")
        return {"opportunities_sent": 0, "questions_sent": 0, "summary": "Parse error"}

    summary = data.get("summary", "")
    opps = data.get("opportunities", [])
    questions = data.get("risk_questions", [])

    opp_sent = 0
    for opp in opps:
        sym = (opp.get("symbol") or "").upper()
        if not sym:
            continue
        # Format and send
        msg = _format_opportunity(opp)
        if _send:
            _send(msg, dedup_key=f"sentinel_opp_{sym}")
        # Record
        try:
            record_decision(
                sym, "opportunity",
                "BUY", _conf_num(opp.get("confidence", "medium")),
                opp.get("reasoning", ""),
                context={"headline": opp.get("headline", ""),
                         "uncertainty": opp.get("uncertainty", "")},
            )
        except Exception as e:
            logger.warning("Sentinel: failed to record opportunity %s: %s", sym, e)
        opp_sent += 1

    q_sent = 0
    for q in questions:
        topic = q.get("topic", "")
        if not topic:
            continue
        msg = _format_risk_question(q)
        if _send:
            _send(msg, dedup_key=f"sentinel_risk_{topic}")
        try:
            record_decision(
                "PORTFOLIO", "risk",
                "HOLD", 0.5,
                q.get("reasoning", ""),
                context={"topic": topic, "question": q.get("question", "")},
            )
        except Exception as e:
            logger.warning("Sentinel: failed to record risk question %s: %s", topic, e)
        q_sent += 1

    return {
        "opportunities_sent": opp_sent,
        "questions_sent": q_sent,
        "summary": summary,
    }
