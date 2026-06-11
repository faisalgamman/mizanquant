"""USX early-entry layer — dual-version with v1.1 literature priors as default.

v1.1 (math-fidelity upgrade): each component matches its published source.
  - TTM Squeeze (John Carter): BB(20,2) inside Keltner(20,1.5×ATR); fires on release.
  - Minervini Trend Template: 5-criteria count replaces naive 52w-proximity.
  - IBD RS-line new high: merge-on-date + rising (RS > 21 bars ago) condition.
  - VCP volume dry-up: only inside a price base (contraction + near-high).
  - Tradability gates: >=200 bars, >=$5, >=$2M ADV — standard screens, not fitted.
  - MACD + ADX confirming: unchanged from v1.

v2 (commit 7961a17): weights re-fitted from 8,849 measured buy-signal outcomes.
*** HONESTY ***: ALL 8,849 outcomes share ONE calendar month (2026-05). The v2
weights were therefore fitted to a single market month — insufficient temporal
diversity. Defaulting to literature priors (v1.1) until >=4 distinct months of
matured, recorded weekly outcomes exist. v2 retained behind USX_VERSION=v2 for
future re-validation once the data supports it.

*** NO DATA-FITTED WEIGHTS IN v1.1 *** — weights are the published literature set
(SQUEEZE 25/RS 25/VOLDRY 15/52W 15/MACD 10/ADX 10). Only math fidelity and input
filtering changed from v1. Threshold 60 unchanged.

HONESTY: this reduces noise; it does NOT guarantee an edge. The paper ledger is
the judge. Pure functions + injectable deps -> unit-tested fully offline.
"""
from __future__ import annotations

import logging
import os

import numpy as np
import pandas as pd

from app.services.technical import adx, bollinger_bands, macd, relative_strength, rsi as _ta_rsi

logger = logging.getLogger("screener")

USX_ACTIVE_VERSION = os.environ.get("USX_VERSION", "v1").lower()
USX_PASS_THRESHOLD = 60.0

# ---- v1.1 weights (sum = 100). Literature priors — NO data-fitting. ----
V1_W_SQUEEZE = 25.0
V1_W_RS = 25.0
V1_W_VOLDRY = 15.0
V1_W_52W = 15.0   # now powers Minervini Trend Template (was 52w-proximity)
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
# v1.1 component scorers — literature fidelity (Carter / Minervini / IBD / VCP)
# ============================================================================

def _v1_ttm_squeeze_score(df: pd.DataFrame) -> tuple[float, bool]:
    """TTM Squeeze (John Carter): BB(20,2) inside Keltner(20,1.5×ATR).

    Score: full W_SQUEEZE when squeeze fires upward (was ON within last 5 bars,
    now releasing with last close > previous close). 60% when squeeze currently ON.
    Returns (score, fired).
    """
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    n = len(close)
    if n < 30:
        return 0.0, False

    # True range
    prev_close = close.shift(1)
    tr = np.maximum(high - low, np.maximum(abs(high - prev_close), abs(low - prev_close)))
    tr = tr.iloc[1:]  # drop first NaN from shift
    close1 = close.iloc[1:]
    if len(tr) < 20:
        return 0.0, False

    # ATR(20) — Wilder's smoothing via EMA
    atr = tr.ewm(span=20, adjust=False).mean().values

    # Bollinger Bands (20, 2) — on close[1:] to align with ATR
    _, bb_upper, bb_lower, _ = bollinger_bands(close1, 20, 2)
    bb_upper = bb_upper.dropna().values
    bb_lower = bb_lower.dropna().values

    # Keltner Channel (20, 1.5×ATR) — middle = EMA20 on close[1:]
    ema20 = close1.ewm(span=20, adjust=False).mean().values
    kc_upper = ema20 + 1.5 * atr
    kc_lower = ema20 - 1.5 * atr

    # Align lengths to the shortest
    m = min(len(bb_upper), len(kc_upper))
    if m < 5:
        return 0.0, False
    bb_upper = bb_upper[-m:]
    bb_lower = bb_lower[-m:]
    kc_upper = kc_upper[-m:]
    kc_lower = kc_lower[-m:]
    close_aligned = close.values[-m:]

    # Squeeze ON: BB inside KC
    squeeze_on = (bb_upper < kc_upper) & (bb_lower > kc_lower)
    squeeze_now = bool(squeeze_on[-1])

    if not squeeze_now:
        # Check if fired recently: was ON in last 5 bars, now OFF
        was_on = squeeze_on[-6:-1].any() if len(squeeze_on) >= 6 else squeeze_on[:-1].any()
        if was_on and len(close_aligned) >= 2 and float(close_aligned[-1]) > float(close_aligned[-2]):
            return V1_W_SQUEEZE, True  # fired upward

    if squeeze_now:
        return V1_W_SQUEEZE * 0.6, False  # building

    return 0.0, False


def _v1_trend_template_score(df: pd.DataFrame) -> tuple[float, bool]:
    """Minervini Trend Template — 5 published criteria (SMA, not EMA).

    Needs >=200 bars. Score = W_52W * (criteria_met / 5). on = all 5 passed.
    """
    close = df["close"].astype(float)
    low = df["low"].astype(float)
    high = df["high"].astype(float)
    n = len(close)
    if n < 200:
        return 0.0, False

    ma50 = close.rolling(50).mean()
    ma150 = close.rolling(150).mean()
    ma200 = close.rolling(200).mean()

    cnow = float(close.iloc[-1])
    criteria = 0

    # 1. close > MA50
    if cnow > float(ma50.iloc[-1]):
        criteria += 1

    # 2. MA50 > MA150 > MA200
    ma50v = float(ma50.iloc[-1])
    ma150v = float(ma150.iloc[-1])
    ma200v = float(ma200.iloc[-1])
    if ma50v > ma150v > ma200v:
        criteria += 1

    # 3. MA200 rising vs 21 bars ago
    if len(ma200.dropna()) >= 22 and float(ma200.iloc[-1]) > float(ma200.iloc[-22]):
        criteria += 1

    # 4. close >= 1.30 × 52w-low
    low52 = float(low.iloc[-252:].min()) if n >= 252 else float(low.min())
    if low52 > 0 and cnow >= 1.30 * low52:
        criteria += 1

    # 5. close >= 0.75 × 52w-high (within 25%)
    high52 = float(high.iloc[-252:].max()) if n >= 252 else float(high.max())
    if high52 > 0 and cnow >= 0.75 * high52:
        criteria += 1

    score = round(V1_W_52W * criteria / 5, 1)
    on = criteria == 5
    return score, on


def _v1_rs_line_score(df: pd.DataFrame, spy_df: pd.DataFrame) -> tuple[float, bool]:
    """IBD RS-line new high: merge-on-date inner-join, RS = close/spy_close.

    Full W_RS: RS >= 0.98×63-bar max AND RS > 21 bars ago (rising).
    60%: RS >= 0.95×63-bar max.
    40% fallback: relative_strength(period=63) > 1.05.
    """
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
    if len(rs) < 63:
        return 0.0, False
    rs_now = float(rs.iloc[-1])
    rs_max = float(rs.iloc[-63:].max())
    rs_21_ago = float(rs.iloc[-22]) if len(rs) >= 22 else 0.0
    if rs_max > 0 and rs_now >= rs_max * 0.98 and rs_now > rs_21_ago:
        return V1_W_RS, True
    if rs_max > 0 and rs_now >= rs_max * 0.95:
        return V1_W_RS * 0.6, False
    if relative_strength(df, spy_df, period=63) > 1.05:
        return V1_W_RS * 0.4, False
    return 0.0, False


def _v1_volume_dryup_score(df: pd.DataFrame) -> tuple[float, bool]:
    """VCP volume dry-up: low vol5/vol20 ratio ONLY when price is in a base.

    In a base: close within 10% of 40-bar high AND 20-bar realized range
    tighter than the prior 20-bar range. Without a base → 0.
    """
    if "volume" not in df.columns or len(df) < 40:
        return 0.0, False
    close = df["close"].astype(float)
    vol = df["volume"].astype(float)
    high = df["high"].astype(float)

    # Base check
    cnow = float(close.iloc[-1])
    h40 = float(high.iloc[-40:].max())
    if h40 <= 0:
        return 0.0, False
    near_high = cnow >= h40 * 0.90

    # Contraction: 20-bar range < prior 20-bar range
    rng20 = float(high.iloc[-20:].max()) - float(df["low"].astype(float).iloc[-20:].min())
    rng_prior = float(high.iloc[-40:-20].max()) - float(df["low"].astype(float).iloc[-40:-20].min())
    contracting = rng20 < rng_prior if rng_prior > 0 else False

    if not (near_high and contracting):
        return 0.0, False  # no base → dry-up signal is meaningless

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


# ── v1 confirming scorers (unchanged from v1) ───────────────────────────────

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
# input filtering gates (tradability — NOT fitted)
# ============================================================================

def _input_gates(df: pd.DataFrame) -> tuple[bool, str]:
    """Hard input-quality gates. Returns (pass, reason)."""
    if df is None or len(df) < 200:
        return False, "insufficient history"
    close = df["close"].astype(float)
    if float(close.iloc[-1]) < 5.0:
        return False, "price < $5"
    if "volume" in df.columns:
        close_v = close.values
        vol_v = df["volume"].astype(float).values
        adv20 = float(np.mean(vol_v[-20:] * close_v[-20:])) if len(vol_v) >= 20 else 0.0
        if adv20 < 2_000_000:
            return False, "illiquid (<$2M ADV)"
    return True, "ok"


# ============================================================================
# composite scorers
# ============================================================================

def _compute_usx_v1(df: pd.DataFrame, spy_df: pd.DataFrame, market_status: dict) -> dict:
    """v1.1 composite — TTM Squeeze, Minervini Template, IBD RS, VCP dry-up, MACD, ADX."""
    if df is None or getattr(df, "empty", True) or "close" not in df.columns:
        return {"usx_score": 0.0, "usx_pass": False, "gate_pass": False,
                "gate_reason": "no data", "signals": [], "breakdown": {}}

    # Input filtering gates (G5: tradability screens)
    inp_pass, inp_reason = _input_gates(df)
    if not inp_pass:
        return {"usx_score": 0.0, "usx_pass": False, "gate_pass": False,
                "gate_reason": inp_reason, "signals": [], "breakdown": {}}

    sq, sq_fired = _v1_ttm_squeeze_score(df)
    rs, rs_on = (0.0, False) if spy_df is None or getattr(spy_df, "empty", True) else _v1_rs_line_score(df, spy_df)
    vd, vd_on = _v1_volume_dryup_score(df)
    tt, tt_all = _v1_trend_template_score(df)
    mac = _v1_macd_turn_score(df)
    ax = _v1_adx_rising_score(df)

    raw = round(sq + rs + vd + tt + mac + ax, 1)
    gate_pass, gate_reason = _gates(market_status or {})
    usx_pass = bool(gate_pass and raw >= USX_PASS_THRESHOLD)

    signals = []
    if sq_fired:
        signals.append("SQUEEZE")
    if rs_on:
        signals.append("RS-HIGH")
    if vd_on:
        signals.append("VOL-DRY")
    if tt_all:
        signals.append("TEMPLATE")
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
            "trend_template": tt, "macd_turn": mac, "adx": ax,
            "version": "v1.1-priors",
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

    version_str = "v1.1-priors" if active_version != "v2" else "v2-2026-06"

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
            else:
                res = _compute_usx_v1(df, spy_df, status)
        except Exception as e:
            logger.debug("usx: %s active failed: %s", sym, e)
            res = {"usx_score": 0.0, "usx_pass": False, "gate_pass": False,
                   "gate_reason": "error", "signals": [], "breakdown": {}}

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
