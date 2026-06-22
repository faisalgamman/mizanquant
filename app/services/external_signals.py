"""Shared 'external signals' — analyst consensus + insider transactions + next earnings.

Single source of truth so the dashboard Analyze card (/api/v1/trade/plan) AND the MizanAI
agent (claude_tools._exec_analyze_stock) report the SAME numbers. All from Finnhub's free
tier (FMP's analyst/insider/earnings endpoints are premium on this plan); earnings falls
back to the FMP cache. Every lookup degrades to {"known": False} on any failure.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

logger = logging.getLogger("screener")


def _analyst(symbol: str) -> dict:
    """Analyst BUY/HOLD/SELL consensus (Finnhub /stock/recommendation, free)."""
    from app.services.finnhub_client import finnhub_client
    recs = finnhub_client.get_recommendation(symbol) or []
    if recs and isinstance(recs[0], dict):
        r0 = recs[0]  # latest period first
        sb = int(r0.get("strongBuy") or 0)
        b = int(r0.get("buy") or 0)
        h = int(r0.get("hold") or 0)
        s = int(r0.get("sell") or 0)
        ss = int(r0.get("strongSell") or 0)
        total = sb + b + h + s + ss
        if total > 0:
            buckets = {"strong_buy": sb, "buy": b, "hold": h, "sell": s, "strong_sell": ss}
            buy_side, sell_side = sb + b, s + ss
            return {
                "known": True, "rating": max(buckets, key=buckets.get),
                "n_analysts": total, "buy": buy_side, "hold": h, "sell": sell_side,
                "period": r0.get("period"), "bearish": sell_side > buy_side,
            }
    return {"known": False}


def _insider(symbol: str) -> dict:
    """Open-market insider buy/sell over the last 90d (Finnhub Form 4, free). Codes
    'S'=sale, 'P'=purchase; awards ('A') and tax ('F') are routine and excluded."""
    from app.services.finnhub_client import finnhub_client
    trades = finnhub_client.get_insider_transactions(symbol) or []
    cutoff = (date.today() - timedelta(days=90)).isoformat()
    sell_sh = buy_sh = 0
    sell_val = buy_val = 0.0
    sellers: set = set()
    for t in trades:
        if str(t.get("transactionDate") or "")[:10] < cutoff:
            continue
        code = (t.get("transactionCode") or "").upper()
        sh = abs(int(t.get("change") or 0))
        px = float(t.get("transactionPrice") or 0) or 0.0
        if code == "S":
            sell_sh += sh
            sell_val += sh * px
            if t.get("name"):
                sellers.add(t.get("name"))
        elif code == "P":
            buy_sh += sh
            buy_val += sh * px
    if sell_sh or buy_sh:
        return {
            "known": True, "window_days": 90,
            "sell_shares": sell_sh, "buy_shares": buy_sh,
            "sell_value": round(sell_val), "buy_value": round(buy_val),
            "net_value": round(buy_val - sell_val), "n_sellers": len(sellers),
            "heavy_sell": bool(sell_val >= 1_000_000 and sell_val > buy_val * 2),
        }
    return {"known": False}


def _earnings(symbol: str) -> dict:
    """Next scheduled earnings (Finnhub calendar primary, FMP cache fallback)."""
    from app.services.precision_gates import EARNINGS_BLACKOUT_DAYS
    from app.services.reference_data import _business_days_between, get_earnings_date
    ed = None
    hour = None
    try:
        from app.services.finnhub_client import finnhub_client
        ec = finnhub_client.get_next_earnings(symbol)
        if ec and ec.get("date"):
            ed = date.fromisoformat(str(ec["date"])[:10])
            hour = ec.get("hour")  # amc | bmo | dmh
    except Exception:
        ed = None
    if ed is None:
        ed = get_earnings_date(symbol)
    bdays = _business_days_between(date.today(), ed) if ed is not None else None
    if ed is not None and bdays is not None and bdays >= 0:
        return {
            "known": True, "date": ed.isoformat(), "business_days": bdays,
            "blackout_days": EARNINGS_BLACKOUT_DAYS,
            "within_blackout": bdays <= EARNINGS_BLACKOUT_DAYS, "hour": hour,
        }
    return {"known": False, "blackout_days": EARNINGS_BLACKOUT_DAYS}


def get_external_signals(symbol: str) -> dict:
    """{'earnings': {...}, 'analyst': {...}, 'insider': {...}} — never raises."""
    symbol = (symbol or "").upper().strip()
    out: dict = {}
    for name, fn in (("earnings", _earnings), ("analyst", _analyst), ("insider", _insider)):
        try:
            out[name] = fn(symbol)
        except Exception as e:
            logger.debug("external_signals %s failed for %s: %s", name, symbol, e)
            out[name] = {"known": False}
    return out
