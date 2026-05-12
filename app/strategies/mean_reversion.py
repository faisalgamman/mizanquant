"""Mean Reversion Strategy — fade extremes at Bollinger Bands with AI.

Timeframes:
  Weekly  → uptrend confirmation (EMA50, RSI 40-60)
  Daily   → primary signal (below lower BB, RSI < 35, >2 ATR from EMA20)
  15min   → entry timing (reversal candle, RSI bounce from <30)

Entry:  Price below lower BB, RSI < 35 oversold, >2 ATR from EMA20, normal volume
Stop:   Lower BB - 1 ATR
TP1:    EMA 20 (close 50%)
TP2:    Middle BB (close 30%)
TP3:    Upper BB (close 20%)
Hold:   3-7 days only

Constraints: Weekly uptrend required, no BEAR regime, no panic volume > 3x avg
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.strategies import (
    BaseStrategy, StrategyInput, StrategySignal,
    StrategyType,
)


class ReversionProbabilityModel:
    """Statistical model for mean reversion probability.

    Features: BB Width, RSI, Distance from EMA (ATR units), Volume ratio
    Uses logistic-inspired scoring with calibrated weights.
    """

    def __init__(self):
        self.weights = {
            "bb_width": 0.20,
            "rsi": 0.30,
            "dist_ema_atr": 0.30,
            "volume": 0.20,
        }

    def probability(self, bb_width: float, rsi: float,
                    dist_ema_atr: float, vol_ratio: float) -> float:
        score = 0.0

        # BB Width: wider bands = more room for reversion
        bw_score = min(1.0, bb_width / 5.0) * self.weights["bb_width"]
        if bb_width > 3.0:
            bw_score *= 1.2
        score += bw_score

        # RSI: lower is better for reversion
        if rsi < 25:
            rsi_score = 1.0
        elif rsi < 35:
            rsi_score = 0.85
        elif rsi < 40:
            rsi_score = 0.6
        elif rsi < 50:
            rsi_score = 0.3
        else:
            rsi_score = 0.0
        score += rsi_score * self.weights["rsi"]

        # Distance from EMA in ATR units: more stretched = higher probability
        if dist_ema_atr > 3.0:
            dist_score = 1.0
        elif dist_ema_atr > 2.0:
            dist_score = 0.85
        elif dist_ema_atr > 1.5:
            dist_score = 0.6
        elif dist_ema_atr > 1.0:
            dist_score = 0.4
        else:
            dist_score = 0.1
        score += dist_score * self.weights["dist_ema_atr"]

        # Volume: low/med is good (no panic), avoid very high
        if vol_ratio < 1.0:
            vol_score = 0.8
        elif vol_ratio < 2.0:
            vol_score = 0.9
        elif vol_ratio < 3.0:
            vol_score = 0.6
        else:
            vol_score = 0.0
        score += vol_score * self.weights["volume"]

        return min(1.0, max(0.0, score))

    def score(self, bb_width: float, rsi: float,
              dist_ema_atr: float, vol_ratio: float) -> int:
        prob = self.probability(bb_width, rsi, dist_ema_atr, vol_ratio)
        return int(round(prob * 100))


class MeanReversionStrategy(BaseStrategy):
    name = "Mean Reversion"
    strategy_type = StrategyType.REVERSION

    def __init__(self, min_score: int = 65):
        super().__init__(min_score)
        self.reversion_model = ReversionProbabilityModel()

    def analyze(self, inp: StrategyInput) -> StrategySignal | None:
        score = self.score_signal(inp)
        if score < self.min_score:
            return None

        df = inp.df_daily
        close_s = df["close"]
        close = close_s.values.astype(float)
        price = float(close[-1])
        details = self._reversion_details(inp)

        bb_lower = details["bb_lower"]
        bb_mid = details["bb_mid"]
        bb_upper = details["bb_upper"]
        atr = self._atr(df)

        stop = round(max(bb_lower - atr, price * 0.93), 2)

        ema20_val = float(self._ema(close_s, 20).iloc[-1])
        tp1 = round(ema20_val, 2)
        tp2 = round(bb_mid, 2)
        tp3 = round(bb_upper, 2)

        reason_parts = []
        if details["below_bb_lower"]:
            reason_parts.append(f"below BB lower ({bb_lower:.2f})")
        if details["rsi_oversold"]:
            reason_parts.append(f"RSI {details['rsi']:.0f} oversold")
        if details["dist_ema_atr"] > 2:
            reason_parts.append(f"{details['dist_ema_atr']:.1f} ATR from EMA20")
        if details["weekly_uptrend"]:
            reason_parts.append("weekly uptrend")
        if details.get("weekly_rsi_ok"):
            reason_parts.append("weekly RSI neutral")
        if not details["panic_volume"]:
            reason_parts.append("normal volume")
        if details.get("reversal_15min"):
            reason_parts.append("15min reversal ✓")
        if details.get("model_prob", 0) > 65:
            reason_parts.append(f"AI {details['model_prob']}% confidence")

        return StrategySignal(
            strategy="Mean Reversion",
            signal="BUY",
            score=score,
            entry=round(price, 2),
            stop=stop,
            tp1=tp1,
            tp2=tp2,
            tp3=tp3,
            hold_days_min=2,
            hold_days_max=5,
            confidence=min(1.0, score / 100 * 0.9),
            reason=" | ".join(reason_parts) if reason_parts else "Mean Reversion",
            details=details,
        )

    def score_signal(self, inp: StrategyInput) -> int:
        df = inp.df_daily
        if len(df) < 30:
            return 0

        mkt = inp.market_status or {}
        if mkt.get("status") == "BEAR":
            return 0

        details = self._reversion_details(inp)
        score = 0

        if details["below_bb_lower"]:    score += 25
        elif details["near_bb_lower"]:   score += 15

        if details["rsi_oversold"]:      score += 20
        elif details["rsi_below_50"]:    score += 10

        if details["dist_ema_atr"] > 3:  score += 15
        elif details["dist_ema_atr"] > 2: score += 10
        elif details["dist_ema_atr"] > 1: score += 5

        if details["weekly_uptrend"]:    score += 15
        if details.get("weekly_rsi_ok"): score += 5

        if not details["panic_volume"]:  score += 10

        # ADX filter: low ADX = ranging = good for reversion
        if details.get("low_adx"):       score += 10
        elif details.get("high_adx"):    score -= 10

        # Avoid catching falling knives
        if not details.get("no_freefall", True):
            score -= 15

        model_p = details.get("model_prob", 0)
        if model_p > 80:                 score += 10
        elif model_p > 65:              score += 5

        if details.get("reversal_15min"):
            score += 10

        return max(0, min(100, score))

    def _reversion_details(self, inp: StrategyInput) -> dict:
        df = inp.df_daily
        close_s = df["close"]
        close = close_s.values.astype(float)
        price = float(close[-1])
        atr = self._atr(df)

        bb_upper, bb_mid, bb_lower, bw = self._bollinger(close_s)
        rsi = self._rsi(close_s)
        vol_ratio = self._volume_ratio(df)

        below_bb_lower = bb_lower > 0 and price < bb_lower
        near_bb_lower = bb_lower > 0 and abs(price - bb_lower) / bb_lower < 0.03
        below_bb_mid = bb_mid > 0 and price < bb_mid
        rsi_oversold = rsi < 35
        rsi_below_50 = rsi < 50
        panic_volume = vol_ratio > 3.0

        ema20_val = float(self._ema(close_s, 20).iloc[-1])
        dist_ema20_pct = abs(price - ema20_val) / price * 100
        dist_ema_atr = abs(price - ema20_val) / atr if atr > 0 else 0

        # ── ADX filter — avoid reversion in strong trends ──
        adx_val = self._adx(df)
        low_adx = adx_val < 15
        high_adx = adx_val > 25

        # ── No freefall check ──
        no_freefall = len(close) >= 5 and close[-1] > close[-5] * 0.95

        # ── Weekly analysis ──
        weekly_uptrend = False
        weekly_rsi = 50
        weekly_rsi_ok = False
        if inp.df_weekly is not None and len(inp.df_weekly) >= 12:
            w = inp.df_weekly
            w_close_s = w["close"] if "close" in w.columns else close_s
            w_close_v = w_close_s.values.astype(float)
            w_price = float(w_close_v[-1])
            w_ema50_val = float(self._ema(w_close_s, 50).iloc[-1]) if len(w_close_s) >= 50 else w_price
            weekly_uptrend = w_price > w_ema50_val
            w_rsi = self._rsi(w_close_s, 14)
            weekly_rsi = w_rsi
            weekly_rsi_ok = 40 <= w_rsi <= 60

        # ── 15min analysis ──
        reversal_15min = False
        rsi_15min_bounce = False
        hammer_15min = False
        doji_15min = False
        if inp.df_15min is not None and len(inp.df_15min) >= 20:
            m = inp.df_15min
            m_close = m["close"].values.astype(float) if "close" in m.columns else close[-1:]
            m_high = m["high"].values.astype(float) if "high" in m.columns else m_close
            m_low = m["low"].values.astype(float) if "low" in m.columns else m_close
            m_open = m["open"].values.astype(float) if "open" in m.columns else m_close

            if len(m_close) >= 2:
                body = abs(m_close[-1] - m_open[-1])
                upper_shadow = m_high[-1] - max(m_close[-1], m_open[-1])
                lower_shadow = min(m_close[-1], m_open[-1]) - m_low[-1]
                total_range = m_high[-1] - m_low[-1]

                if total_range > 0:
                    hammer_15min = (body / total_range < 0.3
                                    and lower_shadow > body * 2
                                    and upper_shadow < body * 0.5)
                    doji_15min = body / total_range < 0.1

            if len(m_close) >= 15:
                m_close_s = pd.Series(m_close)
                m_rsi_vals = []
                for i in range(1, len(m_close_s)):
                    delta = m_close_s.iloc[i] - m_close_s.iloc[i - 1]
                    m_rsi_vals.append(delta)
                if len(m_rsi_vals) >= 14:
                    gains = [max(d, 0) for d in m_rsi_vals]
                    losses = [max(-d, 0) for d in m_rsi_vals]
                    avg_g = sum(gains[-14:]) / 14
                    avg_l = sum(losses[-14:]) / 14
                    m_rsi = 100 - (100 / (1 + avg_g / avg_l)) if avg_l > 0 else 100
                    rsi_15min_bounce = m_rsi < 30

                    avg_g2 = sum(gains[-3:]) / 3 if len(gains) >= 3 else 0
                    avg_l2 = sum(losses[-3:]) / 3 if len(losses) >= 3 else 0
                    rsi_recent = 100 - (100 / (1 + avg_g2 / avg_l2)) if avg_l2 > 0 else 100
                    rsi_15min_bounce = rsi_15min_bounce and rsi_recent > m_rsi

            reversal_15min = hammer_15min or doji_15min or rsi_15min_bounce

        # ── AI Model probability ──
        model_prob = self.reversion_model.score(bw, rsi, dist_ema_atr, vol_ratio)

        return {
            "bb_upper": bb_upper,
            "bb_mid": bb_mid,
            "bb_lower": bb_lower,
            "bb_bandwidth": bw,
            "rsi": rsi,
            "vol_ratio": vol_ratio,
            "below_bb_lower": below_bb_lower,
            "near_bb_lower": near_bb_lower,
            "below_bb_mid": below_bb_mid,
            "rsi_oversold": rsi_oversold,
            "rsi_below_50": rsi_below_50,
            "panic_volume": panic_volume,
            "adx": round(adx_val, 1),
            "low_adx": low_adx,
            "high_adx": high_adx,
            "no_freefall": no_freefall,
            "weekly_uptrend": weekly_uptrend,
            "weekly_rsi": round(weekly_rsi, 1),
            "weekly_rsi_ok": weekly_rsi_ok,
            "dist_ema20_pct": round(dist_ema20_pct, 2),
            "dist_ema_atr": round(dist_ema_atr, 2),
            "price": price,
            "reversal_15min": reversal_15min,
            "hammer_15min": hammer_15min,
            "doji_15min": doji_15min,
            "rsi_15min_bounce": rsi_15min_bounce,
            "model_prob": model_prob,
            "stop": round(max(bb_lower - atr, price * 0.93), 2),
        }
