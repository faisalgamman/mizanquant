"""Claude AI Trading Agent — bilingual conversational assistant.

Uses Anthropic's tool-use API to orchestrate existing trading services
(technical analysis, halal screening, consensus, portfolio, risk).
Read-only: the agent cannot execute trades.
"""

import asyncio
import json
import logging
import time
import uuid
from typing import Optional

import anthropic

from app.config import settings
from app.services.claude_tools import TOOL_SCHEMAS, execute_tool

logger = logging.getLogger("claude_agent")


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

def _build_system_prompt() -> str:
    """Build system prompt with active trading rules injected."""
    base = """You are a bilingual (Arabic & English) Islamic-finance-aware trading assistant.
You help users analyze US stocks, check Sharia compliance, find buy opportunities,
and review portfolio performance.

RULES:
1. ALWAYS respond in the same language the user writes in. If Arabic → respond in Arabic. If English → English.
2. ALWAYS check halal status (check_halal) before recommending any stock.
3. You CANNOT execute trades. You are advisory only. If asked to buy/sell, explain the signal and let the user decide.
4. Use the tools available to you to provide data-driven analysis. Don't guess — call the tools.
5. When presenting numbers, use clear formatting with appropriate precision.
6. For trade recommendations, always include: entry price, stop loss, take profit levels, and position sizing context.
7. When multiple tools are needed, call them to build a complete picture before answering.
8. Be concise but thorough. Prioritize actionable insights.

AVAILABLE STRATEGIES:
- HANA (A): Concentrated trend-following, 3 max positions, 45% min confidence
- marem (B): Diversified dip-buying, 5 max positions, 40% min confidence
- mazem (C): AI ensemble, 4 max positions, 50% min confidence

HALAL SCREENING (AAOIFI):
- Debt/Market Cap < 33%
- Interest Income/Revenue < 5%
- No haram sectors (alcohol, gambling, pork, conventional banking/insurance)
- Cash+Securities/Market Cap < 33%
"""
    try:
        from app.services.agent_reflection import get_active_rules
        rules = get_active_rules(limit=15)
        if rules:
            lines = [base, "", "## Active Trading Rules (from post-trade reflection):"]
            for r in rules:
                conf_str = f"{r['confidence']:.0%}" if r.get('confidence') else "new"
                lines.append(f"- [{r['category']}] {r['rule_text']} (confidence: {conf_str})")
            return "\n".join(lines)
    except Exception:
        pass
    return base


# ---------------------------------------------------------------------------
# Conversation store (in-memory with TTL)
# ---------------------------------------------------------------------------

_conversations: dict[str, dict] = {}
_CONV_TTL = 1800  # 30 minutes


def _cleanup_conversations():
    """Remove stale conversations."""
    now = time.time()
    stale = [k for k, v in _conversations.items() if now - v["last_access"] > _CONV_TTL]
    for k in stale:
        del _conversations[k]


def get_or_create_conversation(conversation_id: Optional[str] = None) -> tuple[str, list]:
    """Get existing conversation history or create a new one."""
    _cleanup_conversations()

    if conversation_id and conversation_id in _conversations:
        conv = _conversations[conversation_id]
        conv["last_access"] = time.time()
        return conversation_id, conv["messages"]

    cid = conversation_id or str(uuid.uuid4())[:8]
    _conversations[cid] = {"messages": [], "last_access": time.time()}
    return cid, _conversations[cid]["messages"]


# ---------------------------------------------------------------------------
# Agent class
# ---------------------------------------------------------------------------

class TradingAgent:
    """Claude-powered trading assistant with tool use."""

    def __init__(self):
        if not settings.ANTHROPIC_API_KEY:
            raise ValueError("ANTHROPIC_API_KEY not configured")

        self.client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        self.model = settings.CLAUDE_MODEL
        self.max_iterations = 8  # max tool-use loop iterations

    async def chat(
        self,
        user_message: str,
        conversation_id: Optional[str] = None,
    ) -> dict:
        """Process a user message and return the agent's response.

        Returns:
            {
                "response": str,
                "conversation_id": str,
                "tools_used": list[str],
                "model": str,
            }
        """
        cid, messages = get_or_create_conversation(conversation_id)

        # Add user message
        messages.append({"role": "user", "content": user_message})

        tools_used = []
        iterations = 0

        while iterations < self.max_iterations:
            iterations += 1

            # Call Claude (run in thread pool to not block event loop)
            try:
                response = await asyncio.to_thread(
                    self.client.messages.create,
                    model=self.model,
                    max_tokens=4096,
                    system=_build_system_prompt(),
                    tools=TOOL_SCHEMAS,
                    messages=messages,
                )
            except anthropic.APIError as e:
                logger.error(f"Claude API error: {e}")
                return {
                    "response": f"Claude API error: {e.message}",
                    "conversation_id": cid,
                    "tools_used": tools_used,
                    "model": self.model,
                }

            # Append assistant response to history
            messages.append({"role": "assistant", "content": response.content})

            # Check if Claude is done (no more tool calls)
            if response.stop_reason == "end_turn":
                # Extract final text
                text_parts = []
                for block in response.content:
                    if block.type == "text":
                        text_parts.append(block.text)

                return {
                    "response": "\n".join(text_parts),
                    "conversation_id": cid,
                    "tools_used": tools_used,
                    "model": self.model,
                }

            # Process tool calls
            if response.stop_reason == "tool_use":
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        tool_name = block.name
                        tool_input = block.input
                        tool_use_id = block.id

                        logger.info(f"Agent calling tool: {tool_name}({tool_input})")
                        tools_used.append(tool_name)

                        # Execute tool in thread pool (services are sync)
                        result_str = await asyncio.to_thread(
                            execute_tool, tool_name, tool_input
                        )

                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": tool_use_id,
                            "content": result_str,
                        })

                # Append tool results as user message
                messages.append({"role": "user", "content": tool_results})
            else:
                # Unexpected stop reason
                break

        # If we exhausted iterations, return whatever text we have
        text = ""
        for block in (response.content if response else []):
            if hasattr(block, "text"):
                text += block.text
        return {
            "response": text or "Agent reached maximum iterations.",
            "conversation_id": cid,
            "tools_used": tools_used,
            "model": self.model,
        }


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_agent: Optional[TradingAgent] = None


def get_agent() -> TradingAgent:
    """Get or create the singleton TradingAgent."""
    global _agent
    if _agent is None:
        _agent = TradingAgent()
    return _agent
