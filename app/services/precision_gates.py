"""Precision entry gates — cut the worst entries without overfitting.

Four conservative, env-tunable filters applied at SELECTION time. They only ever PREVENT
risky entries (they never invent a buy), so the failure direction is safe, and each is
individually toggle-able via env. Missing data FAILS OPEN (absence of a signal must not
silently block the whole universe) — the one place that matters for honesty.

  1. earnings_blackout(symbol)      — no new entry within N business days of earnings (gap risk)
  2. weekly_trend_ok(df)            — the daily entry must agree with the higher-timeframe
                                      (≈weekly) trend: price above a RISING 50-day average
  3. sector_concentration_cap(rows) — keep ≤ N names per sector in the ranked picks, so a
                                      cluster of correlated same-sector longs can't dominate
                                      (the lesson from the IBKR correlated-tech blow-up)
  4. regime_strictness()            — tighten in RISK-OFF: require the weekly-trend gate and
                                      add a small composite penalty (GATE_CONFIG already
                                      scales the min/strong thresholds by regime)

Env: PRECISION_GATES_ENABLED (master), GATE_EARNINGS, GATE_WEEKLY_TREND, GATE_SECTOR_CAP,
GATE_REGIME, EARNINGS_BLACKOUT_DAYS (3), MAX_PICKS_PER_SECTOR (3).
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger("screener")


def _flag(name: str, default: str = "true") -> bool:
    return os.environ.get(name, default).lower() in ("true", "1", "yes", "on")


GATES_ENABLED = _flag("PRECISION_GATES_ENABLED")
EARNINGS_GATE_ON = _flag("GATE_EARNINGS")
WEEKLY_TREND_GATE_ON = _flag("GATE_WEEKLY_TREND")
SECTOR_CAP_ON = _flag("GATE_SECTOR_CAP")
REGIME_GATE_ON = _flag("GATE_REGIME")
EARNINGS_BLACKOUT_DAYS = int(os.environ.get("EARNINGS_BLACKOUT_DAYS", "3"))
MAX_PER_SECTOR = int(os.environ.get("MAX_PICKS_PER_SECTOR", "3"))


def earnings_blackout(symbol: str, days: int | None = None) -> tuple[bool, str | None]:
    """(blocked, reason). True when earnings falls within ``days`` business days — a new
    swing entry then carries binary gap risk. Fails OPEN (unknown date → not blocked)."""
    if not (GATES_ENABLED and EARNINGS_GATE_ON):
        return False, None
    days = EARNINGS_BLACKOUT_DAYS if days is None else days
    try:
        from app.services.reference_data import business_days_until_earnings
        d = business_days_until_earnings(symbol)
    except Exception as e:
        logger.debug("earnings_blackout %s: %s", symbol, e)
        return False, None
    if d is None:
        return False, None
    if 0 <= d <= days:
        return True, f"earnings in {d} business day(s) ≤ {days}"
    return False, None


def weekly_trend_ok(df) -> tuple[bool, str | None]:
    """(ok, reason). Higher-timeframe agreement: price above a RISING ~50-day average
    (a robust weekly-uptrend proxy that needs no resampling). Fails OPEN on thin history."""
    if not (GATES_ENABLED and WEEKLY_TREND_GATE_ON):
        return True, None
    try:
        close = df["close"].astype(float)
        if len(close) < 60:
            return True, None  # not enough to judge the higher trend → don't block
        sma = close.rolling(50).mean()
        price = float(close.iloc[-1])
        sma_now = float(sma.iloc[-1])
        sma_prev = float(sma.iloc[-11])  # ~2 weeks ago
        if price < sma_now:
            return False, f"price {price:.2f} < 50d avg {sma_now:.2f} (against higher trend)"
        if sma_now < sma_prev:
            return False, f"50d avg falling ({sma_now:.2f} < {sma_prev:.2f})"
        return True, None
    except Exception as e:
        logger.debug("weekly_trend_ok: %s", e)
        return True, None


def sector_concentration_cap(rows: list, sector_key: str = "sector",
                             max_per_sector: int | None = None) -> list:
    """Keep at most ``max_per_sector`` names per sector from an ALREADY-RANKED list
    (order preserved → the highest-ranked names in each sector survive). Names with an
    unknown sector are never dropped (absence ≠ concentration)."""
    if not (GATES_ENABLED and SECTOR_CAP_ON) or not rows:
        return rows
    cap = MAX_PER_SECTOR if max_per_sector is None else max_per_sector
    if cap <= 0:
        return rows
    seen: dict[str, int] = {}
    out: list = []
    for r in rows:
        sec = str((r.get(sector_key) if isinstance(r, dict) else "") or "").lower().strip()
        if not sec:
            out.append(r)
            continue
        if seen.get(sec, 0) >= cap:
            continue
        seen[sec] = seen.get(sec, 0) + 1
        out.append(r)
    return out


def regime_strictness() -> dict:
    """Extra strictness by market regime. GATE_CONFIG already scales the min/strong score
    thresholds; this adds (a) whether to REQUIRE the weekly-trend gate, and (b) a small
    composite penalty — both off in RISK ON, escalating into stress."""
    if not (GATES_ENABLED and REGIME_GATE_ON):
        return {"status": "OFF", "require_weekly_trend": False, "composite_penalty": 0}
    try:
        from app.services.market_context import get_market_status
        status = get_market_status().get("status", "RISK ON")
    except Exception:
        status = "RISK ON"
    penalty = {"RISK ON": 0, "CAUTION": 0, "CREDIT STRESS": 5, "EXTREME FEAR": 100}.get(status, 0)
    require_wk = status in ("CAUTION", "CREDIT STRESS", "EXTREME FEAR")
    return {"status": status, "require_weekly_trend": require_wk, "composite_penalty": penalty}


def entry_gate_failures(symbol: str, df, *, require_weekly_trend: bool | None = None) -> list[str]:
    """Run the per-symbol entry gates (earnings + weekly trend) and return the list of
    failure reasons (empty = passed). The weekly-trend gate is applied when globally on OR
    when the regime requires it. Used by both scanners' entry paths."""
    fails: list[str] = []
    blocked, why = earnings_blackout(symbol)
    if blocked and why:
        fails.append(why)
    reg = regime_strictness()
    want_wk = WEEKLY_TREND_GATE_ON if require_weekly_trend is None else require_weekly_trend
    want_wk = want_wk or reg.get("require_weekly_trend", False)
    if GATES_ENABLED and want_wk:
        ok, why = weekly_trend_ok(df)
        if not ok and why:
            fails.append(why)
    return fails
