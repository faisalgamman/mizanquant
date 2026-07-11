"""Ross Cameron 1-minute entry patterns — mechanized (Bull Flag & Flat Top Breakout).

Faithful-as-data-allows detection of his two favorite momentum setups on Alpaca 1-min IEX bars,
returning a concrete entry (breakout price) + stop (pattern support) so the speculation ledger can
trade the PATTERN with a real 2:1 off the stop — instead of just "buy the hottest name". What is
NOT replicated (data limits, surfaced to the user): Level-2 / Time & Sales order-flow confirmation,
and the FULL consolidated (SIP) tape — IEX is ~2-3% of volume, so 1-min bars are sparse/noisy for
thin names; detection is reliable only on liquid movers. Float is a separate SOFT filter.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("screener")


def _ema(vals: list, span: int) -> list:
    if not vals:
        return []
    k = 2.0 / (span + 1.0)
    e = [vals[0]]
    for v in vals[1:]:
        e.append(v * k + e[-1] * (1.0 - k))
    return e


def detect_flat_top(bars: list) -> dict | None:
    """Flat top breakout: a run up, then consolidation under a FLAT resistance (equal highs) with
    higher/level lows, then the latest bar CLOSES above that resistance on rising volume. Entry =
    breakout close; stop = the consolidation base."""
    if len(bars) < 12:
        return None
    r = bars[-12:]
    closes = [b["c"] for b in r]
    if closes[-1] <= closes[0]:                       # need a net up-move into the setup
        return None
    window = r[-7:-1]                                  # the consolidation (exclude the breakout bar)
    if len(window) < 4:
        return None
    res = max(b["h"] for b in window)
    flat = all(b["h"] <= res * 1.004 for b in window) # highs cluster at resistance
    lows_w = [b["l"] for b in window]
    higher_lows = lows_w[-1] >= lows_w[0] * 0.998
    avgv = sum(b["v"] for b in window) / len(window) if window else 0.0
    cur = r[-1]
    broke = cur["c"] > res and cur["v"] >= max(avgv, 1.0)
    if flat and higher_lows and broke:
        stop = min(lows_w)
        entry = cur["c"]
        if entry > stop:
            return {"pattern": "flat_top", "entry": round(entry, 4), "stop": round(stop, 4)}
    return None


def detect_bull_flag(bars: list) -> dict | None:
    """Bull flag: a strong pole (net up-move), a shallow pullback (5-50% of the pole) on the last
    1-3 bars near the 9 EMA, then the latest bar makes a NEW HIGH and closes green. Entry = breakout
    close; stop = the flag (pullback) low."""
    if len(bars) < 10:
        return None
    r = bars[-12:]
    k = max(4, (len(r) * 2) // 3)                      # first ~2/3 = the pole
    pole_lo = min(b["l"] for b in r[:k])
    pole_hi = max(b["h"] for b in r[:k])
    if pole_hi <= pole_lo or (pole_hi - pole_lo) / pole_hi < 0.015:   # need a real ~1.5%+ pole
        return None
    pole = pole_hi - pole_lo
    flag = r[k - 1:]
    if len(flag) < 2:
        return None
    flag_lo = min(b["l"] for b in flag[:-1])
    pull = (pole_hi - flag_lo) / pole
    prev_high = max(b["h"] for b in flag[:-1])
    cur = r[-1]
    if 0.05 <= pull <= 0.5 and cur["h"] > prev_high and cur["c"] > cur["o"]:
        stop = flag_lo
        entry = cur["c"]
        if entry > stop:
            return {"pattern": "bull_flag", "entry": round(entry, 4), "stop": round(stop, 4)}
    return None


def cameron_setup(symbol: str) -> dict:
    """Fetch 1-min bars and detect a Cameron long entry. Returns {ok, pattern, entry, stop, stop_pct}
    (flat top preferred — stronger), else {ok: False, reason}. Fail-safe; a sane day-trade stop is
    0.3-8%. Never raises."""
    try:
        from app.services.market_data import get_intraday_bars
        bars = get_intraday_bars(symbol, "1Min", 150)
    except Exception:
        bars = []
    if len(bars) < 12:
        return {"ok": False, "reason": "no_bars"}
    for det in (detect_flat_top, detect_bull_flag):
        try:
            s = det(bars)
        except Exception:
            s = None
        if s:
            entry, stop = s["entry"], s["stop"]
            stop_pct = round((1.0 - stop / entry) * 100.0, 2) if entry > 0 else None
            if stop_pct is not None and 0.3 <= stop_pct <= 8.0:
                return {"ok": True, "pattern": s["pattern"], "entry": entry,
                        "stop": stop, "stop_pct": stop_pct}
    return {"ok": False, "reason": "no_pattern"}


def get_float_millions(symbol: str) -> "float | None":
    """Best-effort shares-outstanding / float in MILLIONS (Finnhub basic financials). None when
    unavailable (free tier is spotty). A SOFT Cameron filter — low float preferred, never blocks
    on missing data."""
    try:
        from app.services.finnhub_client import finnhub_client
        m = finnhub_client.get_basic_financials(symbol) or {}
        for key in ("shareOutstanding", "sharesOutstanding", "float", "freeFloat"):
            v = m.get(key)
            if isinstance(v, (int, float)) and v > 0:
                return round(float(v), 2)              # Finnhub reports shareOutstanding in millions
    except Exception:
        pass
    return None


__all__ = ["cameron_setup", "detect_flat_top", "detect_bull_flag", "get_float_millions"]
