"""AI Sentinel — resident agent that watches, judges, alerts & discusses risk.

Twice-daily cycle: gather market context + top picks + journal, ask a
multi-provider LLM for structured judgment, then send opportunities and
risk questions to Telegram.

⚠️  CRITICAL: the Sentinel NEVER auto-trades — it only NOTIFIES and DISCUSSES.
Every opportunity message carries an honesty disclaimer.
"""

import json
import logging
import re
from datetime import datetime

logger = logging.getLogger("screener")

# ── constants ───────────────────────────────────────────────────────

_HONESTY_LINE = (
    "⚠️ Quantitative signal · ~coin-flip base accuracy · not advice · "
    "paper ledger not graduated."
)

_JSON_CONTRACT = """{
  "summary": "one-line market read",
  "opportunities": [
    {"symbol":"AAPL","headline":"...","reasoning":"...","confidence":"low|medium|high","uncertainty":"..."}
  ],
  "risk_questions": [
    {"topic":"drawdown","reasoning":"...","question":"...?"}
  ]
}"""


# ── JSON parsing ────────────────────────────────────────────────────


def _parse_json_lenient(raw: str) -> dict:
    """Extract the first {...} block and json.loads it.

    On failure returns {} — the caller falls back to summary-only.
    """
    if not raw:
        return {}
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return {}
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        logger.warning("sentinel: JSON parse failed for raw=%s", raw[:200])
        return {}


def _conf_num(confidence: str) -> float:
    """Map 'low'/'medium'/'high' to 0.0-1.0."""
    mapping = {"low": 0.3, "medium": 0.6, "high": 0.85}
    return mapping.get(str(confidence).lower(), 0.5)


# ── context gathering ───────────────────────────────────────────────


def gather_context() -> dict:
    """Collect everything the sentinel needs — NO new analysis, reuses existing sources.

    Returns:
        {"market": {...}, "opportunities": [...], "recent_journal": [...]}
    """
    ctx: dict = {"market": {}, "opportunities": [], "recent_journal": []}

    # --- market state ---
    try:
        from app.services.regime import get_regime
        r = get_regime()
        ctx["market"]["regime"] = r.state
        ctx["market"]["vix"] = r.vix
        ctx["market"]["vix_pctile"] = r.vix_pctile
    except Exception:
        logger.debug("sentinel: regime unavailable")
        ctx["market"]["regime"] = "UNKNOWN"
        ctx["market"]["vix"] = 0.0

    try:
        from app.services.portfolio_stop import check_drawdown
        dd = check_drawdown()
        ctx["market"]["drawdown_tier"] = dd.get("tier", "unknown")
        ctx["market"]["drawdown_pct"] = dd.get("drawdown_pct", 0.0)
    except Exception:
        logger.debug("sentinel: drawdown unavailable")
        ctx["market"]["drawdown_tier"] = "unknown"
        ctx["market"]["drawdown_pct"] = 0.0

    ctx["market"]["credit_status"] = "unknown"

    # --- top opportunities (deep-picks cache) ---
    try:
        # Pull from the in-process screen cache (same TTL as the UI).
        from app.workspace_server import _cache_get
        cached = _cache_get("deep_picks_15_composite", max_age=1800)
        if cached and isinstance(cached, dict) and "results" in cached:
            for row in cached["results"][:8]:
                ctx["opportunities"].append({
                    "symbol": row.get("symbol", "?"),
                    "composite": row.get("composite", 0),
                    "signal": row.get("swing_signal", "NONE"),
                    "halal": row.get("halal", False),
                    "usx_pass": row.get("usx_pass", False),
                    "score_tech": row.get("score_tech", 0),
                    "score_fund": row.get("score_fund", 0),
                    "score_sentiment": row.get("score_sentiment", 0),
                })
    except Exception:
        logger.debug("sentinel: deep-picks unavailable")

    # --- recent journal ---
    try:
        from app.services.ai_sentinel.journal import recent_decisions
        ctx["recent_journal"] = recent_decisions(10)
    except Exception:
        logger.debug("sentinel: journal unavailable")

    return ctx


# ── prompt building ─────────────────────────────────────────────────


def build_prompt(ctx: dict) -> tuple[str, str]:
    """Return (system, user) prompts for the LLM."""
    system = (
        "You are a cautious trading sentinel. "
        "Output STRICT JSON only — no markdown fences, no commentary outside the JSON object. "
        "The base signals are ~51% win-rate; never overstate confidence; always include an "
        "'uncertainty' note. You never place trades — you only inform and ask questions.\n\n"
        f"Return exactly this shape:\n{_JSON_CONTRACT}"
    )
    user = json.dumps(ctx, default=str, ensure_ascii=False, indent=2)
    return system, user


# ── defaults (overridable for tests) ─────────────────────────────────


def _default_llm(system: str, user: str) -> str:
    """Wrap AIAgent._call_llm (synchronous) — DeepSeek→OpenAI→Anthropic fall-through."""
    from app.ai_agent import AIAgent
    agent = AIAgent()
    result = agent._call_llm(system, user, max_tokens=2000, temperature=0.5)
    return result or ""


def _default_send(text: str, dedup_key: str = "") -> bool:
    """Send a message to Telegram via the notify pipeline."""
    from app.services.notify import send_message
    return send_message(text, dedup_key=dedup_key, critical=False)


# ── formatting ──────────────────────────────────────────────────────


def _format_opportunity(op: dict) -> str:
    """Format an opportunity as a Telegram message with the honesty disclaimer."""
    symbol = op.get("symbol", "?")
    headline = op.get("headline", "No headline")
    reasoning = op.get("reasoning", "")
    confidence = op.get("confidence", "medium")
    uncertainty = op.get("uncertainty", "")
    lines = [
        f"📊 *{symbol}* — {headline}",
        f"🎯 Confidence: {confidence}",
    ]
    if reasoning:
        lines.append(f"💡 {reasoning}")
    if uncertainty:
        lines.append(f"❓ {uncertainty}")
    lines.append("")
    lines.append(_HONESTY_LINE)
    return "\n".join(lines)


def _format_risk_question(q: dict) -> str:
    """Format a risk question as a Telegram message inviting a reply."""
    topic = q.get("topic", "risk")
    question = q.get("question", "")
    reasoning = q.get("reasoning", "")
    lines = [
        f"⚠️ *RISK QUESTION — {topic}*",
    ]
    if question:
        lines.append(f"❓ {question}")
    if reasoning:
        lines.append(f"📝 {reasoning}")
    return "\n".join(lines)


# ── main cycle ──────────────────────────────────────────────────────


def run_sentinel_cycle(*, _llm=None, _send=None, _now=None) -> dict:
    """Run one sentinel cycle: gather, ask LLM, send messages, journal.

    Args:
        _llm:  Callable (system, user) -> str.  Injected for tests.
        _send: Callable (text, dedup_key) -> bool.  Injected for tests.
        _now:  Optional datetime for deterministic testing.

    Returns:
        {"opportunities_sent": int, "questions_sent": int, "summary": str}
    """
    llm = _llm or _default_llm
    send = _send or _default_send
    now = _now or datetime.now()

    # 1. Gather
    ctx = gather_context()

    # 2. Build prompt
    system, user = build_prompt(ctx)

    # 3. Ask LLM
    raw = llm(system, user)
    data = _parse_json_lenient(raw)

    # 4. Parse
    opps = data.get("opportunities", [])
    qs = data.get("risk_questions", [])
    summary = data.get("summary", "")

    if not data:
        # Parse failed entirely — send summary-only fallback if available.
        fallback = raw[:1500] if raw else "Sentinel: LLM returned no parseable output."
        send(fallback, dedup_key=f"sentinel:fallback:{now.date().isoformat()}")
        return {"opportunities_sent": 0, "questions_sent": 0, "summary": fallback[:100]}

    # 5. Send + journal
    from app.services.ai_sentinel.journal import record_decision

    for op in opps:
        symbol = op.get("symbol", "?")
        msg = _format_opportunity(op)
        send(msg, dedup_key=f"opp:{symbol}")
        record_decision(
            symbol, "opportunity",
            op.get("headline", ""),
            _conf_num(op.get("confidence", "medium")),
            op.get("reasoning", ""),
            op,
        )

    for q in qs:
        topic = q.get("topic", "risk")
        msg = _format_risk_question(q)
        send(msg, dedup_key=f"risk:{topic}")
        record_decision(
            topic, "risk_question", "QUESTION", 0,
            q.get("reasoning", ""), q,
        )

    return {
        "opportunities_sent": len(opps),
        "questions_sent": len(qs),
        "summary": summary,
    }
