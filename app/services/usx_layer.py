"""USX early-entry layer — leading-signal overlay on the weekly scanner.

Annotates weekly picks with a forward-looking score that weights PRE-breakout
signals (BB squeeze, RS-line new high, volume dry-up, 52w-high proximity) above
lagging confirmations (MACD, ADX). Two hard gates (market regime + credit) come
from get_market_status() — the same inputs as the USX PRO dashboard.

HONESTY: this reduces noise; it does NOT guarantee an edge. The paper ledger is
the judge. Pure functions + injectable deps → unit-tested fully offline.
"""
from __future__ import annotations

import logging

import pandas as pd

from app.services.technical import adx, bollinger_bands, macd, relative_strength

logger = logging.getLogger("screener")

USX_PASS_THRESHOLD = 60.0  # min usx_score (with gates passing) to be flagged usx_pass

# Score weights (sum = 100). Leading signals dominate (80) over confirming (20).
W_SQUEEZE = 25.0
W_RS = 25.0
W_VOLDRY = 15.0
W_52W = 15.0
W_MACD = 10.0
W_ADX = 10.0


def _bb_squeeze_score(df: pd.DataFrame) -> tuple[float, bool]:
    """Volatility contraction: current bandwidth in the low end of its 6-month range."""
    close = df["close"].astype(float)
    if len(close) < 40:
        return 0.0, False
    _, _, _, bw = bollinger_bands(close, 20, 2)
    bw = bw.dropna()
    if len(bw) < 20:
        return 0.0, False
    bw_now = float(bw.iloc[-1])
    window = bw.iloc[-120:] if len(bw) >= 120 else bw
    q25 = float(window.quantile(0.25))
    q40 = float(window.quantile(0.40))
    if bw_now <= q25:
        return W_SQUEEZE, True
    if bw_now <= q40:
        return W_SQUEEZE * 0.6, False
    return 0.0, False


def _rs_line_score(df: pd.DataFrame, spy_df: pd.DataFrame) -> tuple[float, bool]:
    """RS line (symbol/SPY) making a new high vs its own 63-day max — leads price."""
    try:
        a = df[["date", "close"]].rename(columns={"close": "c"})
        b = spy_df[["date", "close"]].rename(columns={"close": "spy"})
        m = a.merge(b, on="date", how="inner")
    except Exception:
        # fallback: tail-align by position (same US session calendar)
        n = min(len(df), len(spy_df))
        if n < 30:
            return 0.0, False
        rs = (df["close"].astype(float).iloc[-n:].reset_index(drop=True)
              / spy_df["close"].astype(float).iloc[-n:].reset_index(drop=True))
        m = None
    if m is not None:
        if len(m) < 30:
            return 0.0, False
        rs = (m["c"].astype(float) / m["spy"].astype(float))
    rs = rs.dropna()
    if len(rs) < 30:
        return 0.0, False
    rs_now = float(rs.iloc[-1])
    rs_max = float(rs.iloc[-63:].max()) if len(rs) >= 63 else float(rs.max())
    if rs_max > 0 and rs_now >= rs_max * 0.98:
        return W_RS, True            # new RS high — strongest leading signal
    if rs_max > 0 and rs_now >= rs_max * 0.95:
        return W_RS * 0.6, False
    # still a leader by the existing ratio helper?
    if relative_strength(df, spy_df, period=63) > 1.05:
        return W_RS * 0.4, False
    return 0.0, False


def _volume_dryup_score(df: pd.DataFrame) -> tuple[float, bool]:
    """Low recent volume vs its 20-day average = base accumulation (leading)."""
    if "volume" not in df.columns or len(df) < 20:
        return 0.0, False
    vol = df["volume"].astype(float)
    vol5 = float(vol.iloc[-5:].mean())
    vol20 = float(vol.iloc[-20:].mean())
    if vol20 <= 0:
        return 0.0, False
    ratio = vol5 / vol20
    if ratio < 0.7:
        return W_VOLDRY, True
    if ratio < 0.9:
        return W_VOLDRY * 0.5, False
    return 0.0, False


def _proximity_52w_score(df: pd.DataFrame) -> tuple[float, float]:
    """Closeness to the 52-week (252-day) high = launchpad zone (leading)."""
    if len(df) < 30:
        return 0.0, 0.0
    high = df["high"].astype(float)
    close = float(df["close"].astype(float).iloc[-1])
    h52 = float(high.iloc[-252:].max()) if len(high) >= 252 else float(high.max())
    if h52 <= 0:
        return 0.0, 0.0
    prox = close / h52  # 1.0 = at the high
    if prox >= 0.85:
        return W_52W, round(prox * 100, 1)
    if prox >= 0.75:
        return W_52W * 0.5, round(prox * 100, 1)
    return 0.0, round(prox * 100, 1)


def _macd_turn_score(df: pd.DataFrame) -> float:
    """MACD histogram fresh-positive cross (confirming, lighter weight)."""
    close = df["close"].astype(float)
    if len(close) < 35:
        return 0.0
    _, _, hist = macd(close)
    hist = hist.dropna()
    if len(hist) < 2:
        return 0.0
    if float(hist.iloc[-1]) > 0 and float(hist.iloc[-2]) <= 0:
        return W_MACD
    if float(hist.iloc[-1]) > float(hist.iloc[-2]):
        return W_MACD * 0.5
    return 0.0


def _adx_rising_score(df: pd.DataFrame) -> float:
    """ADX > 20 and rising with +DI > -DI (confirming, lighter weight)."""
    if len(df) < 30:
        return 0.0
    adx_s, plus_di, minus_di = adx(df)
    adx_s = adx_s.dropna()
    if len(adx_s) < 6:
        return 0.0
    adx_now = float(adx_s.iloc[-1])
    adx_prev = float(adx_s.iloc[-5])
    bullish = float(plus_di.iloc[-1]) > float(minus_di.iloc[-1])
    if adx_now > 20 and adx_now > adx_prev and bullish:
        return W_ADX
    if adx_now > 20:
        return W_ADX * 0.5
    return 0.0


def _gates(market_status: dict) -> tuple[bool, str]:
    """Hard gates from the USX PRO dashboard inputs. Returns (pass, reason)."""
    status = str(market_status.get("status", "")).upper()
    regime = str(market_status.get("regime", "")).upper()
    if market_status.get("halt_pipeline") or status == "EXTREME FEAR":
        return False, "halt: extreme fear (VIX)"
    if status == "CREDIT STRESS":
        return False, "credit stress (HY/IG)"
    if regime == "BEAR":
        return False, "regime BEAR"
    return True, "ok"


def compute_usx_early(df: pd.DataFrame, spy_df: pd.DataFrame, market_status: dict) -> dict:
    """Compute the USX early-entry score + gates for one symbol.

    Returns dict: {usx_score, usx_pass, gate_pass, gate_reason, signals[list], breakdown{}}.
    Gate failure does NOT zero the raw score (transparency) but forces usx_pass=False.
    """
    if df is None or getattr(df, "empty", True) or "close" not in df.columns:
        return {"usx_score": 0.0, "usx_pass": False, "gate_pass": False,
                "gate_reason": "no data", "signals": [], "breakdown": {}}

    sq, sq_on = _bb_squeeze_score(df)
    rs, rs_on = (0.0, False) if spy_df is None or getattr(spy_df, "empty", True) else _rs_line_score(df, spy_df)
    vd, vd_on = _volume_dryup_score(df)
    px, prox_pct = _proximity_52w_score(df)
    mac = _macd_turn_score(df)
    ax = _adx_rising_score(df)

    raw = round(sq + rs + vd + px + mac + ax, 1)
    gate_pass, gate_reason = _gates(market_status or {})
    usx_pass = bool(gate_pass and raw >= USX_PASS_THRESHOLD)

    signals = []
    if sq_on:
        signals.append("SQUEEZE")
    if rs_on:
        signals.append("RS-HIGH")
    if vd_on:
        signals.append("VOL-DRY")
    if px >= W_52W:
        signals.append("52W")
    if mac >= W_MACD:
        signals.append("MACD+")
    if ax >= W_ADX:
        signals.append("ADX")

    return {
        "usx_score": raw,
        "usx_pass": usx_pass,
        "gate_pass": gate_pass,
        "gate_reason": gate_reason,
        "signals": signals,
        "breakdown": {
            "squeeze": sq, "rs_line": rs, "vol_dryup": vd,
            "prox_52w": px, "prox_52w_pct": prox_pct, "macd_turn": mac, "adx": ax,
        },
    }


def enrich_picks_with_usx(picks: list[dict], *, _fetch=None, _spy_df=None, _status=None) -> list[dict]:
    """Annotate each weekly pick in-place with usx_* fields. Never raises.

    Injectable deps for tests: _fetch(symbol)->df, _spy_df (DataFrame), _status (dict).
    """
    if not picks:
        return picks
    try:
        if _status is not None:
            status = _status
        else:
            from app.services.market_context import get_market_status
            status = get_market_status()
    except Exception as e:
        logger.debug("usx: market status failed: %s", e)
        status = {}

    fetch = _fetch
    if fetch is None:
        from app.services import market_data as md
        def fetch(sym):
            return md.fetch(sym, period="1y")

    spy_df = _spy_df
    if spy_df is None:
        try:
            spy_df = fetch("SPY")
        except Exception as e:
            logger.debug("usx: SPY fetch failed: %s", e)
            spy_df = None

    for p in picks:
        sym = p.get("symbol")
        try:
            df = fetch(sym) if sym else None
            res = compute_usx_early(df, spy_df, status)
        except Exception as e:
            logger.debug("usx: %s failed: %s", sym, e)
            res = {"usx_score": 0.0, "usx_pass": False, "gate_pass": False,
                   "gate_reason": "error", "signals": [], "breakdown": {}}
        p["usx_score"] = res["usx_score"]
        p["usx_pass"] = res["usx_pass"]
        p["usx_gate"] = res["gate_reason"]
        p["usx_signals"] = res["signals"]
        p["usx_breakdown"] = res["breakdown"]
    return picks
