from __future__ import annotations

import json
import logging
import math

from fastapi import APIRouter

from app.services.redis_client import get_redis

logger = logging.getLogger("screener")

router = APIRouter(tags=["v1-scoring"])


def _to_jsonable(obj):
    """Recursively coerce numpy/pandas scalars to JSON-native types.

    FastAPI's ``jsonable_encoder`` cannot serialize numpy scalars — e.g. a
    ``numpy.bool_`` (which it tries to ``dict()``/``vars()`` and 500s on). The
    strategy ``details`` dict embedded in the trade plan can carry such values,
    so we sanitize the whole payload before returning. NaN/Inf floats → None.
    """
    import numpy as np

    if obj is None or isinstance(obj, (str, bool, int)):
        return obj
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {str(k): _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_to_jsonable(v) for v in obj]
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        f = float(obj)
        return f if math.isfinite(f) else None
    if isinstance(obj, np.ndarray):
        return [_to_jsonable(v) for v in obj.tolist()]
    if hasattr(obj, "item"):  # any other numpy scalar
        try:
            return _to_jsonable(obj.item())
        except Exception:
            return str(obj)
    return obj


@router.get("/scoring/weighted")
async def v1_weighted_score(symbol: str = "AAPL"):
    from app.services.market_data import fetch
    from app.services.scoring import weighted_score, _score_to_dict

    redis = await get_redis()
    cache_key = f"scoring:weighted:{symbol.upper()}"

    if redis is not None:
        try:
            cached = await redis.get(cache_key)
            if cached is not None:
                return json.loads(cached)
        except Exception as exc:
            logger.debug("Redis read error for %s: %s", cache_key, exc)

    df = fetch(symbol, period="6mo")
    if df is None:
        return {"error": f"No data for {symbol}"}
    spy_df = fetch("SPY", period="6mo")
    result = weighted_score(df, spy_df=spy_df)
    result_dict = _score_to_dict(result)
    result_dict["symbol"] = symbol.upper()

    # Smart score (RS-vs-SPY gate) — same path as the scanner
    smart = {}
    try:
        from app.workspace_server import _analyze_smart
        smart = _analyze_smart(symbol, spy_df=spy_df) or {}
    except Exception as exc:
        logger.debug("_analyze_smart failed for %s: %s", symbol, exc)
        result_dict["rs_unavailable"] = True
    if smart and smart.get("smart_score"):
        result_dict["smart_score"] = smart.get("smart_score")
        result_dict["smart_verdict"] = smart.get("verdict", "")
    else:
        # Smart layer unavailable → derive a display score from the component
        # points already in result_dict (the exact values the Analyze bars
        # render), so the header never shows 0/100 over full bars. Display-only:
        # does NOT touch weighted_score / _analyze_smart / gates / trades.
        comps = result_dict.get("components") or {}
        pts = sum(float(v) for v in comps.values() if isinstance(v, (int, float)))
        result_dict["smart_score"] = min(100, round(pts))
        if "rs_unavailable" not in result_dict:
            result_dict["rs_unavailable"] = True

    if redis is not None:
        try:
            await redis.setex(cache_key, 300, json.dumps(result_dict, default=str))
        except Exception as exc:
            logger.debug("Redis write error for %s: %s", cache_key, exc)

    return result_dict


@router.get("/trade/plan")
async def v1_trade_plan(symbol: str = "AAPL", portfolio: float = 100000.0):
    from app.services.market_data import fetch
    from app.services.trade_plan import generate_trade_plan

    df = fetch(symbol, period="6mo")
    if df is None:
        return {"error": f"No data for {symbol}"}
    plan = generate_trade_plan(df, portfolio_equity=portfolio)
    plan["symbol"] = symbol.upper()

    try:
        spy_df = fetch("SPY", period="6mo")
        from app.workspace_server import _get_symbol_strategy

        sig = _get_symbol_strategy(symbol, df, spy_df)
        if sig:
            plan["strategy"] = sig.strategy
            plan["strategy_score"] = sig.score
            plan["strategy_reason"] = sig.reason
            plan["strategy_confidence"] = round(sig.confidence, 2)
            plan["hold_days_min"] = sig.hold_days_min
            plan["hold_days_max"] = sig.hold_days_max
            plan["strategy_entry"] = sig.entry
            plan["strategy_stop"] = sig.stop
            plan["strategy_tp1"] = sig.tp1
            plan["strategy_tp2"] = sig.tp2
            plan["strategy_tp3"] = sig.tp3
            plan["details"] = sig.details
            plan["pipeline"] = {
                "data": "loaded",
                "halal": "pending",
                "smart": "scored",
                "strategy_selector": f"{sig.strategy} (score={sig.score})",
                "ai_confirm": f"{'confirmed' if sig.score >= 65 else 'rejected'}",
            }
        else:
            plan["strategy"] = "WAIT"
            plan["strategy_reason"] = "No strategy triggered"
    except Exception as e:
        plan["strategy"] = "WAIT"
        plan["strategy_reason"] = f"Strategy error: {e}"

    # Make the per-strategy exit consistent with the validated Option-A plan from
    # generate_trade_plan (fixed 15% catastrophe stop + 20-day time exit). The UI
    # prefers strategy_* fields, so align them here; otherwise it would show the
    # strategy's own (tighter) stop and contradict the validated exit policy.
    if "stop_loss" in plan and "error" not in plan:
        plan["strategy_stop"] = plan["stop_loss"]
        for _k in ("tp1", "tp2", "tp3"):
            if plan.get(_k) is not None:
                plan["strategy_" + _k] = plan[_k]

    # Earnings proximity — make the risk VISIBLE in the Analyze card. The buy path is
    # already earnings-gated server-side (guards + USX filter); here we surface the date,
    # flag the ±blackout window, and report known=false when the date is unavailable
    # instead of passing it silently. Data is FMP-backed + cached 24h.
    try:
        from app.services.reference_data import (
            get_earnings_date, business_days_until_earnings,
        )
        from app.services.precision_gates import EARNINGS_BLACKOUT_DAYS
        ed = get_earnings_date(symbol)
        bdays = business_days_until_earnings(symbol) if ed is not None else None
        # bdays < 0 ⇒ the cached date is in the PAST (stale calendar) ⇒ the next date is
        # unknown → report known=False so the card prompts a manual check, not a past date.
        if ed is not None and bdays is not None and bdays >= 0:
            plan["earnings"] = {
                "known": True,
                "date": ed.isoformat(),
                "business_days": bdays,
                "blackout_days": EARNINGS_BLACKOUT_DAYS,
                "within_blackout": bdays <= EARNINGS_BLACKOUT_DAYS,
            }
        else:
            plan["earnings"] = {"known": False, "blackout_days": EARNINGS_BLACKOUT_DAYS}
    except Exception as e:
        logger.debug("trade/plan earnings lookup failed for %s: %s", symbol, e)
        plan["earnings"] = {"known": False}

    # Analyst consensus (Finnhub /stock/recommendation — free tier). The price-target
    # endpoint is premium, so we surface the BUY/HOLD/SELL consensus, not a target number;
    # bearish=True flags more sells than buys (e.g. EXPD: 8 sell vs 2 buy). Needs
    # FINNHUB_API_KEY; absent ⇒ known=False and the card omits the chip.
    try:
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
                plan["analyst"] = {
                    "known": True,
                    "rating": max(buckets, key=buckets.get),
                    "n_analysts": total, "buy": buy_side, "hold": h, "sell": sell_side,
                    "period": r0.get("period"),
                    "bearish": sell_side > buy_side,
                }
            else:
                plan["analyst"] = {"known": False}
        else:
            plan["analyst"] = {"known": False}
    except Exception as e:
        logger.debug("trade/plan analyst (finnhub) failed for %s: %s", symbol, e)
        plan["analyst"] = {"known": False}

    # Insider transactions (Finnhub /stock/insider-transactions — free, SEC Form 4).
    # Summarize OPEN-MARKET activity over the last 90 days: code 'S' = sale, 'P' = purchase.
    # Awards ('A') and tax-withholding ('F') are routine and excluded from the signal.
    # heavy_sell flags large net selling (e.g. HWM: a $11.3M exec sale).
    try:
        from datetime import date, timedelta
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
            plan["insider"] = {
                "known": True, "window_days": 90,
                "sell_shares": sell_sh, "buy_shares": buy_sh,
                "sell_value": round(sell_val), "buy_value": round(buy_val),
                "net_value": round(buy_val - sell_val), "n_sellers": len(sellers),
                "heavy_sell": bool(sell_val >= 1_000_000 and sell_val > buy_val * 2),
            }
        else:
            plan["insider"] = {"known": False}
    except Exception as e:
        logger.debug("trade/plan insider (finnhub) failed for %s: %s", symbol, e)
        plan["insider"] = {"known": False}

    # Sanitize numpy scalars (e.g. numpy.bool_ inside sig.details) so FastAPI's
    # encoder can serialize the response instead of 500-ing.
    return _to_jsonable(plan)
