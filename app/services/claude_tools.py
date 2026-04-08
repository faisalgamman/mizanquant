"""Claude AI Agent tool definitions and executors.

Each tool wraps an existing service function so Claude can call it via
the Anthropic tool-use API. Tools are read-only — no trade execution.
"""

import json
import logging
from typing import Any

logger = logging.getLogger("claude_tools")


# ---------------------------------------------------------------------------
# Tool schemas (Anthropic format)
# ---------------------------------------------------------------------------

TOOL_SCHEMAS = [
    {
        "name": "analyze_stock",
        "description": (
            "Run full technical analysis on a US stock: price, EMA, RSI, MACD, "
            "ATR, volume ratio, swing score (0-100), swing signal, support/resistance, "
            "stop loss, take profit levels. Only analyzes halal-compliant stocks."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "US stock ticker symbol (e.g. AAPL, MSFT, BKNG)"
                }
            },
            "required": ["symbol"]
        }
    },
    {
        "name": "check_halal",
        "description": (
            "Check if a stock is Sharia-compliant per AAOIFI standards. "
            "Returns debt ratio, interest ratio, haram sector flag, liquidity ratio, "
            "and overall halal/haram verdict with pass/fail for each screen."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "Stock ticker symbol to check (e.g. AAPL, JPM)"
                }
            },
            "required": ["symbol"]
        }
    },
    {
        "name": "get_buy_signals",
        "description": (
            "Get today's top buy opportunities from the halal stock universe. "
            "Returns up to 15 stocks ranked by swing score with technical signals. "
            "Use this when the user asks for best stocks to buy or opportunities."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "run_consensus",
        "description": (
            "Run the 7-tool AI consensus analysis on a stock: V9 Score, Bollinger Bands, "
            "Stochastic RSI, OBV, ADX, Backtest Win Rate, Monte Carlo simulation. "
            "Returns verdict (STRONG BUY to STRONG SELL), confidence %, individual votes, "
            "and trade levels (entry, SL, TP1/TP2/TP3)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "Stock ticker symbol (e.g. AAPL)"
                }
            },
            "required": ["symbol"]
        }
    },
    {
        "name": "get_portfolio",
        "description": (
            "Get current portfolio status: equity, cash, buying power, open positions, "
            "and P&L for each position. Shows data for a specific strategy account."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "strategy": {
                    "type": "string",
                    "enum": ["HANA", "marem", "mazem", "all"],
                    "description": "Strategy name. Use 'all' for combined view."
                }
            },
            "required": ["strategy"]
        }
    },
    {
        "name": "get_trade_history",
        "description": (
            "Get recent trade execution history: entries, exits, P&L per trade, "
            "strategy used. Returns the most recent trades."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Number of recent trades to return (default 20)"
                }
            },
            "required": []
        }
    },
    {
        "name": "get_performance",
        "description": (
            "Get trading performance metrics: total trades, win rate, average P&L, "
            "Sharpe ratio, max drawdown, profit factor."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "get_risk_status",
        "description": (
            "Get current risk dashboard: open positions count, total exposure, "
            "daily P&L, day trades used (PDT tracker), auto-trading status, "
            "and whether the market is currently open."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
]


# ---------------------------------------------------------------------------
# Strategy name → ID mapping
# ---------------------------------------------------------------------------

_STRATEGY_MAP = {"HANA": "A", "marem": "B", "mazem": "C"}


def _resolve_strategy(name: str) -> str | None:
    """Convert strategy name to ID, or None for 'all'."""
    if name and name.lower() == "all":
        return None
    return _STRATEGY_MAP.get(name, "A")


# ---------------------------------------------------------------------------
# Tool executor functions
# ---------------------------------------------------------------------------

def _exec_analyze_stock(symbol: str) -> dict:
    """Full technical analysis with halal gate."""
    import halal_screener as hs
    symbol = symbol.upper().strip()

    # Halal gate
    is_halal, reason = hs.verify_halal(symbol)
    if not is_halal:
        return {"symbol": symbol, "blocked": True, "reason": reason}

    df = hs.fetch_yf(symbol)
    if df is None or df.empty:
        return {"error": f"Could not fetch market data for {symbol}"}

    result = hs.analyze(symbol, df)
    if result is None:
        return {"error": f"Analysis failed for {symbol}"}
    return result


def _exec_check_halal(symbol: str) -> dict:
    """AAOIFI Sharia compliance check."""
    from app.services.halal_screening import get_halal_status
    symbol = symbol.upper().strip()

    result = get_halal_status(symbol)
    if result is None:
        return {"error": f"Could not retrieve financial data for {symbol}"}

    return {
        "symbol": symbol,
        "status": "HALAL" if result.get("is_halal") else "HARAM",
        "company": result.get("company_name", symbol),
        "sector": result.get("sector", ""),
        "debt_ratio_pct": result.get("debt_ratio", 0),
        "debt_pass": result.get("debt_pass", False),
        "interest_ratio_pct": result.get("interest_ratio", 0),
        "interest_pass": result.get("interest_pass", False),
        "haram_sector": result.get("haram_revenue", False),
        "sector_pass": result.get("haram_pass", False),
        "liquidity_ratio_pct": result.get("liquidity_ratio", 0),
        "liquidity_pass": result.get("liquidity_pass", False),
        "screens_passed": f"{result.get('screens_passed', 0)}/4",
    }


def _exec_get_buy_signals() -> dict:
    """Top buy opportunities from halal universe."""
    import halal_screener as hs

    results = hs.run_screener()
    if not results:
        return {"message": "Screener data not available yet. Try again in 1-2 minutes."}

    # Filter for buys, limit to top 15
    buys = [r for r in results if r.get("swing_score", 0) >= 55][:15]
    watches = [r for r in results if 35 <= r.get("swing_score", 0) < 55][:10]

    return {
        "buy_count": len(buys),
        "watch_count": len(watches),
        "top_buys": [
            {
                "symbol": r["symbol"],
                "price": r["price"],
                "swing_score": r["swing_score"],
                "signal": r["swing_signal"],
                "rsi": r["rsi"],
                "signals": r["signals"],
                "stop_loss": r["stop_loss"],
                "take_profit": r["take_profit"],
            }
            for r in buys
        ],
        "watchlist": [
            {"symbol": r["symbol"], "price": r["price"], "swing_score": r["swing_score"]}
            for r in watches
        ],
    }


def _exec_run_consensus(symbol: str) -> dict:
    """7-tool consensus analysis."""
    import halal_screener as hs

    symbol = symbol.upper().strip()

    try:
        results = hs.run_consensus(symbol, horizon=5, episodes=3)
    except Exception as e:
        logger.error(f"Consensus failed for {symbol}: {e}")
        return {"error": f"Consensus analysis failed: {str(e)}"}

    if not results:
        return {"error": f"No consensus result for {symbol}"}

    result = results[0] if isinstance(results, list) else results

    if not result:
        return {"error": f"No consensus result for {symbol}"}

    return {
        "symbol": symbol,
        "verdict": result.get("Verdict", result.get("verdict", "NEUTRAL")),
        "confidence": result.get("Confidence %", result.get("confidence", 0)),
        "price": result.get("Price", result.get("price", 0)),
        "votes_buy": result.get("Votes BUY", 0),
        "votes_sell": result.get("Votes SELL", 0),
        "votes_hold": result.get("Votes HOLD", 0),
        "stop_loss": result.get("Stop Loss", result.get("stop_loss", 0)),
        "tp1": result.get("TP1", result.get("tp1", 0)),
        "tp2": result.get("TP2", result.get("tp2", 0)),
        "tp3": result.get("TP3", result.get("tp3", 0)),
    }


def _exec_get_portfolio(strategy: str = "all") -> dict:
    """Portfolio status for one or all strategies."""
    from app.services.alpaca_client import get_account, get_positions
    from app.config import STRATEGY_CONFIGS

    sid = _resolve_strategy(strategy)

    if sid:
        # Single strategy
        account = get_account(strategy_id=sid)
        if not account:
            return {"error": f"Cannot connect to {strategy} account"}
        positions = get_positions(strategy_id=sid)
        return {
            "strategy": strategy,
            "equity": account["equity"],
            "cash": account["cash"],
            "buying_power": account["buying_power"],
            "positions": [
                {
                    "symbol": p["symbol"],
                    "qty": p["qty"],
                    "avg_entry": p["avg_entry_price"],
                    "current_price": p["current_price"],
                    "unrealized_pl": p["unrealized_pl"],
                    "unrealized_pl_pct": p["unrealized_plpc"],
                }
                for p in positions
            ],
        }
    else:
        # All strategies
        combined = []
        for s_id, cfg in STRATEGY_CONFIGS.items():
            account = get_account(strategy_id=s_id)
            if account:
                positions = get_positions(strategy_id=s_id)
                combined.append({
                    "strategy": cfg.name,
                    "equity": account["equity"],
                    "cash": account["cash"],
                    "positions_count": len(positions),
                    "positions": [
                        {"symbol": p["symbol"], "qty": p["qty"], "unrealized_pl": p["unrealized_pl"]}
                        for p in positions
                    ],
                })
        return {"strategies": combined}


def _exec_get_trade_history(limit: int = 20) -> dict:
    """Recent trade history."""
    from app.services.trading_engine import get_trade_history
    trades = get_trade_history(limit=limit)
    if not trades:
        return {"message": "No trades executed yet"}
    return {"trades": trades[:limit], "total": len(trades)}


def _exec_get_performance() -> dict:
    """Trading performance metrics."""
    from app.services.trading_engine import get_performance_report
    report = get_performance_report()
    return report


def _exec_get_risk_status() -> dict:
    """Risk dashboard."""
    from app.services.alpaca_client import get_account, get_positions
    from app.services.risk_manager import get_risk_status as _risk_status
    from app.config import STRATEGY_CONFIGS

    sid = next(iter(STRATEGY_CONFIGS), None)
    account = get_account(strategy_id=sid)
    if not account:
        return {"error": "Cannot connect to Alpaca"}
    positions = get_positions(strategy_id=sid)
    risk = _risk_status(account, positions)
    risk["auto_trade_enabled"] = settings.AUTO_TRADE_ENABLED
    return risk


# ---------------------------------------------------------------------------
# Registry: tool name -> executor function
# ---------------------------------------------------------------------------

from app.config import settings

TOOL_REGISTRY: dict[str, Any] = {
    "analyze_stock": lambda **kw: _exec_analyze_stock(kw["symbol"]),
    "check_halal": lambda **kw: _exec_check_halal(kw["symbol"]),
    "get_buy_signals": lambda **kw: _exec_get_buy_signals(),
    "run_consensus": lambda **kw: _exec_run_consensus(kw["symbol"]),
    "get_portfolio": lambda **kw: _exec_get_portfolio(kw.get("strategy", "all")),
    "get_trade_history": lambda **kw: _exec_get_trade_history(kw.get("limit", 20)),
    "get_performance": lambda **kw: _exec_get_performance(),
    "get_risk_status": lambda **kw: _exec_get_risk_status(),
}


def execute_tool(tool_name: str, tool_input: dict) -> str:
    """Execute a tool and return the JSON result string."""
    executor = TOOL_REGISTRY.get(tool_name)
    if not executor:
        return json.dumps({"error": f"Unknown tool: {tool_name}"})

    try:
        result = executor(**tool_input)
        return json.dumps(result, default=str, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Tool {tool_name} failed: {e}")
        return json.dumps({"error": f"Tool execution failed: {str(e)}"})
