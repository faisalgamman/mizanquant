"""Claude AI Agent tool definitions and executors.

Each tool wraps an existing service function so Claude can call it via
the Anthropic tool-use API. Tools are read-only — no trade execution.
"""

import json
import logging
from typing import Any

logger = logging.getLogger("claude_tools")


def _to_openai_tools(anthropic_schemas: list) -> list:
    """Convert Anthropic-format tool schemas to OpenAI/DeepSeek format."""
    openai_tools = []
    for s in anthropic_schemas:
        openai_tools.append({
            "type": "function",
            "function": {
                "name": s["name"],
                "description": s.get("description", ""),
                "parameters": s.get("input_schema", {"type": "object", "properties": {}}),
            }
        })
    return openai_tools


# OpenAI-compatible tool schemas (DeepSeek / Groq / etc.)



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
            "Get current portfolio status: equity, cash, buying power, and EVERY open "
            "position with its qty/entry/current price/P&L. Default 'manual' = the owner's "
            "LIVE IBKR account (use this for 'my portfolio'). 'all' aggregates the automated "
            "Alpaca strategies — only use it when the user explicitly asks about HANA/marem/mazem."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "strategy": {
                    "type": "string",
                    "enum": ["HANA", "marem", "mazem", "manual", "all"],
                    "description": "Strategy name. Default 'manual' (IBKR account). Use 'all' for combined view."
                }
            },
            "required": []
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
    {
        "name": "get_deep_picks",
        "description": "Get screener deep-picks with composite scores, conviction, RRG rotation quadrant, and multi-confirmation status for top symbols or a specific symbol.",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Optional: single symbol to query. Omit for top-10 ranked."},
                "top_n": {"type": "integer", "description": "Number of top symbols to return (default 10, max 20)"}
            }
        }
    },
    {
        "name": "get_measurement_facts",
        "description": "Get MEASURED facts from OUR system: signal accuracy (OVERALL + BUY-SIDE), paper-trade graduation status, active USX version+weights, and static measured ICs from 8,849 buy outcomes (ONE month — weak evidence). Always cite this with its caveat: ~51% accuracy, no price prediction.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "get_accuracy_report",
        "description": "Get trailing accuracy of each signal source (technical, fundamental, ML, consensus) over the last N days. Used to weight sources dynamically.",
        "input_schema": {
            "type": "object",
            "properties": {
                "period_days": {"type": "integer", "description": "Lookback period in days (default 30)"}
            }
        }
    },
    {
        "name": "record_recommendation",
        "description": "Record the agent's trading recommendation with rationale and snapshot BEFORE execution. Call this when you recommend a BUY or STRONG BUY on a symbol.",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Stock ticker"},
                "verdict": {"type": "string", "description": "BUY or STRONG BUY"},
                "confidence": {"type": "number", "description": "Confidence 0-100"},
                "rationale": {"type": "string", "description": "Why you recommend this trade"}
            },
            "required": ["symbol", "verdict", "confidence", "rationale"]
        }
    },
    {
        "name": "get_signal_agreement",
        "description": (
            "Cross-check ONE symbol across INDEPENDENT sources (weekly technical, monthly composite, "
            "Monte-Carlo forecast direction) and return a CALIBRATED confidence "
            "(INSUFFICIENT / LOW / LOW-MEDIUM / MEDIUM — never higher; the system is ~51% coin-flip) "
            "plus any CONFLICTS (e.g. BUY vs falling RSI, BUY vs negative forecast). "
            "Use this to STATE confidence — never invent a confidence number yourself."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Stock ticker symbol (e.g. AAPL)"}
            },
            "required": ["symbol"]
        }
    },
]
DEEPSEEK_TOOL_SCHEMAS = _to_openai_tools(TOOL_SCHEMAS)


# ---------------------------------------------------------------------------
# Strategy name → ID mapping
# ---------------------------------------------------------------------------

_STRATEGY_MAP = {"HANA": "A", "marem": "B", "mazem": "C", "manual": "MANUAL"}


def _resolve_strategy(name: str) -> str | None:
    """Convert strategy name to ID, or None for 'all'. Default: MANUAL (IBKR)."""
    if name and name.lower() == "all":
        return None
    return _STRATEGY_MAP.get(name, _STRATEGY_MAP.get("manual", "MANUAL"))


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

    # Data completeness — the agent must NOT assert a clean "halal" when the ratios
    # that the verdict depends on are missing (they render as "—" in the UI).
    debt = result.get("debt_ratio")
    interest = result.get("interest_ratio")
    data_complete = debt is not None and interest is not None
    return {
        "symbol": symbol,
        "status": "HALAL" if result.get("is_halal") else "HARAM",
        "company": result.get("company_name", symbol),
        "sector": result.get("sector", ""),
        "debt_ratio_pct": debt if debt is not None else "—",
        "debt_pass": result.get("debt_pass", False),
        "interest_ratio_pct": interest if interest is not None else "—",
        "interest_pass": result.get("interest_pass", False),
        "haram_sector": result.get("haram_revenue", False),
        "sector_pass": result.get("haram_pass", False),
        "liquidity_ratio_pct": result.get("liquidity_ratio", 0),
        "liquidity_pass": result.get("liquidity_pass", False),
        "screens_passed": f"{result.get('screens_passed', 0)}/4",
        "halal_confidence": result.get("halal_confidence", "partial" if not data_complete else "high"),
        "data_complete": data_complete,
        "data_note": "Assert 'halal' only if status=HALAL AND data_complete=true AND "
                     "halal_confidence='high'. Otherwise say 'حلال مبدئياً — بيانات ناقصة'.",
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
        "scanner": "weekly",
        "scanner_note": "WEEKLY swing scanner (technical swing_score). NOT the Monthly composite "
                        "scanner — for monthly use get_deep_picks; never present these as monthly.",
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


def _exec_get_portfolio(strategy: str = "manual") -> dict:
    """Portfolio status for one or all strategies. Uses broker factory per-strategy."""
    from app.services.broker.factory import get_broker
    from app.config import STRATEGY_CONFIGS

    sid = _resolve_strategy(strategy)

    def _acct_pos(s_id):
        b = get_broker(strategy_id=s_id)
        if b is None:
            return None, []
        try:
            acct = b.get_account(strategy_id=s_id)
        except Exception:
            acct = None
        try:
            pos = b.get_positions(strategy_id=s_id) or []
        except Exception:
            pos = []
        return acct, pos

    if sid:
        # Single strategy
        account, positions = _acct_pos(sid)
        if not account:
            return {"error": f"Cannot connect to {strategy} account (broker offline)"}
        return {
            "strategy": strategy,
            "equity": account["equity"],
            "cash": account["cash"],
            "buying_power": account["buying_power"],
            "positions": [
                {
                    "symbol": p["symbol"],
                    "qty": p["qty"],
                    "avg_entry": p.get("avg_entry_price", p.get("avg_cost", 0)),
                    "current_price": p.get("current_price", 0),
                    "unrealized_pl": p.get("unrealized_pl", 0),
                    "unrealized_pl_pct": p.get("unrealized_plpc", 0),
                }
                for p in positions
            ],
        }
    else:
        # All strategies
        combined = []
        for s_id, cfg in STRATEGY_CONFIGS.items():
            account, positions = _acct_pos(s_id)
            if account:
                combined.append({
                    "strategy": cfg.name,
                    "equity": account["equity"],
                    "cash": account["cash"],
                    "positions_count": len(positions),
                    "positions": [
                        {"symbol": p["symbol"], "qty": p["qty"], "unrealized_pl": p.get("unrealized_pl", 0)}
                        for p in positions
                    ],
                })
        return {"strategies": combined}


def _exec_get_trade_history(limit: int = 20) -> dict:
    """Recent trade history."""
    from app.services.trade_history import get_trade_history
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
    """Risk dashboard — uses IBKR MANUAL account."""
    from app.services.broker.factory import get_broker
    from app.services.risk_manager import get_risk_status as _risk_status

    b = get_broker(strategy_id="MANUAL")
    if not b:
        return {"error": "IBKR broker unavailable"}
    try:
        account = b.get_account(strategy_id="MANUAL")
    except Exception:
        return {"error": "Cannot connect to IBKR account"}
    if not account:
        return {"error": "Cannot connect to IBKR account"}
    try:
        positions = b.get_positions(strategy_id="MANUAL") or []
    except Exception:
        positions = []
    risk = _risk_status(account, positions)
    risk["auto_trade_enabled"] = settings.AUTO_TRADE_ENABLED
    return risk


# ---------------------------------------------------------------------------
# Registry: tool name -> executor function
# ---------------------------------------------------------------------------

from app.config import settings

def _exec_get_deep_picks(symbol: str = None, top_n: int = 10) -> dict:
    """Return deep-picks with composite/conviction/RRG for top symbols or a specific one."""
    try:
        import halal_screener as hs
        from app.services.conviction_engine import detect_confirmations
        results = []
        universe = list(hs._universe_symbols())[:80]
        # Use the screener compute path
        enriched = []
        for sym in (universe if symbol is None else [symbol.upper()]):
            try:
                row = hs.compute_one(sym) if hasattr(hs, "compute_one") else None
                if row is None:
                    row = hs._score_one(sym) if hasattr(hs, "_score_one") else {}
                if not row:
                    continue
                confirms = detect_confirmations(row, row.get("rotation_quadrant", "unknown"))
                enriched.append({
                    "symbol": sym,
                    "composite_score": row.get("composite_score"),
                    "context_adjusted_score": row.get("context_adjusted_score"),
                    "conviction_score": row.get("conviction_score"),
                    "rotation_quadrant": row.get("rotation_quadrant"),
                    "sector": row.get("sector"),
                    "f_grade": row.get("f_grade"),
                    "confirmations": confirms,
                    "confirmation_count": len(confirms),
                    "strong_buy_qualified": len(confirms) >= 3,
                })
            except Exception:
                continue
        enriched.sort(key=lambda x: x.get("composite_score") or 0, reverse=True)
        if not enriched:
            return {"scanner": "monthly", "status": "not_ready", "count": 0, "picks": [],
                    "message": "Monthly composite scan has no results yet (still warming / cold "
                               "cache). Tell the user the MONTHLY scan is not ready — do NOT "
                               "substitute weekly (get_buy_signals) results."}
        return {"scanner": "monthly",
                "scanner_note": "MONTHLY composite scanner (fundamental+technical+sentiment). "
                                "Different scale from the weekly swing_score.",
                "status": "ok", "count": len(enriched[:top_n]), "picks": enriched[:top_n]}
    except Exception as e:
        return {"scanner": "monthly", "status": "error", "message": str(e)}


def _exec_get_signal_agreement(symbol: str) -> dict:
    """Cross-check a symbol across independent sources → a CALIBRATED confidence.

    Not a confident tone — measured agreement only. The heavy run_consensus is NOT
    called here (it is slow); use it separately if needed. Sources that are cold or
    unavailable are reported as such, never guessed. The honest ceiling is MEDIUM:
    the system is ~51% (coin-flip) on one month of data and does not predict prices.
    """
    import halal_screener as hs
    symbol = symbol.upper().strip()
    sources: dict = {}
    bull = 0
    avail = 0
    conflicts: list[str] = []

    # 1) Weekly technical (fast, reliable)
    weekly_buy = False
    try:
        df = hs.fetch_yf(symbol)
        row = hs.analyze(symbol, df) if df is not None else None
        if row:
            sig = str(row.get("swing_signal", "")).upper()
            weekly_buy = "BUY" in sig
            sources["weekly_technical"] = {
                "signal": row.get("swing_signal"), "swing_score": row.get("swing_score"),
                "rsi": row.get("rsi"), "rsi_trend": row.get("rsi_trend"),
            }
            avail += 1
            if weekly_buy:
                bull += 1
                if row.get("rsi_trend") == "Falling":
                    conflicts.append("weekly BUY but RSI is falling (possible bearish divergence)")
        else:
            sources["weekly_technical"] = {"available": False}
    except Exception:
        sources["weekly_technical"] = {"available": False}

    # 2) Monthly composite (may be cold / not ready)
    try:
        dp = _exec_get_deep_picks(symbol=symbol, top_n=1)
        picks = dp.get("picks") or []
        if picks and picks[0].get("composite_score") is not None:
            comp = picks[0]["composite_score"]
            sources["monthly_composite"] = {"composite_score": comp,
                                            "confirmations": picks[0].get("confirmation_count")}
            avail += 1
            if comp >= 65:
                bull += 1
        else:
            sources["monthly_composite"] = {"available": False, "note": "monthly scan not ready"}
    except Exception:
        sources["monthly_composite"] = {"available": False}

    # 3) Forecast direction (fast Monte-Carlo — magnitude/range, not a price call)
    try:
        from app.services.price_forecast import monte_carlo_forecast
        from app.services import market_data as md
        df2 = md.fetch(symbol, period="1y")
        prices = df2["close"].tolist() if df2 is not None and len(df2) > 30 else None
        if prices:
            fc = monte_carlo_forecast(prices, 20)
            ec = fc.get("expected_change_pct")
            if ec is not None:
                avail += 1
                sources["forecast"] = {"expected_change_pct": ec,
                                       "prob_profit_pct": fc.get("prob_profit_pct")}
                if ec > 0:
                    bull += 1
                elif ec < 0 and weekly_buy:
                    conflicts.append("weekly BUY but Monte-Carlo expected change is negative")
        else:
            sources["forecast"] = {"available": False}
    except Exception:
        sources["forecast"] = {"available": False}

    # Calibrated confidence — conservative by design; ceiling MEDIUM.
    if avail < 2:
        conf = "INSUFFICIENT"
    elif conflicts:
        conf = "LOW"
    elif bull == avail and bull >= 3:
        conf = "MEDIUM"
    elif bull >= 2:
        conf = "LOW-MEDIUM"
    else:
        conf = "LOW"

    return {
        "symbol": symbol,
        "sources_available": avail,
        "bullish_sources": bull,
        "calibrated_confidence": conf,
        "conflicts": conflicts,
        "sources": sources,
        "caveat": ("Calibrated confidence is RELATIVE source agreement only. System accuracy is "
                   "~51% (coin-flip) on ONE month of data and does NOT predict prices. Real "
                   "statistical confidence requires paper-ledger graduation (not yet reached)."),
    }


def _exec_get_accuracy_report(period_days: int = 30) -> dict:
    """Return trailing accuracy per signal source."""
    try:
        from app.services.signal_tracker import get_accuracy_report
        report = get_accuracy_report(period_days)
        return {"status": "ok", "period_days": period_days, "sources": report}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def _exec_record_recommendation(symbol: str, verdict: str,
                                confidence: float, rationale: str) -> dict:
    """Persist agent recommendation to AgentDecision table."""
    try:
        from app.db.connection import get_session
        from app.db.models import AgentDecision
        session = get_session()
        try:
            dec = AgentDecision(
                symbol=symbol.upper(),
                verdict=verdict.upper(),
                confidence=confidence,
                rationale=rationale,
            )
            session.add(dec)
            session.commit()
            dec_id = dec.id
        finally:
            session.close()
        return {"status": "ok", "decision_id": dec_id,
                "message": f"Recommendation recorded for {symbol.upper()}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# --- Static measured facts (2026-06, 8,849 buy outcomes, ONE month) ---

STATIC_MEASURED = {
    "asof": "2026-06",
    "sample": "8,849 buy outcomes, ONE month — weak evidence",
    "useful_ics": {"macd_hist_rising": 0.15, "rs_spy_20": 0.05, "rsi": 0.085},
    "negative_ics": {"prox_52w": -0.21, "ema50_above_200": -0.17, "adx": -0.07},
    "sell_side": "38% accuracy — historically inverted",
    "forecast_agree": "PF 4.34 vs 2.48 (n=902, small)",
}


def _exec_get_measurement_facts() -> dict:
    """Return measured facts from OUR system — live calls + static IC block."""
    result = {"static_measured": STATIC_MEASURED}

    # Signal accuracy (OVERALL + BUY-SIDE)
    try:
        from app.services.signal_tracker import get_accuracy_report
        report = get_accuracy_report(30)
        overall = next((r for r in report if r.get("Source") == "OVERALL"), None)
        buy_side = next((r for r in report if r.get("Source") == "BUY-SIDE"), None)
        result["signal_accuracy"] = {
            "overall": overall,
            "buy_side": buy_side,
            "note": "Buy-side is the honest split; sell-side historically inverted (~38%).",
        }
    except Exception as e:
        result["signal_accuracy"] = {"error": str(e)}

    # Paper-trade graduation (PV + PVM)
    try:
        from app.services.paper_trade_gate import paper_trade_status
        pv = paper_trade_status("PV").as_dict()
        pvm = paper_trade_status("PVM").as_dict()
        result["paper_trade"] = {"PV": pv, "PVM": pvm}
    except Exception as e:
        result["paper_trade"] = {"error": str(e)}

    # USX active version + weights
    try:
        from app.services import usx_layer
        ver = usx_layer.USX_ACTIVE_VERSION
        if ver == "v2":
            weights = {
                "MACD": usx_layer.V2_W_MACD, "RS20": usx_layer.V2_W_RS20,
                "RSI": usx_layer.V2_W_RSI, "EMA50": usx_layer.V2_W_EMA50,
                "SQUEEZE": usx_layer.V2_W_SQUEEZE, "VOLDRY": usx_layer.V2_W_VOLDRY,
                "52W": usx_layer.V2_W_52W, "ADX": usx_layer.V2_W_ADX,
            }
        else:
            weights = {
                "SQUEEZE": usx_layer.V1_W_SQUEEZE, "RS": usx_layer.V1_W_RS,
                "VOLDRY": usx_layer.V1_W_VOLDRY, "52W": usx_layer.V1_W_52W,
                "MACD": usx_layer.V1_W_MACD, "ADX": usx_layer.V1_W_ADX,
            }
        result["usx"] = {"active_version": ver, "weights": weights}
    except Exception as e:
        result["usx"] = {"error": str(e)}

    return result


TOOL_REGISTRY: dict[str, Any] = {
    "analyze_stock": lambda **kw: _exec_analyze_stock(kw["symbol"]),
    "check_halal": lambda **kw: _exec_check_halal(kw["symbol"]),
    "get_buy_signals": lambda **kw: _exec_get_buy_signals(),
    "run_consensus": lambda **kw: _exec_run_consensus(kw["symbol"]),
    "get_portfolio": lambda **kw: _exec_get_portfolio(kw.get("strategy", "manual")),
    "get_trade_history": lambda **kw: _exec_get_trade_history(kw.get("limit", 20)),
    "get_performance": lambda **kw: _exec_get_performance(),
    "get_risk_status": lambda **kw: _exec_get_risk_status(),
    "get_deep_picks": lambda **kw: _exec_get_deep_picks(kw.get("symbol"), kw.get("top_n", 10)),
    "get_signal_agreement": lambda **kw: _exec_get_signal_agreement(kw["symbol"]),
    "get_accuracy_report": lambda **kw: _exec_get_accuracy_report(kw.get("period_days", 30)),
    "get_measurement_facts": lambda **kw: _exec_get_measurement_facts(),
    "record_recommendation": lambda **kw: _exec_record_recommendation(kw["symbol"], kw["verdict"], kw["confidence"], kw["rationale"]),
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
