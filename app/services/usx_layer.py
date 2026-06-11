"""USX v2 early-entry layer — re-weighted from 8,849 measured buy-signal outcomes.

Weights re-fit from rank-IC vs realized outcome AND vs excess-over-SPY
(Phase-1 buy-side report, 2026-06, Option-A exit ~20 days). Squeeze (+0.12
for EXPANSION — the OPPOSITE of the squeeze thesis), 52w-proximity (-0.21,
strongest NEGATIVE), volume-dry-up (+0.05 opposite direction), and ADX
(-0.07) all reward the WRONG side → ZEROED (not inverted — inversion needs
out-of-sample proof). Transfer assumption: sample = consensus signals (the
weekly scanner had NO recorded history); same universe and exit policy.

v1 (squeeze-dominant) → v2 (early-inflection, momentum-rotation).

HONESTY: this reduces noise; it does NOT guarantee an edge. The paper ledger
is the judge. Pure functions + injectable deps → unit-tested fully offline.
"""
from __future__ import annotations

import logging

import pandas as pd

from app.services.technical import macd, relative_strength, rsi as _ta_rsi

logger = logging.getLogger("screener")

USX_VERSION = "v2-2026-06"
USX_PASS_THRESHOLD = 60.0

# ── v2 weights (sum = 100), measured from 8,849 buy outcomes ──────────────
W_MACD  = 35.0   # momentum inflection — measured IC +0.154 (strongest)
W_RS20  = 25.0   # SHORT-term RS vs SPY (20d) — measured +0.053 (63d RS negative)
W_RSI   = 20.0   # RSI(14) strength — measured +0.085
W_EMA50 = 20.0   # price above own EMA50 — measured +0.048
# Zeroed (measured WRONG-direction; NOT inverted — inversion = new hypothesis):
W_SQUEEZE = 0.0
W_VOLDRY  = 0.0
W_52W     = 0.0
W_ADX     = 0.0


# ── v2 component scorers ───────────────────────────────────────────────────

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
        return W_MACD                     # fresh cross — full weight
    if float(hist.iloc[-1]) > float(hist.iloc[-2]):
        return W_MACD * 0.6               # merely rising — partial
    return 0.0


def _rs20_score(df: pd.DataFrame, spy_df: pd.DataFrame) -> tuple[float, bool]:
    """Short-term RS vs SPY (20d). Uses the existing relative_strength helper."""
    try:
        r = relative_strength(df, spy_df, period=20)
    except Exception:
        return 0.0, False
    r = float(r)
    if r >= 1.05:
        return W_RS20, True               # strong outperformer
    if r >= 1.02:
        return W_RS20 * 0.6, False
    if r >= 1.00:
        return W_RS20 * 0.4, False
    return 0.0, False


def _rsi_score(df: pd.DataFrame) -> float:
    """RSI(14) strength — measured +0.085 IC. Uses the TA rsi helper."""
    close = df["close"].astype(float)
    if len(close) < 20:
        return 0.0
    try:
        val = float(_ta_rsi(close, 14).iloc[-1])
    except Exception:
        return 0.0
    if val >= 60:
        return W_RSI
    if val >= 52:
        return W_RSI * 0.6
    if val >= 45:
        return W_RSI * 0.3
    return 0.0


def _ema50_score(df: pd.DataFrame) -> float:
    """Price above own EMA50 — measured +0.048 IC."""
    close = df["close"].astype(float)
    if len(close) < 55:
        return 0.0
    ema50 = close.ewm(span=50, adjust=False).mean()
    if float(close.iloc[-1]) > float(ema50.iloc[-1]):
        return W_EMA50
    return 0.0


# ── gates (unchanged from v1) ──────────────────────────────────────────────

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


# ── composite (v2) ─────────────────────────────────────────────────────────

def compute_usx_early(df: pd.DataFrame, spy_df: pd.DataFrame, market_status: dict) -> dict:
    """Compute the USX v2 early-entry score + gates for one symbol.

    Returns dict: {usx_score, usx_pass, gate_pass, gate_reason, signals[list], breakdown{}}.
    Gate failure does NOT zero the raw score (transparency) but forces usx_pass=False.
    """
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
    if mac >= W_MACD:
        signals.append("MACD+")
    if rs_on:
        signals.append("RS20")
    if rsi >= W_RSI:
        signals.append("RSI")
    if ema >= W_EMA50:
        signals.append("EMA50")

    return {
        "usx_score": raw,
        "usx_pass": usx_pass,
        "gate_pass": gate_pass,
        "gate_reason": gate_reason,
        "signals": signals,
        "breakdown": {
            "macd_turn": mac, "rs20": rs, "rsi": rsi, "ema50": ema,
            "version": USX_VERSION,
        },
    }


# ── enrichment (v2: attaches usx_version to every pick) ────────────────────

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
        p["usx_version"] = USX_VERSION
    return picks
