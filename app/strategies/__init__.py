"""Strategy framework — base class + auto selector for 4 trading strategies.

Strategies:
  MOMENTUM   — ride strong trends; entry on pullback continuation
  REVERSION  — fade extremes; entry at Bollinger extremes
  BREAKOUT   — capture range expansions; entry on resistance breaks
  SWING      — swing pullbacks in uptrends; entry at EMA support
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger("strategies")


class StrategyType(Enum):
    MOMENTUM = "momentum"
    REVERSION = "reversion"
    BREAKOUT = "breakout"
    SWING = "swing"


@dataclass
class StrategySignal:
    strategy: str
    signal: str  # BUY / WAIT / AVOID
    score: int   # 0-100 quality score
    entry: float
    stop: float
    tp1: float
    tp2: float
    tp3: float
    hold_days_min: int
    hold_days_max: int
    confidence: float  # 0-1
    reason: str
    details: dict = field(default_factory=dict)


@dataclass
class StrategyInput:
    df_daily: pd.DataFrame
    spy_df: Optional[pd.DataFrame] = None
    market_status: Optional[dict] = None
    df_weekly: Optional[pd.DataFrame] = None
    df_15min: Optional[pd.DataFrame] = None


class BaseStrategy(ABC):
    name: str = "base"
    strategy_type: StrategyType = StrategyType.MOMENTUM

    def __init__(self, min_score: int = 65):
        self.min_score = min_score

    @abstractmethod
    def analyze(self, inp: StrategyInput) -> StrategySignal | None:
        ...

    @abstractmethod
    def score_signal(self, inp: StrategyInput) -> int:
        ...

    def _atr(self, df: pd.DataFrame, n: int = 14) -> float:
        if len(df) < n + 1:
            return 0.0
        h, l, c = df["high"].values, df["low"].values, df["close"].values
        tr = np.maximum(h[1:] - l[1:], np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1]))
        return float(np.mean(tr[-n:]))

    def _ema(self, s: pd.Series, n: int) -> pd.Series:
        return s.ewm(span=n, adjust=False).mean()

    def _rsi(self, s: pd.Series, n: int = 14) -> float:
        d = s.diff()
        g = d.clip(lower=0).ewm(alpha=1/n, min_periods=n).mean()
        l = (-d.clip(upper=0)).ewm(alpha=1/n, min_periods=n).mean()
        return float((100 - (100 / (1 + g / l))).iloc[-1])

    def _adx(self, df: pd.DataFrame, n: int = 14) -> float:
        if len(df) < n * 2:
            return 0.0
        high, low, close = df["high"], df["low"], df["close"]
        up = high.diff()
        dn = -low.diff()
        plus_dm = np.where((up > dn) & (up > 0), up, 0)
        minus_dm = np.where((dn > up) & (dn > 0), dn, 0)
        tr = np.maximum(high.values[1:] - low.values[1:],
                        np.abs(high.values[1:] - close.values[:-1]),
                        np.abs(low.values[1:] - close.values[:-1]))
        atr = np.convolve(tr, np.ones(n)/n, mode='valid')
        atr = np.mean(atr[-n:])
        if atr == 0:
            return 0.0
        plus_di = 100 * np.convolve(plus_dm, np.ones(n)/n, mode='valid') / atr
        minus_di = 100 * np.convolve(minus_dm, np.ones(n)/n, mode='valid') / atr
        dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di + 1e-9)
        return float(np.mean(dx[-n:]))

    def _rs_vs_spy(self, df: pd.DataFrame, spy_df: Optional[pd.DataFrame],
                   period: int = 20) -> float:
        if spy_df is None or len(spy_df) < period or len(df) < period:
            return 0.0
        sym = df["close"].values
        spy = spy_df["close"].values
        sym_ret = (sym[-1] / sym[-period] - 1) * 100 if sym[-period] > 0 else 0
        spy_ret = (spy[-1] / spy[-period] - 1) * 100 if spy[-period] > 0 else 0
        return round(sym_ret - spy_ret, 2)

    def _volume_ratio(self, df: pd.DataFrame, period: int = 20) -> float:
        if "volume" not in df.columns or len(df) < period:
            return 1.0
        vol = df["volume"].values.astype(float)
        latest = vol[-1]
        avg = np.mean(vol[-period:])
        return round(latest / avg, 2) if avg > 0 else 1.0

    def _vwap(self, df: pd.DataFrame) -> float:
        if not all(c in df.columns for c in ("high", "low", "close", "volume")):
            return float(df["close"].iloc[-1])
        tp = (df["high"] + df["low"] + df["close"]) / 3
        cv = (tp * df["volume"]).cumsum()
        cv_vol = df["volume"].cumsum()
        return float((cv / cv_vol).iloc[-1])

    def _bollinger(self, s: pd.Series, n: int = 20) -> tuple[float, float, float, float]:
        if len(s) < n:
            return 0.0, float(s.iloc[-1]), 0.0, 0.0
        middle = s.rolling(n).mean().iloc[-1]
        std = s.rolling(n).std().iloc[-1]
        upper = middle + 2 * std
        lower = middle - 2 * std
        bw = (upper - lower) / middle * 100
        return float(upper), float(middle), float(lower), float(bw)

    def _entry_stop_tp(self, price: float, atr: float, risk_pct: float = 0.01
                        ) -> tuple[float, float, float, float, float]:
        risk = price * risk_pct
        stop = price - risk
        tp1 = price + risk * 1.5
        tp2 = price + risk * 3.0
        tp3 = price + risk * 5.0
        return round(price, 2), round(stop, 2), round(tp1, 2), round(tp2, 2), round(tp3, 2)


class StrategySelector:
    def __init__(self):
        self.strategies: dict[StrategyType, BaseStrategy] = {}
        self._market_context: dict = {}

    def register(self, strategy: BaseStrategy):
        self.strategies[strategy.strategy_type] = strategy

    def set_market_context(self, ctx: dict):
        self._market_context = ctx

    def select(
        self,
        inp: StrategyInput,
        top_k: int = 2,
    ) -> list[StrategySignal]:
        if not self.strategies:
            return []

        eligible = self._eligible_strategies(inp)
        if not eligible:
            eligible = list(self.strategies.keys())

        signals = []
        for stype in eligible:
            strat = self.strategies.get(stype)
            if strat is None:
                continue
            try:
                sig = strat.analyze(inp)
                if sig:
                    signals.append(sig)
            except Exception as e:
                logger.debug("Strategy %s failed: %s", stype.value, e)

        signals.sort(key=lambda s: s.score, reverse=True)
        return signals[:top_k]

    def best_for_symbol(self, inp: StrategyInput) -> StrategySignal | None:
        ranked = self.select(inp, top_k=1)
        return ranked[0] if ranked else None

    # ── Decision tree ──────────────────────────────────────

    def _eligible_strategies(self, inp: StrategyInput) -> list[StrategyType]:
        """Decision tree: market context → stock metrics → timeframe alignment."""
        mkt = inp.market_status or {}
        status = (mkt.get("status") or "NEUTRAL").upper()
        vix = float(mkt.get("vix", 20))

        # Step 1: Market Context gate
        if status == "BEAR":
            logger.warning(
                "All strategies DISABLED — market_status=%s "
                "(this blocks ALL signal execution)", status,
            )
            return []

        market_preferred: list[StrategyType] = []
        if status in ("BULL", "RISK ON") and vix < 20:
            market_preferred = [StrategyType.MOMENTUM, StrategyType.BREAKOUT]
        elif status in ("CREDIT STRESS", "VIX", "EXTREME FEAR"):
            if status in ("EXTREME FEAR",):
                logger.warning(
                    "All market-preferred strategies DISABLED — "
                    "market_status=%s vix=%.1f", status, vix,
                )
            market_preferred = []  # REVERSION DISABLED — A-Pre.1
        else:
            market_preferred = [StrategyType.SWING]  # REVERSION DISABLED — A-Pre.1

        # Step 2: Stock-specific filtering
        df = inp.df_daily
        if df is None or len(df) < 30:
            return market_preferred

        close = df["close"].values.astype(float)
        high = df["high"].values.astype(float)
        low = df["low"].values.astype(float)
        price = float(close[-1])

        adx_val = self._calc_adx(df)
        bb_squeeze = self._calc_bb_squeeze(df)
        rs_val = self._calc_rs(inp)

        stock_filtered: list[StrategyType] = []
        for st in market_preferred:
            if st == StrategyType.MOMENTUM:
                if adx_val >= 25:
                    stock_filtered.append(st)
                elif adx_val >= 20 and status not in ("CREDIT STRESS",):
                    stock_filtered.append(st)
            elif st == StrategyType.BREAKOUT:
                if bb_squeeze or adx_val >= 25:
                    stock_filtered.append(st)
                elif adx_val >= 20:
                    stock_filtered.append(st)
            # REVERSION (Mean Reversion) DISABLED — see A-Pre.1 / A-Pre.2
            # Re-enable only after Sharpe >= 0.8 and Win Rate >= 55% confirmed
            elif st == StrategyType.SWING:
                if adx_val < 25 and rs_val > -2:
                    stock_filtered.append(st)
                else:
                    stock_filtered.append(st)

        if not stock_filtered:
            return market_preferred

        # Step 3: Timeframe alignment (weekly trend check)
        aligned: list[StrategyType] = []
        for st in stock_filtered:
            if self._check_timeframe(inp, st):
                aligned.append(st)

        return aligned if aligned else stock_filtered

    def _calc_adx(self, df: pd.DataFrame, n: int = 14) -> float:
        if len(df) < n * 2:
            return 0.0
        high, low, close = df["high"], df["low"], df["close"]
        up = high.diff()
        dn = -low.diff()
        plus_dm = np.where((up > dn) & (up > 0), up, 0)
        minus_dm = np.where((dn > up) & (dn > 0), dn, 0)
        tr = np.maximum(high.values[1:] - low.values[1:],
                        np.abs(high.values[1:] - close.values[:-1]),
                        np.abs(low.values[1:] - close.values[:-1]))
        atr = np.convolve(tr, np.ones(n) / n, mode="valid")
        atr = np.mean(atr[-n:]) if len(atr) >= n else 1
        if atr == 0:
            return 0.0
        plus_di = 100 * np.convolve(plus_dm[:len(tr)], np.ones(n) / n, mode="valid") / atr
        minus_di = 100 * np.convolve(minus_dm[:len(tr)], np.ones(n) / n, mode="valid") / atr
        dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di + 1e-9)
        return float(np.mean(dx[-n:]))

    def _calc_bb_squeeze(self, df: pd.DataFrame, n: int = 20) -> bool:
        close_s = df["close"]
        if len(close_s) < n:
            return False
        std = close_s.rolling(n).std()
        middle = close_s.rolling(n).mean()
        bw = (2 * std * 2) / middle * 100
        avg_bw = bw.rolling(n).mean()
        return float(bw.iloc[-1]) > 0 and float(avg_bw.iloc[-1]) > 0 and (float(bw.iloc[-1]) / float(avg_bw.iloc[-1]) < 0.85) if float(avg_bw.iloc[-1]) > 0 else False

    def _calc_rs(self, inp: StrategyInput, period: int = 20) -> float:
        """Calculate RS vs SPY."""
        df = inp.df_daily
        spy_df = inp.spy_df
        if spy_df is None or len(spy_df) < period or len(df) < period:
            return 0.0
        sym = df["close"].values
        spy = spy_df["close"].values
        sym_ret = (sym[-1] / sym[-period] - 1) * 100 if sym[-period] > 0 else 0
        spy_ret = (spy[-1] / spy[-period] - 1) * 100 if spy[-period] > 0 else 0
        return round(sym_ret - spy_ret, 2)

    def _check_timeframe(self, inp: StrategyInput, st: StrategyType) -> bool:
        """Check if strategy's required timeframes are aligned."""
        df_d = inp.df_daily
        df_w = inp.df_weekly
        if df_d is None or len(df_d) < 30:
            return True

        close_d = df_d["close"].values.astype(float)
        price = float(close_d[-1])

        daily_trend = "up" if len(close_d) >= 20 and close_d[-1] > close_d[-20] else "down"

        weekly_trend = daily_trend
        if df_w is not None and len(df_w) >= 5:
            w_close = df_w["close"].values.astype(float)
            weekly_trend = "up" if len(w_close) >= 5 and w_close[-1] > w_close[-5] else "down"

        if st in (StrategyType.MOMENTUM, StrategyType.BREAKOUT):
            return daily_trend == "up" and weekly_trend == "up"
        elif st == StrategyType.REVERSION:
            return weekly_trend == "up"
        elif st == StrategyType.SWING:
            return daily_trend == "up" or weekly_trend == "up"
        return True


def get_strategy_selector() -> StrategySelector:
    from app.strategies.momentum import MomentumStrategy
    from app.strategies.swing import SwingStrategy
    from app.strategies.breakout import BreakoutStrategy
    from app.strategies.momentum_burst import MomentumBurstStrategy

    selector = StrategySelector()
    selector.register(MomentumStrategy())
    # MeanReversionStrategy DISABLED — A-Pre.1 (Sharpe -1.93, WR 26.3%)
    selector.register(BreakoutStrategy())
    selector.register(SwingStrategy())
    selector.register(MomentumBurstStrategy())
    return selector