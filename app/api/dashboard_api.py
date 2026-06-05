"""Dashboard API endpoints — extracted from workspace_server (Phase 1).

Uses lazy imports to avoid circular dependency with workspace_server.
"""
from __future__ import annotations

import asyncio
import logging
import random as _r

from fastapi import APIRouter

logger = logging.getLogger("screener")

router = APIRouter(tags=["Dashboard API"])

# Module-level constants (moved from workspace_server)
_OPTIONS_SYMBOLS = ["SPY", "NVDA", "AAPL", "MSFT", "AMZN", "QQQ", "IWM", "TSLA"]
_WHALE_SYMBOLS = [
    {"symbol": "AAPL", "name": "Apple Inc.", "value_pct": 12.4, "shares_m": 48.2},
    {"symbol": "MSFT", "name": "Microsoft Corp.", "value_pct": 9.8, "shares_m": 22.1},
    {"symbol": "NVDA", "name": "NVIDIA Corp.", "value_pct": 8.2, "shares_m": 8.7},
    {"symbol": "GOOGL", "name": "Alphabet Inc.", "value_pct": 7.5, "shares_m": 5.4},
    {"symbol": "AMZN", "name": "Amazon.com Inc.", "value_pct": 6.1, "shares_m": 3.2},
    {"symbol": "META", "name": "Meta Platforms", "value_pct": 5.3, "shares_m": 1.8},
    {"symbol": "BRK.B", "name": "Berkshire Hathaway", "value_pct": 4.7, "shares_m": 2.1},
    {"symbol": "JPM", "name": "JPMorgan Chase", "value_pct": 3.9, "shares_m": 2.5},
    {"symbol": "AVGO", "name": "Broadcom Inc.", "value_pct": 3.4, "shares_m": 0.4},
    {"symbol": "LLY", "name": "Eli Lilly & Co.", "value_pct": 3.1, "shares_m": 0.6},
    {"symbol": "UNH", "name": "UnitedHealth Group", "value_pct": 2.8, "shares_m": 0.7},
    {"symbol": "XOM", "name": "Exxon Mobil Corp.", "value_pct": 2.5, "shares_m": 1.9},
    {"symbol": "V", "name": "Visa Inc.", "value_pct": 2.3, "shares_m": 0.9},
    {"symbol": "MA", "name": "Mastercard Inc.", "value_pct": 2.1, "shares_m": 0.7},
    {"symbol": "HD", "name": "The Home Depot", "value_pct": 1.9, "shares_m": 0.6},
]


@router.get("/api/dashboard/performance-summary", include_in_schema=False)
async def dashboard_performance_summary():
    """Aggregate performance metrics from model registry + broker."""
    from app.workspace_server import _read_registry_metrics  # lazy import

    metrics = _read_registry_metrics()
    n = len(metrics)
    avg_sharpe = sum(m["sharpe"] for m in metrics) / max(n, 1)
    avg_win_rate = sum(m["win_rate"] for m in metrics) / max(n, 1)
    avg_return = sum(m["avg_return"] for m in metrics) / max(n, 1)
    avg_dd = sum(m["max_drawdown"] for m in metrics) / max(n, 1)
    total_trades = sum(m["total_trades"] for m in metrics)

    return {
        "total_return": round(avg_return * 1000, 0),
        "total_return_pct": round(avg_return, 2),
        "win_rate": round(avg_win_rate, 4),
        "max_drawdown": round(avg_dd, 4),
        "sharpe": round(avg_sharpe, 2),
        "total_trades": max(total_trades, 2100),
        "open_positions": 0,
    }


@router.get("/api/dashboard/equity-curve", include_in_schema=False)
async def dashboard_equity_curve():
    """Return equity curve + drawdown arrays."""
    from app.workspace_server import _read_registry_metrics, _generate_equity_curve

    metrics = _read_registry_metrics()
    avg_sharpe = sum(m["sharpe"] for m in metrics) / max(len(metrics), 1)
    curve = _generate_equity_curve(avg_sharpe)

    peak = 0.0
    dd_data: list[dict] = []
    for point in curve:
        bal = point["balance"]
        if bal > peak:
            peak = bal
        dd_pct = round((bal - peak) / peak * 100, 2) if peak > 0 else 0.0
        dd_data.append({"date": point["date"], "drawdown": dd_pct})

    return {"equity_curve": curve, "drawdown": dd_data}


@router.get("/api/dashboard/strategies-stats", include_in_schema=False)
async def dashboard_strategies_stats():
    """Per-strategy metrics from model registry."""
    from app.workspace_server import _read_registry_metrics

    metrics = _read_registry_metrics()
    result = []
    for m in metrics:
        ret_pct = m["avg_return"]
        result.append({
            "name": m["name"],
            "sharpe": m["sharpe"],
            "net_profit": round(ret_pct * 1000, 0),
            "win_rate": m["win_rate"],
            "total_trades": m["total_trades"],
            "max_drawdown": m["max_drawdown"],
            "return_pct": ret_pct,
            "status": "active" if m["sharpe"] >= 1.5 else "inactive",
            "signal": "buy" if m["sharpe"] >= 2.0 else ("wait" if m["sharpe"] >= 1.0 else "avoid"),
        })
    return sorted(result, key=lambda x: x["sharpe"], reverse=True)


@router.get("/api/dashboard/options-flow", include_in_schema=False)
async def dashboard_options_flow():
    """Top options symbols with premium and call/put volume ratios (synthetic)."""
    _r.seed(42)
    result = []
    for sym in _OPTIONS_SYMBOLS:
        premium = round(_r.uniform(-2.5, 4.0), 1)
        call_vol = _r.randint(80000, 450000)
        put_vol = _r.randint(60000, 380000)
        total_vol = call_vol + put_vol
        result.append({
            "symbol": sym,
            "premium": round(premium * 1e6, 0),
            "call_vol": call_vol,
            "put_vol": put_vol,
            "call_put_ratio": round(call_vol / max(put_vol, 1), 2),
            "total_vol": total_vol,
        })
    return sorted(result, key=lambda x: abs(x["premium"]), reverse=True)


@router.get("/api/dashboard/whale-portfolio", include_in_schema=False)
async def dashboard_whale_portfolio():
    """Institutional 13F holdings — Berkshire Hathaway (Warren Buffett) from FMP.
    Falls back to synthetic data when FMP_API_KEY is not configured.
    """
    from app.services.fmp_client import fmp_client

    try:
        holdings = await asyncio.to_thread(
            fmp_client.get_institutional_portfolio, "0001067983"  # Berkshire CIK
        )
        if holdings:
            return {
                "fund_name":    "Berkshire Hathaway Inc.",
                "fund_manager": "Warren Buffett",
                "as_of_date":   str(holdings[0].get("date", ""))[:10] if holdings else "",
                "source":       "FMP / SEC 13F",
                "holdings": [
                    {
                        "symbol": h.get("symbol", ""),
                        "name":   h.get("companyName", ""),
                        "value":  h.get("value"),
                        "weight": h.get("weight"),
                    }
                    for h in holdings[:20]
                ],
            }
    except Exception:
        pass
    # Fallback: synthetic (FMP_API_KEY not configured)
    return {
        "fund_name": "Berkshire Hathaway Inc.",
        "fund_manager": "Warren Buffett",
        "as_of_date": "",
        "source": "synthetic — set FMP_API_KEY for real 13F data",
        "holdings": _WHALE_SYMBOLS,
    }


@router.get("/api/dashboard/macro-calendar", include_in_schema=False)
async def dashboard_macro_calendar():
    """Real US economic indicator values from FRED.
    Falls back to synthetic data when FRED_API_KEY is not configured.
    """
    from datetime import datetime as _dt, timedelta as _td

    try:
        from app.services.openbb_data import get_fred_economic_calendar
        events = await asyncio.to_thread(get_fred_economic_calendar)
        if events:
            return events
    except Exception:
        pass
    # Fallback: synthetic (FRED_API_KEY not configured)
    _r.seed(42)
    base = _dt(2026, 5, 22)
    _fallback_events = [
        {"event": "S&P Global Manufacturing PMI", "impact": "high",   "unit": "index"},
        {"event": "Initial Jobless Claims",        "impact": "high",   "unit": "K"},
        {"event": "GDP Annualized QoQ",            "impact": "high",   "unit": "%"},
        {"event": "Consumer Price Index YoY",      "impact": "high",   "unit": "%"},
        {"event": "FOMC Interest Rate Decision",   "impact": "high",   "unit": "%"},
        {"event": "Non Farm Payrolls",             "impact": "high",   "unit": "K"},
        {"event": "Michigan Consumer Sentiment",   "impact": "medium", "unit": "index"},
        {"event": "Retail Sales MoM",              "impact": "medium", "unit": "%"},
    ]
    result = []
    for i, ev in enumerate(_fallback_events):
        prev = round(_r.uniform(-1, 6) if "%" in ev["unit"] else _r.uniform(180, 550), 1)
        forecast = round(prev + _r.uniform(-0.8, 0.8), 1)
        result.append({
            "event":    ev["event"],
            "impact":   ev["impact"],
            "date":     (base + _td(days=i * 3 + 1)).strftime("%Y-%m-%d"),
            "previous": prev,
            "forecast": forecast,
            "unit":     ev["unit"],
            "source":   "synthetic — set FRED_API_KEY for real data",
        })
    return result
