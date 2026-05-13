"""Mean Reversion Strategy — fade extremes at Bollinger Bands.

Hard conditions (all must pass):
  1. Close < Bollinger Lower Band (20, 2σ)
  2. RSI(14) < 35
  3. Distance from EMA20 > 2 ATR
  4. Price > EMA50 weekly (uptrend confirmation)
  5. Price > EMA200 daily (no secular downtrend)
  6. Volume > 70% of 20d average

Stop:   Entry - 1.5 × ATR (fixed)
TP1:    EMA20 (close 50%)
TP2:    Middle BB (close 30%)
TP3:    Upper BB (close 20%)
Hold:   7 days exactly (time stop)
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.strategies import (
    BaseStrategy, StrategyInput, StrategySignal,
    StrategyType,
)


class MeanReversionStrategy(BaseStrategy):
    name = "Mean Reversion"
    strategy_type = StrategyType.REVERSION

    def __init__(self, min_score: int = 100):
        super().__init__(min_score)

    def analyze(self, inp: StrategyInput) -> StrategySignal | None:
        passed, details = self._check_conditions(inp)
        if not passed:
            return None

        df = inp.df_daily
        close_s = df["close"]
        price = float(close_s.values[-1])
        atr = self._atr(df)

        bb_mid = details["bb_mid"]
        bb_upper = details["bb_upper"]
        ema20_val = details["ema20"]
        stop = round(price - 1.5 * atr, 2)

        reason = (
            f"below BB lower ({details['bb_lower']:.2f}) | "
            f"RSI {details['rsi']:.0f} oversold | "
            f"{details['dist_ema_atr']:.1f} ATR from EMA20 | "
            f"weekly uptrend | "
            f"above EMA200 | "
            f"volume {details['vol_ratio']:.1f}x avg"
        )

        return StrategySignal(
            strategy="Mean Reversion",
            signal="BUY",
            score=100,
            entry=round(price, 2),
            stop=stop,
            tp1=round(ema20_val, 2),
            tp2=round(bb_mid, 2),
            tp3=round(bb_upper, 2),
            hold_days_min=7,
            hold_days_max=7,
            confidence=0.7,
            reason=reason,
            details=details,
        )

    def score_signal(self, inp: StrategyInput) -> int:
        passed, _ = self._check_conditions(inp)
        return 100 if passed else 0

    def _check_conditions(self, inp: StrategyInput) -> tuple[bool, dict]:
        """Check all 6 hard entry conditions. ALL must pass."""
        df = inp.df_daily
        if len(df) < 30:
            return False, {}

        mkt = inp.market_status or {}
        if mkt.get("status") == "BEAR":
            return False, {}

        close_s = df["close"]
        close = close_s.values.astype(float)
        price = float(close[-1])
        atr = self._atr(df)

        bb_upper, bb_mid, bb_lower, bw = self._bollinger(close_s)
        rsi = self._rsi(close_s)
        vol_ratio = self._volume_ratio(df)
        ema20_val = float(self._ema(close_s, 20).iloc[-1])
        dist_ema_atr = abs(price - ema20_val) / atr if atr > 0 else 0

        # Condition 1: Close < BB Lower Band
        cond1 = bb_lower > 0 and price < bb_lower

        # Condition 2: RSI < 35
        cond2 = rsi < 35

        # Condition 3: Distance from EMA20 > 2 ATR
        cond3 = dist_ema_atr > 2

        # Condition 4: Price > EMA50 weekly
        cond4 = False
        if inp.df_weekly is not None and len(inp.df_weekly) >= 12:
            w_close_s = inp.df_weekly["close"]
            w_price = float(w_close_s.values[-1])
            w_ema50 = float(self._ema(w_close_s, 50).iloc[-1]) if len(w_close_s) >= 50 else w_price
            cond4 = w_price > w_ema50

        # Condition 5: Price > EMA200 daily
        cond5 = False
        if len(close_s) >= 200:
            ema200 = float(self._ema(close_s, 200).iloc[-1])
            cond5 = price > ema200

        # Condition 6: Volume > 70% of 20d average
        cond6 = vol_ratio > 0.7

        details = {
            "bb_upper": bb_upper,
            "bb_mid": bb_mid,
            "bb_lower": bb_lower,
            "bb_bandwidth": bw,
            "rsi": rsi,
            "vol_ratio": vol_ratio,
            "ema20": ema20_val,
            "dist_ema_atr": round(dist_ema_atr, 2),
            "price": price,
            "atr": atr,
            "cond1_below_bb_lower": cond1,
            "cond2_rsi_oversold": cond2,
            "cond3_dist_ema_atr": cond3,
            "cond4_weekly_uptrend": cond4,
            "cond5_above_ema200": cond5,
            "cond6_volume_ok": cond6,
            "adx": round(self._adx(df), 1),
        }

        all_pass = cond1 and cond2 and cond3 and cond4 and cond5 and cond6
        return all_pass, details
