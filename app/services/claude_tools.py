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
            "Analyze a US stock using the SAME data the dashboard Analyze card shows: "
            "Monthly composite score + verdict, price, change, the technical factor "
            "breakdown (RS-vs-SPY, Trend, Regime, MACD, Volume, RSI, ADX, Bollinger, VWAP, "
            "Gap), AAOIFI halal verdict, and ATR-based trade levels (entry/stop/take-profit). "
            "Report the numbers it returns verbatim — they match the on-screen card."
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
        "name": "get_explosion_picks",
        "description": "RESEARCH-ONLY day-trade 'Explosion' scanner: ranks ALL liquid US stocks by a "
                       "technical explosion score (relative volume + momentum + volatility + gap) on "
                       "daily bars, for day/short-term trading research. NOT halal-screened and NOT "
                       "tradeable — never present these as buy recommendations. Use ONLY when the user "
                       "explicitly asks about the Explosion / day-trade scanner.",
        "input_schema": {
            "type": "object",
            "properties": {
                "top_n": {"type": "integer", "description": "Number of top movers to return (default 10, max 20)"}
            }
        }
    },
    {
        "name": "get_stock_news",
        "description": "Recent news headlines for a stock (FMP primary, Alpaca fallback). Call this "
                       "when recommending or explaining a stock, to tie any DIRECTLY-relevant headline "
                       "(earnings, guidance, product, M&A, regulatory, analyst action) to the rationale. "
                       "News is CONTEXT, not a price predictor — never fabricate causation.",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Stock symbol"},
                "limit": {"type": "integer", "description": "Max headlines (default 6, max 12)"}
            },
            "required": ["symbol"]
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
            "Cross-check ONE symbol across INDEPENDENT sources (technical — the SAME weighted_score "
            "lens as the dashboard Analyze card, monthly composite, Monte-Carlo forecast direction) "
            "and return a CALIBRATED confidence "
            "(INSUFFICIENT / LOW / LOW-MEDIUM / MEDIUM — never higher; the system is ~51% coin-flip) "
            "plus any CONFLICTS (e.g. technical-vs-forecast, technical-vs-composite). "
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
    {
        "name": "get_signal_calibration",
        "description": (
            "Measure whether a scanner's SCORE actually predicts forward returns, from the "
            "paper-validation ledger: per-score-band win-rate & average return, the "
            "score-vs-return rank correlation (+1 = higher score → higher return), a "
            "monotonicity flag, and (monthly) per-component attribution. Use when the user "
            "asks whether the scoring 'works' / is calibrated / which factors carry signal. "
            "READ-ONLY measurement — small samples are directional, not proof; always surface "
            "closed_trades and sufficient_sample."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "scanner": {
                    "type": "string",
                    "enum": ["weekly", "monthly", "pairs"],
                    "description": "Which scanner's ledger to measure. Default 'weekly'."
                }
            },
            "required": []
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
    """Analyze a stock from the SAME sources the dashboard Analyze card shows.

    Earlier this tool used a legacy yfinance + swing-score path, so the agent
    reported different numbers than the on-screen card (e.g. PWR: agent swing
    10/100 @ $706 vs card composite 75/100 STRONG BUY @ $721). It now reads the
    identical sources the Analyze column uses:
      • headline composite + verdict + price + change → cached Monthly-composite
        row (screener_deep_picks — exactly what the scanner/card displays) when
        the symbol is among the live picks;
      • technical factor breakdown (RS/Trend/Regime/MACD/Volume/RSI/ADX/BB/VWAP/
        Gap) → weighted_score  (same engine as /api/v1/scoring/weighted);
      • headline + halal verdict for non-pick symbols → _analyze_smart;
      • trade levels → generate_trade_plan  (same as /api/v1/trade/plan).
    Market data comes from Alpaca/Tiingo (the card's source), not yfinance.
    """
    import asyncio
    import concurrent.futures

    symbol = symbol.upper().strip()
    out: dict = {"symbol": symbol,
                 "data_source": "dashboard Analyze card (composite + weighted_score + trade plan)"}

    # ── 1) Cached Monthly-composite row — the EXACT numbers the card header shows.
    #    The UI scanner list is top-25 by composite (App.jsx → deep-picks?limit=25);
    #    a symbol the user clicked to analyze is therefore in this list.
    crow: dict = {}
    try:
        from app.workspace_server import screener_deep_picks

        def _dp():
            return asyncio.run(screener_deep_picks(limit=25, use_cache="true"))

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            dp = ex.submit(_dp).result(timeout=40)
        for r in (dp.get("results") or []):
            if str(r.get("symbol", "")).upper() == symbol:
                crow = r
                break
    except Exception as e:
        logger.debug("analyze_stock deep-picks lookup failed for %s: %s", symbol, e)
    out["in_scanner"] = bool(crow)

    # ── 2) Market data — Alpaca/Tiingo, the SAME source the card uses (NOT yfinance).
    df = spy_df = None
    try:
        from app.services.market_data import fetch
        df = fetch(symbol, period="6mo")
        spy_df = fetch("SPY", period="6mo")
    except Exception as e:
        logger.debug("analyze_stock data fetch failed for %s: %s", symbol, e)
    if df is None and not crow:
        return {"symbol": symbol, "error": f"Could not fetch market data for {symbol}"}

    # ── 3) Technical factor breakdown — identical to the card's bars (37/109 style).
    _MAX = {"rs": 25, "trend": 15, "regime": 15, "macd": 15, "volume": 10,
            "rsi": 8, "adx": 7, "bb": 5, "vwap": 5, "gap": 4}
    factors: dict = {}
    technical_total = technical_max = technical_signal = None
    if df is not None:
        try:
            from app.services.scoring import weighted_score, _score_to_dict
            ws = _score_to_dict(weighted_score(df, spy_df=spy_df))
            comps = ws.get("components") or {}
            factors = {k: {"score": comps.get(k), "max": _MAX[k]} for k in _MAX if k in comps}
            # The card's "37/109" sub-total is the SUM of component points (not the
            # gated/bonused ws.total), so mirror that exactly.
            technical_total = sum(int(comps[k]) for k in _MAX
                                  if k in comps and isinstance(comps[k], (int, float)))
            technical_max = sum(_MAX[k] for k in _MAX if k in comps) or 109
            technical_signal = ws.get("signal")
        except Exception as e:
            logger.debug("analyze_stock weighted_score failed for %s: %s", symbol, e)

    # ── 4) Headline (composite + verdict) + price/change/halal/sector.
    #    Prefer the cached scanner row (what the card shows); else compute live.
    smart: dict = {}
    if not crow and df is not None:
        try:
            from app.workspace_server import _analyze_smart
            smart = _analyze_smart(symbol, spy_df=spy_df) or {}
        except Exception as e:
            logger.debug("analyze_stock _analyze_smart failed for %s: %s", symbol, e)

    src = crow or smart
    composite_score = (src.get("composite_score")
                       if src.get("composite_score") is not None else src.get("smart_score"))
    verdict = src.get("signal_composite") or src.get("signal")
    price = src.get("price")
    change_pct = src.get("change_pct")
    company = src.get("company") or src.get("name") or symbol
    sector = src.get("sector") or ""
    halal_verdict = src.get("halal_verdict") or ("halal" if src.get("is_halal") else None)
    halal_reasons = src.get("halal_reasons") or []
    if not halal_verdict:
        try:
            from app.services.halal_screening import get_halal_status
            hstat = get_halal_status(symbol) or {}
            halal_verdict = hstat.get("halal_verdict") or ("halal" if hstat.get("is_halal") else None)
            halal_reasons = hstat.get("halal_reasons") or halal_reasons
        except Exception:
            pass

    # ── 5) Trade levels — same generator as the card's /api/v1/trade/plan.
    plan: dict = {}
    if df is not None:
        try:
            from app.services.trade_plan import generate_trade_plan
            plan = generate_trade_plan(df) or {}
        except Exception as e:
            logger.debug("analyze_stock trade_plan failed for %s: %s", symbol, e)

    # ── 6) Reconciliation note — mirrors the card's two-lens reconciliation.
    tech_strong = (technical_total is not None and technical_max
                   and technical_total / technical_max >= 0.65)
    verdict_buy = bool(verdict) and "BUY" in str(verdict).upper()
    if tech_strong and not verdict_buy:
        recon = "Technically strong, but fundamentals/system gates temper the net verdict."
    elif (not tech_strong) and verdict_buy:
        recon = "Net BUY thanks to fundamentals despite weak technicals."
    else:
        recon = ""

    # ── 7) External signals — analyst consensus + insider trades + next earnings, from the
    #    SAME helper the Analyze card uses, so the agent and the card never disagree.
    try:
        from app.services.external_signals import get_external_signals
        ext = get_external_signals(symbol)
    except Exception as e:
        logger.debug("analyze_stock external signals failed for %s: %s", symbol, e)
        ext = {}

    out.update({
        "company": company,
        "sector": sector,
        "analyst_consensus": ext.get("analyst"),
        "insider_activity": ext.get("insider"),
        "earnings": ext.get("earnings"),
        "price": price,
        "change_pct": change_pct,
        # Headline (the card's big number) — Monthly composite when available.
        "composite_score": composite_score,
        "verdict": verdict,
        "composite_max": 100,
        # Technical lens (the card's factor bars).
        "technical_score": technical_total,
        "technical_max": technical_max,
        "technical_signal": technical_signal,
        "factors": factors,
        "rsi": (src.get("momentum_details") or {}).get("rsi"),
        "atr_pct": src.get("atr_pct"),
        "ext_pct": src.get("ext_pct"),
        # Halal — AAOIFI three-state, same verdict the card badge shows.
        "halal_verdict": halal_verdict,
        "halal_reasons": halal_reasons,
        "tradeable_halal": halal_verdict == "halal",
        # Trade levels — same as the card's trade plan.
        "entry": plan.get("strategy_entry") or plan.get("entry_price") or plan.get("entry"),
        "stop_loss": plan.get("strategy_stop") or plan.get("stop_loss") or plan.get("stop"),
        "take_profit": plan.get("strategy_tp1") or plan.get("take_profit") or plan.get("tp1"),
        "rr_ratio": plan.get("rr_ratio") or plan.get("strategy_rr"),
        "reconciliation": recon,
        "consistency_note": (
            "These numbers come from the SAME sources as the dashboard Analyze card "
            "(Monthly composite row + weighted_score + trade plan + Finnhub external signals). "
            "Report them as-is — do NOT recompute from another source. Any difference from the "
            "card is cache-refresh timing, not a different engine. When the composite verdict "
            "(fundamentals) and the technical bars disagree, state BOTH lenses honestly. "
            "analyst_consensus / insider_activity / earnings ARE available here (Finnhub) — when "
            "their known=true, cite them in the analysis (e.g. analyst buy/hold/sell split, heavy "
            "insider selling, earnings within the blackout window); when known=false, say the "
            "data is unavailable — never invent or deny it generically."),
    })
    return out


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
    """Return deep-picks from the SAME warm composite cache the UI Monthly Scanner shows.

    The previous version recomputed via hs.compute_one / hs._score_one (which do not
    exist) and ALWAYS returned empty — so the agent reported "monthly not ready" even
    when the UI was full of picks. We now read the real /api/screener/deep-picks
    endpoint function (the pattern paper_validation already uses), run in a fresh
    thread so a new event loop is safe regardless of caller context.
    """
    import asyncio
    import concurrent.futures
    limit = max(int(top_n or 10), 1)
    try:
        from app.workspace_server import screener_deep_picks

        def _call():
            return asyncio.run(screener_deep_picks(limit=max(limit, 15), use_cache="true"))

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            res = ex.submit(_call).result(timeout=40)
    except Exception as e:
        return {"scanner": "monthly", "status": "error", "message": str(e)}

    rows = res.get("results") if isinstance(res, dict) else None
    if not rows:
        return {"scanner": "monthly", "status": "not_ready", "count": 0, "picks": [],
                "scan_pct": res.get("scan_pct") if isinstance(res, dict) else None,
                "message": "Monthly composite scan has no results yet (cold cache / still "
                           "scanning). Tell the user the MONTHLY scan is not ready — do NOT "
                           "substitute weekly (get_buy_signals) results."}
    if symbol:
        sym = symbol.upper().strip()
        rows = [r for r in rows if str(r.get("symbol", "")).upper() == sym] or rows[:1]
    picks = [{
        "symbol": r.get("symbol"),
        "composite_score": r.get("composite_score") if r.get("composite_score") is not None
                           else r.get("smart_score"),
        "signal": r.get("signal") or r.get("signal_composite"),
        "score_tech": r.get("score_tech"),
        "score_fund": r.get("score_fund"),
        "f_grade": r.get("f_grade"),
        "sector": r.get("sector"),
        "conviction_score": r.get("conviction_score"),
        "rotation_quadrant": r.get("rotation_quadrant"),
        "confirmation_count": r.get("confirmation_count"),
        "is_halal": r.get("is_halal", True),
        "price": r.get("price"),
    } for r in rows[:limit]]
    return {"scanner": "monthly",
            "scanner_note": "MONTHLY composite scanner (fundamental+technical+sentiment) — same "
                            "source as the UI Monthly Scanner.",
            "source": res.get("source"), "status": "ok", "count": len(picks), "picks": picks}


def _exec_get_explosion_picks(top_n: int = 10) -> dict:
    """RESEARCH-ONLY day-trade 'Explosion' scanner (technical-only, ALL liquid US stocks).

    Reads the warm daytrade cache via /api/screener/daytrade. On a cold cache the endpoint
    kicks a background scan and returns immediately, so we report 'not_ready' (don't block).
    NOT halal-screened and NOT tradeable — the scanner_note tells the agent to treat it as
    research and never present it as buy recommendations.
    """
    import asyncio
    import concurrent.futures
    limit = max(min(int(top_n or 10), 20), 1)
    try:
        from app.workspace_server import screener_daytrade

        def _call():
            return asyncio.run(screener_daytrade(limit=max(limit, 15)))

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            res = ex.submit(_call).result(timeout=40)
    except Exception as e:
        return {"scanner": "explosion", "status": "error", "message": str(e)}

    rows = res.get("results") if isinstance(res, dict) else None
    if not rows:
        return {"scanner": "explosion", "status": "not_ready", "count": 0, "picks": [],
                "message": "The day-trade Explosion scan is not ready yet (cold cache / still "
                           "scanning the liquid US universe, ~1-3 min). It is RESEARCH ONLY — "
                           "technical, NOT halal-screened, NOT tradeable."}
    picks = [{
        "symbol": r.get("symbol"),
        "explosion_score": r.get("explosion_score"),
        "change_pct": r.get("change_pct"),
        "rvol": r.get("rvol"),
        "gap_pct": r.get("gap_pct"),
        "halal_verdict": r.get("halal_verdict"),  # cached flag only; null = not screened
    } for r in rows[:limit]]
    return {"scanner": "explosion",
            "scanner_note": "RESEARCH-ONLY day-trade 'explosion' scanner — technical-only "
                            "(RVOL + momentum + volatility + gap) over ALL liquid US stocks. NOT "
                            "halal-screened and NOT tradeable: NEVER present these as buy "
                            "recommendations and never route them to a trade; the buy path is "
                            "halal-gated. Use only for technical/day-trade research when asked.",
            "research_only": True, "status": "ok", "count": len(picks), "picks": picks}


def _exec_get_stock_news(symbol: str, limit: int = 6) -> dict:
    """Recent headlines for a symbol (FMP primary, Alpaca fallback), normalized.

    Returned so the agent can CONNECT a relevant headline to its rationale. News is context,
    not a price predictor — the note tells the agent not to fabricate causation.
    """
    sym = (symbol or "").upper().strip()
    if not sym:
        return {"status": "error", "message": "symbol required"}
    limit = max(min(int(limit or 6), 12), 1)

    raw = []
    src = "fmp"
    try:
        from app.services.fmp_client import fmp_client
        raw = fmp_client.get_stock_news(sym, limit=limit) or []
    except Exception:
        raw = []
    if not raw:
        try:
            from app.services.market_data import get_alpaca_news
            raw = get_alpaca_news(sym, limit=limit) or []
            src = "alpaca"
        except Exception:
            raw = []

    items = []
    for n in (raw or [])[:limit]:
        title = n.get("title") or n.get("headline") or ""
        if not title:
            continue
        items.append({
            "title": title,
            "text": (n.get("text") or n.get("summary") or "")[:240],
            "date": n.get("publishedDate") or n.get("published") or n.get("date") or "",
            "source": n.get("site") or n.get("publisher") or src,
            "url": n.get("url") or n.get("link") or "",
        })
    if not items:
        return {"symbol": sym, "status": "no_news", "count": 0, "news": [],
                "message": "No recent news available (or the news source is cold). Say the "
                           "recommendation is technical/quant-driven, not news-driven."}
    return {"symbol": sym, "status": "ok", "source": src, "count": len(items), "news": items,
            "note": "Recent headlines = CONTEXT for the rationale, NOT a price predictor. Cite a "
                    "headline only if it plausibly relates to the move/recommendation; never fabricate "
                    "causation. If none are relevant, say the call is technical/quant-driven."}


def _exec_get_signal_agreement(symbol: str) -> dict:
    """Cross-check a symbol across independent sources → a CALIBRATED confidence.

    Not a confident tone — measured agreement only. The heavy run_consensus is NOT
    called here (it is slow); use it separately if needed. Sources that are cold or
    unavailable are reported as such, never guessed. The honest ceiling is MEDIUM:
    the system is ~51% (coin-flip) on one month of data and does not predict prices.
    """
    symbol = symbol.upper().strip()
    sources: dict = {}
    bull = 0
    avail = 0
    conflicts: list[str] = []

    # 1) Technical — the SAME weighted_score lens the dashboard Analyze card shows
    #    (RS/Trend/Regime/MACD/Volume/RSI/ADX/Bollinger/VWAP/Gap), fed by Alpaca data.
    #    Replaces the legacy yfinance swing read, which contradicted the card.
    tech_bull = False
    _MAX = {"rs": 25, "trend": 15, "regime": 15, "macd": 15, "volume": 10,
            "rsi": 8, "adx": 7, "bb": 5, "vwap": 5, "gap": 4}
    try:
        from app.services.market_data import fetch as _md_fetch
        from app.services.scoring import weighted_score, _score_to_dict
        _df = _md_fetch(symbol, period="6mo")
        _spy = _md_fetch("SPY", period="6mo")
        if _df is not None and len(_df) > 20:
            ws = _score_to_dict(weighted_score(_df, spy_df=_spy))
            comps = ws.get("components") or {}
            tech_pts = sum(int(comps[k]) for k in _MAX
                           if k in comps and isinstance(comps[k], (int, float)))
            tech_max = sum(_MAX[k] for k in _MAX if k in comps) or 109
            tech_bull = tech_max > 0 and tech_pts / tech_max >= 0.65
            sources["technical"] = {
                "technical_score": tech_pts, "technical_max": tech_max,
                "signal": ws.get("signal"),
                "note": "same as the Analyze card's technical bars",
            }
            avail += 1
            if tech_bull:
                bull += 1
        else:
            sources["technical"] = {"available": False}
    except Exception:
        sources["technical"] = {"available": False}

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
            elif comp < 38 and tech_bull:
                conflicts.append("technicals are strong but the Monthly composite is weak (fundamentals drag)")
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
                elif ec < 0 and tech_bull:
                    conflicts.append("technical lens is bullish but Monte-Carlo expected change is negative")
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


def _exec_get_signal_calibration(scanner: str = "weekly") -> dict:
    """Read-only: does a higher scanner score actually yield a higher forward return?

    Reads the paper-validation ledger (no trade-path impact). For the monthly scanner it
    also attaches per-component attribution (which composite parts carry signal).
    """
    from app.services.signal_calibration import calibration_report, component_attribution
    rep = calibration_report(scanner)
    if str(scanner).lower() == "monthly":
        rep["component_attribution"] = component_attribution("monthly")
    return rep


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
    "get_explosion_picks": lambda **kw: _exec_get_explosion_picks(kw.get("top_n", 10)),
    "get_stock_news": lambda **kw: _exec_get_stock_news(kw["symbol"], kw.get("limit", 6)),
    "get_signal_agreement": lambda **kw: _exec_get_signal_agreement(kw["symbol"]),
    "get_signal_calibration": lambda **kw: _exec_get_signal_calibration(kw.get("scanner", "weekly")),
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
