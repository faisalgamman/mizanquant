"""
Technical analysis functions extracted from the halal_screener monolith.

Provides indicators (EMA, RSI, MACD, ATR), risk metrics,
data preparation utilities, and the swing-trading scoring function.
"""

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Indicators
# ---------------------------------------------------------------------------

def ema(s, n):
    """Exponential moving average.

    Args:
        s: pandas Series of prices.
        n: span (number of periods).

    Returns:
        pandas Series with the EMA values.
    """
    return s.ewm(span=n, adjust=False).mean()


def rsi(s, n=14):
    """Relative Strength Index.

    Args:
        s: pandas Series of prices.
        n: lookback period (default 14).

    Returns:
        pandas Series with RSI values in [0, 100].
    """
    d = s.diff()
    g = d.clip(lower=0).ewm(alpha=1 / n, min_periods=n).mean()
    l = (-d.clip(upper=0)).ewm(alpha=1 / n, min_periods=n).mean()
    return 100 - (100 / (1 + g / l))


def macd(s):
    """MACD indicator (12/26/9).

    Args:
        s: pandas Series of prices.

    Returns:
        Tuple of (macd_line, signal_line, histogram) as pandas Series.
    """
    m = ema(s, 12) - ema(s, 26)
    return m, ema(m, 9), m - ema(m, 9)


def sma(s, n):
    """Simple Moving Average.

    Args:
        s: pandas Series of prices.
        n: window size (number of periods).

    Returns:
        pandas Series with the SMA values.
    """
    return s.rolling(window=n, min_periods=n).mean()


def atr(df, n=14):
    """Average True Range.

    Args:
        df: DataFrame with columns 'high', 'low', 'close'.
        n: lookback period (default 14).

    Returns:
        pandas Series with the ATR values.
    """
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat(
        [h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1 / n, min_periods=n).mean()


def adx(df, n=14):
    """Average Directional Index (properly smoothed).

    Measures trend strength regardless of direction.
    ADX > 25 = strong trend, ADX < 20 = weak/no trend.

    Args:
        df: DataFrame with columns 'high', 'low', 'close'.
        n: smoothing period (default 14).

    Returns:
        Tuple of (adx_series, plus_di_series, minus_di_series) as pandas Series.
    """
    high, low, close = df["high"], df["low"], df["close"]

    # Directional movement
    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0),
                        index=high.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0),
                         index=high.index)

    # True Range
    tr = pd.concat([high - low, (high - close.shift()).abs(),
                    (low - close.shift()).abs()], axis=1).max(axis=1)

    # Wilder's smoothing (EMA with alpha=1/n)
    atr_smooth = tr.ewm(alpha=1/n, min_periods=n).mean()
    plus_dm_smooth = plus_dm.ewm(alpha=1/n, min_periods=n).mean()
    minus_dm_smooth = minus_dm.ewm(alpha=1/n, min_periods=n).mean()

    # Directional indicators
    plus_di = (plus_dm_smooth / atr_smooth) * 100
    minus_di = (minus_dm_smooth / atr_smooth) * 100

    # DX and ADX
    di_sum = plus_di + minus_di
    di_sum = di_sum.replace(0, 1e-9)  # avoid division by zero
    dx = (abs(plus_di - minus_di) / di_sum) * 100
    adx_val = dx.ewm(alpha=1/n, min_periods=n).mean()

    return adx_val, plus_di, minus_di


def roc(s, n=10):
    """Rate of Change (percentage).

    Measures the percentage change over n periods.

    Args:
        s: pandas Series of prices.
        n: lookback period (default 10).

    Returns:
        pandas Series with ROC values in percent.
    """
    return (s / s.shift(n) - 1) * 100


def bollinger_bands(s, n=20, k=2):
    """Bollinger Bands.

    Args:
        s: pandas Series of prices.
        n: SMA period (default 20).
        k: number of standard deviations (default 2).

    Returns:
        Tuple of (upper_band, middle_band, lower_band, bandwidth_pct) as pandas Series.
    """
    middle = s.rolling(n).mean()
    std = s.rolling(n).std()
    upper = middle + k * std
    lower = middle - k * std
    bandwidth = ((upper - lower) / middle) * 100
    return upper, middle, lower, bandwidth


def stochastic(df, k_period=14, d_period=3):
    """Stochastic Oscillator (%K and %D).

    Compares closing price to the high-low range over k_period.
    %K > 80 = overbought, %K < 20 = oversold.

    Args:
        df: DataFrame with columns 'high', 'low', 'close'.
        k_period: lookback for %K (default 14).
        d_period: smoothing for %D (default 3).

    Returns:
        Tuple of (percent_k, percent_d) as pandas Series.
    """
    low_min = df["low"].rolling(k_period).min()
    high_max = df["high"].rolling(k_period).max()
    denom = high_max - low_min
    denom = denom.replace(0, 1e-9)
    percent_k = ((df["close"] - low_min) / denom) * 100
    percent_d = percent_k.rolling(d_period).mean()
    return percent_k, percent_d


def obv(df):
    """On-Balance Volume.

    Cumulative volume indicator — adds volume on up days, subtracts on down days.

    Args:
        df: DataFrame with columns 'close' and 'volume'.

    Returns:
        pandas Series with OBV values.
    """
    direction = np.sign(df["close"].diff())
    return (df["volume"] * direction).cumsum()


def vwap(df):
    """Volume Weighted Average Price (intraday approximation).

    Args:
        df: DataFrame with columns 'high', 'low', 'close', 'volume'.

    Returns:
        pandas Series with VWAP values.
    """
    typical_price = (df["high"] + df["low"] + df["close"]) / 3
    cum_tp_vol = (typical_price * df["volume"]).cumsum()
    cum_vol = df["volume"].cumsum()
    return cum_tp_vol / cum_vol


# ---------------------------------------------------------------------------
# Risk / performance metrics
# ---------------------------------------------------------------------------

def calc_metrics(returns):
    """Calculate a suite of risk-adjusted performance metrics.

    Args:
        returns: array-like of simple period returns.

    Returns:
        Dict with Sharpe Ratio, Sortino Ratio, Max Drawdown %,
        Calmar Ratio, VaR 95%, CVaR 95%, Win Rate %, Profit Factor.
        Empty dict when *returns* is empty.
    """
    returns = np.array(returns)
    if len(returns) == 0:
        return {}
    mean_r = np.mean(returns)
    std_r = np.std(returns) + 1e-9
    sharpe = float(mean_r / std_r * np.sqrt(252))
    downside = returns[returns < 0]
    sortino_denom = np.std(downside) + 1e-9 if len(downside) > 0 else 1e-9
    sortino = float(mean_r / sortino_denom * np.sqrt(252))
    cumulative = np.cumprod(1 + returns)
    peak = np.maximum.accumulate(cumulative)
    drawdowns = (cumulative - peak) / peak
    max_dd = float(np.min(drawdowns))
    calmar = float(mean_r * 252 / abs(max_dd)) if max_dd != 0 else 0
    sorted_r = np.sort(returns)
    var_95 = float(-np.percentile(sorted_r, 5))
    tail = sorted_r[sorted_r <= -var_95]
    cvar_95 = float(-np.mean(tail)) if len(tail) > 0 else var_95
    wins = returns[returns > 0]
    losses = returns[returns < 0]
    win_rate = float(len(wins) / len(returns) * 100) if len(returns) > 0 else 0
    profit_factor = (
        float(np.sum(wins) / abs(np.sum(losses)))
        if len(losses) > 0 and np.sum(losses) != 0
        else 0
    )
    return {
        "Sharpe Ratio": round(sharpe, 3),
        "Sortino Ratio": round(sortino, 3),
        "Max Drawdown %": round(max_dd * 100, 2),
        "Calmar Ratio": round(calmar, 3),
        "VaR 95%": round(var_95 * 100, 2),
        "CVaR 95%": round(cvar_95 * 100, 2),
        "Win Rate %": round(win_rate, 1),
        "Profit Factor": round(profit_factor, 3),
    }


# ---------------------------------------------------------------------------
# Data preparation utilities
# ---------------------------------------------------------------------------

def safe_scale(train, test):
    """Z-score normalisation fitted on *train*, applied to both splits.

    Args:
        train: numpy array of training values.
        test: numpy array of test values.

    Returns:
        Tuple of (scaled_train, scaled_test, mean, std).
    """
    mean = np.mean(train)
    std = np.std(train) + 1e-9
    return (train - mean) / std, (test - mean) / std, mean, std


def walk_forward_split(X, y, n_splits=3, test_size=0.2):
    """Walk-forward cross-validation splits.

    Produces chronologically ordered train/test folds so that each test
    window sits immediately after its training window.

    Args:
        X: feature array.
        y: target array.
        n_splits: number of folds (default 3).
        test_size: fraction of data used per test fold (default 0.2).

    Returns:
        List of tuples (X_train, y_train, X_test, y_test) ordered
        from earliest to latest.
    """
    n = len(X)
    folds = []
    fold_size = int(n * test_size)
    for i in range(n_splits):
        test_end = n - i * fold_size
        test_start = test_end - fold_size
        if test_start <= 0:
            break
        folds.append(
            (X[:test_start], y[:test_start], X[test_start:test_end], y[test_start:test_end])
        )
    return folds[::-1]


def prepare_sequences(prices, seq_len, horizon, use_safe_scale=True):
    """Prepare ML sequences from a 1-D price array.

    Builds overlapping (seq_len -> horizon) windows, splits 80/20
    chronologically, and optionally z-score scales using training stats.

    Args:
        prices: 1-D numpy array of prices.
        seq_len: number of past time-steps per sample.
        horizon: number of future time-steps to predict.
        use_safe_scale: whether to z-score normalise (default True).

    Returns:
        Tuple of (X_train, y_train, X_test, y_test, mean, std).
        When *use_safe_scale* is False, mean=0 and std=1.
    """
    X, y = [], []
    for i in range(len(prices) - seq_len - horizon):
        X.append(prices[i : i + seq_len].reshape(-1, 1))
        y.append(prices[i + seq_len : i + seq_len + horizon])
    X = np.array(X)
    y = np.array(y)
    split = int(len(X) * 0.8)
    X_train, X_test = X[:split], X[split:]
    y_train, y_test = y[:split], y[split:]
    if use_safe_scale:
        mean_p = X_train.mean()
        std_p = X_train.std() + 1e-9
        X_train = (X_train - mean_p) / std_p
        X_test = (X_test - mean_p) / std_p
        y_train_s = (y_train - mean_p) / std_p
        return X_train, y_train_s, X_test, y_test, mean_p, std_p
    return X_train, y_train, X_test, y_test, 0, 1


# ---------------------------------------------------------------------------
# Swing-trading score
# ---------------------------------------------------------------------------

def get_score(row):
    """Calculate the composite swing-trading score for a row of indicators.

    Evaluates trend (EMA), momentum (RSI, MACD histogram), volume,
    volatility (ATR), and proximity to support.

    Args:
        row: dict-like with keys 'close', 'ema21', 'rsi', 'rsi_prev',
             'hist', 'hist_prev', 'vol_ratio', 'atr_v', 'support'.

    Returns:
        Integer score (0-100).
    """
    s = 0
    if row["close"] > row["ema21"]:
        s += 25
    if 40 <= row["rsi"] <= 60 and row["rsi"] > row["rsi_prev"]:
        s += 20
    elif 35 <= row["rsi"] <= 65 and row["rsi"] > row["rsi_prev"]:
        s += 10
    if row["hist"] > 0 and row["hist_prev"] <= 0:
        s += 20
    elif row["hist"] > 0 and row["hist"] > row["hist_prev"]:
        s += 10
    if row["vol_ratio"] >= 1.5:
        s += 15
    elif row["vol_ratio"] >= 1.2:
        s += 8
    if 1.0 <= (row["atr_v"] / row["close"] * 100) <= 4.0:
        s += 10
    if ((row["close"] - row["support"]) / row["support"] * 100) <= 2.0:
        s += 10
    return s
