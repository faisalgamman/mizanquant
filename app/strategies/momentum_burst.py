"""Momentum Burst Strategy — daily breakouts with volume confirmation.

Targets single-day explosive moves (+5%+) with strong volume
(3x+ avg), close near high, and nascent trend (ADX > 20).

Entry conditions (ALL must pass):
  1. Day change >= +5%
  2. Volume ratio >= 3.0x 20-day avg
  3. Close >= 70% of daily range (buyers in control at close)
  4. ADX(14) > 20
  5. RSI(14) between 40-70
  6. Price above EMA50 daily
  7. Market not BEAR regime
  8. Stock is halal (pre-filtered elsewhere)

Exit:
  Stop: Entry - 1.5 * ATR
  TP1:  Entry * 1.05 (+5%)  — close 40%
  TP2:  Entry * 1.08 (+8%)  — close 35%
  TP3:  Trailing stop 3%    — close 25%
  Time stop: 5 trading days
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.strategies import (
    BaseStrategy, StrategyInput, StrategySignal, StrategyType,
)


class MomentumBurstStrategy(BaseStrategy):
    name = "Momentum Burst"
    strategy_type = StrategyType.MOMENTUM

    def __init__(self, min_score: int = 50):
        super().__init__(min_score)

    def analyze(self, inp: StrategyInput) -> StrategySignal | None:
        score = self.score_signal(inp)
        if score < self.min_score:
            return None

        df = inp.df_daily
        close = df["close"].values.astype(float)
        price = float(close[-1])
        details = self._burst_details(inp)

        atr_val = self._atr(df)
        risk = atr_val * 1.5
        stop = round(price - risk, 2)
        tp1 = round(price * 1.05, 2)
        tp2 = round(price * 1.08, 2)
        tp3 = round(price * 1.12, 2)

        reason_parts = []
        if details.get("day_change_pct", 0) >= 5:
            reason_parts.append(f"Day +{details['day_change_pct']:.1f}%")
        if details.get("vol_ratio", 0) >= 3:
            reason_parts.append(f"Vol {details['vol_ratio']:.1f}×")
        if details.get("close_position", 0) >= 0.7:
            reason_parts.append(f"Range top {details['close_position']*100:.0f}%")
        if details.get("adx", 0) > 25:
            reason_parts.append(f"ADX {details['adx']:.0f}")
        if details.get("above_ema50"):
            reason_parts.append(">EMA50")

        return StrategySignal(
            strategy="Momentum Burst",
            signal="BUY",
            score=score,
            entry=round(price, 2),
            stop=stop,
            tp1=tp1,
            tp2=tp2,
            tp3=tp3,
            hold_days_min=1,
            hold_days_max=5,
            confidence=min(1.0, score / 100 * 0.85 + 0.15),
            reason=" | ".join(reason_parts) if reason_parts else "Burst detected",
            details=details,
        )

    def score_signal(self, inp: StrategyInput) -> int:
        df = inp.df_daily
        if len(df) < 50:
            return 0

        details = self._burst_details(inp)
        score = 0

        cond = details.get("conditions", {})
        if cond.get("day_change"):    score += 20
        if cond.get("volume_ratio"):  score += 20
        if cond.get("close_position"): score += 15
        if cond.get("adx"):           score += 15
        if cond.get("rsi_ok"):        score += 10
        if cond.get("above_ema50"):   score += 10
        if cond.get("market_ok"):     score += 10

        return min(100, score)

    def _burst_details(self, inp: StrategyInput) -> dict:
        df = inp.df_daily
        close = df["close"].values.astype(float)
        high = df["high"].values.astype(float)
        low = df["low"].values.astype(float)
        vol = df["volume"].values.astype(float) if "volume" in df.columns else None
        price = float(close[-1])

        day_change_pct = ((close[-1] - close[-2]) / close[-2]) * 100 if len(close) >= 2 else 0
        atr_val = self._atr(df)

        vol_ratio = 0.0
        if vol is not None and len(vol) >= 21:
            avg_vol = float(np.mean(vol[-21:-1]))
            vol_ratio = round(float(vol[-1]) / avg_vol, 2) if avg_vol > 0 else 0

        daily_range = high[-1] - low[-1]
        close_position = (close[-1] - low[-1]) / daily_range if daily_range > 0 else 0.5

        adx_val = self._adx(df)
        rsi_val = self._rsi(pd.Series(close))

        ema50 = self._ema(pd.Series(close), 50)
        above_ema50 = float(ema50.iloc[-1]) < price if len(ema50) > 0 else False

        market_ok = True
        if inp.market_status:
            status = inp.market_status.get("status", "").upper()
            market_ok = "BEAR" not in status and "EXTREME" not in status

        conditions = {
            "day_change": day_change_pct >= 5,
            "volume_ratio": vol_ratio >= 3.0,
            "close_position": close_position >= 0.7,
            "adx": adx_val > 20,
            "rsi_ok": 40 <= rsi_val <= 70,
            "above_ema50": above_ema50,
            "market_ok": market_ok,
        }

        all_pass = all(conditions.values())

        return {
            "day_change_pct": round(day_change_pct, 2),
            "vol_ratio": vol_ratio,
            "close_position": round(close_position, 3),
            "adx": round(adx_val, 1),
            "rsi": round(rsi_val, 1),
            "above_ema50": above_ema50,
            "atr": round(atr_val, 2),
            "price": price,
            "conditions": conditions,
            "all_conditions_pass": all_pass,
            "entry_signal": all_pass,
        }
