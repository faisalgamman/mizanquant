"""Finance-aware news sentiment via the existing LLM (DeepSeek).

The generic VADER lexicon misreads finance phrasing — "beats estimates", "raises
guidance", "downgrade", "guidance cut", "SEC probe", "dilution" all carry
market meaning VADER doesn't know. This scores the same headlines with the LLM the
app already runs (no new infra, no GPU), and it fails OPEN so VADER stays the
fallback. Cached per symbol (6 h) and only ever called on shortlists (the
deep-picks enrichment / analyze card), never the bulk universe scan — so it's a
handful of cheap calls, not hundreds.
"""

from __future__ import annotations

import json
import os
import re
import time

LLM_SENTIMENT: bool = os.environ.get("LLM_SENTIMENT", "true").lower() not in ("false", "0", "no")
_TTL: float = float(os.environ.get("LLM_SENTIMENT_TTL", "21600"))  # 6 h
_cache: dict[str, tuple[float, dict]] = {}

_SYS = (
    "You are a sell-side equity analyst. Judge the SHORT-TERM stock-price sentiment "
    "of news headlines for one ticker. Be finance-aware: beats / raised guidance / "
    "upgrade / buyback / new contract = bullish; misses / cut guidance / downgrade / "
    "probe / lawsuit / dilution / layoffs = bearish. Weigh material news over noise. "
    'Reply with ONLY compact JSON: {"score": <float -1..1>, '
    '"label": "bullish|neutral|bearish", "why": "<=12 words"}.'
)

__all__ = ["llm_news_sentiment", "LLM_SENTIMENT"]


def _parse(raw: str | None) -> dict | None:
    if not raw:
        return None
    try:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            return None
        d = json.loads(m.group(0))
        s = max(-1.0, min(1.0, float(d.get("score"))))
        label = str(d.get("label", "")).lower().strip()
        if label not in ("bullish", "neutral", "bearish"):
            label = "bullish" if s > 0.15 else "bearish" if s < -0.15 else "neutral"
        return {"score": round(s, 3), "label": label, "why": str(d.get("why", ""))[:60]}
    except Exception:
        return None


def _fetch_headlines(symbol: str) -> list[str]:
    """Same sources as the VADER path (Alpaca → FMP), best-effort."""
    try:
        from app.services.market_data import get_alpaca_news
        h = [n.get("title", "") for n in (get_alpaca_news(symbol, 10) or []) if n.get("title")]
        if h:
            return h
    except Exception:
        pass
    try:
        from app.services.fmp_client import fmp_client
        news = fmp_client.get_stock_news(symbol, limit=10)
        if news:
            return [(n.get("title") or n.get("headline") or "") for n in news if n]
    except Exception:
        pass
    return []


def llm_news_sentiment(symbol: str, headlines: list[str] | None = None) -> dict | None:
    """Finance-aware sentiment for *symbol*'s recent headlines.

    Returns ``{"score": -1..1, "label", "why", "n", "method": "llm"}`` or None
    (→ the caller falls back to VADER). Cached per symbol; fails open.
    """
    if not LLM_SENTIMENT:
        return None
    sym = (symbol or "").upper().strip()
    if not sym:
        return None
    now = time.time()
    hit = _cache.get(sym)
    if hit and now - hit[0] < _TTL:
        return hit[1]
    try:
        heads = [h for h in (headlines if headlines is not None else _fetch_headlines(sym)) if h][:10]
        if not heads:
            return None
        from app.ai_agent import AIAgent
        agent = AIAgent()
        if not getattr(agent, "_llm_available", False):
            return None
        user = f"Ticker: {sym}\nHeadlines:\n" + "\n".join(f"- {h}" for h in heads)
        parsed = _parse(agent._call_llm(_SYS, user, max_tokens=120, temperature=0.0))
        if parsed is None:
            return None
        out = {**parsed, "n": len(heads), "method": "llm"}
        _cache[sym] = (now, out)
        return out
    except Exception:
        return None
