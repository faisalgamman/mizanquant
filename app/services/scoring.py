"""Weighted Score System — 100-point composite scoring for trading signals.

USX PRO V5.0 Scoring Engine:
  - Hard Gates (must-pass or Score = 0)
  - Redistributed weights aligned with USX PRO methodology
  - Penalty system for negative indicators
  - USX Proxy Score for OpenBB/USX PRO convergence validation
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import numpy as np
import pandas as pd

from app.services.market_context import get_market_context
from app.services.technical import (
    adx as calc_adx,
    bollinger_bands,
    ema,
    macd as calc_macd,
    rsi as calc_rsi,
    volume_ratio as calc_volume_ratio,
)

logger = logging.getLogger("screener")


# ═══════════════════════════════════════════════════════════════════════
# Hard Gates — أي فشل = Score = 0 فوراً
# ═══════════════════════════════════════════════════════════════════════

class HardGate(Enum):
    RS_VS_SPY = "rs_vs_spy"
    VOLUME = "volume"
    MACD = "macd"
    ADX = "adx"
    VWAP = "vwap"


HARD_GATE_CONFIG = {
    HardGate.RS_VS_SPY: {"threshold": -2.0, "desc": "RS vs SPY > -2%"},
    HardGate.VOLUME: {"threshold": 0.5, "desc": "Volume > 50% 20d avg"},
    HardGate.MACD: {"threshold": 0, "desc": "MACD Hist > 0 or recent positive crossover"},
    HardGate.ADX: {"threshold": 15, "desc": "ADX > 15"},
    HardGate.VWAP: {"threshold": 0, "desc": "Price > VWAP"},
}


@dataclass
class GateResult:
    passed: bool
    failed_gates: list[str] = field(default_factory=list)


class ScoreResult(dict):
    """100-point score result that works as both a dict and an object."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.total: int = kwargs.get("total", 0)
        self.confidence: float = kwargs.get("confidence", 0)
        self.components: dict = kwargs.get("components", {})
        self.thresholds: dict = kwargs.get("thresholds", {})
        self.signal: str = kwargs.get("signal", "AVOID")
        self.hard_gates_passed: bool = kwargs.get("hard_gates_passed", False)
        self.penalties: list[str] = kwargs.get("penalties", [])
        self.penalty_total: int = kwargs.get("penalty_total", 0)
        self.usx_proxy: dict = kwargs.get("usx_proxy", {})
        self.gate_source: str = kwargs.get("gate_source", "")
        self.converged: bool = kwargs.get("converged", True)
        self.subtotal: int = kwargs.get("subtotal", self.total)
        self.details: dict = kwargs.get("details", {})


def check_hard_gates(
    df: pd.DataFrame,
    spy_df: Optional[pd.DataFrame] = None,
) -> GateResult:
    """فحص الشروط الإلزامية — فشل أي شرط = فشل الكل."""
    close = df["close"].astype(float)
    latest_close = float(close.iloc[-1])
    failed = []

    # 1. RS vs SPY > -2%
    if spy_df is not None and len(spy_df) >= 20 and len(close) >= 20:
        spy_close = spy_df["close"].astype(float)
        sym_price_20d = float(close.iloc[-20])
        spy_price_20d = float(spy_close.iloc[-20])
        spy_latest = float(spy_close.iloc[-1])
        sym_ret = (latest_close / sym_price_20d - 1) * 100 if sym_price_20d > 0 else 0
        spy_ret = (spy_latest / spy_price_20d - 1) * 100 if spy_price_20d > 0 else 0
        rs_vs_spy_pct = sym_ret - spy_ret
        if rs_vs_spy_pct <= HARD_GATE_CONFIG[HardGate.RS_VS_SPY]["threshold"]:
            failed.append(f"RS vs SPY = {rs_vs_spy_pct:+.2f}% (حد {HARD_GATE_CONFIG[HardGate.RS_VS_SPY]['threshold']}%)")
    else:
        # بدون بيانات SPY → نفذ الشرط لتكون محافظة
        failed.append("RS vs SPY: no SPY data")

    # 2. Volume > 50% 20d average
    if "volume" in df.columns and len(df) >= 20:
        vol_ratio = calc_volume_ratio(df)
        if vol_ratio < HARD_GATE_CONFIG[HardGate.VOLUME]["threshold"]:
            failed.append(f"Volume ratio = {vol_ratio:.2f} (حد {HARD_GATE_CONFIG[HardGate.VOLUME]['threshold']})")

    # 3. MACD Histogram > 0 أو تحول إيجابي مؤخراً
    macd_line, signal_line, histogram = calc_macd(close)
    if len(histogram) >= 3:
        latest_hist = float(histogram.iloc[-1])
        prev_hist = float(histogram.iloc[-2])
        prev2_hist = float(histogram.iloc[-3])
        # إيجابي الآن أو تحول من سالب لموجب في آخر 3 شمعات
        macd_positive = latest_hist > 0
        recent_crossover = prev_hist <= 0 and latest_hist > 0
        strong_crossover = prev2_hist <= 0 and prev_hist > 0
        if not (macd_positive or recent_crossover or strong_crossover):
            failed.append(f"MACD Hist = {latest_hist:.6f} (يجب > 0 أو تحول إيجابي)")
    else:
        failed.append("MACD: insufficient data")

    # 4. ADX > 15
    if all(c in df.columns for c in ("high", "low")):
        adx_val, _, _ = calc_adx(df, 14)
        latest_adx = float(adx_val.iloc[-1])
        if latest_adx <= HARD_GATE_CONFIG[HardGate.ADX]["threshold"]:
            failed.append(f"ADX = {latest_adx:.1f} (حد {HARD_GATE_CONFIG[HardGate.ADX]['threshold']})")
    else:
        failed.append("ADX: missing high/low columns")

    # 5. Price > VWAP
    if all(c in df.columns for c in ("high", "low", "volume")):
        typical_price = (df["high"].astype(float) + df["low"].astype(float) + close) / 3
        cum_vwap = (typical_price * df["volume"].astype(float)).cumsum() / df["volume"].astype(float).cumsum()
        vwap_val = float(cum_vwap.iloc[-1])
        if latest_close <= vwap_val:
            failed.append(f"Price ({latest_close:.2f}) ≤ VWAP ({vwap_val:.2f})")

    return GateResult(passed=len(failed) == 0, failed_gates=failed)


# ═══════════════════════════════════════════════════════════════════════
# Penalty System — خصم من الـ Score النهائي
# ═══════════════════════════════════════════════════════════════════════

PENALTIES = {
    "rs_spy_very_negative": {"points": -15, "condition": "RS vs SPY < -2%"},
    "volume_too_low": {"points": -10, "condition": "Volume < 30% 20d avg"},
    "macd_negative_declining": {"points": -10, "condition": "MACD < 0 and declining"},
    "below_ema200": {"points": -20, "condition": "Price below EMA200"},
    "adx_choppy": {"points": -8, "condition": "ADX < 15"},
    "bb_breakdown": {"points": -5, "condition": "BB Squeeze breakdown"},
}


def calculate_penalties(
    df: pd.DataFrame,
    spy_df: Optional[pd.DataFrame] = None,
) -> tuple[list[str], int]:
    """حساب العقوبات السلبية."""
    penalties_detail = []
    total_penalty = 0

    close = df["close"].astype(float)
    latest_close = float(close.iloc[-1])

    # 1. RS vs SPY very negative (< -2%)
    if spy_df is not None and len(spy_df) >= 20 and len(close) >= 20:
        spy_close = spy_df["close"].astype(float)
        sym_price_20d = float(close.iloc[-20])
        spy_price_20d = float(spy_close.iloc[-20])
        spy_latest = float(spy_close.iloc[-1])
        sym_ret_pct = (latest_close / sym_price_20d - 1) * 100 if sym_price_20d > 0 else 0
        spy_ret_pct = (spy_latest / spy_price_20d - 1) * 100 if spy_price_20d > 0 else 0
        relative = sym_ret_pct - spy_ret_pct
        if relative < -2.0:
            total_penalty += PENALTIES["rs_spy_very_negative"]["points"]
            penalties_detail.append(f"RS vs SPY = {relative:+.2f}% → -15")

    # 2. Volume extremely low (< 30% of avg)
    if "volume" in df.columns and len(df) >= 20:
        vol_ratio = calc_volume_ratio(df)
        if vol_ratio < 0.3:
            total_penalty += PENALTIES["volume_too_low"]["points"]
            penalties_detail.append(f"Volume ratio = {vol_ratio:.2f} → -10")

    # 3. MACD negative and declining
    macd_line, signal_line, histogram = calc_macd(close)
    if len(histogram) >= 2:
        latest_hist = float(histogram.iloc[-1])
        prev_hist = float(histogram.iloc[-2])
        if latest_hist < 0 and prev_hist < latest_hist:
            total_penalty += PENALTIES["macd_negative_declining"]["points"]
            penalties_detail.append(f"MACD declining ({latest_hist:.4f} < {prev_hist:.4f}) → -10")

    # 4. Price below EMA200
    if len(close) >= 200:
        ema_200 = float(ema(close, 200).iloc[-1])
        if latest_close < ema_200:
            total_penalty += PENALTIES["below_ema200"]["points"]
            penalties_detail.append(f"Below EMA200 ({latest_close:.2f} < {ema_200:.2f}) → -20")

    # 5. ADX choppy
    if all(c in df.columns for c in ("high", "low")):
        adx_val, _, _ = calc_adx(df, 14)
        latest_adx = float(adx_val.iloc[-1])
        if latest_adx < 15:
            total_penalty += PENALTIES["adx_choppy"]["points"]
            penalties_detail.append(f"ADX = {latest_adx:.1f} → -8")

    # 6. BB Squeeze breakdown (squeeze ended downward)
    try:
        upper, middle, lower, bandwidth = bollinger_bands(close, 20, 2)
        latest_bw = float(bandwidth.iloc[-1])
        avg_bw = float(bandwidth.iloc[-20:].mean()) if len(bandwidth) >= 20 else latest_bw
        prev_bw = float(bandwidth.iloc[-2]) if len(bandwidth) >= 2 else latest_bw
        # squeeze was active but now breaking down
        if prev_bw < avg_bw * 0.8 and latest_bw > prev_bw and latest_close < float(lower.iloc[-1]):
            total_penalty += PENALTIES["bb_breakdown"]["points"]
            penalties_detail.append(f"BB Squeeze breakdown → -5")
    except Exception:
        pass

    return penalties_detail, total_penalty


# ═══════════════════════════════════════════════════════════════════════
# USX Proxy Score — تقليد مبسّط لـ USX PRO
# ═══════════════════════════════════════════════════════════════════════

def usx_proxy_score(
    df: pd.DataFrame,
    spy_df: Optional[pd.DataFrame] = None,
) -> dict:
    """
    USX PRO Proxy — 5 conditions, each 1/5 = 20% of total.

    Conditions:
      1. RS vs SPY > 0%
      2. Volume > 20d average
      3. MACD Histogram > 0
      4. ADX > 20
      5. BB Squeeze active (bandwidth < avg)
    """
    if df is None or df.empty:
        return {"score": 0, "max_score": 5, "details": [], "proxy_pct": 0}

    conditions = []
    close = df["close"].astype(float)
    latest_close = float(close.iloc[-1])

    # 1. RS vs SPY > 0%
    rs_ok = False
    if spy_df is not None and len(spy_df) >= 20 and len(close) >= 20:
        try:
            spy_close = spy_df["close"].astype(float)
            sym_price_20d = float(close.iloc[-20])
            spy_price_20d = float(spy_close.iloc[-20])
            spy_latest = float(spy_close.iloc[-1])
            sym_ret = latest_close / sym_price_20d if sym_price_20d > 0 else 1
            spy_ret = spy_latest / spy_price_20d if spy_price_20d > 0 else 1
            rs_pct = (sym_ret / spy_ret - 1) * 100 if spy_ret > 0 else 0
            rs_ok = rs_pct > 0
            conditions.append({"name": "RS>0%", "pass": rs_ok, "value": f"{rs_pct:+.2f}%"})
        except Exception:
            conditions.append({"name": "RS>0%", "pass": False, "value": "err"})
    else:
        conditions.append({"name": "RS>0%", "pass": False, "value": "no data"})

    # 2. Volume > 20d average
    vol_ok = False
    if "volume" in df.columns and len(df) >= 20:
        vol_ratio = calc_volume_ratio(df)
        vol_ok = vol_ratio >= 1.0
        conditions.append({"name": "Vol>Avg", "pass": vol_ok, "value": f"{vol_ratio:.2f}x"})
    else:
        conditions.append({"name": "Vol>Avg", "pass": False, "value": "no data"})

    # 3. MACD Histogram > 0
    macd_ok = False
    try:
        _, _, histogram = calc_macd(close)
        latest_hist = float(histogram.iloc[-1])
        macd_ok = latest_hist > 0
        conditions.append({"name": "MACD>0", "pass": macd_ok, "value": f"{latest_hist:.4f}"})
    except Exception:
        conditions.append({"name": "MACD>0", "pass": False, "value": "err"})

    # 4. ADX > 20
    adx_ok = False
    if all(c in df.columns for c in ("high", "low")):
        try:
            adx_val, _, _ = calc_adx(df, 14)
            latest_adx = float(adx_val.iloc[-1])
            adx_ok = latest_adx > 20
            conditions.append({"name": "ADX>20", "pass": adx_ok, "value": f"{latest_adx:.1f}"})
        except Exception:
            conditions.append({"name": "ADX>20", "pass": False, "value": "err"})
    else:
        conditions.append({"name": "ADX>20", "pass": False, "value": "no data"})

    # 5. BB Squeeze active
    squeeze_ok = False
    try:
        _, _, _, bandwidth = bollinger_bands(close, 20, 2)
        latest_bw = float(bandwidth.iloc[-1])
        avg_bw = float(bandwidth.iloc[-20:].mean()) if len(bandwidth) >= 20 else latest_bw
        squeeze_ok = latest_bw < avg_bw * 0.8
        conditions.append({"name": "Squeeze", "pass": squeeze_ok, "value": f"bw={latest_bw:.4f} vs avg={avg_bw:.4f}"})
    except Exception:
        conditions.append({"name": "Squeeze", "pass": False, "value": "err"})

    passed = sum(1 for c in conditions if c["pass"])
    proxy_pct = round((passed / 5) * 100, 1)

    return {
        "score": passed,
        "max_score": 5,
        "details": conditions,
        "proxy_pct": proxy_pct,
    }


# ═══════════════════════════════════════════════════════════════════════
# Main Scoring Engine
# ═══════════════════════════════════════════════════════════════════════

# Gate values mapped to market regime
GATE_CONFIG = {
    "RISK ON":       {"min_gate": 60, "strong_gate": 75},
    "CAUTION":       {"min_gate": 65, "strong_gate": 80},
    "CREDIT STRESS": {"min_gate": 70, "strong_gate": 85},
    "EXTREME FEAR":  {"min_gate": 999, "strong_gate": 999},  # Pipeline halt
}


def weighted_score(

    df: pd.DataFrame,
    spy_df: Optional[pd.DataFrame] = None,
    vix: Optional[float] = None,
) -> ScoreResult:
    """
    Compute the 100-point weighted score for a symbol.

    Returns ScoreResult object (use as dict via .total, .signal, etc.).
    Callers needing a plain dict: call _score_to_dict(result).
    """
    result = weighted_score_raw(df, spy_df, vix)

    # Check hard gates — any failure = Score = 0
    gates = check_hard_gates(df, spy_df)
    result.hard_gates_passed = gates.passed
    if not gates.passed:
        result.total = 0
        result.signal = "AVOID"
        result.penalties = gates.failed_gates
    else:
        # Determine signal based on market gates
        market_status = get_market_status()
        min_gate = float(market_status.get("min_score", 60))
        strong_gate = float(market_status.get("strong_gate", 75))

        if result.total >= strong_gate:
            result.signal = "QUALIFIED"
        elif result.total >= min_gate:
            result.signal = "WATCH"
        else:
            result.signal = "AVOID"

        result.thresholds = {
            "min_gate": min_gate,
            "strong_gate": strong_gate,
        }

    return result


def weighted_score_raw(
    df: pd.DataFrame,
    spy_df: Optional[pd.DataFrame] = None,
    vix: Optional[float] = None,
) -> ScoreResult:
    """Pure scoring without market gate lookup (for testing/internals)."""
    close = df["close"].astype(float)
    latest_close = float(close.iloc[-1])
    scores = {}
    details = {}

    # ── 1. RS vs SPY — 25 pts (precise 20-day calculation) ──
    rs_raw = _calc_rs_vs_spy(close, spy_df)
    rs_pct = rs_raw["rs_pct"]
    if rs_pct > 5.0:
        rs_score = 25  # LEADER
        rs_label = "LEADER"
    elif rs_pct > 2.0:
        rs_score = 18  # ABOVE
        rs_label = "ABOVE"
    elif rs_pct > 0.0:
        rs_score = 10  # NEUTRAL
        rs_label = "NEUTRAL"
    elif rs_pct > -2.0:
        rs_score = 0   # LAG (hard gate should catch this)
        rs_label = "LAG"
    else:
        rs_score = 0   # LAGGARD
        rs_label = "LAGGARD"
    scores["rs"] = rs_score
    details["rs"] = {"value": rs_pct, "label": rs_label, "max": 25}

    # ── 2. Daily Trend (EMA) — 15 pts ──
    ema_20 = float(ema(close, 20).iloc[-1])
    ema_50 = float(ema(close, 50).iloc[-1]) if len(close) >= 50 else latest_close

    trend_score = 0
    if latest_close > ema_20 > ema_50:
        trend_score = 15
    elif latest_close > ema_20:
        trend_score = 12
    elif latest_close > ema_50:
        trend_score = 8
    elif latest_close < ema_50 and latest_close < ema_20:
        trend_score = 0
    else:
        trend_score = 4
    scores["trend"] = trend_score
    details["trend"] = {"value": trend_score, "max": 15}

    # ── 3. Regime (SPY+VIX) — 10 pts ──
    mc = get_market_context()
    spy_regime = mc.get("spy_regime", {}).get("regime", "neutral")
    vix_val = vix or mc.get("vix", {}).get("vix", 20)

    if spy_regime == "bull" and vix_val < 20:
        regime_score = 10
    elif spy_regime == "bull":
        regime_score = 8
    elif spy_regime == "neutral" and vix_val < 25:
        regime_score = 6
    elif spy_regime == "bear":
        regime_score = 0
    else:
        regime_score = 4
    scores["regime"] = regime_score
    details["regime"] = {"value": regime_score, "max": 10}

    # ── 4. MACD Histogram — 15 pts (gradient + crossover bonus) ──
    macd_line, signal_line, histogram = calc_macd(close)
    macd_score, macd_details = _calc_macd_gradient(histogram)
    scores["macd"] = macd_score
    details["macd"] = macd_details

    # MACD crossover bonus (extra 5 pts, outside 100)
    macd_cross_bonus = 0
    if len(histogram) >= 3:
        h1 = float(histogram.iloc[-1])
        h2 = float(histogram.iloc[-2])
        h3 = float(histogram.iloc[-3])
        if h2 <= 0 and h1 > 0:
            macd_cross_bonus = 5
        elif h3 <= 0 and h2 > 0:
            macd_cross_bonus = 3
    macd_total = min(15, macd_score + macd_cross_bonus)
    scores["macd"] = macd_total
    details["macd_crossover_bonus"] = macd_cross_bonus

    # ── 5. Volume — 10 pts (ratio-based) ──
    if "volume" in df.columns and len(df) >= 20:
        vol_ratio = calc_volume_ratio(df)
        if vol_ratio >= 2.0:
            vol_score = 10
        elif vol_ratio >= 1.2:
            vol_score = 8
        elif vol_ratio >= 0.8:
            vol_score = 6
        elif vol_ratio >= 0.5:
            vol_score = 3
        else:
            vol_score = 0
        details["volume"] = {"ratio": vol_ratio, "value": vol_score, "max": 10}
    else:
        vol_score = 0
        details["volume"] = {"ratio": 0, "value": 0, "max": 10}
    scores["volume"] = vol_score

    # ── 6. RSI Zone — 8 pts (unchanged) ──
    rsi_val = float(calc_rsi(close, 14).iloc[-1])
    if 40 <= rsi_val <= 60:
        rsi_score = 8
    elif 30 <= rsi_val <= 70:
        rsi_score = 5
    elif rsi_val < 30:
        rsi_score = 3
    else:
        rsi_score = 1
    scores["rsi"] = rsi_score
    details["rsi"] = {"value": rsi_val, "score": rsi_score, "max": 8}

    # ── 7. ADX Trend — 7 pts (unchanged) ──
    if all(c in df.columns for c in ("high", "low")):
        adx_val, plus_di, minus_di = calc_adx(df, 14)
        latest_adx = float(adx_val.iloc[-1])
        latest_pdi = float(plus_di.iloc[-1])
        latest_mdi = float(minus_di.iloc[-1])
        if latest_adx > 25 and latest_pdi > latest_mdi:
            adx_score = 7
        elif latest_adx > 25:
            adx_score = 5
        elif latest_adx >= 20:
            adx_score = 3
        else:
            adx_score = 0
    else:
        adx_score = 0
        latest_adx = 0
    scores["adx"] = adx_score
    details["adx"] = {"value": latest_adx, "score": adx_score, "max": 7}

    # ── 8. BB Squeeze — 5 pts (unchanged) ──
    try:
        upper, middle, lower, bandwidth = bollinger_bands(close, 20, 2)
        latest_bw = float(bandwidth.iloc[-1])
        avg_bw = float(bandwidth.iloc[-20:].mean()) if len(bandwidth) >= 20 else latest_bw
        if latest_bw < avg_bw * 0.8:
            bb_score = 5
        elif latest_bw < avg_bw:
            bb_score = 3
        else:
            bb_score = 1
    except Exception:
        bb_score = 0
    scores["bb"] = bb_score
    details["bb"] = {"value": bb_score, "max": 5}

    # ── 9. VWAP — 5 pts (unchanged) ──
    if all(c in df.columns for c in ("high", "low", "volume")):
        typical_price = (df["high"].astype(float) + df["low"].astype(float) + close) / 3
        cum_vwap = (typical_price * df["volume"].astype(float)).cumsum() / df["volume"].astype(float).cumsum()
        vwap_val = float(cum_vwap.iloc[-1])
        if latest_close > vwap_val:
            vwap_score = 5
        elif latest_close > vwap_val * 0.99:
            vwap_score = 3
        else:
            vwap_score = 0
    else:
        vwap_score = 0
        vwap_val = latest_close
    scores["vwap"] = vwap_score
    details["vwap"] = {"value": vwap_val, "score": vwap_score, "max": 5}

    # ── Gap removed (merged into Volume) ──
    scores["gap"] = 0
    details["gap"] = {"value": 0, "max": 0, "note": "merged into Volume"}

    # ── Subtotal before penalties ──
    subtotal = sum(scores.values())

    # ── Penalty deductions ──
    penalty_detail, penalty_total = calculate_penalties(df, spy_df)
    final_total = max(0, subtotal + penalty_total)

    total = final_total
    confidence = round(final_total / 100 * 100, 1)

    # ── USX Proxy Score ──
    usx = usx_proxy_score(df, spy_df)

    # ── Convergence check ──
    # If OpenBB score is HIGH but USX proxy is LOW → not converged (possible false signal)
    converged = True
    if final_total >= 65 and usx["proxy_pct"] < 40:
        converged = False
    elif final_total >= 65 and usx["proxy_pct"] >= 60:
        converged = True

    result = ScoreResult(
        total=final_total,
        confidence=confidence,
        components=scores,
        thresholds={
            "strong": 80,
            "signal": 65,
            "minimum": 65,
        },
        penalties=penalty_detail,
        penalty_total=penalty_total,
        usx_proxy=usx,
        converged=converged,
    )

    result.details = details  # type: ignore
    result.subtotal = subtotal  # type: ignore

    return result


def _score_to_dict(result: ScoreResult) -> dict:
    """Convert ScoreResult to JSON-serializable dict (backward compat)."""
    return {
        "total": result.total,
        "confidence": result.confidence,
        "components": result.components,
        "thresholds": result.thresholds or {},
        "signal": result.signal,
        "hard_gates_passed": result.hard_gates_passed,
        "penalties": result.penalties or [],
        "penalty_total": result.penalty_total,
        "usx_proxy": result.usx_proxy or {},
        "gate_source": result.gate_source,
        "converged": result.converged,
        "subtotal": getattr(result, "subtotal", result.total),
    }


def _score_to_dict_old(result: dict) -> dict:
    """Legacy path for old-style dict results — passthrough."""
    return result


def get_market_status() -> dict:
    """Get dynamic market status with gate values from market_context.

    Delegates to market_context.get_market_status() which uses
    VIX percentile + HY/IG credit ratio for classification.
    Then maps status to min_gate / strong_gate values.
    """
    try:
        from app.services.market_context import get_market_status as _mc_status
        mc = _mc_status()
        status = mc.get("status", "RISK ON")
    except Exception:
        status = "RISK ON"

    gates = GATE_CONFIG.get(status, GATE_CONFIG["RISK ON"])
    result = {
        "status": status,
        "min_score": gates["min_gate"],
        "strong_gate": gates["strong_gate"],
    }

    # Halt pipeline for EXTREME FEAR
    result["halt_pipeline"] = status == "EXTREME FEAR"

    return result


def is_signal_ready(score_result: dict | ScoreResult, min_score: int | None = None) -> bool:
    """Check if weighted score meets the minimum threshold."""
    if isinstance(score_result, ScoreResult):
        total = score_result.total
    else:
        total = score_result.get("total", 0)
    if min_score is None:
        min_score = get_market_status().get("min_score", 65)
    return total >= min_score


def _calc_rs_vs_spy(
    close: pd.Series,
    spy_df: Optional[pd.DataFrame],
) -> dict:
    """Calculate precise RS vs SPY over 20 trading days."""
    result = {"rs_pct": 0.0, "label": "NEUTRAL", "details": {}}

    if spy_df is None or len(spy_df) < 20 or len(close) < 20:
        return result

    try:
        spy_close = spy_df["close"].astype(float)
        latest_close = float(close.iloc[-1])
        sym_price_20d = float(close.iloc[-20])
        spy_latest = float(spy_close.iloc[-1])
        spy_price_20d = float(spy_close.iloc[-20])

        if sym_price_20d == 0 or spy_price_20d == 0:
            return result

        sym_ret = latest_close / sym_price_20d
        spy_ret = spy_latest / spy_price_20d

        rs_ratio = sym_ret / spy_ret if spy_ret != 0 else 1.0
        rs_pct = (rs_ratio - 1.0) * 100.0

        if rs_pct > 5.0:
            label = "LEADER"
        elif rs_pct > 2.0:
            label = "ABOVE"
        elif rs_pct > 0.0:
            label = "NEUTRAL"
        elif rs_pct > -2.0:
            label = "LAG"
        else:
            label = "LAGGARD"

        result = {
            "rs_pct": round(rs_pct, 2),
            "label": label,
            "details": {
                "sym_20d_ret": round((sym_ret - 1) * 100, 2),
                "spy_20d_ret": round((spy_ret - 1) * 100, 2),
            },
        }
    except Exception as e:
        logger.error("RS calculation error: %s", e)

    return result


def _calc_macd_gradient(histogram: pd.Series) -> tuple[int, dict]:
    """Calculate MACD score based on gradient (tiered system)."""
    details = {"max": 15}

    if len(histogram) < 3:
        return 0, details

    try:
        latest = float(histogram.iloc[-1])
        prev = float(histogram.iloc[-2])
        prev2 = float(histogram.iloc[-3])

        if latest > 0 and latest > prev and prev > 0:
            score = 15
            label = "rising"
        elif latest > 0 and latest >= prev:
            score = 12
            label = "positive steady"
        elif latest > 0:
            score = 8
            label = "positive declining"
        elif latest <= 0 and prev <= 0 and latest > prev:
            score = 4
            label = "recovering"
        elif latest <= 0 and latest <= prev:
            score = 1
            label = "declining"
        else:
            score = 0
            label = "flat"

        # Recent crossover bonus (within last 3 bars)
        if prev <= 0 and latest > 0:
            score = max(score, 12)
            label += " + crossover"
        elif prev2 <= 0 and prev > 0:
            score = max(score, 10)
            label += " + crossover-2"

        details.update({"score": score, "label": label, "hist": latest})
        return score, details

    except Exception:
        return 0, details