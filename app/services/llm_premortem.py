"""LLM pre-mortem — a devil's-advocate risk check before a BUY.

Every scanner is built to find reasons to BUY; nothing argues the other side. This
asks the LLM the pre-mortem question — "if this long fails over the next few weeks,
why?" — and returns concise risk flags + a risk level. It runs on shortlists only
(analyze card / agent), reuses the DeepSeek client, is cached, and fails OPEN.
"""

from __future__ import annotations

import json
import os
import re
import time

LLM_PREMORTEM: bool = os.environ.get("LLM_PREMORTEM", "true").lower() not in ("false", "0", "no")
_TTL: float = float(os.environ.get("LLM_PREMORTEM_TTL", "21600"))  # 6 h
_cache: dict[str, tuple[float, dict]] = {}

_SYS = (
    "You are a risk-focused equity analyst running a PRE-MORTEM: assume a proposed "
    "SHORT-TERM long in this ticker has FAILED a few weeks from now, and state the most "
    "likely concrete reasons WHY (stretched valuation, upcoming earnings risk, sector "
    "weakness, technical breakdown, heavy insider selling, news/legal overhang, crowded "
    "trade). Be specific and skeptical. Reply with ONLY compact JSON: "
    '{"risk": "low|medium|high", "flags": ["<=5 word phrase", ...]} with at most 4 flags.'
)

__all__ = ["llm_premortem", "LLM_PREMORTEM"]


def _parse(raw: str | None) -> dict | None:
    if not raw:
        return None
    try:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            return None
        d = json.loads(m.group(0))
        risk = str(d.get("risk", "")).lower().strip()
        if risk not in ("low", "medium", "high"):
            risk = "medium"
        flags = [str(f)[:48] for f in (d.get("flags") or []) if str(f).strip()][:4]
        return {"risk": risk, "flags": flags}
    except Exception:
        return None


def llm_premortem(symbol: str, context: str | None = None) -> dict | None:
    """Devil's-advocate risk flags for a proposed long. Returns
    ``{"risk": low|medium|high, "flags": [...], "method": "llm"}`` or None
    (caller degrades gracefully). Cached per symbol; fails open."""
    if not LLM_PREMORTEM:
        return None
    sym = (symbol or "").upper().strip()
    if not sym:
        return None
    now = time.time()
    hit = _cache.get(sym)
    if hit and now - hit[0] < _TTL:
        return hit[1]
    try:
        from app.ai_agent import AIAgent
        agent = AIAgent()
        if not getattr(agent, "_llm_available", False):
            return None
        user = f"Ticker: {sym}"
        if context:
            user += f"\nContext: {context[:600]}"
        parsed = _parse(agent._call_llm(_SYS, user, max_tokens=140, temperature=0.2))
        if parsed is None:
            return None
        out = {**parsed, "method": "llm"}
        _cache[sym] = (now, out)
        return out
    except Exception:
        return None
