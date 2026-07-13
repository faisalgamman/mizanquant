"""Daily NAV log for the paper ledgers (core PVC vs momentum satellite PVSA) — the race curve.

The ledger summaries give 'return since inception' but not the PATH: drawdown, volatility, and
when each strategy led. This appends ONE row per UTC day (open-position market value + unrealized
return + realized P/L + count) for each ledger, persisted as JSON in CACHE_DIR so the forward
out-of-sample record accumulates across restarts. Idempotent per day (a same-day re-run replaces
that day's row). Best-effort and fail-safe — a bad read/write never raises into the scheduler.

This is measurement only: it reads the simulated ledgers and writes a log. It places no orders and
changes no scoring. Mirrors the persisted-config pattern of :mod:`app.services.factor_weights`.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone

logger = logging.getLogger("screener")

_LOCK = threading.Lock()
_MAX_ROWS = 500          # ~2 years of daily rows; oldest trimmed
_LEDGER_ACCOUNT = 100000.0   # notional capital each paper ledger is seeded with


def _nav_return(summary: dict):
    """TRUE cumulative NAV return % since inception = (realized P&L + open unrealized $) / capital.

    Unlike the snapshot ``unrealized_pct`` (open positions only), this BANKS realized P&L, so it is
    a real equity curve — the correct basis to compare against the buy-and-hold ghost lines. None on
    bad inputs."""
    try:
        real = float(summary.get("realized_pnl") or 0.0)
        mv, cb = summary.get("market_value"), summary.get("cost_basis")
        if mv is not None and cb is not None:
            return round((real + (float(mv) - float(cb))) / _LEDGER_ACCOUNT * 100.0, 3)
        upl = float(summary.get("unrealized_pct") or 0.0)     # fallback: realized% + snapshot upl%
        return round(real / _LEDGER_ACCOUNT * 100.0 + upl, 3)
    except Exception:
        return None


def _path() -> str:
    base = os.environ.get("CACHE_DIR") or "/data"
    try:
        os.makedirs(base, exist_ok=True)
    except Exception:
        base = "."
    return os.path.join(base, "ledger_nav.json")


def _read() -> list:
    try:
        with open(_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def record_nav() -> dict:
    """Compute today's NAV row for both ledgers and append it (replacing any existing same-day
    row). Returns the row. Never raises — each ledger read is guarded so one failure can't block
    the other or the caller (the scheduler)."""
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    row: dict = {"date": day}
    try:
        from app.services.paper_validation import core_ledger_summary
        c = core_ledger_summary()
        row["core_upl"] = c.get("unrealized_pct")
        row["core_mv"] = c.get("market_value")
        row["core_realized"] = c.get("realized_pnl")
        row["core_open"] = c.get("open")
        row["core_nav"] = _nav_return(c)          # true cumulative NAV (banks realized P&L)
    except Exception as e:
        logger.debug("record_nav core failed: %s", e)
    try:
        from app.services.paper_validation import satellite_ledger_summary
        s = satellite_ledger_summary()
        row["sat_upl"] = s.get("unrealized_pct")
        row["sat_mv"] = s.get("market_value")
        row["sat_realized"] = s.get("realized_pnl")
        row["sat_open"] = s.get("open")
        row["sat_nav"] = _nav_return(s)
    except Exception as e:
        logger.debug("record_nav satellite failed: %s", e)
    try:
        from app.services.paper_validation import explorer_ledger_summary
        x = explorer_ledger_summary()
        row["exp_upl"] = x.get("unrealized_pct")
        row["exp_mv"] = x.get("market_value")
        row["exp_realized"] = x.get("realized_pnl")
        row["exp_open"] = x.get("open")
        row["exp_nav"] = _nav_return(x)
    except Exception as e:
        logger.debug("record_nav explorer failed: %s", e)

    with _LOCK:
        rows = [r for r in _read() if isinstance(r, dict) and r.get("date") != day]
        rows.append(row)
        rows = rows[-_MAX_ROWS:]
        try:
            with open(_path(), "w", encoding="utf-8") as fh:
                json.dump(rows, fh, ensure_ascii=False)
        except Exception as e:
            logger.error("record_nav write failed: %s", e)
    return row


def _ghost_benchmarks(rows: list) -> dict:
    """Attach buy-and-hold SPY / SPUS / HLAL 'ghost' returns to each row (from HISTORICAL prices
    keyed on the row's date, relative to the race inception) — the passive halal alternative over
    the exact same window. Derived, not stored: works retroactively and self-corrects. Fail-safe.

    SPUS = SP Funds S&P 500 Sharia, HLAL = Wahed FTSE USA Shariah — a Muslim investor could just
    buy either; comparing our active book to them is the honest time-weighted test (immune to the
    per-trade-count problem, since it is a continuous NAV, not a sum over closed 'trades')."""
    out: dict = {}
    if not rows:
        return out
    try:
        from datetime import datetime, timezone
        from app.services.risk_metrics import _bench_lookup
    except Exception:
        return out

    def _mid(d):
        return datetime.strptime(d, "%Y-%m-%d").replace(hour=16, tzinfo=timezone.utc)

    inception = rows[0].get("date")
    for sym, key in (("SPY", "spy_ret"), ("SPUS", "spus_ret"), ("HLAL", "hlal_ret")):
        try:
            look = _bench_lookup(sym)
            if look is None:
                continue
            base = look(_mid(inception))
            if not base or base <= 0:
                continue
            last = None
            for r in rows:
                px = look(_mid(r.get("date", inception)))
                if px and px > 0:
                    r[key] = round((px / base - 1.0) * 100.0, 3)
                    last = r[key]
            out[sym] = {"cum_ret": last, "halal": sym in ("SPUS", "HLAL")}
        except Exception as e:
            logger.debug("ghost benchmark %s failed: %s", sym, e)
    return out


def _backfill_nav(rows: list) -> None:
    """Reconstruct the true cumulative NAV for rows recorded BEFORE the *_nav field existed —
    from their stored realized $ + snapshot unrealized %: nav ≈ realized/capital% + unrealized%."""
    for r in rows:
        for pre in ("core", "sat", "exp"):
            if r.get(pre + "_nav") is None:
                real, upl = r.get(pre + "_realized"), r.get(pre + "_upl")
                if real is not None or upl is not None:
                    r[pre + "_nav"] = round(float(real or 0.0) / _LEDGER_ACCOUNT * 100.0
                                            + float(upl or 0.0), 3)


def nav_history() -> dict:
    """The full daily race series (ascending by date) + a small summary for the UI."""
    rows = sorted(_read(), key=lambda r: r.get("date", ""))
    _backfill_nav(rows)
    benchmarks = _ghost_benchmarks(rows)
    latest = rows[-1] if rows else {}
    return {
        "rows": rows,
        "days": len(rows),
        "latest": latest,
        "benchmarks": benchmarks,
        "note": "Daily out-of-sample race of the paper core (PVC) vs the momentum satellite (PVSA), "
                "with buy-and-hold SPY / SPUS / HLAL (halal ETF) ghost lines over the same window — "
                "the honest 'did we beat just holding a halal index?' reference. Measurement only.",
    }


# ── PIT basket-membership archive — the permanent survivorship-bias killer ─────
# One JSONL line per day: the halal basket EXACTLY as it stood (symbols + prices). A year from
# now this is a true point-in-time universe: any future backtest can replay membership as-was,
# with zero survivorship bias. Append-only; idempotent per day.

def _basket_hist_path() -> str:
    base = os.environ.get("CACHE_DIR") or "/data"
    try:
        os.makedirs(base, exist_ok=True)
    except Exception:
        base = "."
    return os.path.join(base, "basket_history.jsonl")


def record_basket_membership() -> dict:
    """Append today's halal-basket membership to the PIT archive (skips if already logged today
    or the basket is empty). Fail-safe — never raises into the scheduler."""
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        from app.workspace_server import _cache_get
        basket = _cache_get("halal_basket", max_age=86400 * 3) or {}
        rows = basket.get("results") or []
    except Exception as e:
        logger.debug("basket archive: cache unavailable: %s", e)
        rows = []
    if not rows:
        return {"skipped": True, "reason": "empty basket"}
    path = _basket_hist_path()
    try:                                      # idempotent: check the last line's date only
        from collections import deque
        with open(path, "r", encoding="utf-8") as fh:
            last = deque(fh, maxlen=1)
        if last and json.loads(last[0]).get("date") == day:
            return {"skipped": True, "reason": "already logged today", "date": day}
    except FileNotFoundError:
        pass
    except Exception:
        pass                                  # unreadable tail → just append (dupes are harmless)
    entry = {"date": day, "n": len(rows),
             "members": [{"s": r.get("symbol"), "p": r.get("price")} for r in rows if r.get("symbol")]}
    with _LOCK:
        try:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.error("basket archive write failed: %s", e)
            return {"error": str(e)}
    logger.info("basket archive: %s logged %d members", day, entry["n"])
    return {"date": day, "n": entry["n"]}


def basket_history_summary() -> dict:
    """Light summary of the PIT archive: per-day membership counts (no member lists)."""
    days = []
    try:
        with open(_basket_hist_path(), "r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                    days.append({"date": row.get("date"), "n": row.get("n")})
                except Exception:
                    continue
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.debug("basket history read failed: %s", e)
    return {"days": len(days), "series": days[-370:],
            "note": "Point-in-time halal-basket membership archive (one line/day, symbols+prices "
                    "stored on disk). Future backtests replay membership as-was — no survivorship bias."}


# ── Weekly race digest → Telegram (the system reports to the user) ────────────

def race_digest_text() -> str:
    """Compose the weekly Arabic race digest from the NAV series + graduation eval + the
    circuit-breaker state. Pure composition — no side effects."""
    h = nav_history()
    latest = h.get("latest") or {}
    def _p(v):
        return ("+" if isinstance(v, (int, float)) and v >= 0 else "") + (f"{v:.2f}%" if isinstance(v, (int, float)) else "—")
    lines = ["🏁 تقرير السباق الأسبوعيّ — MIZAN", ""]
    lines.append(f"🎯 النواة: { _p(latest.get('core_upl')) } ({latest.get('core_open') or '—'} اسماً)")
    lines.append(f"🌙 القمر (زخم): { _p(latest.get('sat_upl')) } ({latest.get('sat_open') or '—'})")
    lines.append(f"🚀 المستكشف (ذيل): { _p(latest.get('exp_upl')) } ({latest.get('exp_open') or '—'})")
    vals = {k: latest.get(k) for k in ("core_upl", "sat_upl", "exp_upl") if isinstance(latest.get(k), (int, float))}
    if vals:
        names = {"core_upl": "النواة", "sat_upl": "القمر", "exp_upl": "المستكشف"}
        lead = max(vals, key=vals.get)
        lines.append(f"المتصدّر: {names[lead]}")
    lines.append(f"أيام مسجّلة: {h.get('days', 0)}")
    try:
        from app.services.graduation_criteria import evaluate_satellites
        ev = evaluate_satellites()
        if ev.get("locked"):
            remaining = max(0, int(ev.get("min_days", 0)) - int(ev.get("span_days", 0)))
            lines.append(f"📜 المعايير مقفلة — الحكم بعد ~{remaining} يوم")
            for k in ("sat", "exp"):
                e = (ev.get("engines") or {}).get(k) or {}
                if e.get("verdict") in ("graduated", "archive"):
                    lines.append(f"⚖️ {e.get('label')}: {e.get('verdict')}")
        else:
            lines.append("📜 معايير التخرّج غير مقفلة بعد — اقفلها من صفحة محفظة النواة")
    except Exception:
        pass
    try:
        from app.services.circuit_breaker import circuit_breaker_state
        cb = circuit_breaker_state()
        lines.append("🛑 بطاقة القواطع: " + ("مُقرّة ✓" if cb.get("approved") else "لم تُقرّ بعد"))
    except Exception:
        pass
    lines.append("")
    lines.append("قياس ظلّيّ — لا صفقات آليّة. التفاصيل: /mizan → محفظة النواة")
    return "\n".join(lines)


def send_race_digest() -> dict:
    """Send the weekly digest to the configured Telegram chat. Deliberately bypasses the
    TELEGRAM_BUY_ONLY signal filter (this is the user's own status report, not a buy signal)
    by using the direct sender. Returns {sent: bool}."""
    text = race_digest_text()
    try:
        from app.services.notify import _send_text_now
        ok = _send_text_now(text)
        return {"sent": bool(ok), "chars": len(text)}
    except Exception as e:
        logger.error("race digest send failed: %s", e)
        return {"sent": False, "error": str(e), "chars": len(text)}


__all__ = ["record_nav", "nav_history", "record_basket_membership", "basket_history_summary",
           "race_digest_text", "send_race_digest"]
