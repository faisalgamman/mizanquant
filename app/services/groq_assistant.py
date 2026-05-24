"""MizanAI — Groq-powered trading assistant with Function Calling.

Provides:
  - chat_completion()   : raw Groq API call
  - trading_chat()      : full agentic loop with platform tool calls
  - TOOL_DEFINITIONS    : function schemas for Groq function calling

Tools the AI can call (mapped to internal platform APIs):
  get_market_status    → /api/market/status
  get_macro_indicators → /api/macro/indicators
  screen_stock         → /api/v1/screen/{symbol}
  get_portfolio        → /api/v1/portfolio
  get_halal_score      → /api/v1/halal/{symbol}
  get_etf_holdings     → /api/etf/{symbol}/holdings
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("screener")

# ── System prompt ──────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are MizanAI, an intelligent trading assistant embedded in MizanQuant — \
a professional Islamic-compliant quantitative trading platform.

Your role:
- Analyze stocks, portfolios, and market conditions
- Check Halal compliance (AAOIFI Standard 21)
- Interpret macro-economic data (FRED)
- Give concise, data-driven insights

You can orchestrate ACROSS the whole platform via tools. The platform is a unified
ecosystem driven by one reactive state — the MarketContextBundle (regime + macro +
sector rotation → a risk_posture of risk_on/neutral/risk_off):
- get_market_context  → the reactive state. CALL FIRST for any regime/market question.
- get_sector_rotation → which sectors are leading/lagging (RRG quadrants).
- get_etf_sectors     → an ETF's sector allocation.
- screen_stock / get_top_signals → halal-screened opportunities (sector rotation already factored in).
- get_forecast        → posture-conditioned price direction for a stock.
- get_risk_status     → would the Risk Desk allow a trade now (posture, halt, VaR, Kelly)?
- get_backtest_history→ historical strategy performance.
- get_macro_indicators / get_portfolio / get_etf_holdings.

Orchestration examples:
- "Best halal pick now?" → get_market_context → get_sector_rotation → get_top_signals → get_risk_status.
- "Where is AAPL headed?" → get_forecast (note the conditioned_direction vs raw).

Rules:
- Always call the appropriate tool before answering data questions. Chain tools when a
  question spans layers (regime → sector → stock → risk).
- Be brief and precise — traders value conciseness
- Format numbers clearly: "$42.50", "3.2%", "+0.8σ"
- Respond in the same language the user writes in (Arabic or English)
- End every response with a one-line disclaimer: "⚠ Not financial advice."
- When discussing halal compliance cite AAOIFI Standard 21 thresholds (5% haram revenue, 30% debt ratio)
- When the risk_posture is risk_off, say so and be more cautious in recommendations.

Platform stack: FastAPI + OpenBB 4.7.1 + FMP + FRED + RL Agents + ML Ensemble.
"""

# ── Tool definitions (Groq function calling) ───────────────────────────────────

TOOL_DEFINITIONS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "get_market_status",
            "description": (
                "Get current market regime (Bull/Bear/Neutral/Volatile), "
                "VIX level, and overall market health score. "
                "Call this before any portfolio or market discussion."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_macro_indicators",
            "description": (
                "Get live FRED macroeconomic indicators: "
                "CPI YoY%, Federal Funds Rate, 10Y-2Y Yield Spread, "
                "Unemployment Rate, High-Yield Credit Spread. "
                "Call when asked about inflation, interest rates, or recession risk."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "screen_stock",
            "description": (
                "Get comprehensive analysis of a stock: quality score (0-100), "
                "halal compliance status, AI recommendation, technical signals, "
                "and fundamental metrics. Call for ANY stock-specific question."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "Stock ticker (e.g., AAPL, MSFT, GOOGL, AMZN)",
                    }
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_portfolio",
            "description": (
                "Get current paper trading portfolio: open positions, "
                "equity value, daily P&L, and win rate. "
                "Call when asked about the portfolio or performance."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_etf_holdings",
            "description": (
                "Get top holdings and sector allocation of an ETF. "
                "Call when asked about ETF composition (e.g., SPY, QQQ, XLK)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "ETF ticker (e.g., SPY, QQQ, IWM, XLK)",
                    }
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_top_signals",
            "description": (
                "Get the top 10 buy signals from the ML screener right now, "
                "ranked by composite score. Call when asked for best opportunities."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "min_score": {
                        "type": "number",
                        "description": "Minimum quality score threshold (0-100, default 70)",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_market_context",
            "description": (
                "Get the unified MarketContextBundle — the platform's reactive state: "
                "market regime (BULL/BEAR/NEUTRAL), derived risk_posture "
                "(risk_on/neutral/risk_off), VIX, FRED macro, and the ranked sector "
                "rotation. CALL THIS FIRST for any 'what's the market doing' or "
                "regime/posture question — it conditions screening, risk, and forecasting."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_sector_rotation",
            "description": (
                "Get the sector Relative Rotation Graph: each SPDR sector ETF's "
                "quadrant (leading/improving/weakening/lagging) vs SPY. Call when asked "
                "which sectors are strong/rotating, or to pick the best sector."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_etf_sectors",
            "description": (
                "Get an ETF's sector allocation (weights). Call when asked how an ETF "
                "is split across sectors (e.g. SPY, QQQ)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "ETF ticker (e.g. SPY, QQQ, XLK)"},
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_forecast",
            "description": (
                "Get a forward price forecast for a stock with posture-conditioned "
                "direction (up/down/neutral). Uses a fast statistical model by default. "
                "Call when asked where a stock is headed or for a price prediction."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Stock ticker (e.g. AAPL)"},
                    "model": {"type": "string", "description": "Model name (default 'arima', fast)"},
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_risk_status",
            "description": (
                "Get the Risk Desk status: risk_posture, whether a market halt is "
                "active, Kelly position-sizing config, 24h guard-block count, and a "
                "historical VaR estimate. Call to answer 'would the Risk Desk allow a "
                "trade right now?' or about portfolio risk limits."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_backtest_history",
            "description": (
                "Get recent persisted backtest runs (Sharpe, return, drawdown, win rate, "
                "deflated Sharpe). Call when asked about historical strategy/backtest "
                "performance. Optional symbol filter."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Optional symbol filter (e.g. AAPL)"},
                },
                "required": [],
            },
        },
    },
]

# ── Internal tool execution ────────────────────────────────────────────────────

async def _execute_tool(name: str, args: dict) -> str:
    """Execute a platform tool call and return result as JSON string."""
    import asyncio
    import httpx

    base = "http://127.0.0.1:6910"  # internal loopback

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            if name == "get_market_status":
                r = await client.get(f"{base}/api/market/status")
                data = r.json()
                return json.dumps({
                    "regime":     data.get("regime", "Unknown"),
                    "vix":        data.get("vix"),
                    "vix_pct":    data.get("vix_percentile"),
                    "score":      data.get("score"),
                    "trend":      data.get("trend"),
                    "yield_spread": data.get("yield_spread"),
                    "fed_rate":   data.get("fed_rate"),
                }, default=str)

            elif name == "get_macro_indicators":
                r = await client.get(f"{base}/api/macro/indicators")
                data = r.json()
                return json.dumps({
                    "cpi_yoy":     data.get("cpi_yoy"),
                    "fed_rate":    data.get("fed_rate"),
                    "yield_spread": data.get("t10y2y"),
                    "unemployment": data.get("unemployment"),
                    "hy_spread":   data.get("hy_spread"),
                    "source":      "FRED",
                }, default=str)

            elif name == "screen_stock":
                sym = args.get("symbol", "").upper()
                r = await client.get(f"{base}/api/v1/screen/{sym}")
                data = r.json()
                return json.dumps({
                    "symbol":      sym,
                    "score":       data.get("composite_score"),
                    "halal":       data.get("halal_status"),
                    "recommend":   data.get("recommendation"),
                    "signals":     data.get("signals", [])[:3],
                    "price":       data.get("price"),
                    "pe_ratio":    data.get("pe_ratio"),
                    "debt_ratio":  data.get("debt_ratio"),
                }, default=str)

            elif name == "get_portfolio":
                r = await client.get(f"{base}/api/v1/portfolio")
                positions = r.json() if isinstance(r.json(), list) else r.json().get("positions", [])
                return json.dumps({
                    "positions": positions[:10],
                    "count":     len(positions),
                }, default=str)

            elif name == "get_etf_holdings":
                sym = args.get("symbol", "SPY").upper()
                r = await client.get(f"{base}/api/etf/{sym}/holdings")
                data = r.json()
                return json.dumps({
                    "symbol":   sym,
                    "holdings": data.get("holdings", [])[:10],
                }, default=str)

            elif name == "get_top_signals":
                min_score = args.get("min_score", 70)
                r = await client.get(f"{base}/screener?limit=10&min_score={min_score}")
                signals = r.json() if isinstance(r.json(), list) else []
                return json.dumps({
                    "signals": [
                        {"symbol": s.get("symbol"), "score": s.get("composite_score"),
                         "halal": s.get("halal_status"), "rec": s.get("recommendation")}
                        for s in signals[:10]
                    ]
                }, default=str)

            elif name == "get_market_context":
                r = await client.get(f"{base}/api/context/bundle")
                d = r.json()
                return json.dumps({
                    "regime":       d.get("regime"),
                    "risk_posture": d.get("risk_posture"),
                    "reasons":      d.get("reasons"),
                    "vix":          d.get("vix"),
                    "macro":        d.get("macro"),
                    "top_sectors":  d.get("top_sectors"),
                    "gates":        d.get("gates"),
                    "version":      d.get("version"),
                }, default=str)

            elif name == "get_sector_rotation":
                r = await client.get(f"{base}/api/chart/rotation")
                d = r.json()
                return json.dumps({
                    "benchmark": d.get("benchmark"),
                    "data":      d.get("data", []),
                }, default=str)

            elif name == "get_etf_sectors":
                sym = args.get("symbol", "SPY").upper()
                r = await client.get(f"{base}/api/etf/{sym}/sectors")
                d = r.json()
                return json.dumps({"symbol": sym, "sectors": d.get("sectors", [])}, default=str)

            elif name == "get_forecast":
                sym = args.get("symbol", "AAPL").upper()
                model = args.get("model") or "arima"
                r = await client.get(f"{base}/api/forecast/{model}?symbol={sym}")
                d = r.json()
                res = d.get("results", [])
                last = res[-1] if res else {}
                return json.dumps({
                    "symbol":                sym,
                    "model":                 d.get("model", model),
                    "last_predicted":        last.get("predicted"),
                    "last_actual":           last.get("actual"),
                    "raw_direction":         d.get("raw_direction"),
                    "conditioned_direction": d.get("conditioned_direction"),
                    "context_posture":       d.get("context_posture"),
                    "metrics":               d.get("metrics"),
                }, default=str)

            elif name == "get_risk_status":
                r = await client.get(f"{base}/api/risk/status")
                return r.text  # already a compact JSON status object

            elif name == "get_backtest_history":
                sym = (args.get("symbol") or "").upper()
                url = f"{base}/api/backtest/history?limit=10"
                if sym:
                    url += f"&symbol={sym}"
                r = await client.get(url)
                d = r.json()
                return json.dumps({"count": d.get("count", 0), "runs": d.get("runs", [])}, default=str)

    except Exception as exc:
        logger.warning("Tool %s failed: %s", name, exc)
        return json.dumps({"error": str(exc), "note": "Tool call failed — answer from general knowledge"})

    return json.dumps({"error": "unknown tool"})


# ── Main chat function ─────────────────────────────────────────────────────────

async def trading_chat(
    messages: list[dict],
    model: str | None = None,
    use_tools: bool = True,
    max_tool_rounds: int = 3,
) -> dict:
    """Full agentic chat loop with automatic tool calling.

    Args:
        messages:        List of {"role": "user"|"assistant", "content": "..."}
        model:           Groq model ID. Defaults to settings.GROQ_MODEL
        use_tools:       Whether to enable function calling
        max_tool_rounds: Max tool call iterations to prevent infinite loops

    Returns:
        {
          "content":    str,          # final assistant message
          "model":      str,
          "tool_calls": list[dict],   # tools that were called + results
          "usage":      dict,         # token usage
          "error":      str | None,
        }
    """
    from app.config import settings
    from groq import AsyncGroq

    if not settings.GROQ_API_KEY:
        return {
            "content": "GROQ_API_KEY is not configured. Add it to Railway environment variables.",
            "model": "none",
            "tool_calls": [],
            "usage": {},
            "error": "missing_api_key",
        }

    groq_model = model or settings.GROQ_MODEL
    client = AsyncGroq(api_key=settings.GROQ_API_KEY)

    # Sanitize incoming messages — Groq rejects any non-standard fields
    # (e.g. the UI's _toolCalls / _usage display metadata). Keep only the
    # OpenAI/Groq-valid keys.
    _ALLOWED = ("role", "content", "tool_calls", "tool_call_id", "name")
    clean_messages: list[dict] = []
    for m in messages:
        cm = {k: m[k] for k in _ALLOWED if k in m and m[k] is not None}
        if "role" not in cm:
            continue
        if "content" not in cm:
            cm["content"] = ""
        clean_messages.append(cm)

    # Build full message list with system prompt
    full_messages = [{"role": "system", "content": SYSTEM_PROMPT}] + clean_messages
    tool_log: list[dict] = []

    for _round in range(max_tool_rounds + 1):
        try:
            kwargs: dict[str, Any] = dict(
                model=groq_model,
                messages=full_messages,
                temperature=0.3,
                max_tokens=1024,
            )
            if use_tools and _round < max_tool_rounds:
                kwargs["tools"] = TOOL_DEFINITIONS
                kwargs["tool_choice"] = "auto"

            response = await client.chat.completions.create(**kwargs)
            msg = response.choices[0].message

            # If no tool calls → we have the final answer
            if not getattr(msg, "tool_calls", None):
                return {
                    "content":    msg.content or "",
                    "model":      groq_model,
                    "tool_calls": tool_log,
                    "usage":      {
                        "prompt_tokens":     response.usage.prompt_tokens,
                        "completion_tokens": response.usage.completion_tokens,
                        "total_tokens":      response.usage.total_tokens,
                    },
                    "error": None,
                }

            # Execute all tool calls in this round
            # Build assistant message dict compatible with groq 0.x and 1.x
            asst_msg: dict = {"role": "assistant", "content": msg.content or ""}
            if msg.tool_calls:
                asst_msg["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name":      tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ]
            full_messages.append(asst_msg)
            for tc in msg.tool_calls:
                fn_name = tc.function.name
                fn_args = json.loads(tc.function.arguments or "{}")
                result  = await _execute_tool(fn_name, fn_args)
                tool_log.append({"tool": fn_name, "args": fn_args, "result": result[:300]})
                full_messages.append({
                    "role":         "tool",
                    "tool_call_id": tc.id,
                    "content":      result,
                })

        except Exception as exc:
            logger.error("Groq chat error (round %d): %s", _round, exc)
            return {
                "content":    f"Sorry, an error occurred: {exc}",
                "model":      groq_model,
                "tool_calls": tool_log,
                "usage":      {},
                "error":      str(exc),
            }

    # Fallback if max rounds hit
    return {
        "content":    "I reached the tool call limit. Please rephrase your question.",
        "model":      groq_model,
        "tool_calls": tool_log,
        "usage":      {},
        "error":      "max_rounds_exceeded",
    }


# ── Quick helpers ──────────────────────────────────────────────────────────────

async def quick_analyze(symbol: str) -> str:
    """One-shot: get a brief AI take on a stock."""
    result = await trading_chat(
        messages=[{"role": "user", "content": f"Give me a brief analysis of {symbol.upper()}. Check its halal status, quality score, and whether it looks like a buy right now."}],
        use_tools=True,
    )
    return result["content"]
