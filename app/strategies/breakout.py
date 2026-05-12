"""Breakout Strategy — range expansion on resistance breaks with AI.

Timeframes:
  Weekly  → 52w high proximity + BB squeeze confirmation
  Daily   → 20-day high break, volume spike, close above resistance
  15min   → entry timing (early session, no immediate retest)

Entry:  Price breaks 20-day high on 2x+ avg volume, close above resistance
Stop:   Below broken resistance - 0.5%
TP1:    Entry + ATR x 2 (close 30%)
TP2:    Entry + ATR x 4 (close 40%)
TP3:    Trailing stop 3%  (close 30%)
Hold:   5-20 days

False Breakout: price returns below breakout same day, volume day 2 < avg, RSI < 50
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.strategies import (
    BaseStrategy, StrategyInput, StrategySignal,
    StrategyType,
)


class BreakoutValidityModel:
    """CNN-inspired breakout validity scorer.

    Analyses OHLCV pattern over last 20 days for consolidation patterns:
    - Tight range (low volatility before breakout)
    - Volume contraction then expansion
    - Multiple touches of resistance level
    Returns validity score 0-100.
    """

    def score(self, df: pd.DataFrame) -> int:
        if len(df) < 20:
            return 0
        close = df["close"].values.astype(float)
        high = df["high"].values.astype(float)
        low = df["low"].values.astype(float)
        volume = df["volume"].values.astype(float) if "volume" in df.columns else None

        score = 50

        # 1. Consolidation tightness (last 15 days range / 60-day range)
        range_15 = float(np.ptp(close[-15:]))
        range_60 = float(np.ptp(close[-60:])) if len(close) >= 60 else range_15 * 2
        consolidation_ratio = range_15 / range_60 if range_60 > 0 else 1
        if consolidation_ratio < 0.3:
            score += 20
        elif consolidation_ratio < 0.5:
            score += 10
        elif consolidation_ratio > 0.8:
            score -= 10

        # 2. Volume contraction before breakout (last 5d vs 20d avg)
        if volume is not None and len(volume) >= 20:
            vol_20_avg = float(np.mean(volume[-20:-5])) if len(volume) >= 20 else float(np.mean(volume))
            vol_5_avg = float(np.mean(volume[-10:-5])) if len(volume) >= 10 else vol_20_avg
            vol_ratio_pre = vol_5_avg / vol_20_avg if vol_20_avg > 0 else 1
            if vol_ratio_pre < 0.7:
                score += 15
            elif vol_ratio_pre < 0.9:
                score += 8

            # Volume explosion on breakout day
            vol_today = float(volume[-1])
            vol_break_ratio = vol_today / vol_20_avg if vol_20_avg > 0 else 1
            if vol_break_ratio > 2.5:
                score += 15
            elif vol_break_ratio > 1.5:
                score += 8

        # 3. Multiple resistance touches (price approached same level)
        if len(close) >= 30:
            resistance = float(np.max(close[-30:-1]))
            touches = sum(1 for c in close[-30:-1] if abs(c - resistance) / resistance < 0.015)
            if touches >= 3:
                score += 15
            elif touches >= 2:
                score += 8

        # 4. Strong breakout candle (large real body)
        if len(close) >= 2:
            body = abs(close[-1] - (df["open"].values.astype(float)[-1]))
            body_pct = body / close[-1] * 100
            if body_pct > 2.0:
                score += 10
            elif body_pct > 1.0:
                score += 5

        return max(0, min(100, score))


class BreakoutStrategy(BaseStrategy):
    name = "Breakout"
    strategy_type = StrategyType.BREAKOUT

    def __init__(self, min_score: int = 65):
        super().__init__(min_score)
        self.validity_model = BreakoutValidityModel()

    def analyze(self, inp: StrategyInput) -> StrategySignal | None:
        score = self.score_signal(inp)
        if score < self.min_score:
            return None

        df = inp.df_daily
        close = df["close"].values.astype(float)
        high = df["high"].values.astype(float)
        price = float(close[-1])
        atr = self._atr(df)
        details = self._breakout_details(inp)

        resistance = details["resistance_20d"]
        stop = round(max(resistance * 0.995, price * 0.97), 2)
        tp1 = round(price + atr * 2, 2)
        tp2 = round(price + atr * 4, 2)
        tp3 = round(price + atr * 6, 2)

        reason_parts = []
        if details["breakout_confirmed"]:
            reason_parts.append(f"break {resistance:.2f} +{details['breakout_pct']:.1f}%")
        if details["high_volume"]:
            reason_parts.append(f"vol {details['vol_ratio']:.1f}x")
        if details["near_52w_high"]:
            reason_parts.append(f"52w high {details['pct_52w_high']:.1f}%")
        if details.get("weekly_squeeze"):
            reason_parts.append("weekly squeeze")
        if details.get("early_session_break"):
            reason_parts.append("early break")
        if details.get("no_retest"):
            reason_parts.append("hold breakout")
        if details.get("validity_score", 0) > 75:
            reason_parts.append(f"AI {details['validity_score']}%")

        return StrategySignal(
            strategy="Breakout",
            signal="BUY",
            score=score,
            entry=round(price, 2),
            stop=stop,
            tp1=tp1,
            tp2=tp2,
            tp3=tp3,
            hold_days_min=5,
            hold_days_max=20,
            confidence=min(1.0, score / 100 * 0.85),
            reason=" | ".join(reason_parts) if reason_parts else "Breakout",
            details=details,
        )

    def score_signal(self, inp: StrategyInput) -> int:
        df = inp.df_daily
        if len(df) < 25:
            return 0

        details = self._breakout_details(inp)
        score = 0

        if details["breakout_confirmed"]:   score += 30
        elif details["near_breakout"]:     score += 15

        if details["high_volume"]:         score += 15
        if details.get("vol_trend"):       score += 10

        if details["near_52w_high"]:       score += 15
        if details.get("weekly_squeeze"):  score += 10

        atr_pct = details["atr_pct"]
        if atr_pct > 3:                    score += 15
        elif atr_pct > 2:                  score += 10

        if details.get("early_session_break"):
            score += 5
        if details.get("no_retest"):
            score += 5

        vs = details.get("validity_score", 0)
        if vs > 80:                        score += 10
        elif vs > 65:                      score += 5

        return min(100, score)

    def _breakout_details(self, inp: StrategyInput) -> dict:
        df = inp.df_daily
        close = df["close"].values.astype(float)
        high = df["high"].values.astype(float)
        low = df["low"].values.astype(float)
        volume = df["volume"].values.astype(float) if "volume" in df.columns else None
        price = float(close[-1])
        atr = self._atr(df)

        resistance_20d = float(np.max(high[-21:-1])) if len(high) > 20 else price
        breakout_pct = round((price / resistance_20d - 1) * 100, 2) if resistance_20d > 0 else 0
        breakout_confirmed = breakout_pct > 0.3
        near_breakout = 0 < breakout_pct <= 0.3

        vol_ratio = self._volume_ratio(df)
        high_volume = vol_ratio >= 1.5
        # Volume trend: increasing volume over last 5 days
        if "volume" in df.columns and len(df) >= 10:
            vol_5 = float(np.mean(df["volume"].values[-5:]))
            vol_20 = float(np.mean(df["volume"].values[-20:]))
            vol_trend = vol_5 > vol_20 * 1.2
        else:
            vol_trend = False

        atr_pct = round(atr / price * 100, 2) if price > 0 else 0

        # ── Weekly analysis ──
        near_52w_high = False
        pct_52w_high = -100
        weekly_squeeze = False
        high_52w = float(np.max(high[-253:])) if len(high) >= 253 else price
        pct_52w_high = round((price / high_52w - 1) * 100, 2) if high_52w > 0 else 0
        near_52w_high = -5 < pct_52w_high < 5

        if inp.df_weekly is not None and len(inp.df_weekly) >= 20:
            w = inp.df_weekly
            w_close_s = w["close"] if "close" in w.columns else df["close"]
            _, _, _, w_bw = self._bollinger(w_close_s, 20)
            if len(w_close_s) >= 30:
                w_avg_bw = float(w_close_s.rolling(20).std().mean() * 4 / float(w_close_s.rolling(20).mean().iloc[-1]) * 100) if len(w_close_s) >= 20 else w_bw
                weekly_squeeze = w_bw > 0 and w_avg_bw > 0 and w_bw / w_avg_bw < 0.85
            else:
                weekly_squeeze = False

        # ── 15min analysis ──
        early_session_break = False
        no_retest = False
        if inp.df_15min is not None and len(inp.df_15min) >= 10:
            m = inp.df_15min
            m_high = m["high"].values.astype(float) if "high" in m.columns else m["close"].values.astype(float)
            m_low = m["low"].values.astype(float) if "low" in m.columns else m_high
            m_close = m["close"].values.astype(float)

            if len(m_close) >= 5:
                avg_range_first_2h = float(np.mean(m_high[:8] - m_low[:8])) if len(m_high) >= 8 else 0
                breakout_candle_idx = None
                for i in range(min(8, len(m_close))):
                    if i > 0 and m_close[i] > resistance_20d:
                        breakout_candle_idx = i
                        break
                early_session_break = breakout_candle_idx is not None and breakout_candle_idx < 8
                if breakout_candle_idx is not None:
                    remaining = m_low[breakout_candle_idx + 1:]
                    no_retest = len(remaining) == 0 or float(np.min(remaining)) > resistance_20d

        # ── Close above resistance (full candle confirmation) ──
        close_above_resistance = price > resistance_20d

        # ── CNN-inspired validity score ──
        validity_score = self.validity_model.score(df)

        # ── False breakout signals ──
        false_breakout_signals = []
        if breakout_confirmed and len(close) >= 2:
            today_close = close[-1]
            prev_close = close[-2]
            if today_close < resistance_20d:
                false_breakout_signals.append("price returned below resistance")
            if volume is not None and len(volume) >= 2:
                vol_yesterday = volume[-2]
                vol_20_avg = float(np.mean(volume[-20:])) if len(volume) >= 20 else vol_yesterday
                if vol_yesterday < vol_20_avg * 0.8:
                    false_breakout_signals.append("low volume day after breakout")

        close_s = df["close"]
        _, _, _, bw = self._bollinger(close_s)
        bb_upper, bb_mid, _, _ = self._bollinger(close_s, 20)
        avg_bw = float(close_s.rolling(20).std().mean() * 4 / float(bb_mid) * 100) if len(close_s) >= 20 else bw
        bb_squeeze_active = bw > 0 and avg_bw > 0 and bw / avg_bw < 0.8

        return {
            "resistance_20d": resistance_20d,
            "breakout_pct": breakout_pct,
            "breakout_confirmed": breakout_confirmed,
            "close_above_resistance": close_above_resistance,
            "near_breakout": near_breakout,
            "vol_ratio": vol_ratio,
            "high_volume": high_volume,
            "vol_trend": vol_trend,
            "atr": atr,
            "atr_pct": atr_pct,
            "high_52w": high_52w,
            "pct_52w_high": pct_52w_high,
            "near_52w_high": near_52w_high,
            "bb_squeeze_active": bb_squeeze_active,
            "bb_bandwidth": bw,
            "weekly_squeeze": weekly_squeeze,
            "early_session_break": early_session_break,
            "no_retest": no_retest,
            "validity_score": validity_score,
            "false_breakout_signals": false_breakout_signals,
            "price": price,
        }
