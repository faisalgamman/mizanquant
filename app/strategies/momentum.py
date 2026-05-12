"""Momentum Strategy — multi-timeframe trend rider with Q-Learning AI.

Timeframes:
  Weekly  → trend confirmation (EMA20, RSI 50-75, volume)
  Daily   → primary signal (RS vs SPY, ADX, MACD, VWAP)
  15min   → entry timing (EMA9/21 cross, volume spike)

Entry:  RS vs SPY > +3%, ADX > 25, MACD positive+rising, weekly uptrend
Stop:   Low of last 5 days - 0.5%
TP1:    Entry + Risk x 1.5   (close 40%)
TP2:    Entry + Risk x 3.0   (close 40%)
TP3:    Trailing stop 2%     (close 20%)
Hold:   5-15 days

Auto-Exit: RS < 0%, ADX < 20, RSI > 80
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.strategies import (
    BaseStrategy, StrategyInput, StrategySignal,
    StrategyType,
)


class MomentumQModel:
    """Simple Q-Learning model for momentum scoring.

    State: quantised (RS_bucket, ADX_bucket, Vol_bucket, MACD_bucket)
    Actions: AVOID(0), HOLD(1), BUY(2)
    """

    N_BUCKETS = 5

    def __init__(self):
        self.q_table = np.zeros((self.N_BUCKETS,) * 4 + (3,), dtype=np.float32)
        self.lr = 0.1
        self.gamma = 0.9
        self.epsilon = 0.1

    def _bucket(self, val: float, edges: tuple[float, ...]) -> int:
        for i, e in enumerate(edges):
            if val <= e:
                return min(i, self.N_BUCKETS - 1)
        return self.N_BUCKETS - 1

    def _state(self, rs: float, adx: float, vol: float, macd: float) -> tuple:
        rs_b = self._bucket(rs, (0, 2, 5, 10))
        adx_b = self._bucket(adx, (15, 20, 25, 35))
        vol_b = self._bucket(vol, (0.8, 1.2, 1.5, 2.0))
        macd_b = self._bucket(macd, (-0.5, 0, 0.5, 1.0))
        return (rs_b, adx_b, vol_b, macd_b)

    def predict(self, rs: float, adx: float, vol: float, macd: float) -> int:
        s = self._state(rs, adx, vol, macd)
        return int(np.argmax(self.q_table[s]))

    def score(self, rs: float, adx: float, vol: float, macd: float) -> int:
        """Return 0-100 momentum confidence score."""
        action = self.predict(rs, adx, vol, macd)
        if action == 2:
            return 85
        elif action == 1:
            return 50
        return 15

    def train_step(self, rs: float, adx: float, vol: float, macd: float,
                   reward: float, next_state=None):
        s = self._state(rs, adx, vol, macd)
        a = self.predict(rs, adx, vol, macd) if np.random.random() > self.epsilon else np.random.randint(3)
        if next_state:
            ns = self._state(*next_state)
            td = reward + self.gamma * float(np.max(self.q_table[ns]))
        else:
            td = reward
        td -= self.q_table[s + (a,)]
        self.q_table[s + (a,)] += self.lr * td


class MomentumStrategy(BaseStrategy):
    name = "Momentum"
    strategy_type = StrategyType.MOMENTUM

    def __init__(self, min_score: int = 65):
        super().__init__(min_score)
        self.q_model = MomentumQModel()

    def analyze(self, inp: StrategyInput) -> StrategySignal | None:
        score = self.score_signal(inp)
        if score < self.min_score:
            return None

        df = inp.df_daily
        close = df["close"].values.astype(float)
        price = float(close[-1])
        details = self._momentum_details(inp)

        risk = self._calc_risk(inp)
        stop = self._calc_stop(inp)
        entry = price
        tp1 = round(entry + risk * 1.5, 2)
        tp2 = round(entry + risk * 3.0, 2)
        tp3 = round(entry + risk * 5.0, 2)

        reason_parts = []
        if details["rs_vs_spy"] >= 3:
            reason_parts.append(f"RS +{details['rs_vs_spy']:.1f}%")
        if details["adx"] >= 25:
            reason_parts.append(f"ADX {details['adx']:.0f}")
        if details["macd_positive"] and details["macd_rising"]:
            reason_parts.append("MACD+↑")
        elif details["macd_positive"]:
            reason_parts.append("MACD+")
        if details["above_vwap"]:
            reason_parts.append(">VWAP")
        if details.get("weekly_uptrend"):
            reason_parts.append("weekly↑")
        if details.get("entry_15min_ready"):
            reason_parts.append("15min entry ✓")
        if details.get("q_score", 0) > 70:
            reason_parts.append("AI confirmed")

        return StrategySignal(
            strategy="Momentum",
            signal="BUY",
            score=score,
            entry=round(entry, 2),
            stop=round(stop, 2),
            tp1=tp1,
            tp2=tp2,
            tp3=tp3,
            hold_days_min=5,
            hold_days_max=15,
            confidence=min(1.0, score / 100 * 0.9 + 0.1),
            reason=" | ".join(reason_parts) if reason_parts else "Momentum",
            details=details,
        )

    def _calc_risk(self, inp: StrategyInput) -> float:
        """Risk = Entry - Stop."""
        entry = float(inp.df_daily["close"].values[-1])
        stop = self._calc_stop(inp)
        return entry - stop

    def _calc_stop(self, inp: StrategyInput) -> float:
        """Stop = low of last 5 trading days - 0.5%."""
        df = inp.df_daily
        low = df["low"].values.astype(float)
        lookback = min(5, len(low))
        low_5d = float(np.min(low[-lookback:]))
        return round(low_5d * 0.995, 2)

    def score_signal(self, inp: StrategyInput) -> int:
        df = inp.df_daily
        if len(df) < 30:
            return 0

        details = self._momentum_details(inp)
        score = 0

        if details["rs_vs_spy"] > 5:    score += 25
        elif details["rs_vs_spy"] > 3: score += 20
        elif details["rs_vs_spy"] > 0: score += 10

        if details["adx"] > 30:         score += 20
        elif details["adx"] > 25:       score += 15
        elif details["adx"] > 20:       score += 8

        if details["macd_positive"] and details["macd_rising"]:
            score += 20
        elif details["macd_positive"]:
            score += 12

        if details["above_vwap"]:       score += 10

        if details.get("weekly_uptrend"):
            score += 15

        if details.get("weekly_rsi_ok"):
            score += 5

        if details.get("weekly_volume_surge"):
            score += 5

        q_s = details.get("q_score", 0)
        if q_s > 70:
            score += 10
        elif q_s > 40:
            score += 5

        return min(100, score)

    def _momentum_details(self, inp: StrategyInput) -> dict:
        df = inp.df_daily
        close_s = df["close"]
        close = close_s.values.astype(float)
        price = float(close[-1])
        rs_vs_spy = self._rs_vs_spy(df, inp.spy_df)
        adx = self._adx(df)
        vwap = self._vwap(df)
        vol_ratio = self._volume_ratio(df)

        macd_line = self._ema(close_s, 12) - self._ema(close_s, 26)
        macd_signal = self._ema(macd_line, 9)
        macd_hist = macd_line - macd_signal
        macd_positive = len(macd_hist) > 0 and float(macd_hist.iloc[-1]) > 0
        macd_rising = len(macd_hist) >= 2 and float(macd_hist.iloc[-1]) > float(macd_hist.iloc[-2])

        rsi = self._rsi(close_s)
        above_vwap = float(close[-1]) > vwap

        stop_price = self._calc_stop(inp)
        risk_pct = round((price - stop_price) / price * 100, 2) if price > 0 else 0

        # ── Weekly analysis ──
        weekly_uptrend = False
        weekly_rsi = 50
        weekly_rsi_ok = False
        weekly_volume_surge = False
        if inp.df_weekly is not None and len(inp.df_weekly) >= 12:
            w = inp.df_weekly
            w_close = w["close"] if "close" in w.columns else w.get("Close", close_s)
            w_close_s = w_close if isinstance(w_close, pd.Series) else w_close
            w_close_v = w_close_s.values.astype(float)
            w_price = float(w_close_v[-1])
            w_ema20 = self._ema(w_close_s, 20)
            w_ema20_v = float(w_ema20.iloc[-1]) if len(w_ema20) > 0 else w_price
            weekly_uptrend = w_price > w_ema20_v

            w_rsi = self._rsi(w_close_s, 14)
            weekly_rsi = w_rsi
            weekly_rsi_ok = 50 <= w_rsi <= 75

            if "volume" in w.columns:
                w_vol = w["volume"].values.astype(float)
                w_vol_ratio = float(w_vol[-1]) / (float(np.mean(w_vol[-10:])) + 1e-9)
                weekly_volume_surge = w_vol_ratio > 1.0

        # ── 15min analysis ──
        entry_15min_ready = False
        ema9_15m = None
        ema21_15m = None
        vol_ratio_15m = None
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

                    if "volume" in m.columns:
                        m_vol = m["volume"].values.astype(float)
                        m_vol_avg = float(np.mean(m_vol[-20:])) if len(m_vol) >= 20 else 1
                        m_vol_latest = float(m_vol[-1])
                        vol_ratio_15m = round(m_vol_latest / m_vol_avg, 2) if m_vol_avg > 0 else 1.0

        # ── Q-Learning score ──
        macd_val = float(macd_hist.iloc[-1]) if len(macd_hist) > 0 else 0
        q_score = self.q_model.score(rs_vs_spy, adx, vol_ratio, macd_val)

        # ── Auto-exit monitoring ──
        exit_reasons = []
        if rs_vs_spy < 0:
            exit_reasons.append(f"RS dropped to {rs_vs_spy:.1f}%")
        if adx < 20:
            exit_reasons.append(f"ADX weakened to {adx:.0f}")
        if rsi > 80:
            exit_reasons.append(f"RSI overbought ({rsi:.0f})")

        # ── Low of last 5 days for stop ──
        low_5d = float(np.min(close[-5:])) if len(close) >= 5 else price * 0.97

        return {
            "rs_vs_spy": rs_vs_spy,
            "adx": adx,
            "macd_positive": macd_positive,
            "macd_rising": macd_rising,
            "macd_val": round(macd_val, 4),
            "above_vwap": above_vwap,
            "vol_ratio": vol_ratio,
            "rsi": rsi,
            "price": price,
            "weekly_uptrend": weekly_uptrend,
            "weekly_rsi": round(weekly_rsi, 1),
            "weekly_rsi_ok": weekly_rsi_ok,
            "weekly_volume_surge": weekly_volume_surge,
            "entry_15min_ready": entry_15min_ready,
            "ema9_15m": round(ema9_15m, 2) if ema9_15m else None,
            "ema21_15m": round(ema21_15m, 2) if ema21_15m else None,
            "vol_ratio_15m": vol_ratio_15m,
            "q_score": q_score,
            "stop_price": stop_price,
            "risk_pct": risk_pct,
            "low_5d": round(low_5d, 2),
            "exit_reasons": exit_reasons,
            "entry_signal": entry_15min_ready and weekly_uptrend and macd_positive and above_vwap,
        }
