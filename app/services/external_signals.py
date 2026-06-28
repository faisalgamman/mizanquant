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
    top_seller = None  # the single largest open-market sale, with % of that insider's stake
    for t in trades:
        if str(t.get("transactionDate") or "")[:10] < cutoff:
            continue
        code = (t.get("transactionCode") or "").upper()
        sh = abs(int(t.get("change") or 0))
        px = float(t.get("transactionPrice") or 0) or 0.0
        if code == "S":
            sell_sh += sh
            val = sh * px
            sell_val += val
            if t.get("name"):
                sellers.add(t.get("name"))
            # % of the insider's stake sold: 'share' is holdings AFTER the sale, so the
            # pre-sale stake = after + sold. A large % (e.g. 39%) is a strong "abnormal /
            # high-conviction" signal vs a routine 2-3% trim. Finnhub has NO 10b5-1 flag,
            # so this %-of-stake is the best available normal-vs-significant gauge.
            held_after = abs(int(t.get("share") or 0))
            before = held_after + sh
            pct = round(sh / before * 100, 1) if before > 0 else None
            if top_seller is None or val > top_seller["value"]:
                top_seller = {"name": t.get("name"), "value": round(val),
                              "shares": sh, "pct_of_stake": pct,
                              "date": str(t.get("transactionDate") or "")[:10]}
        elif code == "P":
            buy_sh += sh
            buy_val += sh * px
    if sell_sh or buy_sh:
        big_pct_sale = bool(top_seller and (top_seller.get("pct_of_stake") or 0) >= 25)
        return {
            "known": True, "window_days": 90,
            "sell_shares": sell_sh, "buy_shares": buy_sh,
            "sell_value": round(sell_val), "buy_value": round(buy_val),
            "net_value": round(buy_val - sell_val), "n_sellers": len(sellers),
            "top_seller": top_seller,
            # Heavy = large $ net selling OR one insider dumping a big slice of their stake.
            "heavy_sell": bool((sell_val >= 1_000_000 and sell_val > buy_val * 2) or big_pct_sale),
        }
    return {"known": False}


def insider_rank_adjustment(symbol: str, *, sell_penalty: float = 15.0,
                            buy_bonus: float = 5.0) -> dict:
    """Composite-rank adjustment from recent insider activity — DEMOTE heavy selling,
    lightly PROMOTE net buying. Returns {'adj', 'flag', 'top_seller', 'known'}; adj=0 with
    known=False when there's no insider data (FAIL-OPEN — absence must never penalize a
    name). Used to fold insider conviction into the deep-picks selection score."""
    try:
        ins = _insider((symbol or "").upper().strip())
    except Exception as e:
        logger.debug("insider_rank_adjustment %s failed: %s", symbol, e)
        return {"adj": 0.0, "flag": None, "known": False}
    if not ins.get("known"):
        return {"adj": 0.0, "flag": None, "known": False}
    net = float(ins.get("net_value") or 0)
    if ins.get("heavy_sell"):
        adj, flag = -abs(sell_penalty), "heavy_sell"
    elif net > 0:
        adj, flag = abs(buy_bonus), "net_buy"
    else:
        adj, flag = 0.0, ("net_sell" if net < 0 else "neutral")
    return {"adj": adj, "flag": flag, "top_seller": ins.get("top_seller"), "known": True}


def fundamentals_rank_adjustment(symbol: str, *, max_bonus: float = 15.0,
                                 max_penalty: float = 10.0) -> dict:
    """Composite-rank adjustment from FREE Finnhub fundamentals (/stock/metric) — REWARD
    real revenue growth + positive cash generation + quality (ROE), PENALIZE shrinking
    revenue and heavy leverage. Returns {'adj', 'known', ...}; adj=0/known=False on no data
    (FAIL-OPEN). Repairs the degraded score_fund (FMP legacy-403, yfinance IP-blocked)."""
    try:
        from app.services.finnhub_client import finnhub_client
        m = finnhub_client.get_basic_financials((symbol or "").upper().strip())
    except Exception as e:
        logger.debug("fundamentals_rank_adjustment %s failed: %s", symbol, e)
        return {"adj": 0.0, "known": False}
    if not m:
        return {"adj": 0.0, "known": False}

    def _f(*keys):
        for k in keys:
            v = m.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return float(v)
        return None

    rev = _f("revenueGrowthTTMYoy", "revenueGrowthQuarterlyYoy")          # %
    fcf_ps = _f("cashFlowPerShareTTM", "cashFlowPerShareAnnual")
    roe = _f("roeTTM", "roeRfy")                                          # %
    de = _f("totalDebt/totalEquityQuarterly", "totalDebt/totalEquityAnnual")
    score = 0.0
    if rev is not None:
        score += 6.0 if rev > 15 else 3.0 if rev > 5 else (-5.0 if rev < 0 else 0.0)
    if fcf_ps is not None:
        score += 4.0 if fcf_ps > 0 else -4.0
    if roe is not None and roe > 15:
        score += 3.0
    if de is not None and de > 2:
        score -= 3.0
    adj = max(-abs(max_penalty), min(abs(max_bonus), score))
    return {"adj": adj, "known": True, "revenue_growth": rev,
            "fcf_per_share": fcf_ps, "roe": roe, "debt_equity": de}


def _fundamentals(symbol: str) -> dict:
    """Display fundamentals for the Analyze card — FREE Finnhub /stock/metric: revenue
    growth (TTM YoY), cash-flow/share, ROE, leverage, gross margin. {'known': False} when
    unavailable. Carries the same strong/weak verdict the selection scorer uses."""
    try:
        from app.services.finnhub_client import finnhub_client
        m = finnhub_client.get_basic_financials((symbol or "").upper().strip())
    except Exception:
        return {"known": False}
    if not m:
        return {"known": False}

    def _f(*keys):
        for k in keys:
            v = m.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return round(float(v), 2)
        return None

    rev = _f("revenueGrowthTTMYoy", "revenueGrowthQuarterlyYoy")
    fcf_ps = _f("cashFlowPerShareTTM", "cashFlowPerShareAnnual")
    roe = _f("roeTTM", "roeRfy")
    de = _f("totalDebt/totalEquityQuarterly", "totalDebt/totalEquityAnnual")
    gm = _f("grossMarginTTM", "grossMarginAnnual")
    if rev is None and fcf_ps is None and roe is None:
        return {"known": False}
    adj = fundamentals_rank_adjustment(symbol).get("adj", 0.0)  # cached fetch reused
    return {
        "known": True, "revenue_growth": rev, "fcf_per_share": fcf_ps, "roe": roe,
        "debt_equity": de, "gross_margin": gm, "score_adj": adj,
        "strong": adj >= 8, "weak": adj <= -4,
    }


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


def _market_regime() -> dict:
    """Broad-market trend: is SPY below its EMA21 (bearish daily trend)? Mirrors the USX
    regime gate so the Analyze card / agent can WARN that the per-stock swing signal (which
    judges each name on its OWN trend, relative-strength) is firing while the whole market is
    in a downtrend — even strong stocks tend to fall in a selloff. SPY bars are cached ~10 min."""
    try:
        from app.services.market_data import fetch
        df = fetch("SPY", period="3mo")
        if df is None or len(df) < 21:
            return {"known": False}
        close = df["close"] if "close" in df.columns else df["Close"]
        price = float(close.iloc[-1])
        ema21 = float(close.ewm(span=21, adjust=False).mean().iloc[-1])
        return {"known": True, "spy_bearish": bool(price < ema21),
                "spy_price": round(price, 2), "spy_ema21": round(ema21, 2)}
    except Exception as e:
        logger.debug("market_regime failed: %s", e)
        return {"known": False}


def get_external_signals(symbol: str) -> dict:
    """{'earnings':{}, 'analyst':{}, 'insider':{}, 'market':{}} — never raises."""
    symbol = (symbol or "").upper().strip()
    out: dict = {}
    for name, fn in (("earnings", _earnings), ("analyst", _analyst),
                     ("insider", _insider), ("fundamentals", _fundamentals)):
        try:
            out[name] = fn(symbol)
        except Exception as e:
            logger.debug("external_signals %s failed for %s: %s", name, symbol, e)
            out[name] = {"known": False}
    try:
        out["market"] = _market_regime()
    except Exception as e:
        logger.debug("external_signals market failed: %s", e)
        out["market"] = {"known": False}
    return out
