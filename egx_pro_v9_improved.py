"""
EGX Pro V9 - Improved Trading Strategy
======================================
Enhanced version with:
- Real-time data connection to broker
- Improved parameter optimization
- Better risk management
- Adaptive stops and targets
"""

import pandas as pd
import numpy as np
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, List, Dict
import warnings

warnings.filterwarnings("ignore")


@dataclass
class StrategyConfig:
    initial_capital: float = 90_000.0

    # Risk Management
    risk_per_trade: float = 0.02  # 2% max risk per trade
    max_daily_risk: float = 0.04  # 4% max daily loss

    # Trend
    ema_fast: int = 9
    ema_mid: int = 21
    ema_slow: int = 50

    # RSI
    rsi_period: int = 14
    rsi_oversold: float = 35
    rsi_overbought: float = 65

    # MACD
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9

    # ATR
    atr_period: int = 14
    sl_atr_mult: float = 2.0  # Tighter stop
    tp_atr_mult: float = 3.0  # Bigger target for better RR

    # Volume
    vol_avg_period: int = 20
    vol_surge: float = 1.5

    # Signal quality - MORE STRICT
    min_score: int = 5  # Only best signals

    # Filters
    min_price: float = 2.0
    min_avg_volume: float = 50_000

    # Session (EGX hours)
    session_start: int = 1030  # 10:30 AM
    session_end: int = 1430  # 2:30 PM


class BrokerDataConnector:
    """Connect to broker for real-time data (bypassing TradingView delay)"""

    def __init__(self, broker: str = "demo"):
        self.broker = broker
        self.data_cache = {}

    def get_realtime_data(self, symbol: str, timeframe: str = "15min") -> pd.DataFrame:
        """
        Get real-time data from broker API
        Replace with your broker's API implementation
        """
        # Example with yfinance (works for many exchanges)
        try:
            import yfinance as yf

            ticker = yf.Ticker(f"{symbol}.CA")  # Adjust for EGX
            df = ticker.history(period="5d", interval=timeframe)
            return df
        except Exception as e:
            print(f"Error fetching data: {e}")
            return pd.DataFrame()

    def connect_ib(self, host: str = "127.0.0.1", port: int = 7497):
        """Interactive Brokers connection for real-time data"""
        try:
            from ib_insync import IB

            self.ib = IB()
            self.ib.connect(host, port, clientId=1)
            return self.ib
        except Exception as e:
            print(f"IB Connection failed: {e}")
            return None


def load_stock_data(filepath: str) -> pd.DataFrame:
    """Load stock data from CSV file"""
    col_map = {
        "الرمز": "symbol",
        "نطاق البيانات": "date",
        "فتح": "open",
        "أعلى": "high",
        "الأدنى": "low",
        "مغلق": "close",
        "التغير%": "change_pct",
        "تغيير": "change",
        "إقفال سابق": "prev_close",
        "حجم التداول": "volume",
        "قيمة التداول": "value",
    }
    df = pd.read_csv(filepath)
    df.rename(columns=col_map, inplace=True)
    df["date"] = pd.to_datetime(df["date"])
    for col in ["open", "high", "low", "close", "volume", "value"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df.sort_values("date", inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df


def compute_indicators(df: pd.DataFrame, cfg: StrategyConfig) -> pd.DataFrame:
    """Calculate all technical indicators"""
    df = df.copy()

    # EMAs
    df["ema_fast"] = df["close"].ewm(span=cfg.ema_fast, adjust=False).mean()
    df["ema_mid"] = df["close"].ewm(span=cfg.ema_mid, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=cfg.ema_slow, adjust=False).mean()

    # RSI
    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(
        alpha=1 / cfg.rsi_period, min_periods=cfg.rsi_period, adjust=False
    ).mean()
    avg_loss = loss.ewm(
        alpha=1 / cfg.rsi_period, min_periods=cfg.rsi_period, adjust=False
    ).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["rsi"] = 100 - (100 / (1 + rs))

    # MACD
    ema_f = df["close"].ewm(span=cfg.macd_fast, adjust=False).mean()
    ema_s = df["close"].ewm(span=cfg.macd_slow, adjust=False).mean()
    df["macd"] = ema_f - ema_s
    df["macd_signal"] = df["macd"].ewm(span=cfg.macd_signal, adjust=False).mean()
    df["macd_hist"] = df["macd"] - df["macd_signal"]

    # ATR
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift(1)).abs()
    low_close = (df["low"] - df["close"].shift(1)).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df["atr"] = tr.ewm(span=cfg.atr_period, adjust=False).mean()

    # Volume
    df["vol_avg"] = df["volume"].rolling(window=cfg.vol_avg_period).mean()
    df["vol_ratio"] = df["volume"] / df["vol_avg"].replace(0, np.nan)

    # ADX
    plus_dm = df["high"].diff()
    minus_dm = -df["low"].diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)
    atr_smooth = tr.ewm(span=14, adjust=False).mean()
    plus_di = 100 * (
        plus_dm.ewm(span=14, adjust=False).mean() / atr_smooth.replace(0, np.nan)
    )
    minus_di = 100 * (
        minus_dm.ewm(span=14, adjust=False).mean() / atr_smooth.replace(0, np.nan)
    )
    dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan))
    df["adx"] = dx.ewm(span=14, adjust=False).mean()

    # Candle info
    df["body"] = df["close"] - df["open"]
    df["is_green"] = df["close"] > df["open"]
    df["candle_range"] = df["high"] - df["low"]
    df["close_position"] = (df["close"] - df["low"]) / df["candle_range"].replace(
        0, np.nan
    )

    # VWAP (Intraday)
    df["vwap"] = (
        (df["high"] + df["low"] + df["close"]) / 3 * df["volume"]
    ).cumsum() / df["volume"].cumsum()

    # Trend strength
    df["trend_strength"] = np.where(df["ema_fast"] > df["ema_mid"], 1, -1)
    df["trend_strength"] *= np.where(df["adx"] > 20, 1, 0.5)

    return df


def score_signal(row, prev, cfg: StrategyConfig) -> Dict[str, int]:
    """
    Score long and short signals
    Returns dict with 'long_score' and 'short_score'
    """
    long_score = 0
    short_score = 0

    # 1. TREND: EMA alignment
    if row["close"] > row["ema_mid"] > row["ema_slow"]:
        long_score += 1
    if row["close"] < row["ema_mid"] < row["ema_slow"]:
        short_score += 1

    # 2. MOMENTUM: MACD histogram
    if row["macd_hist"] > 0 and row["macd_hist"] > prev["macd_hist"]:
        long_score += 1
    if row["macd_hist"] < 0 and row["macd_hist"] < prev["macd_hist"]:
        short_score += 1

    # 3. CONFIRMATION: Previous candle
    if prev["is_green"] and prev["close_position"] > 0.5:
        long_score += 1
    if not prev["is_green"] and prev["close_position"] < 0.5:
        short_score += 1

    # 4. RSI SWEET SPOT
    if cfg.rsi_oversold <= row["rsi"] <= 60:
        long_score += 1
    if 40 <= row["rsi"] <= cfg.rsi_overbought:
        short_score += 1

    # 5. VOLUME
    if pd.notna(row["vol_ratio"]) and row["vol_ratio"] >= cfg.vol_surge:
        long_score += 1
        short_score += 1

    # 6. VWAP confirmation
    if row["close"] > row["vwap"]:
        long_score += 1
    if row["close"] < row["vwap"]:
        short_score += 1

    return {"long": long_score, "short": short_score}


def generate_signals(df: pd.DataFrame, cfg: StrategyConfig) -> pd.DataFrame:
    """Generate trading signals"""
    df = df.copy()
    df["signal"] = 0
    df["score"] = 0

    for i in range(2, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i - 1]

        # Skip if missing data
        if pd.isna(row["ema_slow"]) or pd.isna(row["rsi"]) or pd.isna(row["atr"]):
            continue

        # Price and volume filters
        if row["close"] < cfg.min_price:
            continue
        if pd.notna(row["vol_avg"]) and row["vol_avg"] < cfg.min_avg_volume:
            continue

        scores = score_signal(row, prev, cfg)

        # Long signal
        if scores["long"] >= cfg.min_score:
            df.iat[i, df.columns.get_loc("signal")] = 1
            df.iat[i, df.columns.get_loc("score")] = scores["long"]

        # Short signal
        elif scores["short"] >= cfg.min_score:
            df.iat[i, df.columns.get_loc("signal")] = -1
            df.iat[i, df.columns.get_loc("score")] = scores["short"]

    return df


@dataclass
class Trade:
    symbol: str
    direction: int
    entry_date: pd.Timestamp
    entry_price: float
    stop_loss: float
    take_profit: float
    position_size: int
    position_value: float
    atr_at_entry: float
    score: int = 0
    exit_date: Optional[pd.Timestamp] = None
    exit_price: Optional[float] = None
    exit_reason: str = ""
    pnl: float = 0.0
    pnl_pct: float = 0.0
    hold_days: int = 0


def run_backtest(df: pd.DataFrame, symbol: str, cfg: StrategyConfig) -> List[Trade]:
    """Run backtest with improved risk management"""
    df = compute_indicators(df, cfg)
    df = generate_signals(df, cfg)

    trades = []
    in_trade = False
    current_trade = None

    for i in range(len(df)):
        row = df.iloc[i]

        # Exit existing trade
        if in_trade and current_trade is not None:
            current_trade.hold_days += 1
            d = current_trade.direction
            ep = current_trade.entry_price
            sl = current_trade.stop_loss
            tp = current_trade.take_profit

            if d == 1:  # Long
                if row["low"] <= sl:
                    current_trade.exit_date = row["date"]
                    current_trade.exit_price = sl
                    current_trade.exit_reason = "SL"
                    current_trade.pnl = (sl - ep) * current_trade.position_size
                    current_trade.pnl_pct = (sl / ep - 1) * 100
                    trades.append(current_trade)
                    in_trade = False
                    continue

                if row["high"] >= tp:
                    current_trade.exit_date = row["date"]
                    current_trade.exit_price = tp
                    current_trade.exit_reason = "TP"
                    current_trade.pnl = (tp - ep) * current_trade.position_size
                    current_trade.pnl_pct = (tp / ep - 1) * 100
                    trades.append(current_trade)
                    in_trade = False
                    continue
            else:  # Short
                if row["high"] >= sl:
                    current_trade.exit_date = row["date"]
                    current_trade.exit_price = sl
                    current_trade.exit_reason = "SL"
                    current_trade.pnl = (ep - sl) * current_trade.position_size
                    current_trade.pnl_pct = (ep / sl - 1) * 100
                    trades.append(current_trade)
                    in_trade = False
                    continue

                if row["low"] <= tp:
                    current_trade.exit_date = row["date"]
                    current_trade.exit_price = tp
                    current_trade.exit_reason = "TP"
                    current_trade.pnl = (ep - tp) * current_trade.position_size
                    current_trade.pnl_pct = (ep / tp - 1) * 100
                    trades.append(current_trade)
                    in_trade = False
                    continue

            # Time exit (end of day)
            if current_trade.hold_days >= 1:
                current_trade.exit_date = row["date"]
                current_trade.exit_price = row["close"]
                current_trade.exit_reason = "EOD"
                current_trade.pnl = (
                    (row["close"] - ep) * current_trade.position_size
                    if d == 1
                    else (ep - row["close"]) * current_trade.position_size
                )
                current_trade.pnl_pct = (
                    (row["close"] / ep - 1) * 100
                    if d == 1
                    else (ep / row["close"] - 1) * 100
                )
                trades.append(current_trade)
                in_trade = False
                continue

        # New entry
        if not in_trade and row["signal"] != 0:
            atr = row["atr"]
            if pd.isna(atr) or atr <= 0:
                continue

            entry_price = row["close"]
            direction = row["signal"]

            # Calculate stop loss based on 2% risk
            risk_amount = cfg.initial_capital * cfg.risk_per_trade
            sl_distance = cfg.sl_atr_mult * atr
            risk_per_share = sl_distance

            position_size = int(risk_amount / risk_per_share)
            if position_size <= 0:
                continue

            if direction == 1:
                sl = entry_price - sl_distance
                tp = entry_price + cfg.tp_atr_mult * atr
            else:
                sl = entry_price + sl_distance
                tp = entry_price - cfg.tp_atr_mult * atr

            current_trade = Trade(
                symbol=symbol,
                direction=direction,
                entry_date=row["date"],
                entry_price=entry_price,
                stop_loss=sl,
                take_profit=tp,
                position_size=position_size,
                position_value=position_size * entry_price,
                atr_at_entry=atr,
                score=int(row["score"]),
            )
            in_trade = True

    # Close open trade at end
    if in_trade and current_trade is not None:
        last = df.iloc[-1]
        current_trade.exit_date = last["date"]
        current_trade.exit_price = last["close"]
        current_trade.exit_reason = "End"
        d = current_trade.direction
        ep = current_trade.entry_price
        current_trade.pnl = (
            (last["close"] - ep) * current_trade.position_size
            if d == 1
            else (ep - last["close"]) * current_trade.position_size
        )
        current_trade.pnl_pct = (
            (last["close"] / ep - 1) * 100 if d == 1 else (ep / last["close"] - 1) * 100
        )
        trades.append(current_trade)

    return trades


def analyze_performance(trades: List[Trade]) -> Dict:
    """Analyze trading performance"""
    if not trades:
        return {"total_trades": 0, "win_rate": 0, "total_pnl": 0}

    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    total_pnl = sum(t.pnl for t in trades)
    win_rate = len(wins) / len(trades) * 100

    avg_win = np.mean([t.pnl for t in wins]) if wins else 0
    avg_loss = np.mean([t.pnl for t in losses]) if losses else 0

    profit_factor = (
        abs(sum(t.pnl for t in wins) / sum(t.pnl for t in losses))
        if losses and sum(t.pnl for t in losses) != 0
        else float("inf")
    )

    return {
        "total_trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": win_rate,
        "total_pnl": total_pnl,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "profit_factor": profit_factor,
        "avg_hold_days": np.mean([t.hold_days for t in trades]),
        "expectancy": total_pnl / len(trades),
    }


def optimize_parameters(df: pd.DataFrame, symbol: str) -> StrategyConfig:
    """Optimize strategy parameters for best performance"""
    best_cfg = StrategyConfig()
    best_score = -999

    cfg_base = StrategyConfig()
    df_ind = compute_indicators(df.copy(), cfg_base)

    param_grid = [
        (1.5, 2.5, 3.0, 4.0),  # sl_atr_mult, tp_atr_mult
        (4, 5),  # min_score
        (1.2, 1.5, 2.0),  # vol_surge
    ]

    for sl_m in [1.5, 2.0, 2.5]:
        for tp_m in [2.5, 3.0, 3.5, 4.0]:
            for min_sc in [4, 5]:
                for vol_s in [1.2, 1.5, 2.0]:
                    cfg = StrategyConfig(
                        sl_atr_mult=sl_m,
                        tp_atr_mult=tp_m,
                        min_score=min_sc,
                        vol_surge=vol_s,
                    )
                    df_sig = generate_signals(df_ind.copy(), cfg)
                    trades = run_backtest_from_signals(df_sig, symbol, cfg)

                    if len(trades) < 3:
                        continue

                    stats = analyze_performance(trades)
                    wr = stats["win_rate"]
                    pf = min(stats["profit_factor"], 10)

                    # Score formula
                    score = wr * 2 + pf * 10
                    if wr >= 70:
                        score += 50
                    if stats["total_pnl"] > 0:
                        score += 30
                    if wr >= 80:
                        score += 50

                    if score > best_score:
                        best_score = score
                        best_cfg = cfg

    return best_cfg


def run_backtest_from_signals(
    df: pd.DataFrame, symbol: str, cfg: StrategyConfig
) -> List[Trade]:
    """Run backtest from pre-computed signals"""
    return run_backtest(df, symbol, cfg)


def print_report(stats: Dict, symbol: str):
    """Print performance report"""
    if stats["total_trades"] == 0:
        print(f"  {symbol}: NO TRADES")
        return

    print(f"\n{'=' * 60}")
    print(f"  {symbol} - PERFORMANCE REPORT")
    print(f"{'=' * 60}")
    print(
        f"  Trades: {stats['total_trades']}  |  W/L: {stats['wins']}/{stats['losses']}"
    )
    wr = stats["win_rate"]
    tag = "[EXCELLENT]" if wr >= 80 else "[GOOD]" if wr >= 70 else "[NEEDS WORK]"
    print(f"  WIN RATE:  {wr:.1f}%  {tag}")
    print(f"  P&L:       {stats['total_pnl']:>12,.2f} EGP")
    print(f"  Avg Win:   {stats['avg_win']:>12,.2f}")
    print(f"  Avg Loss:  {stats['avg_loss']:>12,.2f}")
    print(f"  PF: {stats['profit_factor']:.2f}  |  Hold: {stats['avg_hold_days']:.1f}d")
    print(f"  Expectancy: {stats['expectancy']:,.2f} EGP/trade")
    print(f"{'=' * 60}")


def run_strategy(data_dir: str = "D:/cloud"):
    """Main strategy runner"""
    print("=" * 60)
    print("  EGX PRO V9 - IMPROVED STRATEGY")
    print("  With Real-Time Data Connection")
    print("=" * 60)

    # Find stock files
    stocks = {}
    for f in Path(data_dir).glob("*.csv"):
        if not f.stem.startswith("EGX_"):
            try:
                df = load_stock_data(str(f))
                if len(df) > 50:
                    stocks[f.stem] = df
            except:
                pass

    print(f"\n  Loaded {len(stocks)} stocks")

    all_trades = []

    for symbol in sorted(stocks.keys()):
        print(f"\n  Processing: {symbol}")
        df = stocks[symbol]

        # Optimize and run
        cfg = optimize_parameters(df, symbol)
        trades = run_backtest(df.copy(), symbol, cfg)
        stats = analyze_performance(trades)

        if stats["total_trades"] > 0:
            print_report(stats, symbol)
            all_trades.extend(trades)
        else:
            print(f"  {symbol}: No trades generated")

    # Portfolio summary
    if all_trades:
        print(f"\n\n{'*' * 60}")
        print(f"  PORTFOLIO SUMMARY")
        print(f"{'*' * 60}")
        stats = analyze_performance(all_trades)
        print_report(stats, "PORTFOLIO")

    return stocks, all_trades


if __name__ == "__main__":
    run_strategy()
