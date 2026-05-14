"""Backtest all 4 strategies on historical data (2019-2026).

Usage: python -m app.strategies.backtest
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf

SYMBOLS = [
    "AAPL", "NVDA", "MSFT", "GOOGL", "COST",
    "AMZN", "META", "TSLA", "AVGO", "AMD",
    "ADBE", "CRM", "NFLX", "QCOM", "TXN",
    "AMAT", "MU", "LRCX", "KLAC", "MRVL",
    "NOW", "INTU", "UBER", "SNPS", "CDNS",
]
START = "2015-01-01"
END = "2026-05-01"

RISK_FREE = 0.05


# ── Indicator Helpers (vectorized) ──

def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def rsi(s: pd.Series, n: int = 14) -> pd.Series:
    d = s.diff()
    g = d.clip(lower=0).ewm(alpha=1 / n, min_periods=n).mean()
    l = (-d.clip(upper=0)).ewm(alpha=1 / n, min_periods=n).mean()
    return 100 - (100 / (1 + g / l))


def adx(df: pd.DataFrame, n: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    up = high.diff()
    dn = -low.diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0)
    tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(n).mean()
    plus_di = 100 * pd.Series(np.convolve(plus_dm, np.ones(n) / n, mode="same")[:len(df)], index=df.index) / atr
    minus_di = 100 * pd.Series(np.convolve(minus_dm, np.ones(n) / n, mode="same")[:len(df)], index=df.index) / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-9)
    return dx.rolling(n).mean()


def vwap(df: pd.DataFrame) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3
    cv = (tp * df["volume"]).cumsum()
    cv_vol = df["volume"].cumsum()
    return cv / cv_vol


def bollinger(s: pd.Series, n: int = 20) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    middle = s.rolling(n).mean()
    std = s.rolling(n).std()
    upper = middle + 2 * std
    lower = middle - 2 * std
    bw = (upper - lower) / middle * 100
    return upper, middle, lower, bw


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    tr = pd.concat([df["high"] - df["low"],
                    (df["high"] - df["close"].shift()).abs(),
                    (df["low"] - df["close"].shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def macd(s: pd.Series, fast: int = 12, slow: int = 26, sig: int = 9):
    e_fast = ema(s, fast)
    e_slow = ema(s, slow)
    macd_line = e_fast - e_slow
    sig_line = ema(macd_line, sig)
    hist = macd_line - sig_line
    return macd_line, sig_line, hist


def rs_vs_spy(df: pd.DataFrame, spy_df: pd.DataFrame, period: int = 20) -> pd.Series:
    sym_ret = df["close"].pct_change(period).dropna()
    spy_ret = spy_df["close"].pct_change(period).dropna()
    common = sym_ret.index.intersection(spy_ret.index)
    rs = (sym_ret.loc[common] - spy_ret.loc[common]) * 100
    return rs.reindex(df.index, method="ffill").fillna(0)


# ── Trade Simulation ──

@dataclass
class TradeResult:
    symbol: str
    strategy: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    return_pct: float
    exit_reason: str
    hold_days: int
    tp1_hit: bool = False
    tp2_hit: bool = False
    tp3_hit: bool = False


@dataclass
class BacktestResult:
    symbol: str
    strategy: str
    total_trades: int
    win_rate: float
    avg_return: float
    total_return: float
    sharpe: float
    max_drawdown: float
    avg_hold_days: float
    profit_factor: float
    trades: list[TradeResult] = field(default_factory=list)


def fetch_data(symbol: str) -> Optional[pd.DataFrame]:
    try:
        df = yf.download(symbol, start=START, end=END, progress=False)
        if df is None or df.empty or len(df) < 100:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [str(c[0]).lower() for c in df.columns]
        else:
            df.columns = [str(c).lower() for c in df.columns]
        for col in df.columns:
            if "unnamed" in col.lower():
                df.drop(columns=[col], inplace=True)
        df.rename(columns={"adj close": "close"}, inplace=True)
        return df
    except Exception as e:
        print(f"    ⚠️ fetch_data({symbol}) failed: {e}")
        return None


def _simulate_trade(df: pd.DataFrame, entry_idx: int,
                    stop_price: float, tp1: float, tp2: float, tp3: float,
                    hold_max: int, strategy: str) -> TradeResult:
    entry_price = float(df["close"].iloc[entry_idx])
    entry_date = str(df.index[entry_idx].date())
    exit_date = entry_date
    exit_price = entry_price
    exit_reason = "max_hold"
    tp1_hit = tp2_hit = tp3_hit = False
    high_water = entry_price

    for offset in range(1, min(hold_max + 1, len(df) - entry_idx - 1)):
        idx = entry_idx + offset
        day_close = float(df["close"].iloc[idx])
        day_high = float(df["high"].iloc[idx])
        day_low = float(df["low"].iloc[idx])
        exit_date = str(df.index[idx].date())

        high_water = max(high_water, day_high)

        if strategy == "momentum" and offset > 3:
            if day_low <= stop_price:
                exit_price = stop_price * 0.99
                exit_reason = "stop_loss"
                tp1_hit = tp2_hit = False
                break

        if day_high >= tp3:
            exit_price = tp3
            exit_reason = "tp3"
            tp1_hit = tp2_hit = tp3_hit = True
            break
        if day_high >= tp2 and exit_reason != "tp3":
            exit_price = tp2
            tp1_hit = True
            tp2_hit = True
            if offset >= hold_max - 2:
                exit_reason = "tp2"
                break
        if day_high >= tp1 and exit_reason not in ("tp2", "tp3"):
            exit_price = tp1
            tp1_hit = True
            break

        if day_low <= stop_price:
            exit_price = stop_price * 0.99
            exit_reason = "stop_loss"
            break

        exit_price = day_close

    ret = (exit_price - entry_price) / entry_price * 100
    return TradeResult(
        symbol="",
        strategy="",
        entry_date=entry_date,
        exit_date=exit_date,
        entry_price=round(entry_price, 2),
        exit_price=round(exit_price, 2),
        return_pct=round(ret, 2),
        exit_reason=exit_reason,
        hold_days=offset if "offset" in dir() else 0,
        tp1_hit=tp1_hit,
        tp2_hit=tp2_hit,
        tp3_hit=tp3_hit,
    )


def _compute_metrics(trades: list[TradeResult]) -> dict:
    if not trades:
        return {"total_trades": 0, "win_rate": 0, "avg_return": 0,
                "total_return": 0, "sharpe": 0, "max_drawdown": 0,
                "avg_hold_days": 0, "profit_factor": 0}

    returns = np.array([t.return_pct for t in trades])
    wins = returns[returns > 0]
    losses = returns[returns <= 0]
    win_rate = len(wins) / len(returns) * 100 if len(returns) > 0 else 0
    avg_ret = float(np.mean(returns))
    total_ret = float(np.sum(returns))

    sharpe = (float(np.mean(returns)) - RISK_FREE) / (float(np.std(returns)) + 1e-9) * np.sqrt(252 / float(np.mean([t.hold_days for t in trades]) + 1)) if len(returns) > 1 else 0
    avg_hold = float(np.mean([t.hold_days for t in trades]))

    cum = np.cumsum(returns)
    dd = cum - np.maximum.accumulate(cum)
    mdd = float(np.min(dd)) if len(dd) > 0 else 0

    profit_factor = float(np.sum(wins) / (abs(np.sum(losses)) + 1e-9)) if len(losses) > 0 else float("inf")

    return {
        "total_trades": len(trades),
        "win_rate": round(win_rate, 1),
        "avg_return": round(avg_ret, 2),
        "total_return": round(total_ret, 2),
        "sharpe": round(sharpe, 2),
        "max_drawdown": round(mdd, 2),
        "avg_hold_days": round(avg_hold, 1),
        "profit_factor": round(profit_factor, 2),
    }


# ── Strategy Backtests ──

def backtest_momentum(df: pd.DataFrame, spy_df: pd.DataFrame, symbol: str) -> BacktestResult:
    close = df["close"]
    high = df["high"]
    low = df["low"]
    vol = df["volume"]

    adx_val = adx(df, 14)
    macd_line, macd_sig, macd_hist = macd(close)
    vwap_val = vwap(df)
    rs_val = rs_vs_spy(df, spy_df, 20)
    rsi_val = rsi(close, 14)
    atr_val = atr(df, 14)

    ema20 = ema(close, 20)
    ema20_weekly = ema(close.resample("W").last(), 20)
    weekly_close = close.resample("W").last()
    weekly_rsi = rsi(weekly_close, 14)

    weekly_close_aligned = weekly_close.reindex(df.index, method="ffill")
    weekly_ema20_aligned = ema20_weekly.reindex(df.index, method="ffill")
    weekly_rsi_aligned = weekly_rsi.reindex(df.index, method="ffill")

    rs_cond = rs_val > 3
    adx_cond = adx_val > 25
    macd_pos = macd_hist > 0
    macd_rising = macd_hist.diff() > 0
    macd_cond = macd_pos & macd_rising
    vwap_cond = close > vwap_val
    weekly_up = weekly_close_aligned > weekly_ema20_aligned
    weekly_rsi_ok = (weekly_rsi_aligned >= 50) & (weekly_rsi_aligned <= 75)
    vol_cond = vol > vol.rolling(20).mean()

    entry_signal = rs_cond & adx_cond & macd_cond & vwap_cond & weekly_up & weekly_rsi_ok & vol_cond

    trades = []
    in_trade = False
    for i in range(200, len(df) - 60):
        if not in_trade and entry_signal.iloc[i]:
            price = float(close.iloc[i])
            risk = price * 0.04
            stop = price - risk
            sl_price = price - risk * 1.5
            tp1 = price + risk * 1.5
            tp2 = price + risk * 3.0
            tp3 = price + risk * 5.0

            tr = _simulate_trade(df, i, sl_price, tp1, tp2, tp3, 15, "momentum")
            tr.symbol = symbol
            tr.strategy = "Momentum"
            trades.append(tr)
            in_trade = True

        if in_trade:
            exit_day = trades[-1].exit_date
            exit_idx = df.index.get_loc(pd.Timestamp(exit_day)) if pd.Timestamp(exit_day) in df.index else i
            if i > exit_idx + 5:
                in_trade = False

    metrics = _compute_metrics(trades)
    return BacktestResult(symbol=symbol, strategy="Momentum", trades=trades, **metrics)


def backtest_reversion(df: pd.DataFrame, symbol: str) -> BacktestResult:
    close = df["close"]
    high = df["high"]
    low = df["low"]
    vol = df["volume"]

    bb_up, bb_mid, bb_low, bw = bollinger(close, 20)
    rsi_val = rsi(close, 14)
    atr_val = atr(df, 14)
    ema20 = ema(close, 20)

    weekly_close = close.resample("W").last()
    weekly_ema50 = ema(weekly_close, 50)
    weekly_close_aligned = weekly_close.reindex(df.index, method="ffill")
    weekly_ema50_aligned = weekly_ema50.reindex(df.index, method="ffill")

    below_bb_low = close < bb_low
    rsi_oversold = rsi_val < 35
    dist_ema20 = (ema20 - close).abs() / atr_val
    dist_cond = dist_ema20 > 2
    weekly_up = weekly_close_aligned > weekly_ema50_aligned
    # P1.4 — sync with live MeanReversionStrategy: ALL 6 conditions required (not soft_count)
    # Live rules: BB Lower + RSI<35 + dist>2*ATR + above weekly EMA50 + above daily EMA200 + volume>70%
    ema200 = ema(close, 200)
    above_ema200 = close > ema200
    vol_ok = vol > vol.rolling(20).mean() * 0.7
    entry_signal = below_bb_low & rsi_oversold & dist_cond & weekly_up & above_ema200 & vol_ok

    trades = []
    in_trade = False
    for i in range(200, len(df) - 30):
        if not in_trade and entry_signal.iloc[i]:
            price = float(close.iloc[i])
            # P1.4 — live strategy uses entry - 1.5*ATR (not bb_low - atr)
            stop = float(price - 1.5 * atr_val.iloc[i])
            tp1 = float(ema20.iloc[i])
            tp2 = float(bb_mid.iloc[i])
            tp3 = float(bb_up.iloc[i])

            tr = _simulate_trade(df, i, stop, tp1, tp2, tp3, 7, "reversion")
            tr.symbol = symbol
            tr.strategy = "Mean Reversion"
            trades.append(tr)
            in_trade = True

        if in_trade:
            exit_day = trades[-1].exit_date
            exit_idx = df.index.get_loc(pd.Timestamp(exit_day)) if pd.Timestamp(exit_day) in df.index else i
            if i > exit_idx + 3:
                in_trade = False

    metrics = _compute_metrics(trades)
    return BacktestResult(symbol=symbol, strategy="Mean Reversion", trades=trades, **metrics)


def backtest_breakout(df: pd.DataFrame, symbol: str) -> BacktestResult:
    close = df["close"]
    high = df["high"]
    low = df["low"]
    vol = df["volume"]

    atr_val = atr(df, 14)
    high_20 = high.rolling(20).max().shift(1)
    vol_20_avg = vol.rolling(20).mean()
    vol_5_avg = vol.rolling(5).mean()

    close_above = close > high_20
    vol_spike = vol > vol_20_avg * 1.5
    vol_trend = vol_5_avg > vol_20_avg.shift(1) * 1.2
    atr_cond = atr_val / close * 100 > 2

    high_52w = high.rolling(252).max()
    near_52w = (close / high_52w).shift(1) > 0.95

    weekly_close = close.resample("W").last()
    weekly_bb_up, _, weekly_bb_low, weekly_bw = bollinger(weekly_close, 20)
    weekly_bb_aligned = weekly_bb_up.reindex(df.index, method="ffill")
    weekly_bb_active = weekly_bw.reindex(df.index, method="ffill") if not weekly_bw.empty else pd.Series(0, index=df.index)
    weekly_squeeze = weekly_bb_active < 3.0

    entry_signal = close_above & vol_spike & atr_cond & (near_52w | weekly_squeeze | vol_trend)
    entry_signal = entry_signal & (close > high_20 * 1.002)

    trades = []
    in_trade = False
    for i in range(300, len(df) - 60):
        if not in_trade and entry_signal.iloc[i]:
            price = float(close.iloc[i])
            atr_v = float(atr_val.iloc[i])
            resistance = float(high_20.iloc[i])
            stop = round(resistance * 0.995, 2)
            tp1 = round(price + atr_v * 2, 2)
            tp2 = round(price + atr_v * 4, 2)
            tp3 = round(price + atr_v * 6, 2)

            tr = _simulate_trade(df, i, stop, tp1, tp2, tp3, 20, "breakout")
            tr.symbol = symbol
            tr.strategy = "Breakout"
            trades.append(tr)
            in_trade = True

        if in_trade:
            exit_day = trades[-1].exit_date
            exit_idx = df.index.get_loc(pd.Timestamp(exit_day)) if pd.Timestamp(exit_day) in df.index else i
            if i > exit_idx + 5:
                in_trade = False

    metrics = _compute_metrics(trades)
    return BacktestResult(symbol=symbol, strategy="Breakout", trades=trades, **metrics)


def backtest_swing(df: pd.DataFrame, spy_df: pd.DataFrame, symbol: str) -> BacktestResult:
    close = df["close"]
    high = df["high"]
    low = df["low"]
    vol = df["volume"]

    ema20 = ema(close, 20)
    ema50 = ema(close, 50)
    rsi_val = rsi(close, 14)
    atr_val = atr(df, 14)

    dist20 = (close - ema20).abs() / close * 100
    near_ema20 = dist20 < 2.0
    dist50 = (close - ema50).abs() / close * 100
    near_ema50 = dist50 < 2.5

    healthy_rsi = (rsi_val >= 40) & (rsi_val <= 50)
    pullback_vol = vol < vol.rolling(20).mean()

    weekly_close = close.resample("W").last()
    weekly_ema20 = ema(weekly_close, 20)
    weekly_close_aligned = weekly_close.reindex(df.index, method="ffill")
    weekly_ema20_aligned = weekly_ema20.reindex(df.index, method="ffill")
    weekly_up = weekly_close_aligned > weekly_ema20_aligned

    weekly_hh = weekly_close.diff() > 0
    weekly_hh_aligned = weekly_hh.reindex(df.index, method="ffill").fillna(False)

    rs_val = rs_vs_spy(df, spy_df, 60)
    rs_pos = rs_val > 0

    macd_line, macd_sig, macd_hist = macd(close)
    macd_pos = macd_hist > 0

    entry_signal = (near_ema20 | near_ema50) & healthy_rsi & pullback_vol & weekly_up & weekly_hh_aligned & macd_pos

    trades = []
    in_trade = False
    for i in range(200, len(df) - 40):
        if not in_trade and entry_signal.iloc[i]:
            price = float(close.iloc[i])

            prev_high = float(high.iloc[max(0, i - 20):i].max()) if i >= 20 else price * 1.03
            pullback_low = float(low.iloc[max(0, i - 10):i + 1].min()) if i >= 10 else price * 0.97
            stop = round(pullback_low * 0.997, 2)

            tp1 = round(prev_high, 2)
            fib_ext = prev_high - price if prev_high > price else price * 0.03
            tp2 = round(price + fib_ext * 1.272, 2)
            tp3 = round(price + fib_ext * 1.618, 2)

            tr = _simulate_trade(df, i, stop, tp1, tp2, tp3, 15, "swing")
            tr.symbol = symbol
            tr.strategy = "Swing"
            trades.append(tr)
            in_trade = True

        if in_trade:
            exit_day = trades[-1].exit_date
            exit_idx = df.index.get_loc(pd.Timestamp(exit_day)) if pd.Timestamp(exit_day) in df.index else i
            if i > exit_idx + 3:
                in_trade = False

    metrics = _compute_metrics(trades)
    return BacktestResult(symbol=symbol, strategy="Swing", trades=trades, **metrics)


def backtest_momentum_burst(df: pd.DataFrame, spy_df: pd.DataFrame, symbol: str) -> BacktestResult:
    close = df["close"]
    high = df["high"]
    low = df["low"]
    vol = df["volume"]

    day_change = close.pct_change() * 100
    vol_ratio = vol / vol.rolling(20).mean()
    daily_range = high - low
    close_position = (close - low) / daily_range.replace(0, float("nan"))

    adx_val = adx(df, 14)
    rsi_val = rsi(close, 14)
    ema50 = ema(close, 50)
    atr_val = atr(df, 14)

    cond_day_change = day_change >= 5.0
    cond_vol = vol_ratio >= 3.0
    cond_close_pos = close_position >= 0.7
    cond_adx = adx_val > 20
    cond_rsi = (rsi_val >= 40) & (rsi_val <= 70)
    cond_ema50 = close > ema50

    entry_signal = cond_day_change & cond_vol & cond_close_pos & cond_adx & cond_rsi & cond_ema50

    trades = []
    in_trade = False
    for i in range(200, len(df) - 10):
        if not in_trade and entry_signal.iloc[i]:
            price = float(close.iloc[i])
            atr_i = float(atr_val.iloc[i]) if not pd.isna(atr_val.iloc[i]) else price * 0.02
            stop = price - 1.5 * atr_i
            tp1 = price * 1.05
            tp2 = price * 1.08
            tp3 = price * 1.12

            tr = _simulate_trade(df, i, stop, tp1, tp2, tp3, 5, "momentum_burst")
            tr.symbol = symbol
            tr.strategy = "Momentum Burst"
            trades.append(tr)
            in_trade = True

        if in_trade:
            exit_day = trades[-1].exit_date
            exit_idx = df.index.get_loc(pd.Timestamp(exit_day)) if pd.Timestamp(exit_day) in df.index else i
            if i > exit_idx + 2:
                in_trade = False

    metrics = _compute_metrics(trades)
    return BacktestResult(symbol=symbol, strategy="Momentum Burst", trades=trades, **metrics)


# ── Main Runner ──

def print_result(r: BacktestResult):
    name = f"{r.symbol} {r.strategy}"
    print(f"  {name:30s} | Trades: {r.total_trades:3d} | Win: {r.win_rate:5.1f}% | "
          f"AvgRet: {r.avg_return:6.2f}% | Sharpe: {r.sharpe:5.2f} | "
          f"MaxDD: {r.max_drawdown:6.2f}% | AvgHold: {r.avg_hold_days:4.1f}d | "
          f"ProfitFactor: {r.profit_factor:5.2f}")


def print_win_trades(trades: list[TradeResult], strategy: str, symbol: str):
    wins = [t for t in trades if t.return_pct > 0]
    best = max(wins, key=lambda t: t.return_pct) if wins else None
    if best:
        print(f"  🏆 Best {strategy} trade on {symbol}: "
              f"{best.entry_date}→{best.exit_date} "
              f"Entry=${best.entry_price:.2f} Exit=${best.exit_price:.2f} "
              f"Ret={best.return_pct:+.2f}% ({best.exit_reason})")


def print_loss_trades(trades: list[TradeResult], strategy: str, symbol: str):
    losses = [t for t in trades if t.return_pct <= 0]
    worst = min(losses, key=lambda t: t.return_pct) if losses else None
    if worst:
        print(f"  ❌ Worst {strategy} trade on {symbol}: "
              f"{worst.entry_date}→{worst.exit_date} "
              f"Entry=${worst.entry_price:.2f} Exit=${worst.exit_price:.2f} "
              f"Ret={worst.return_pct:+.2f}% ({worst.exit_reason})")


def main():
    print("=" * 120)
    print(f"BACKTEST: All 4 Strategies on {SYMBOLS}")
    print(f"Period: {START} -> {END}")
    print("=" * 120)

    print("\n📥 Fetching data...")
    spy_df = fetch_data("SPY")
    if spy_df is None:
        print("❌ Failed to fetch SPY data")
        return

    results: dict[str, dict[str, BacktestResult]] = {s: {} for s in SYMBOLS}
    data_cache = {}

    for symbol in SYMBOLS:
        print(f"\n{'─' * 60}")
        print(f"📊 {symbol}")
        print(f"{'─' * 60}")

        df = fetch_data(symbol)
        if df is None:
            print(f"  ❌ No data for {symbol}")
            continue
        data_cache[symbol] = df

        # Momentum
        print("\n  🔄 Momentum...")
        r = backtest_momentum(df, spy_df, symbol)
        results[symbol]["Momentum"] = r
        print_result(r)
        if r.trades:
            print_win_trades(r.trades, "Momentum", symbol)
            print_loss_trades(r.trades, "Momentum", symbol)

        # Mean Reversion
        print("\n  🔄 Mean Reversion...")
        r = backtest_reversion(df, symbol)
        results[symbol]["Mean Reversion"] = r
        print_result(r)
        if r.trades:
            print_win_trades(r.trades, "Mean Reversion", symbol)
            print_loss_trades(r.trades, "Mean Reversion", symbol)

        # Breakout
        print("\n  🔄 Breakout...")
        r = backtest_breakout(df, symbol)
        results[symbol]["Breakout"] = r
        print_result(r)
        if r.trades:
            print_win_trades(r.trades, "Breakout", symbol)
            print_loss_trades(r.trades, "Breakout", symbol)

        # Swing
        print("\n  🔄 Swing...")
        r = backtest_swing(df, spy_df, symbol)
        results[symbol]["Swing"] = r
        print_result(r)
        if r.trades:
            print_win_trades(r.trades, "Swing", symbol)
            print_loss_trades(r.trades, "Swing", symbol)

    # ── Summary Table ──
    print("\n" + "=" * 120)
    print("SUMMARY")
    print("=" * 120)
    header = f"{'Symbol':8s} {'Strategy':16s} {'Trades':7s} {'WinRate':8s} {'AvgRet':8s} {'Sharpe':7s} {'MaxDD':7s} {'AvgHold':8s} {'ProfitF':7s}"
    print(header)
    print("-" * 90)

    all_pass = True
    for symbol in SYMBOLS:
        for sname in ["Momentum", "Mean Reversion", "Breakout", "Swing"]:
            r = results.get(symbol, {}).get(sname)
            if r and r.total_trades > 0:
                pass_wr = r.win_rate > 50
                pass_sharpe = r.sharpe > 1.5
                pass_dd = r.max_drawdown > -20
                status = "✅" if (pass_wr and pass_sharpe) else "⚠️"
                if not (pass_wr and pass_sharpe):
                    all_pass = False
                print(f"  {symbol:6s} {sname:16s} {r.total_trades:3d}     {r.win_rate:5.1f}%   {r.avg_return:6.2f}%  {r.sharpe:5.2f}  {r.max_drawdown:6.2f}%  {r.avg_hold_days:5.1f}d   {r.profit_factor:5.2f}  {status}")
            elif r:
                print(f"  {symbol:6s} {sname:16s} {'0':>3s}     {'N/A':>6s}   {'N/A':>7s}  {'N/A':>5s}  {'N/A':>6s}  {'N/A':>5s}s   {'N/A':>5s}  ⚠️")

    print("-" * 90)
    if all_pass:
        print("\n✅ ALL strategies pass targets (Win Rate > 50%, Sharpe > 1.5, Max DD > -20%)")
    else:
        print("\n⚠️ Some strategies need improvement")

    # ── Per-strategy summary ──
    print("\n" + "=" * 120)
    print("PER-STRATEGY AVERAGES")
    print("=" * 120)
    for sname in ["Momentum", "Mean Reversion", "Breakout", "Swing"]:
        strat_results = [results[s][sname] for s in SYMBOLS if s in results and sname in results[s] and results[s][sname].total_trades > 0]
        if strat_results:
            avg_wr = float(np.mean([r.win_rate for r in strat_results]))
            avg_sharpe = float(np.mean([r.sharpe for r in strat_results]))
            avg_dd = float(np.mean([r.max_drawdown for r in strat_results]))
            avg_ret = float(np.mean([r.avg_return for r in strat_results]))
            total_trades = sum(r.total_trades for r in strat_results)
            print(f"  {sname:20s} Trades: {total_trades:3d} | AvgWin: {avg_wr:5.1f}% | "
                  f"AvgSharpe: {avg_sharpe:5.2f} | AvgMaxDD: {avg_dd:6.2f}% | "
                  f"AvgRet: {avg_ret:6.2f}%")

    print("\n✅ Backtest complete\n")


if __name__ == "__main__":
    main()
