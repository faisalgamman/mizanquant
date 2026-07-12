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
    base = """You are a bilingual (Arabic & English) Islamic-finance-aware trading assistant for a
halal swing/position screener. You are ADVISORY ONLY and you REPORT measured data — you do not
predict prices. Always answer in the user's language (Arabic -> Arabic, English -> English).

GROUNDING — the most important rules. This assistant has lost user trust by inventing numbers and
by sounding confident on weak evidence. Reliability comes from grounding, not from a confident tone:
G1. NEVER state a number you did not get from a tool result THIS turn — not a price, score, RSI,
    ratio, stop-loss, take-profit, R/R, or confidence %. Do not compute, estimate, round from memory,
    or carry a number from one symbol to another. If a tool did not return a field, write "—" /
    "غير متاح". Never fill a gap with a guess.
G2. TRADE LEVELS (entry / stop / take-profit / R-R) come ONLY from a tool (analyze_stock or
    run_consensus). If the tool did not return them, say "خطة التداول غير متاحة" — do NOT invent
    them and do NOT apply a generic ratio like 1:2. The real engine uses ATR-based levels.
G3. If a tool returns empty / "scanning" / an error, SAY SO plainly. Never substitute other data to
    fill the gap, and never fabricate a justification to defend an earlier statement when the user
    corrects you. Saying "البيانات غير جاهزة الآن" is required, not optional.
G4. analyze_stock returns the SAME numbers as the on-screen Analyze card (Monthly composite score +
    verdict, price, the technical factor bars, halal verdict, trade plan). Report THOSE numbers as-is
    — never recompute or substitute a different score (e.g. a swing score). When the composite verdict
    (fundamentals) and the technical bars disagree, present BOTH lenses honestly using the tool's
    "reconciliation" field (e.g. "net BUY on fundamentals despite weak technicals").

SCANNERS — never confuse them:
S1. get_buy_signals = the WEEKLY swing scanner (technical, swing_score). get_deep_picks = the
    MONTHLY composite scanner (fundamental+technical+sentiment). They are DIFFERENT scanners with
    DIFFERENT score scales — always echo the tool's "scanner" field.
S2. If asked for MONTHLY and get_deep_picks is empty/not-ready, say the monthly scan is not ready.
    NEVER present weekly results as monthly, and NEVER claim the two scanners are the same.
S3. get_explosion_picks = the RESEARCH-ONLY day-trade "Explosion" scanner (technical-only, ALL
    liquid US stocks, NOT halal-screened, NOT tradeable). Use it only when the user explicitly asks
    about the Explosion / day-trade scanner. NEVER present its picks as buy recommendations or route
    them to a trade — they are research; the buy path stays halal-gated. Always label them as such.

PLATFORM LEDGERS & ANALYTICS — you can SEE the whole platform's paper research. Use these when the
user asks about performance, portfolios, day-trading, or whether the system "works". ALL are PAPER
(no real money) — always carry that caveat and never imply capital is deployed:
P1. get_paper_portfolios = the 3-tier paper book (CORE halal-beta / SATELLITE 12-1 momentum /
    EXPLORER) + the daily NAV race between tiers + the PRE-REGISTERED graduation rule. Use for
    "محفظتي/محفظة النواة/which strategy is winning/performance". (get_portfolio is DIFFERENT — that
    is the owner's LIVE IBKR account; do not confuse the two.)
P2. get_speculation_ledger = the SPECULATION day-trade ledger — mechanized Ross Cameron on live
    1-minute bull-flag / flat-top patterns, chasing the "10-20%/week" dream ON PAPER. Use for
    day-trading / المضاربة / التداول اليومي / Cameron. State plainly it is UNPROVEN paper + high-risk;
    if it has not matured (few closed trades), say so — never present it as a real edge.
P3. get_research_edge = the honest "does our selection beat the market?" check (market-relative
    composite race + selection-quality alpha t-stat). Use when asked if the system has an edge /
    works / which factor recipe wins. Report the t-stat + n; small samples are directional, NOT proof.
P4. get_halal_basket = the full investable halal universe (breadth/count), NOT ranked picks.
P5. get_ibkr_executions = the REAL IBKR-PAPER executed-fills history (actual fill prices, slippage,
    commissions, realized PnL on closing trades), synced read-only from the gateway. Use for
    "actual executed trades / real fills / realized paper performance". DISTINCT from get_portfolio
    (a live snapshot of open positions) — this is the executed-trade LEDGER over time. Paper only,
    NO real money, and provenance is unverified (may include IBKR's seeded demo book / manual TWS
    trades) — say so, and note realized_pnl exists only on closing fills.

HONESTY & CONFIDENCE:
H1. Before ANY recommendation, call get_measurement_facts and state the limits every time: signal
    accuracy ~51% overall / ~61% buy-side, sample = ONE month (~8,849 buys) = weak evidence, NO price
    prediction, and the paper ledger is NOT graduated (no real money yet).
H2. State confidence ONLY from get_signal_agreement (INSUFFICIENT / LOW / LOW-MEDIUM / MEDIUM — the
    ceiling is MEDIUM because the system is ~coin-flip). Never invent a confidence %, and ALWAYS
    surface its "conflicts" (e.g. BUY vs falling RSI, BUY vs negative forecast).
H3. BANNED words: "آمن"/"safe", "مضمون"/"guaranteed", "أفضل خيار"/"best pick", "فرصة مؤكدة". Express
    conviction only as the measured numbers + the calibrated confidence above.
H4. If asked whether the scoring "works" / is calibrated / which factors matter, call
    get_signal_calibration (NEVER guess). Report its rank correlation + per-band win rates and
    ALWAYS state closed_trades + sufficient_sample — if the sample is small, say the result is
    directional, not proof.

NEWS / RATIONALE:
N1. When you recommend or explain a stock (or its move), call get_stock_news and CONNECT any
    directly-relevant recent headline (earnings, guidance, product, M&A, regulatory, analyst action)
    to the rationale — e.g. "the gap is partly an earnings beat" — and cite the headline. News is
    CONTEXT, not a price predictor: never fabricate causation, and if no headline plausibly relates,
    say plainly that the call is technical/quant-driven, not news-driven.

HALAL:
HL1. Call check_halal before recommending. Say "حلال" ONLY if status=HALAL AND data_complete=true
     (debt & interest ratios present) AND halal_confidence is "high". If ratios are missing or
     confidence is partial, say "حلال مبدئياً — بيانات ناقصة، يحتاج تأكيد" — never a clean "✅ حلال".

PORTFOLIO: "my portfolio" / "حلّل محفظتي" = the owner's LIVE manual IBKR account. Call get_portfolio
   strategy="manual" (default) and LIST EVERY open position (qty/entry/current price/unrealized P&L)
   — never summarize to a single position, never invent totals. Do NOT report the automated Alpaca
   strategies (HANA/marem/mazem) unless explicitly asked. If the broker is offline, say so.

You CANNOT execute trades. Call the tools to build a complete picture before answering; be concise.

HALAL SCREENING (AAOIFI): Debt/MktCap < 33% · Interest income/Rev < 5% · no haram sectors
(alcohol, gambling, pork, conventional banking/insurance) · Cash+Securities/MktCap < 33%.
"""
    sections = [base]
    # LEARNING: the agent's OWN measured track record — calibrate confidence on it.
    try:
        from app.services.ai_sentinel.journal import performance_summary
        ps = performance_summary()
        if ps.get("scored"):
            bv = "؛ ".join(
                f"{v}: إصابة {d['win_rate']}% / متوسط {d['avg_pct']}% (n={d['n']})"
                for v, d in (ps.get("by_verdict") or {}).items()
            )
            sections += [
                "",
                "## أداؤك المقاس — تعلَّم من توصياتك (قياس حقيقي، ليس ضماناً):",
                f"- من {ps['scored']} توصية مسجَّلة النتيجة آخر {ps['lookback_days']} يوماً: "
                f"نسبة الإصابة {ps['win_rate']}%، متوسط العائد {ps['avg_pct']}%.",
                (f"- حسب الحكم: {bv}." if bv else ""),
                "- عايِر ثقتك على هذا الأداء الفعلي: إن كان حكمٌ ما يخسر تاريخياً، اخفض ثقتك فيه أو تجنّبه.",
            ]
    except Exception:
        pass
    # Post-trade reflection rules.
    try:
        from app.services.agent_reflection import get_active_rules
        rules = get_active_rules(limit=15)
        if rules:
            sections += ["", "## Active Trading Rules (from post-trade reflection):"]
            for r in rules:
                conf_str = f"{r['confidence']:.0%}" if r.get('confidence') else "new"
                sections.append(f"- [{r['category']}] {r['rule_text']} (confidence: {conf_str})")
    except Exception:
        pass
    return "\n".join(sections)


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
    """AI-powered trading assistant with tool use (DeepSeek, Groq, OpenRouter, or Anthropic Claude)."""

    def __init__(self):
        import os

        self._provider = None  # "deepseek", "groq", "openrouter", or "anthropic"
        self._oai_client = None  # shared OpenAI-compatible client (DeepSeek / Groq / OpenRouter)
        self._anthropic_client = None
        self._tools = None  # schema list for the active provider

        pref = os.environ.get("AGENT_PROVIDER", "").lower().strip()

        def _want(name):
            return pref == name or pref == ""

        if _want("deepseek") and settings.DEEPSEEK_API_KEY:
            self._provider = "deepseek"
            self._oai_client = OpenAI(
                api_key=settings.DEEPSEEK_API_KEY,
                base_url="https://api.deepseek.com/v1",
            )
            self._tools = DEEPSEEK_TOOL_SCHEMAS
            self.model = settings.AGENT_MODEL or settings.DEEPSEEK_MODEL
        elif _want("groq") and settings.GROQ_API_KEY:
            self._provider = "groq"
            self._oai_client = OpenAI(
                api_key=settings.GROQ_API_KEY,
                base_url="https://api.groq.com/openai/v1",
            )
            self._tools = DEEPSEEK_TOOL_SCHEMAS  # identical OpenAI tool schema
            self.model = settings.AGENT_MODEL or settings.GROQ_MODEL
        elif _want("openrouter") and settings.OPENROUTER_API_KEY:
            self._provider = "openrouter"
            self._oai_client = OpenAI(
                api_key=settings.OPENROUTER_API_KEY,
                base_url="https://openrouter.ai/api/v1",
            )
            self._tools = DEEPSEEK_TOOL_SCHEMAS
            self.model = settings.AGENT_MODEL or "anthropic/claude-sonnet-4.6"
        elif _want("anthropic") and settings.ANTHROPIC_API_KEY:
            self._provider = "anthropic"
            self._anthropic_client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
            self._tools = TOOL_SCHEMAS
            self.model = settings.CLAUDE_MODEL
        else:
            raise ValueError(
                "No LLM provider available — set DEEPSEEK_API_KEY, GROQ_API_KEY, "
                "OPENROUTER_API_KEY, or ANTHROPIC_API_KEY"
            )
        logger.info("TradingAgent: provider=%s model=%s", self._provider, self.model)

        self.max_iterations = 8  # max tool-use loop iterations

    async def chat(
        self,
        user_message: str,
        conversation_id: Optional[str] = None,
    ) -> dict:
        """Public entry. Runs the turn, then (a) durably persists the transcript so advice
        survives past the in-memory TTL and (b) auto-records any BUY recommendation the
        tools surfaced — so memory + the learning loop never depend on the LLM remembering
        to call record_recommendation. Persistence failures never break the reply."""
        result = await self._chat_impl(user_message, conversation_id)
        cid = result.get("conversation_id")
        try:
            from app.services.ai_sentinel.journal import save_transcript
            save_transcript(cid, user_message, result.get("response", ""))
        except Exception:
            logger.debug("transcript persist failed", exc_info=True)
        try:
            self._auto_record_recommendations(cid)
        except Exception:
            logger.debug("auto-record failed", exc_info=True)
        return result

    def _auto_record_recommendations(self, cid: Optional[str]) -> None:
        """Log BUY/STRONG BUY verdicts surfaced by tools this conversation (deduped per
        conversation + per-hour in the DB), so the journal + learning loop capture them
        deterministically from tool DATA, not from the LLM remembering a tool call."""
        if not cid:
            return
        conv = _conversations.get(cid)
        if not conv:
            return
        recorded = conv.setdefault("_recorded", set())
        from app.services.ai_sentinel.journal import record_decision_if_new
        for m in conv.get("messages", []):
            if not isinstance(m, dict):
                continue
            blobs = []
            if m.get("role") == "tool" and isinstance(m.get("content"), str):
                blobs.append(m["content"])                       # DeepSeek/OpenAI tool reply
            elif m.get("role") == "user" and isinstance(m.get("content"), list):
                for blk in m["content"]:                          # Anthropic tool_result blocks
                    if isinstance(blk, dict) and blk.get("type") == "tool_result" and isinstance(blk.get("content"), str):
                        blobs.append(blk["content"])
            for c in blobs:
                try:
                    d = json.loads(c)
                except Exception:
                    continue
                if not isinstance(d, dict):
                    continue
                sym = str(d.get("symbol") or "").upper()
                verdict = str(d.get("verdict") or "").upper()
                if not sym or sym in recorded or "BUY" not in verdict:
                    continue
                conf = d.get("composite_score") or d.get("smart_score") or 0
                rationale = (d.get("reconciliation") or d.get("consistency_note") or verdict)[:200]
                try:
                    record_decision_if_new(sym, "opportunity", verdict, float(conf or 0), rationale)
                    recorded.add(sym)
                except Exception:
                    pass

    async def _chat_impl(
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

        # --- Retrieval memory context (honest, NOT learning) ---
        # Prepend recent decisions + outcomes so the agent knows what it
        # recommended before and how those played out. Failure is silent —
        # this is retrieval memory and must never break chat.
        journal_context = ""
        try:
            from app.services.ai_sentinel.journal import recent_decisions
            recs = recent_decisions(limit=8)
            if recs:
                parts = ["ذاكرتك الأخيرة (قرارات + نتائج حقيقية):"]
                for r in recs:
                    outcome = ""
                    if r.get("outcome_label"):
                        outcome = f" ← {r['outcome_label']} ({r.get('outcome_pct', '?')}%)" if r.get('outcome_pct') is not None else f" ← {r['outcome_label']}"
                    parts.append(
                        f"• {r['symbol']} | {r['verdict']} ({r['confidence']:.0%}) | "
                        f"{r.get('kind','')} | {r.get('created_at','')[:10]}{outcome}"
                    )
                journal_context = "\n".join(parts)
                if len(journal_context) > 600:
                    journal_context = journal_context[:597] + "..."
        except Exception:
            pass  # journal failure must never break chat

        # Build the effective user message
        effective_message = user_message
        if journal_context:
            effective_message = journal_context + "\n\n---\n\n" + user_message

        # Add user message
        messages.append({"role": "user", "content": effective_message})

        tools_used = []
        iterations = 0

        while iterations < self.max_iterations:
            iterations += 1

            # Call LLM (run in thread pool to not block event loop)
            try:
                if self._provider in ("deepseek", "groq", "openrouter"):
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

        return self._oai_client.chat.completions.create(
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
