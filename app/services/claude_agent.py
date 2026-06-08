"""AI Trading Agent — bilingual conversational assistant.

Uses DeepSeek (primary) or Anthropic Claude (fallback) tool-use API to
orchestrate existing trading services (technical analysis, halal screening,
consensus, portfolio, risk).
Read-only: the agent cannot execute trades.
"""

import asyncio
import json
import logging
import time
import uuid
from typing import Optional

import anthropic
from openai import OpenAI

from app.config import settings
from app.services.claude_tools import TOOL_SCHEMAS, DEEPSEEK_TOOL_SCHEMAS, execute_tool

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
    """AI-powered trading assistant with tool use (DeepSeek or Claude)."""

    def __init__(self):
        self._provider = None  # "deepseek" or "anthropic"
        self._deepseek_client = None
        self._anthropic_client = None
        self._tools = None  # schema list for the active provider

        if settings.DEEPSEEK_API_KEY:
            self._provider = "deepseek"
            self._deepseek_client = OpenAI(
                api_key=settings.DEEPSEEK_API_KEY,
                base_url="https://api.deepseek.com/v1",
            )
            self._tools = DEEPSEEK_TOOL_SCHEMAS
            self.model = settings.DEEPSEEK_MODEL
            logger.info("TradingAgent: using DeepSeek (%s)", self.model)
        elif settings.ANTHROPIC_API_KEY:
            self._provider = "anthropic"
            self._anthropic_client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
            self._tools = TOOL_SCHEMAS
            self.model = settings.CLAUDE_MODEL
            logger.info("TradingAgent: using Anthropic Claude (%s)", self.model)
        else:
            raise ValueError("No LLM API key configured — set DEEPSEEK_API_KEY or ANTHROPIC_API_KEY")

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

            # Call LLM (run in thread pool to not block event loop)
            try:
                if self._provider == "deepseek":
                    response = await asyncio.to_thread(
                        self._call_deepseek,
                        messages=messages,
                        tools=self._tools,
                    )
                    # DeepSeek response processing
                    if response is None:
                        return {
                            "response": "DeepSeek API error — no response",
                            "conversation_id": cid,
                            "tools_used": tools_used,
                            "model": self.model,
                        }
                    # Process DeepSeek response
                    choice = response.choices[0]
                    finish_reason = choice.finish_reason
                    
                    # Build the assistant message in OpenAI/DeepSeek-native format.
                    # Preserve tool_calls — DeepSeek REQUIRES each to be answered by a
                    # role="tool" message carrying the matching tool_call_id, otherwise
                    # the next request 400s (this was the "no answers" bug).
                    msg = choice.message
                    assistant_entry = {"role": "assistant", "content": msg.content or ""}
                    if msg.tool_calls:
                        assistant_entry["content"] = msg.content or None
                        assistant_entry["tool_calls"] = [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.function.name,
                                    "arguments": tc.function.arguments,
                                },
                            }
                            for tc in msg.tool_calls
                        ]
                    messages.append(assistant_entry)

                    # Done — model returned a final answer with no tool calls.
                    if finish_reason == "stop" or not msg.tool_calls:
                        return {
                            "response": msg.content or "",
                            "conversation_id": cid,
                            "tools_used": tools_used,
                            "model": self.model,
                        }

                    # Execute each requested tool; append its result as a role="tool"
                    # message keyed by tool_call_id (the OpenAI tool-calling contract).
                    for tc in msg.tool_calls:
                        tool_name = tc.function.name
                        try:
                            tool_input = json.loads(tc.function.arguments or "{}")
                        except (ValueError, TypeError):
                            tool_input = {}
                        logger.info("Agent calling tool: %s(%s)", tool_name, tool_input)
                        tools_used.append(tool_name)
                        result_str = await asyncio.to_thread(
                            execute_tool, tool_name, tool_input
                        )
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": str(result_str),
                        })
                    # Loop back for the next DeepSeek call. MUST continue — never fall
                    # through to the legacy Anthropic-only block after the try/except,
                    # which does response.content and crashes on a DeepSeek response
                    # (this stray fall-through was why tool queries returned no answer).
                    continue

                else:  # anthropic
                    response = await asyncio.to_thread(
                        self._anthropic_client.messages.create,
                        model=self.model,
                        max_tokens=4096,
                        system=_build_system_prompt(),
                        tools=TOOL_SCHEMAS,
                        messages=messages,
                    )
                    
                    # Append assistant response to history
                    messages.append({"role": "assistant", "content": response.content})
                    
                    # Check if Claude is done
                    if response.stop_reason == "end_turn":
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
                                
                                result_str = await asyncio.to_thread(
                                    execute_tool, tool_name, tool_input
                                )
                                
                                tool_results.append({
                                    "type": "tool_result",
                                    "tool_use_id": tool_use_id,
                                    "content": result_str,
                                })
                        
                        messages.append({"role": "user", "content": tool_results})
                    else:
                        break
                        
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


    def _call_deepseek(self, messages: list, tools: list):
        """Call DeepSeek API with tool definitions."""
        system_msg = _build_system_prompt()
        # chat() now builds OpenAI/DeepSeek-native history (user / assistant+tool_calls /
        # tool+tool_call_id), so just prepend the system message and pass it through.
        api_messages = [{"role": "system", "content": system_msg}, *messages]

        return self._deepseek_client.chat.completions.create(
            model=self.model,
            messages=api_messages,
            tools=tools,
            max_tokens=4096,
            temperature=0.7,
        )


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
