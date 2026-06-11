"""USX early-entry layer — dual-version with v1 literature priors as default.

v1 (commit 24056d4): weights from published anomaly research — squeeze/RS-line
high/volume-dry-up/52w-proximity dominate (80) over confirming (20).

v2 (commit 7961a17): weights re-fitted from 8,849 measured buy-signal outcomes.
*** HONESTY ***: ALL 8,849 outcomes share ONE calendar month (2026-05). The v2
weights were therefore fitted to a single market month — insufficient temporal
diversity. Defaulting to literature priors (v1) until >=4 distinct months of
matured, recorded weekly outcomes exist. v2 retained behind USX_VERSION=v2 for
future re-validation once the data supports it.

HONESTY: this reduces noise; it does NOT guarantee an edge. The paper ledger is
the judge. Pure functions + injectable deps -> unit-tested fully offline.
"""
from __future__ import annotations

import logging
import os

import pandas as pd

from app.services.technical import adx, bollinger_bands, macd, relative_strength, rsi as _ta_rsi

logger = logging.getLogger("screener")

USX_ACTIVE_VERSION = os.environ.get("USX_VERSION", "v1").lower()
USX_PASS_THRESHOLD = 60.0

# ---- v1 weights (sum = 100). Leading signals dominate (80) over confirming (20). ----
V1_W_SQUEEZE = 25.0
V1_W_RS = 25.0
V1_W_VOLDRY = 15.0
V1_W_52W = 15.0
V1_W_MACD = 10.0
V1_W_ADX = 10.0

# ---- v2 weights (sum = 100), measured from 8,849 buy outcomes (ONE month: 2026-05) ----
V2_W_MACD = 35.0
V2_W_RS20 = 25.0
V2_W_RSI = 20.0
V2_W_EMA50 = 20.0
V2_W_SQUEEZE = 0.0
V2_W_VOLDRY = 0.0
V2_W_52W = 0.0
V2_W_ADX = 0.0


# ============================================================================
# v1 component scorers (literature priors)
# ============================================================================

def _v1_bb_squeeze_score(df: pd.DataFrame) -> tuple[float, bool]:
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
        return V1_W_SQUEEZE, True
    if bw_now <= q40:
        return V1_W_SQUEEZE * 0.6, False
    return 0.0, False


def _v1_rs_line_score(df: pd.DataFrame, spy_df: pd.DataFrame) -> tuple[float, bool]:
    """RS line (symbol/SPY) making a new high vs its own 63-day max — leads price."""
    try:
        a = df[["date", "close"]].rename(columns={"close": "c"})
        b = spy_df[["date", "close"]].rename(columns={"close": "spy"})
        m = a.merge(b, on="date", how="inner")
    except Exception:
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
        return V1_W_RS, True
    if rs_max > 0 and rs_now >= rs_max * 0.95:
        return V1_W_RS * 0.6, False
    if relative_strength(df, spy_df, period=63) > 1.05:
        return V1_W_RS * 0.4, False
    return 0.0, False


def _v1_volume_dryup_score(df: pd.DataFrame) -> tuple[float, bool]:
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
        return V1_W_VOLDRY, True
    if ratio < 0.9:
        return V1_W_VOLDRY * 0.5, False
    return 0.0, False


def _v1_proximity_52w_score(df: pd.DataFrame) -> tuple[float, float]:
    """Closeness to the 52-week (252-day) high = launchpad zone (leading)."""
    if len(df) < 30:
        return 0.0, 0.0
    high = df["high"].astype(float)
    close = float(df["close"].astype(float).iloc[-1])
    h52 = float(high.iloc[-252:].max()) if len(high) >= 252 else float(high.max())
    if h52 <= 0:
        return 0.0, 0.0
    prox = close / h52
    if prox >= 0.85:
        return V1_W_52W, round(prox * 100, 1)
    if prox >= 0.75:
        return V1_W_52W * 0.5, round(prox * 100, 1)
    return 0.0, round(prox * 100, 1)


def _v1_macd_turn_score(df: pd.DataFrame) -> float:
    """MACD histogram fresh-positive cross (confirming, lighter weight)."""
    close = df["close"].astype(float)
    if len(close) < 35:
        return 0.0
    _, _, hist = macd(close)
    hist = hist.dropna()
    if len(hist) < 2:
        return 0.0
    if float(hist.iloc[-1]) > 0 and float(hist.iloc[-2]) <= 0:
        return V1_W_MACD
    if float(hist.iloc[-1]) > float(hist.iloc[-2]):
        return V1_W_MACD * 0.5
    return 0.0


def _v1_adx_rising_score(df: pd.DataFrame) -> float:
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
        return V1_W_ADX
    if adx_now > 20:
        return V1_W_ADX * 0.5
    return 0.0


# ============================================================================
# v2 component scorers (measured from 8,849 one-month outcomes)
# ============================================================================

def _macd_turn_score(df: pd.DataFrame) -> float:
    """MACD histogram fresh-positive cross (v2 strongest weight)."""
    close = df["close"].astype(float)
    if len(close) < 35:
        return 0.0
    _, _, hist = macd(close)
    hist = hist.dropna()
    if len(hist) < 2:
        return 0.0
    if float(hist.iloc[-1]) > 0 and float(hist.iloc[-2]) <= 0:
        return V2_W_MACD
    if float(hist.iloc[-1]) > float(hist.iloc[-2]):
        return V2_W_MACD * 0.6
    return 0.0


def _rs20_score(df: pd.DataFrame, spy_df: pd.DataFrame) -> tuple[float, bool]:
    """Short-term RS vs SPY (20d)."""
    try:
        r = relative_strength(df, spy_df, period=20)
    except Exception:
        return 0.0, False
    r = float(r)
    if r >= 1.05:
        return V2_W_RS20, True
    if r >= 1.02:
        return V2_W_RS20 * 0.6, False
    if r >= 1.00:
        return V2_W_RS20 * 0.4, False
    return 0.0, False


def _rsi_score(df: pd.DataFrame) -> float:
    """RSI(14) strength."""
    close = df["close"].astype(float)
    if len(close) < 20:
        return 0.0
    try:
        val = float(_ta_rsi(close, 14).iloc[-1])
    except Exception:
        return 0.0
    if val >= 60:
        return V2_W_RSI
    if val >= 52:
        return V2_W_RSI * 0.6
    if val >= 45:
        return V2_W_RSI * 0.3
    return 0.0


def _ema50_score(df: pd.DataFrame) -> float:
    """Price above own EMA50."""
    close = df["close"].astype(float)
    if len(close) < 55:
        return 0.0
    ema50 = close.ewm(span=50, adjust=False).mean()
    if float(close.iloc[-1]) > float(ema50.iloc[-1]):
        return V2_W_EMA50
    return 0.0


# ============================================================================
# shared gates (identical in v1 and v2)
# ============================================================================

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


# ============================================================================
# composite scorers
# ============================================================================

def _compute_usx_v1(df: pd.DataFrame, spy_df: pd.DataFrame, market_status: dict) -> dict:
    """v1 composite — literature priors: squeeze, RS-line, vol-dry-up, 52w, macd, adx."""
    if df is None or getattr(df, "empty", True) or "close" not in df.columns:
        return {"usx_score": 0.0, "usx_pass": False, "gate_pass": False,
                "gate_reason": "no data", "signals": [], "breakdown": {}}

    sq, sq_on = _v1_bb_squeeze_score(df)
    rs, rs_on = (0.0, False) if spy_df is None or getattr(spy_df, "empty", True) else _v1_rs_line_score(df, spy_df)
    vd, vd_on = _v1_volume_dryup_score(df)
    px, prox_pct = _v1_proximity_52w_score(df)
    mac = _v1_macd_turn_score(df)
    ax = _v1_adx_rising_score(df)

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
    if px >= V1_W_52W:
        signals.append("52W")
    if mac >= V1_W_MACD:
        signals.append("MACD+")
    if ax >= V1_W_ADX:
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
            "version": "v1-priors",
        },
    }


def _compute_usx_v2(df: pd.DataFrame, spy_df: pd.DataFrame, market_status: dict) -> dict:
    """v2 composite — re-weighted from 8,849 one-month outcomes."""
    if df is None or getattr(df, "empty", True) or "close" not in df.columns:
        return {"usx_score": 0.0, "usx_pass": False, "gate_pass": False,
                "gate_reason": "no data", "signals": [], "breakdown": {}}

    mac = _macd_turn_score(df)
    rs, rs_on = (0.0, False) if spy_df is None or getattr(spy_df, "empty", True) else _rs20_score(df, spy_df)
    rsi = _rsi_score(df)
    ema = _ema50_score(df)

    raw = round(mac + rs + rsi + ema, 1)
    gate_pass, gate_reason = _gates(market_status or {})
    usx_pass = bool(gate_pass and raw >= USX_PASS_THRESHOLD)

    signals = []
    if mac >= V2_W_MACD:
        signals.append("MACD+")
    if rs_on:
        signals.append("RS20")
    if rsi >= V2_W_RSI:
        signals.append("RSI")
    if ema >= V2_W_EMA50:
        signals.append("EMA50")

    return {
        "usx_score": raw,
        "usx_pass": usx_pass,
        "gate_pass": gate_pass,
        "gate_reason": gate_reason,
        "signals": signals,
        "breakdown": {
            "macd_turn": mac, "rs20": rs, "rsi": rsi, "ema50": ema,
            "version": "v2-2026-06",
        },
    }


# ============================================================================
# public dispatcher (reads USX_ACTIVE_VERSION at call time)
# ============================================================================

def compute_usx_early(df: pd.DataFrame, spy_df: pd.DataFrame, market_status: dict) -> dict:
    """Compute the USX early-entry score + gates for one symbol.

    Dispatches to v1 or v2 based on USX_ACTIVE_VERSION (env USX_VERSION, default v1).
    """
    ver = os.environ.get("USX_VERSION", "v1").lower()
    if ver == "v2":
        return _compute_usx_v2(df, spy_df, market_status)
    return _compute_usx_v1(df, spy_df, market_status)


# ============================================================================
# enrichment with shadow scoring
# ============================================================================

def enrich_picks_with_usx(picks: list[dict], *, _fetch=None, _spy_df=None, _status=None) -> list[dict]:
    """Annotate each weekly pick in-place with usx_* fields. Never raises.

    Active version drives usx_score/usx_pass/usx_signals. Shadow records
    BOTH version scores on every pick for future A/B comparison at zero extra cost.
    """
    if not picks:
        return picks

    active_version = os.environ.get("USX_VERSION", "v1").lower()
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
        except Exception as e:
            logger.debug("usx: %s fetch failed: %s", sym, e)
            df = None

        # Active version (drives usx_score, usx_pass, usx_signals)
        try:
            if active_version == "v2":
                res = _compute_usx_v2(df, spy_df, status)
                version_str = "v2-2026-06"
            else:
                res = _compute_usx_v1(df, spy_df, status)
                version_str = "v1-priors"
        except Exception as e:
            logger.debug("usx: %s active failed: %s", sym, e)
            res = {"usx_score": 0.0, "usx_pass": False, "gate_pass": False,
                   "gate_reason": "error", "signals": [], "breakdown": {}}
            version_str = active_version

        p["usx_score"] = res["usx_score"]
        p["usx_pass"] = res["usx_pass"]
        p["usx_gate"] = res["gate_reason"]
        p["usx_signals"] = res["signals"]
        p["usx_breakdown"] = res["breakdown"]
        p["usx_version"] = version_str

        # Shadow: compute the OTHER version's raw score (never affects active path)
        shadow_v1 = None
        shadow_v2 = None
        try:
            if active_version != "v1":
                sres = _compute_usx_v1(df, spy_df, status)
                shadow_v1 = sres["usx_score"]
            if active_version != "v2":
                sres = _compute_usx_v2(df, spy_df, status)
                shadow_v2 = sres["usx_score"]
        except Exception:
            pass  # shadow failure must never affect active

        p["usx_shadow"] = {
            "v1_score": shadow_v1,
            "v2_score": shadow_v2,
            "active": active_version,
        }

    return picks
