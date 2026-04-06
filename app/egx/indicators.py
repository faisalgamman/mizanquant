"""EGX technical indicators — self-contained, no imports from US stock code.

Implements the full EGX Pro V9 indicator suite:
- EMA (9, 21, 50)
- RSI (14)
- MACD (12, 26, 9)
- ATR (14)
- ADX (14)
- VWAP
- Candle analysis
- Bollinger Bands
- Stochastic RSI
- OBV (On-Balance Volume)
"""

import numpy as np
import pandas as pd


def compute_indicators(df: pd.DataFrame, cfg) -> pd.DataFrame:
    """Calculate all technical indicators for EGX data.

    Args:
        df: DataFrame with columns [date, open, high, low, close, volume]
        cfg: EgxStrategyConfig with indicator parameters

    Returns:
        DataFrame with all indicator columns added.
    """
    df = df.copy()

    # --- EMAs ---
    df["ema_fast"] = df["close"].ewm(span=cfg.ema_fast, adjust=False).mean()
    df["ema_mid"] = df["close"].ewm(span=cfg.ema_mid, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=cfg.ema_slow, adjust=False).mean()

    # --- RSI ---
    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / cfg.rsi_period, min_periods=cfg.rsi_period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / cfg.rsi_period, min_periods=cfg.rsi_period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["rsi"] = 100 - (100 / (1 + rs))

    # --- MACD ---
    ema_f = df["close"].ewm(span=cfg.macd_fast, adjust=False).mean()
    ema_s = df["close"].ewm(span=cfg.macd_slow, adjust=False).mean()
    df["macd"] = ema_f - ema_s
    df["macd_signal"] = df["macd"].ewm(span=cfg.macd_signal, adjust=False).mean()
    df["macd_hist"] = df["macd"] - df["macd_signal"]

    # --- ATR ---
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift(1)).abs()
    low_close = (df["low"] - df["close"].shift(1)).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df["atr"] = tr.ewm(span=cfg.atr_period, adjust=False).mean()

    # --- Volume ---
    df["vol_avg"] = df["volume"].rolling(window=cfg.vol_avg_period).mean()
    df["vol_ratio"] = df["volume"] / df["vol_avg"].replace(0, np.nan)

    # --- ADX ---
    plus_dm = df["high"].diff()
    minus_dm = -df["low"].diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)
    atr_smooth = tr.ewm(span=14, adjust=False).mean()
    plus_di = 100 * (plus_dm.ewm(span=14, adjust=False).mean() / atr_smooth.replace(0, np.nan))
    minus_di = 100 * (minus_dm.ewm(span=14, adjust=False).mean() / atr_smooth.replace(0, np.nan))
    dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan))
    df["adx"] = dx.ewm(span=14, adjust=False).mean()
    df["plus_di"] = plus_di
    df["minus_di"] = minus_di

    # --- Candle analysis ---
    df["body"] = df["close"] - df["open"]
    df["is_green"] = df["close"] > df["open"]
    df["candle_range"] = df["high"] - df["low"]
    df["close_position"] = (df["close"] - df["low"]) / df["candle_range"].replace(0, np.nan)

    # --- VWAP ---
    typical_price = (df["high"] + df["low"] + df["close"]) / 3
    df["vwap"] = (typical_price * df["volume"]).cumsum() / df["volume"].cumsum()

    # --- Bollinger Bands ---
    sma20 = df["close"].rolling(20).mean()
    std20 = df["close"].rolling(20).std()
    df["bb_upper"] = sma20 + 2 * std20
    df["bb_lower"] = sma20 - 2 * std20
    df["bb_mid"] = sma20
    df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / sma20.replace(0, np.nan)
    df["bb_pct"] = (df["close"] - df["bb_lower"]) / (df["bb_upper"] - df["bb_lower"]).replace(0, np.nan)

    # --- Stochastic RSI ---
    rsi_min = df["rsi"].rolling(14).min()
    rsi_max = df["rsi"].rolling(14).max()
    df["stoch_rsi"] = (df["rsi"] - rsi_min) / (rsi_max - rsi_min).replace(0, np.nan)
    df["stoch_rsi_k"] = df["stoch_rsi"].rolling(3).mean()
    df["stoch_rsi_d"] = df["stoch_rsi_k"].rolling(3).mean()

    # --- OBV (On-Balance Volume) ---
    obv = [0]
    for i in range(1, len(df)):
        if df["close"].iloc[i] > df["close"].iloc[i - 1]:
            obv.append(obv[-1] + df["volume"].iloc[i])
        elif df["close"].iloc[i] < df["close"].iloc[i - 1]:
            obv.append(obv[-1] - df["volume"].iloc[i])
        else:
            obv.append(obv[-1])
    df["obv"] = obv
    df["obv_ema"] = pd.Series(obv).ewm(span=20, adjust=False).mean().values

    # --- Trend strength ---
    df["trend_strength"] = np.where(df["ema_fast"] > df["ema_mid"], 1, -1)
    df["trend_strength"] *= np.where(df["adx"] > 20, 1, 0.5)

    # --- SMA 50 / 200 ---
    df["sma_50"] = df["close"].rolling(50).mean()
    df["sma_200"] = df["close"].rolling(200).mean()

    return df
