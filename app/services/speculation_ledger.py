"""PVSP — the SPECULATION shadow ledger: measuring the 10-20%/week dream with paper, not money.

The user asked whether fast intraday/overnight speculation on exploding stocks can deliver
10-20% WEEKLY. Instead of arguing, this ledger MEASURES it: every ~20 minutes during market
hours it paper-trades exactly that strategy — buy the day-trade explosion scanner's hottest
names at the LIVE price, exit intraday on a take-profit or stop, or force-exit by next day —
with realistic slippage haircuts on both sides. Every fill goes to TradeHistory (strategy_id
"PVSP") so the honest arithmetic (win rate, avg win/loss, compounded weekly return, worst run)
accumulates in public view on the Lab's Discovery monitor.

PREDICTION (stated up front, falsifiable): spectacular single trades, brutal aggregate. If the
data proves otherwise past the locked graduation criteria, it earns a satellite slot like
everything else — the user decides.

SHADOW ONLY: never places a broker order, never touches live scoring. Env knobs:
SPEC_SLOTS(5) SPEC_TP_PCT(10) SPEC_SL_PCT(5) SPEC_MAX_HOLD_HRS(30) SPEC_MIN_SCORE(55)
SPEC_SLIP_BPS(10 = 0.10% per side) SPEC_ACCOUNT(10000) SPEC_HALAL_ONLY(true) SPEC_ENABLED(true).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from sqlalchemy.exc import SQLAlchemyError

from app.db.database import SessionLocal
from app.db.models import TradeHistory

logger = logging.getLogger("screener")

PV_SPEC = "PVSP"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _env_f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _flag(name: str, default: bool) -> bool:
    return os.environ.get(name, "true" if default else "false").strip().lower() in ("true", "1", "yes", "on")


def _cfg() -> dict:
    return {
        "slots": int(_env_f("SPEC_SLOTS", 5)),
        "tp_pct": _env_f("SPEC_TP_PCT", 10.0),           # first target = 2R (Ross Cameron 2:1)
        "sl_pct": _env_f("SPEC_SL_PCT", 5.0),            # initial stop = 1R
        "max_hold_hrs": _env_f("SPEC_MAX_HOLD_HRS", 30.0),
        "min_score": _env_f("SPEC_MIN_SCORE", 55.0),
        "slip": _env_f("SPEC_SLIP_BPS", 10.0) / 1e4,      # per side
        "account": _env_f("SPEC_ACCOUNT", 10000.0),
        "halal_only": _flag("SPEC_HALAL_ONLY", True),
        "enabled": _flag("SPEC_ENABLED", True),
        # ── Ross Cameron mechanizable rules ──────────────────────────────────
        "scale_out": _flag("SPEC_SCALE_OUT", True),      # sell HALF at 1R, stop→breakeven, ride rest
        "max_price": _env_f("SPEC_MAX_PRICE", 20.0),     # "stocks under $10 make 50% moves"; cap the profile
        "min_rvol": _env_f("SPEC_MIN_RVOL", 2.0),        # high relative volume = his #1 requirement
        "min_gap": _env_f("SPEC_MIN_GAP", 0.0),          # optional gap-and-go filter (0 = off)
        "cameron_patterns": _flag("SPEC_CAMERON_PATTERNS", True),  # require a 1-min bull-flag/flat-top to enter
        "max_float_m": _env_f("SPEC_MAX_FLOAT_M", 0.0),  # soft low-float filter (millions; 0 = off, data spotty)
    }


def _age_hours(created_at) -> float:
    if not created_at:
        return 0.0
    c = created_at.replace(tzinfo=None) if getattr(created_at, "tzinfo", None) else created_at
    return max(0.0, (datetime.now(timezone.utc).replace(tzinfo=None) - c).total_seconds() / 3600.0)


def _explosion_candidates(cfg: dict) -> list[dict]:
    """Hottest names from the cached day-trade explosion scan (no compute here — read-only).
    [{symbol, price, score}] sorted best-first; [] when the cache is cold."""
    try:
        from app.workspace_server import _cache_get
        # Prefer the dedicated CHEAP-MOVER scan (Cameron's real universe: sub-$20 gappers from the
        # Alpaca screener); fall back to the S&P day-trade scan (which rarely has cheap names).
        data = _cache_get("speculation_scan", max_age=3600) or _cache_get("daytrade_scan", max_age=3600) or {}
        rows = data.get("results") or []
    except Exception as e:
        logger.debug("spec candidates: scan cache unavailable: %s", e)
        return []
    out = []
    for r in rows:
        try:
            score = float(r.get("explosion_score") or 0)
            price = float(r.get("price") or 0)
            rvol = float(r.get("rvol") or 0)
            gap = abs(float(r.get("gap_pct") or 0))
        except (TypeError, ValueError):
            continue
        if not r.get("symbol") or price <= 0 or score < cfg["min_score"]:
            continue
        if cfg["halal_only"] and not r.get("is_halal"):
            continue
        # ── Ross Cameron stock profile: cheap + high relative volume + (optional) gap ──
        if cfg["max_price"] > 0 and price > cfg["max_price"]:
            continue
        if cfg["min_rvol"] > 0 and rvol < cfg["min_rvol"]:
            continue
        if cfg["min_gap"] > 0 and gap < cfg["min_gap"]:
            continue
        out.append({"symbol": str(r["symbol"]).upper(), "price": price, "score": score,
                    "rvol": rvol, "gap": gap})
    out.sort(key=lambda x: -x["score"])
    return out


def speculation_tick() -> dict:
    """One paper cycle: manage exits (TP/SL/time) at LIVE prices, then fill free slots from the
    explosion scan. Called every ~20 min during market hours by the scheduler; safe to call
    anytime (no-ops when disabled / no data). NEVER places a real order."""
    cfg = _cfg()
    if not cfg["enabled"]:
        return {"skipped": True, "reason": "disabled"}
    from app.services.market_data import get_live_prices

    db = SessionLocal()
    closed = opened = held = 0
    try:
        open_rows = db.query(TradeHistory).filter(
            TradeHistory.strategy_id == PV_SPEC, TradeHistory.pnl_pct.is_(None)).all()
        held_syms = {t.symbol for t in open_rows}
        cands = _explosion_candidates(cfg)
        want = [c["symbol"] for c in cands if c["symbol"] not in held_syms][:max(0, cfg["slots"] - len(open_rows))]
        px = get_live_prices(list(held_syms) + want)

        # ── exits (Ross Cameron risk model) on ABSOLUTE price levels stored at entry (dynamic when a
        #    1-min pattern set the stop; fixed-% fallback for legacy rows): scale half at +1R & move
        #    stop to breakeven, ride to the 2R target; a stopped-out runner is a SMALL WIN not a loss. ──
        scaled_now = 0
        for t in open_rows:
            cur = px.get(t.symbol)
            entry = float(t.entry_price or 0)
            if not cur or entry <= 0:
                held += 1                        # can't price honestly → hold
                continue
            sd = dict(t.signal_details) if isinstance(t.signal_details, dict) else {}
            stop_price = float(sd.get("stop_price") or entry * (1.0 - cfg["sl_pct"] / 100.0))
            target_price = float(sd.get("target_price") or entry * (1.0 + cfg["tp_pct"] / 100.0))
            scale_price = float(sd.get("scale_price") or (entry + (entry - stop_price)))   # +1R
            scaled = bool(sd.get("scaled"))
            age = _age_hours(t.created_at)

            # scale-out: first time we reach +1R, "sell half" and lock the stop at breakeven
            if cfg["scale_out"] and not scaled and cur >= scale_price:
                scale_fill = cur * (1.0 - cfg["slip"])
                sd["scaled"] = True
                sd["scale_pnl"] = round((scale_fill / entry - 1.0) * 100, 2)
                t.signal_details = sd
                scaled = True
                scaled_now += 1

            reason = None
            if cur >= target_price:
                reason = "tp"                    # 2R target
            elif not scaled and cur <= stop_price:
                reason = "sl"                    # pattern/1R stop (pre-scale)
            elif scaled and cur <= entry:
                reason = "be"                    # breakeven stop after scaling → small win
            elif age >= cfg["max_hold_hrs"]:
                reason = "time"
            if not reason:
                held += 1
                continue

            final_fill = cur * (1.0 - cfg["slip"])
            final_leg = (final_fill / entry - 1.0) * 100
            pnl_pct = (0.5 * float(sd.get("scale_pnl") or 0.0) + 0.5 * final_leg) if scaled else final_leg
            t.exit_price = round(final_fill, 4)
            t.pnl_pct = round(pnl_pct, 2)
            t.pnl = round(float(t.qty or 0) * entry * pnl_pct / 100.0, 2)
            t.closed_at = _utc_now()
            t.status = "closed"
            sd["exit_reason"] = reason
            sd["hold_hours"] = round(age, 1)
            t.signal_details = sd
            closed += 1

        # ── entries: a hot name is bought ONLY when it prints a Ross-Cameron 1-min setup
        #    (bull flag / flat top); the pattern's support sets a DYNAMIC stop and a 2:1 target. ──
        budget = cfg["account"] / max(cfg["slots"], 1)
        patt_skips = 0
        for sym in want:
            cur = px.get(sym)
            if not cur or cur <= 0:
                continue
            cand = next((c for c in cands if c["symbol"] == sym), {})
            pattern, pat_stop = None, None
            if cfg["cameron_patterns"]:
                try:
                    from app.services.cameron_patterns import cameron_setup
                    s = cameron_setup(sym)
                except Exception:
                    s = {"ok": False}
                if not s.get("ok"):
                    patt_skips += 1
                    continue                     # no 1-min bull-flag/flat-top right now → don't enter
                pattern, pat_stop = s["pattern"], float(s["stop"])
            flt = None
            if cfg["max_float_m"] > 0:
                try:
                    from app.services.cameron_patterns import get_float_millions
                    flt = get_float_millions(sym)
                except Exception:
                    flt = None
                if flt is not None and flt > cfg["max_float_m"]:
                    continue                     # too large a float (soft filter)
            fill = cur * (1.0 + cfg["slip"])     # buy above the tape
            stop_price = pat_stop if (pat_stop is not None and pat_stop < fill) else fill * (1.0 - cfg["sl_pct"] / 100.0)
            risk = fill - stop_price
            target_price = fill + 2.0 * risk     # 2:1 off the real stop
            scale_price = fill + risk            # +1R
            qty = round(budget / fill, 6)
            db.add(TradeHistory(
                created_at=_utc_now(), strategy_id=PV_SPEC, symbol=sym, side="buy",
                qty=qty, entry_price=round(fill, 4), position_value=round(qty * fill, 2),
                confidence=float(cand.get("score") or 0), status="open",
                signal_details={"source": "speculation_paper", "strategy": "ross_cameron_momentum",
                                "pattern": pattern, "explosion_score": cand.get("score"),
                                "rvol": cand.get("rvol"), "gap_pct": cand.get("gap"), "float_m": flt,
                                "stop_price": round(stop_price, 4), "target_price": round(target_price, 4),
                                "scale_price": round(scale_price, 4), "scale_out": cfg["scale_out"],
                                "max_hold_hrs": cfg["max_hold_hrs"], "slip_bps": cfg["slip"] * 1e4,
                                "asof": _utc_now().isoformat()},
            ))
            opened += 1

        db.commit()
        return {"opened": opened, "closed": closed, "held": held, "scaled": scaled_now,
                "pattern_skips": patt_skips, "candidates": len(cands), "slots": cfg["slots"]}
    except SQLAlchemyError as e:
        db.rollback()
        logger.error("speculation_tick failed: %s", e)
        return {"error": str(e)}
    finally:
        db.close()


def speculation_summary() -> dict:
    """The dream, measured: open positions at live prices + the closed ledger's honest arithmetic
    (win rate, avg win vs avg loss, compounded per-slot return, weekly rate, exit-reason mix)."""
    from app.services.market_data import get_live_prices
    cfg = _cfg()
    db = SessionLocal()
    open_pos: list[dict] = []
    closed: list[dict] = []
    first_at = None
    try:
        rows = db.query(TradeHistory).filter(TradeHistory.strategy_id == PV_SPEC).all()
        px = get_live_prices([t.symbol for t in rows if t.pnl_pct is None])
        for t in rows:
            at = t.created_at
            if at and (first_at is None or at < first_at):
                first_at = at
            if t.pnl_pct is None:
                entry = float(t.entry_price or 0)
                cur = px.get(t.symbol) or entry
                sdp = t.signal_details if isinstance(t.signal_details, dict) else {}
                open_pos.append({"symbol": t.symbol, "entry": round(entry, 2), "current": round(cur, 2),
                                 "upl_pct": round((cur / entry - 1.0) * 100, 2) if entry > 0 else None,
                                 "hold_hours": round(_age_hours(t.created_at), 1),
                                 "pattern": sdp.get("pattern"), "scaled": bool(sdp.get("scaled")),
                                 "stop": sdp.get("stop_price"), "target": sdp.get("target_price")})
            else:
                sd = t.signal_details if isinstance(t.signal_details, dict) else {}
                closed.append({"pnl_pct": float(t.pnl_pct), "reason": sd.get("exit_reason"),
                               "hold_hours": sd.get("hold_hours")})
    except SQLAlchemyError as e:
        logger.debug("speculation_summary failed: %s", e)
    finally:
        db.close()

    n = len(closed)
    wins = [c["pnl_pct"] for c in closed if c["pnl_pct"] > 0]
    losses = [c["pnl_pct"] for c in closed if c["pnl_pct"] <= 0]
    # compounded per-slot growth: each closed trade compounds one slot's capital
    eq = 1.0
    for c in closed:
        eq *= (1.0 + c["pnl_pct"] / 100.0 / max(cfg["slots"], 1))   # each trade is 1/slots of book
    days = 0.0
    if first_at is not None:
        fa = first_at.replace(tzinfo=None) if getattr(first_at, "tzinfo", None) else first_at
        days = max(0.04, (datetime.now(timezone.utc).replace(tzinfo=None) - fa).total_seconds() / 86400.0)
    weekly = ((eq ** (7.0 / days)) - 1.0) * 100 if (n and days > 0.5) else None
    reasons: dict = {}
    for c in closed:
        r = c.get("reason") or "?"
        reasons[r] = reasons.get(r, 0) + 1
    return {
        "strategy_id": PV_SPEC, "enabled": cfg["enabled"], "halal_only": cfg["halal_only"],
        "research_only": not cfg["halal_only"],
        "research_note": (None if cfg["halal_only"] else
                          "بحث فقط — غير مفلتر شرعيّاً. كون كاميرون (أسهم رخيصة متفجّرة) يهيمن عليه ما لا "
                          "يوافق الشريعة (صناديق رافعة/كريبتو/SPAC)، فهذا الدفتر يقيس «حلم 10-20%» على "
                          "المتحرّكات الحقيقيّة لأغراض البحث فقط — ليس توصية شراء ولا مالاً حقيقيّاً، والمسار "
                          "الحلال للاستثمار يبقى منفصلاً ومحميّاً."),
        "universe": "alpaca_screener_cheap_movers",
        "strategy": ("Ross Cameron momentum (mechanized): cheap + high-RVOL selection, ENTER only on a "
                     "1-min bull-flag/flat-top with a dynamic stop at pattern support, 2:1 target, scale "
                     "half at 1R → breakeven. Limits: IEX 1-min bars (~2-3% of volume, thin names noisy), "
                     "no Level-2 order flow; float filter soft (data spotty)."),
        "config": {k: cfg[k] for k in ("slots", "tp_pct", "sl_pct", "max_hold_hrs", "min_score",
                                        "scale_out", "max_price", "min_rvol", "cameron_patterns", "max_float_m")},
        "open": open_pos, "closed_n": n,
        "win_rate": round(len(wins) / n * 100, 1) if n else None,
        "avg_win_pct": round(sum(wins) / len(wins), 2) if wins else None,
        "avg_loss_pct": round(sum(losses) / len(losses), 2) if losses else None,
        "book_return_pct": round((eq - 1.0) * 100, 2) if n else None,
        "weekly_rate_pct": round(weekly, 2) if weekly is not None else None,
        "target_weekly_pct": "10-20 (the dream being measured)",
        "days_running": round(days, 1) if first_at else 0,
        "exit_reasons": reasons,
        "note": "Paper measurement of fast speculation (explosion names, TP/SL/next-day exit, "
                "slippage both sides). The weekly rate is the number to compare against the 10-20% "
                "dream. Shadow only — never trades real money.",
    }


__all__ = ["speculation_tick", "speculation_summary", "PV_SPEC"]
