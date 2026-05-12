"""Swing Trading Strategy — buy pullbacks to EMA in confirmed uptrends.

Timeframes:
  Weekly  → higher highs/lows, EMA20 uptrend, RS vs SPY positive
  Daily   → pullback to EMA20/50, RSI 40-50, low volume pullback
  15min   → EMA9/21 cross, MACD positive

Entry:  Price at EMA20/50 in uptrend, RSI 40-50 healthy pullback, reversal candle
Stop:   Below pullback low - 0.3%
TP1:    Previous swing high (close 40%)
TP2:    Fibonacci 1.272 (close 35%)
TP3:    Fibonacci 1.618 (close 25%)
Hold:   3-15 days

Constraints: Weekly uptrend, higher highs/lows, no BEAR/CREDIT/EXTREME FEAR
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.strategies import (
    BaseStrategy, StrategyInput, StrategySignal,
    StrategyType,
)


class SwingEntryQualityModel:
    """LSTM-inspired swing entry quality scorer.

    Features: EMA20 distance, EMA50 distance, RSI, volume ratio,
              weekly trend strength, 3-month RS vs SPY
    Returns quality score 0-100.
    """

    def score(self, inp: StrategyInput, details: dict) -> int:
        score = 50

        # 1. EMA distance: mid-range is healthy (not too close, not too far)
        dist_ema20 = details.get("dist_ema20_pct", 5)
        if 1.0 <= dist_ema20 <= 3.0:
            score += 15
        elif dist_ema20 < 5.0:
            score += 8
        else:
            score -= 5

        # 2. RSI zone
        rsi = details.get("rsi", 50)
        if 40 <= rsi <= 50:
            score += 15
        elif 35 <= rsi < 40:
            score += 8
        elif rsi > 55:
            score -= 10

        # 3. Volume pattern: pullback volume < uptrend volume
        vol_ratio = details.get("vol_ratio", 1)
        if vol_ratio < 0.8:
            score += 10
        elif vol_ratio < 1.2:
            score += 5
        elif vol_ratio > 1.5:
            score -= 5

        # 4. Higher TF trend strength
        if details.get("weekly_uptrend"):
            score += 10
        if details.get("higher_highs"):
            score += 5
        if details.get("higher_lows"):
            score += 5

        # 5. RS vs SPY
        rs = details.get("rs_vs_spy_3m", 0)
        if rs > 5:
            score += 10
        elif rs > 0:
            score += 5
        elif rs < -5:
            score -= 10

        # 6. MACD confirmation
        if details.get("macd_positive"):
            score += 5
        if details.get("macd_rising"):
            score += 5

        return max(0, min(100, score))


class SwingStrategy(BaseStrategy):
    name = "Swing"
    strategy_type = StrategyType.SWING

    def __init__(self, min_score: int = 65):
        super().__init__(min_score)
        self.quality_model = SwingEntryQualityModel()

    def analyze(self, inp: StrategyInput) -> StrategySignal | None:
        score = self.score_signal(inp)
        if score < self.min_score:
            return None

        df = inp.df_daily
        close = df["close"].values.astype(float)
        price = float(close[-1])
        details = self._swing_details(inp)

        swing_high = details.get("prev_swing_high", price * 1.03)
        pullback_low = details.get("pullback_low", price * 0.97)
        stop = round(pullback_low * 0.997, 2)

        tp1 = round(swing_high, 2)
        fib_ext = swing_high - price if swing_high > price else price * 0.03
        tp2 = round(price + fib_ext * 1.272, 2)
        tp3 = round(price + fib_ext * 1.618, 2)

        reason_parts = []
        if details["near_ema20"]:
            reason_parts.append(f"pullback EMA20 ({details['dist_ema20_pct']:.1f}%)")
        elif details["near_ema50"]:
            reason_parts.append(f"pullback EMA50 ({details['dist_ema50_pct']:.1f}%)")
        if details["healthy_pullback"]:
            reason_parts.append(f"RSI {details['rsi']:.0f} healthy pullback")
        if details["volume_healthy"]:
            reason_parts.append("light volume pullback")
        if details["weekly_uptrend"]:
            reason_parts.append("weekly uptrend")
        if details["higher_highs"]:
            reason_parts.append("higher highs")
        if details.get("higher_lows"):
            reason_parts.append("higher lows")
        if details.get("reversal_candle"):
            reason_parts.append("reversal candle ✓")
        if details.get("entry_15min_ready"):
            reason_parts.append("15min entry ✓")
        if details.get("rs_vs_spy_3m", 0) > 0:
            reason_parts.append("RS vs SPY+ 3m")
        if details.get("model_quality", 0) > 65:
            reason_parts.append(f"AI {details['model_quality']}%")

        return StrategySignal(
            strategy="Swing",
            signal="BUY",
            score=score,
            entry=round(price, 2),
            stop=stop,
            tp1=tp1,
            tp2=tp2,
            tp3=tp3,
            hold_days_min=3,
            hold_days_max=15,
            confidence=min(1.0, score / 100 * 0.85),
            reason=" | ".join(reason_parts) if reason_parts else "Swing",
            details=details,
        )

    def score_signal(self, inp: StrategyInput) -> int:
        df = inp.df_daily
        if len(df) < 50:
            return 0

        mkt = inp.market_status or {}
        if mkt.get("status") in ("CREDIT STRESS", "EXTREME FEAR", "BEAR"):
            return 0

        details = self._swing_details(inp)
        score = 0

        if details["weekly_uptrend"]:     score += 20
        if details["higher_highs"]:       score += 10
        if details.get("higher_lows"):    score += 10

        if details["near_ema20"]:         score += 15
        elif details["near_ema50"]:       score += 10

        if details["healthy_pullback"]:   score += 15
        if details["volume_healthy"]:     score += 10

        if details.get("reversal_candle"):
            score += 10
        if details.get("entry_15min_ready"):
            score += 5
        if details.get("macd_rising"):
            score += 5
        if details.get("rs_vs_spy_3m", 0) > 0:
            score += 5

        mq = details.get("model_quality", 0)
        if mq > 75:                       score += 10
        elif mq > 60:                     score += 5

        return min(100, score)

    def _swing_details(self, inp: StrategyInput) -> dict:
        df = inp.df_daily
        close_s = df["close"]
        close = close_s.values.astype(float)
        high = df["high"].values.astype(float) if "high" in df.columns else close
        low = df["low"].values.astype(float) if "low" in df.columns else close
        price = float(close[-1])

        ema20 = self._ema(close_s, 20)
        ema50 = self._ema(close_s, 50)
        ema20_val = float(ema20.iloc[-1])
        ema50_val = float(ema50.iloc[-1])

        dist_ema20 = abs(price - ema20_val) / price * 100
        dist_ema50 = abs(price - ema50_val) / price * 100
        near_ema20 = dist_ema20 < 2.0
        near_ema50 = dist_ema50 < 2.5 and not near_ema20

        rsi = self._rsi(close_s)
        healthy_pullback = 40 <= rsi <= 50

        macd_line = self._ema(close_s, 12) - self._ema(close_s, 26)
        macd_signal = self._ema(macd_line, 9)
        macd_hist = macd_line - macd_signal
        macd_positive = len(macd_hist) > 0 and float(macd_hist.iloc[-1]) > 0
        macd_rising = len(macd_hist) >= 2 and float(macd_hist.iloc[-1]) > float(macd_hist.iloc[-2])

        vol_ratio = self._volume_ratio(df)
        volume_healthy = vol_ratio < 1.2

        # ── Weekly uptrend + Higher Highs/Lows ──
        weekly_uptrend = False
        higher_highs = False
        higher_lows = False
        prev_swing_high = float(np.max(close[-20:])) if len(close) >= 20 else price
        pullback_low = float(np.min(close[-10:])) if len(close) >= 10 else price * 0.97

        if inp.df_weekly is not None and len(inp.df_weekly) >= 10:
            w = inp.df_weekly
            w_close_s = w["close"] if "close" in w.columns else close_s
            w_close = w_close_s.values.astype(float)
            w_high = w["high"].values.astype(float) if "high" in w.columns else w_close
            w_low = w["low"].values.astype(float) if "low" in w.columns else w_close
            w_price = float(w_close[-1])

            w_ema20 = self._ema(w_close_s, 20)
            w_ema20_v = float(w_ema20.iloc[-1]) if len(w_ema20) > 0 else w_price
            weekly_uptrend = w_price > w_ema20_v

            if len(w_close) >= 10:
                prev_swing_high = float(np.max(w_close[-10:]))
                w_hh = [w_close[i] for i in range(1, len(w_close))
                        if w_close[i] > w_close[i-1] and w_close[i] > float(np.max(w_close[max(0,i-5):i]))]
                higher_highs = len(w_hh) >= 2 and w_hh[-1] > w_hh[0]

                w_ll = [w_close[i] for i in range(1, len(w_close))
                        if w_close[i] < w_close[i-1] and w_close[i] < float(np.min(w_close[max(0,i-5):i]))]
                higher_lows = len(w_ll) >= 2 and w_ll[-1] > w_ll[0]

                if len(w_low) >= 10:
                    pullback_low = min(pullback_low, float(np.min(w_low[-5:])))

        if len(close) >= 30:
            daily_highs = close[-30:]
            hh = [daily_highs[i] for i in range(1, len(daily_highs))
                  if daily_highs[i] > daily_highs[i-1]]
            higher_highs = higher_highs or (len(hh) >= 2 and hh[-1] > hh[0])

        # ── Daily reversal candle at EMA ──
        reversal_candle = False
        if len(close) >= 2 and len(high) >= 2 and len(low) >= 2:
            prev_close = close[-2]
            prev_open = df["open"].values.astype(float)[-2] if "open" in df.columns else prev_close
            curr_open = df["open"].values.astype(float)[-1] if "open" in df.columns else close[-1]

            body = abs(close[-1] - curr_open)
            lower_shadow = min(curr_open, close[-1]) - low[-1]
            upper_shadow = high[-1] - max(curr_open, close[-1])
            total = high[-1] - low[-1]

            hammer = total > 0 and body / total < 0.3 and lower_shadow > body * 2 and upper_shadow < body * 0.5
            bullish_engulfing = curr_open < prev_close and close[-1] > prev_open
            reversal_candle = hammer or bullish_engulfing

        # ── 15min EMA9/21 cross + MACD ──
        entry_15min_ready = False
        ema9_15m = None
        ema21_15m = None
        macd_15m_positive = False
        if inp.df_15min is not None and len(inp.df_15min) >= 30:
            m = inp.df_15min
            m_close = m["close"] if "close" in m.columns else m.get("Close")
            if m_close is not None and len(m_close) >= 21:
                m_s = m_close if isinstance(m_close, pd.Series) else m_close
                ema9 = self._ema(m_s, 9)
                ema21 = self._ema(m_s, 21)
                if len(ema9) > 0 and len(ema21) > 0:
                    ema9_15m = float(ema9.iloc[-1])
                    ema21_15m = float(ema21.iloc[-1])
                    ema9_prev = float(ema9.iloc[-2]) if len(ema9) >= 2 else ema9_15m
                    ema21_prev = float(ema21.iloc[-2]) if len(ema21) >= 2 else ema21_15m
                    entry_15min_ready = ema9_prev <= ema21_prev and ema9_15m > ema21_15m

                    m_macd = self._ema(m_s, 12) - self._ema(m_s, 26)
                    m_macd_sig = self._ema(m_macd, 9)
                    m_macd_hist = m_macd - m_macd_sig
                    macd_15m_positive = len(m_macd_hist) > 0 and float(m_macd_hist.iloc[-1]) > 0

        # ── RS vs SPY 3-month ──
        rs_vs_spy = self._rs_vs_spy(df, inp.spy_df, period=60)

        # ── LSTM-inspired quality score ──
        details = {
            "ema20_val": round(ema20_val, 2),
            "ema50_val": round(ema50_val, 2),
            "dist_ema20_pct": round(dist_ema20, 2),
            "dist_ema50_pct": round(dist_ema50, 2),
            "near_ema20": near_ema20,
            "near_ema50": near_ema50,
            "rsi": round(rsi, 1),
            "healthy_pullback": healthy_pullback,
            "vol_ratio": vol_ratio,
            "volume_healthy": volume_healthy,
            "macd_positive": macd_positive,
            "macd_rising": macd_rising,
            "weekly_uptrend": weekly_uptrend,
            "higher_highs": higher_highs,
            "higher_lows": higher_lows,
            "prev_swing_high": prev_swing_high,
            "pullback_low": round(pullback_low, 2),
            "reversal_candle": reversal_candle,
            "entry_15min_ready": entry_15min_ready,
            "ema9_15m": round(ema9_15m, 2) if ema9_15m else None,
            "ema21_15m": round(ema21_15m, 2) if ema21_15m else None,
            "macd_15m_positive": macd_15m_positive,
            "rs_vs_spy_3m": rs_vs_spy,
            "price": price,
        }
        details["model_quality"] = self.quality_model.score(inp, details)
        return details
