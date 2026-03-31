import time, logging, pandas as pd, yfinance as yf, numpy as np, sys
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

sys.path.insert(0, "D:/Stock-Prediction-Models-master/openbb-forecast")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("screener")
app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

HALAL_STOCKS = [
    "AAPL","MSFT","NVDA","GOOGL","AMZN","META","TSLA","AMD","INTC",
    "CSCO","ORCL","IBM","QCOM","TXN","AVGO","CRM","ADBE","NOW","SNOW",
    "PLTR","NET","DDOG","ZS","CRWD","PANW","FTNT","CDNS","SNPS",
    "KLAC","LRCX","AMAT","MCHP","MPWR","ENPH","FSLR",
    "JNJ","PFE","ABBV","MRK","LLY","TMO","ABT","MDT","AMGN","GILD",
    "REGN","VRTX","ISRG","BSX","EW","SYK","BDX","IQV","A","WAT",
    "MTD","IDXX","PODD","DXCM","ALGN",
    "XOM","CVX","COP","EOG","SLB","OXY","PSX","VLO","MPC","HAL",
    "DVN","FANG","APA","BKR",
    "WMT","COST","TGT","HD","LOW","NKE","PG","KO","PEP","CL",
    "KMB","GIS","SYY","CMG","EL","CHD","CLX","K","CPB","HRL","MKC",
    "VZ","T","TMUS",
    "GE","CAT","MMM","HON","DE","EMR","ETN","PH","ROK","AME",
    "IEX","XYL","OTIS","CARR","DOV","FTV","ROP",
    "LDOS","SAIC","BAH","LMT","NOC","GD","TDG","HII",
    "NEE","DUK","SO","AEP","EXC","SRE","PPL","XEL","ES","ETR",
    "AMT","PLD","CCI","EQIX","PSA","O","WELL","DLR","AVB","EQR",
    "F","GM","UBER","RIVN",
]

CACHE_TTL = 300
_cache = {}
_cache_ts = {}

def cache_get(k):
    if time.time() - _cache_ts.get(k, 0) < CACHE_TTL:
        return _cache.get(k)
    return None

def cache_set(k, v):
    _cache[k] = v
    _cache_ts[k] = time.time()

def fetch_yf(symbol, period="2y", start=None, end=None):
    try:
        if start and end:
            df = yf.download(symbol, start=start, end=end, auto_adjust=True, progress=False)
        else:
            df = yf.download(symbol, period=period, auto_adjust=True, progress=False)
        df.columns = [c[0].lower() if isinstance(c, tuple) else c.lower() for c in df.columns]
        df = df.reset_index()
        df.columns = [c.lower() for c in df.columns]
        return df if len(df) >= 200 else None
    except Exception as e:
        logger.error(f"{symbol}: {e}")
        return None

def ema(s, n): return s.ewm(span=n, adjust=False).mean()

def rsi(s, n=14):
    d = s.diff()
    g = d.clip(lower=0).ewm(alpha=1/n, min_periods=n).mean()
    l = (-d.clip(upper=0)).ewm(alpha=1/n, min_periods=n).mean()
    return 100 - (100 / (1 + g/l))

def macd(s):
    m = ema(s, 12) - ema(s, 26)
    return m, ema(m, 9), m - ema(m, 9)

def atr(df, n=14):
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/n, min_periods=n).mean()

def calc_metrics(returns):
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
    profit_factor = float(np.sum(wins) / abs(np.sum(losses))) if len(losses) > 0 and np.sum(losses) != 0 else 0
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

def safe_scale(train, test):
    mean = np.mean(train)
    std = np.std(train) + 1e-9
    return (train - mean) / std, (test - mean) / std, mean, std

def walk_forward_split(X, y, n_splits=3, test_size=0.2):
    n = len(X)
    folds = []
    fold_size = int(n * test_size)
    for i in range(n_splits):
        test_end = n - i * fold_size
        test_start = test_end - fold_size
        if test_start <= 0:
            break
        folds.append((X[:test_start], y[:test_start], X[test_start:test_end], y[test_start:test_end]))
    return folds[::-1]

def get_score(row):
    s = 0
    if row["close"] > row["ema21"]: s += 25
    if 40 <= row["rsi"] <= 60 and row["rsi"] > row["rsi_prev"]: s += 20
    elif 35 <= row["rsi"] <= 65 and row["rsi"] > row["rsi_prev"]: s += 10
    if row["hist"] > 0 and row["hist_prev"] <= 0: s += 20
    elif row["hist"] > 0 and row["hist"] > row["hist_prev"]: s += 10
    if row["vol_ratio"] >= 1.5: s += 15
    elif row["vol_ratio"] >= 1.2: s += 8
    if 1.0 <= (row["atr_v"] / row["close"] * 100) <= 4.0: s += 10
    if ((row["close"] - row["support"]) / row["support"] * 100) <= 2.0: s += 10
    return s

def analyze(symbol, df):
    try:
        close = df["close"]
        price = float(close.iloc[-1])
        ema21 = float(ema(close, 21).iloc[-1])
        sma50 = float(close.tail(50).mean())
        sma200 = float(close.tail(200).mean())
        rsi_s = rsi(close)
        rsi_val = float(rsi_s.iloc[-1])
        rsi_prv = float(rsi_s.iloc[-2])
        _, _, hist_s = macd(close)
        hist_now = float(hist_s.iloc[-1])
        hist_prev = float(hist_s.iloc[-2])
        atr_val = float(atr(df).iloc[-1])
        avg_vol = float(df["volume"].iloc[-20:].mean())
        vol_rat = float(df["volume"].iloc[-1] / avg_vol) if avg_vol else 0
        support = float(df["low"].iloc[-10:].min())
        score = 0
        signals = []
        if price > ema21: score += 25; signals.append("Trend OK")
        if 40 <= rsi_val <= 60 and rsi_val > rsi_prv: score += 20; signals.append(f"RSI {rsi_val:.1f}")
        elif 35 <= rsi_val <= 65 and rsi_val > rsi_prv: score += 10; signals.append(f"RSI {rsi_val:.1f}")
        if hist_now > 0 and hist_prev <= 0: score += 20; signals.append("MACD crossover")
        elif hist_now > 0 and hist_now > hist_prev: score += 10; signals.append("MACD expanding")
        if vol_rat >= 1.5: score += 15; signals.append(f"Vol {vol_rat:.1f}x")
        elif vol_rat >= 1.2: score += 8; signals.append(f"Vol {vol_rat:.1f}x")
        atr_pct = atr_val / price * 100
        if 1.0 <= atr_pct <= 4.0: score += 10; signals.append(f"ATR {atr_pct:.1f}%")
        dist = (price - support) / support * 100
        if dist <= 2.0: score += 10; signals.append(f"Sup {dist:.1f}%")
        if score >= 75: sig = "STRONG BUY"
        elif score >= 55: sig = "BUY"
        elif score >= 35: sig = "WATCH"
        else: sig = "NO TRADE"
        pos = int((100000 * 0.01) / atr_val) if atr_val > 0 else 0
        chg1w = float(((price - close.iloc[-5]) / close.iloc[-5]) * 100) if len(close) >= 5 else 0
        chg1m = float(((price - close.iloc[-21]) / close.iloc[-21]) * 100) if len(close) >= 21 else 0
        chg3m = float(((price - close.iloc[-63]) / close.iloc[-63]) * 100) if len(close) >= 63 else 0
        return {"symbol": symbol, "price": round(price, 2), "ema21": round(ema21, 2),
                "sma50": round(sma50, 2), "sma200": round(sma200, 2), "rsi": round(rsi_val, 1),
                "rsi_trend": "Rising" if rsi_val > rsi_prv else "Falling",
                "atr_pct": round(atr_pct, 2), "volume_ratio": round(vol_rat, 2),
                "chg_1w": round(chg1w, 2), "chg_1m": round(chg1m, 2), "chg_3m": round(chg3m, 2),
                "swing_score": round(score, 1), "swing_signal": sig, "signals": ", ".join(signals),
                "stop_loss": round(price - atr_val, 2), "take_profit": round(price + (2 * atr_val), 2),
                "position_size": pos, "halal": "Yes"}
    except Exception as e:
        logger.error(f"{symbol}: {e}")
        return None

def run_screener():
    cached = cache_get("all")
    if cached: return cached
    results = []
    for symbol in HALAL_STOCKS:
        df = fetch_yf(symbol)
        if df is None: continue
        r = analyze(symbol, df)
        if r: results.append(r)
    results.sort(key=lambda x: x["swing_score"], reverse=True)
    cache_set("all", results)
    return results

def run_backtest(symbol, start_date, end_date, portfolio, risk_pct, hold_days):
    try:
        df = fetch_yf(symbol, start=start_date, end=end_date)
        if df is None: return [{"Error": f"No data for {symbol}"}]
        df["ema21"] = ema(df["close"], 21)
        df["rsi"] = rsi(df["close"])
        df["rsi_prev"] = df["rsi"].shift(1)
        _, _, h = macd(df["close"])
        df["hist"] = h
        df["hist_prev"] = h.shift(1)
        df["atr_v"] = atr(df)
        df["vol_ratio"] = df["volume"] / df["volume"].rolling(20).mean()
        df["support"] = df["low"].rolling(10).min()
        df = df.dropna().reset_index(drop=True)
        df["score"] = df.apply(get_score, axis=1)
        trades = []
        port = portfolio
        in_trade = False
        entry_idx = entry_price = sl = tp = pos_size = 0
        trade_returns = []
        for i in range(len(df)):
            row = df.iloc[i]
            if in_trade:
                days = i - entry_idx
                if float(row["low"]) <= sl:
                    pnl = (sl - entry_price) * pos_size
                    port += pnl
                    ret = (sl - entry_price) / entry_price
                    trade_returns.append(ret)
                    trades.append({"Entry Date": str(df.iloc[entry_idx]["date"])[:10], "Exit Date": str(row["date"])[:10], "Entry": round(entry_price, 2), "Exit": round(sl, 2), "Result": "Stop Loss", "PnL": round(pnl, 2), "Portfolio": round(port, 2), "Days": days, "Score": int(df.iloc[entry_idx]["score"])})
                    in_trade = False
                elif float(row["high"]) >= tp:
                    pnl = (tp - entry_price) * pos_size
                    port += pnl
                    ret = (tp - entry_price) / entry_price
                    trade_returns.append(ret)
                    trades.append({"Entry Date": str(df.iloc[entry_idx]["date"])[:10], "Exit Date": str(row["date"])[:10], "Entry": round(entry_price, 2), "Exit": round(tp, 2), "Result": "Take Profit", "PnL": round(pnl, 2), "Portfolio": round(port, 2), "Days": days, "Score": int(df.iloc[entry_idx]["score"])})
                    in_trade = False
                elif days >= hold_days:
                    pnl = (float(row["close"]) - entry_price) * pos_size
                    port += pnl
                    ret = (float(row["close"]) - entry_price) / entry_price
                    trade_returns.append(ret)
                    trades.append({"Entry Date": str(df.iloc[entry_idx]["date"])[:10], "Exit Date": str(row["date"])[:10], "Entry": round(entry_price, 2), "Exit": round(float(row["close"]), 2), "Result": "Time Stop", "PnL": round(pnl, 2), "Portfolio": round(port, 2), "Days": days, "Score": int(df.iloc[entry_idx]["score"])})
                    in_trade = False
            if not in_trade and float(df.iloc[i]["score"]) >= 55:
                entry_price = float(df.iloc[i]["close"])
                atr_v = float(df.iloc[i]["atr_v"])
                sl = entry_price - atr_v
                tp = entry_price + (2 * atr_v)
                risk = entry_price - sl
                pos_size = int((port * (risk_pct / 100)) / risk) if risk > 0 else 0
                if pos_size > 0:
                    in_trade = True
                    entry_idx = i
        if not trades: return [{"Symbol": symbol, "Message": "No trades found"}]
        wins = [t for t in trades if t["Result"] == "Take Profit"]
        losses = [t for t in trades if t["Result"] == "Stop Loss"]
        metrics = calc_metrics(trade_returns)
        summary = [{"Symbol": symbol, "Period": f"{start_date} to {end_date}",
                    "Total Trades": len(trades), "Winners": len(wins), "Losers": len(losses),
                    "Win Rate %": round(len(wins) / len(trades) * 100, 1),
                    "Total PnL": round(sum(t["PnL"] for t in trades), 2),
                    "Final Portfolio": round(port, 2),
                    "Return %": round((port - portfolio) / portfolio * 100, 2),
                    "Avg Win": round(sum(t["PnL"] for t in wins) / len(wins), 2) if wins else 0,
                    "Avg Loss": round(sum(t["PnL"] for t in losses) / len(losses), 2) if losses else 0,
                    **metrics}]
        return summary + trades
    except Exception as e:
        return [{"Error": str(e)}]

def run_monte_carlo(symbol, days, simulations):
    try:
        from openbb_forecast.simulation.monte_carlo import MonteCarloSimulator
        df = fetch_yf(symbol)
        if df is None: return [{"Error": f"No data for {symbol}"}]
        prices = np.array(df["close"].values, dtype=np.float64).flatten()
        prices = prices[~np.isnan(prices)]
        mc = MonteCarloSimulator(seed=42)
        result = mc.simulate(prices, n_simulations=simulations, forecast_days=days)
        s = result["summary"]
        summary = [{"Symbol": symbol.upper(), "Current Price": round(float(s["initial_price"]), 2),
                    "Expected Price": round(float(s["expected_terminal_price"]), 2),
                    "Prob Profit %": round(float(s["prob_profit"]) * 100, 1),
                    "Annual Vol %": round(float(s["annualized_volatility"]) * 100, 1),
                    "Annual Drift %": round(float(s["annualized_drift"]) * 100, 1),
                    "VaR 95%": round(float(s["terminal_var_95"]) * 100, 1),
                    "CVaR 95%": round(float(s["terminal_cvar_95"]) * 100, 1),
                    "Simulations": simulations, "Forecast Days": days}]
        day_stats = []
        for d in result["day_stats"][::5]:
            day_stats.append({"Day": d["day"], "Mean Price": round(d["mean_price"], 2),
                              "Median": round(d["median_price"], 2),
                              "P5 Bear": round(d["percentile_5"], 2),
                              "P25": round(d["percentile_25"], 2),
                              "P75": round(d["percentile_75"], 2),
                              "P95 Bull": round(d["percentile_95"], 2),
                              "VaR%": round(d["var_95"] * 100, 2)})
        return summary + day_stats
    except Exception as e:
        return [{"Error": str(e)}]

def prepare_sequences(prices, seq_len, horizon, use_safe_scale=True):
    X, y = [], []
    for i in range(len(prices) - seq_len - horizon):
        X.append(prices[i:i+seq_len].reshape(-1, 1))
        y.append(prices[i+seq_len:i+seq_len+horizon])
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

def run_lstm(symbol, horizon):
    try:
        from openbb_forecast.models.lstm import LSTMForecaster
        df = fetch_yf(symbol)
        if df is None: return [{"Error": f"No data for {symbol}"}]
        prices = np.array(df["close"].values, dtype=np.float64).flatten()
        prices = prices[~np.isnan(prices)]
        SEQ_LEN = 20
        X_train, y_train, X_test, y_test, mean_p, std_p = prepare_sequences(prices, SEQ_LEN, horizon)
        folds = walk_forward_split(X_train, y_train, n_splits=3)
        fold_scores = []
        for X_tr, y_tr, X_val, y_val in folds:
            lstm = LSTMForecaster(hidden_size=64, num_layers=2, epochs=30, learning_rate=0.001, patience=5)
            lstm.fit(X_tr, y_tr)
            preds = lstm.predict(X_val)
            mse = float(np.mean((preds - y_val) ** 2))
            fold_scores.append(round(mse, 4))
        lstm_final = LSTMForecaster(hidden_size=64, num_layers=2, epochs=50, learning_rate=0.001, patience=10)
        lstm_final.fit(X_train, y_train)
        preds = lstm_final.predict(X_test[-1:])
        pred_prices = preds[0] * std_p + mean_p
        last_price = float(prices[-1])
        summary = [{"Symbol": symbol.upper(), "Current Price": round(last_price, 2),
                    "Model": "LSTM + Walk-Forward", "Folds": len(fold_scores),
                    "Avg Fold MSE": round(float(np.mean(fold_scores)), 4),
                    "Forecast Horizon": horizon, "Training Samples": len(X_train)}]
        forecasts = []
        for i, p in enumerate(pred_prices):
            change = ((float(p) - last_price) / last_price) * 100
            forecasts.append({"Day": i+1, "Predicted Price": round(float(p), 2),
                              "Change %": round(change, 2),
                              "Signal": "BUY" if change > 1 else "SELL" if change < -1 else "HOLD"})
        return summary + forecasts
    except Exception as e:
        return [{"Error": str(e)}]

def run_transformer(symbol, horizon):
    try:
        from openbb_forecast.models.transformer import TransformerForecaster
        df = fetch_yf(symbol)
        if df is None: return [{"Error": f"No data for {symbol}"}]
        prices = np.array(df["close"].values, dtype=np.float64).flatten()
        prices = prices[~np.isnan(prices)]
        SEQ_LEN = 20
        X_train, y_train, X_test, y_test, mean_p, std_p = prepare_sequences(prices, SEQ_LEN, horizon)
        folds = walk_forward_split(X_train, y_train, n_splits=3)
        fold_scores = []
        for X_tr, y_tr, X_val, y_val in folds:
            tf = TransformerForecaster(d_model=64, n_heads=4, num_layers=2, epochs=30, patience=5)
            tf.fit(X_tr, y_tr)
            preds = tf.predict(X_val)
            mse = float(np.mean((preds - y_val) ** 2))
            fold_scores.append(round(mse, 4))
        tf_final = TransformerForecaster(d_model=64, n_heads=4, num_layers=2, epochs=50, patience=10)
        tf_final.fit(X_train, y_train)
        preds = tf_final.predict(X_test[-1:])
        pred_prices = preds[0] * std_p + mean_p
        last_price = float(prices[-1])
        summary = [{"Symbol": symbol.upper(), "Current Price": round(last_price, 2),
                    "Model": "Transformer + Walk-Forward", "Folds": len(fold_scores),
                    "Avg Fold MSE": round(float(np.mean(fold_scores)), 4),
                    "Forecast Horizon": horizon}]
        forecasts = []
        for i, p in enumerate(pred_prices):
            change = ((float(p) - last_price) / last_price) * 100
            forecasts.append({"Day": i+1, "Predicted Price": round(float(p), 2),
                              "Change %": round(change, 2),
                              "Signal": "BUY" if change > 1 else "SELL" if change < -1 else "HOLD"})
        return summary + forecasts
    except Exception as e:
        return [{"Error": str(e)}]

def run_ensemble(symbol, horizon):
    try:
        from openbb_forecast.models.ensemble import StackingForecaster
        df = fetch_yf(symbol)
        if df is None: return [{"Error": f"No data for {symbol}"}]
        prices = np.array(df["close"].values, dtype=np.float64).flatten()
        prices = prices[~np.isnan(prices)]
        SEQ_LEN = 20
        X, y = [], []
        mean_p = prices.mean(); std_p = prices.std() + 1e-9
        norm = (prices - mean_p) / std_p
        for i in range(len(norm) - SEQ_LEN - horizon):
            X.append(norm[i:i+SEQ_LEN])
            y.append(norm[i+SEQ_LEN:i+SEQ_LEN+horizon])
        X = np.array(X); y = np.array(y)
        split = int(len(X) * 0.8)
        X_train, y_train = X[:split], y[:split]
        ensemble = StackingForecaster()
        ensemble.fit(X_train, y_train)
        preds = ensemble.predict(X[-1:])
        pred_prices = preds[0] * std_p + mean_p
        last_price = float(prices[-1])
        summary = [{"Symbol": symbol.upper(), "Current Price": round(last_price, 2),
                    "Model": "Stacking Ensemble (XGB+RF+GBR)", "Forecast Horizon": horizon}]
        forecasts = []
        for i, p in enumerate(pred_prices):
            change = ((float(p) - last_price) / last_price) * 100
            forecasts.append({"Day": i+1, "Predicted Price": round(float(p), 2),
                              "Change %": round(change, 2),
                              "Signal": "BUY" if change > 1 else "SELL" if change < -1 else "HOLD"})
        return summary + forecasts
    except Exception as e:
        return [{"Error": str(e)}]

def run_dqn(symbol, episodes):
    try:
        from openbb_forecast.agents.double_dqn import DoubleDQNAgent
        from openbb_forecast.agents.environment import TradingEnvironment
        from openbb_forecast.backtesting.transaction_costs import TransactionCostModel
        from openbb_forecast.risk.manager import RiskManager
        df = fetch_yf(symbol)
        if df is None: return [{"Error": f"No data for {symbol}"}]
        prices = np.array(df["close"].values, dtype=np.float64).flatten()
        prices = prices[~np.isnan(prices)]
        split = int(len(prices) * 0.8)
        train_prices = prices[:split]
        cost_model = TransactionCostModel(commission_bps=10)
        try:
            risk_mgr = RiskManager(max_position_size=0.2, stop_loss_pct=0.05, max_drawdown_pct=0.15)
        except Exception:
            risk_mgr = None
        train_env = TradingEnvironment(prices=train_prices, window_size=30,
                                       initial_capital=10000, cost_model=cost_model,
                                       risk_manager=risk_mgr)
        agent = DoubleDQNAgent(state_size=train_env.state_size, action_size=train_env.action_size)
        rewards = agent.train(train_env, episodes=episodes)
        state = train_env.reset()
        action = agent.select_action(state)
        action_map = {0: "HOLD", 1: "BUY", 2: "SELL"}
        signal = action_map.get(int(action), "HOLD")
        last_price = float(prices[-1])
        metrics = calc_metrics([r / 10000 for r in rewards])
        summary = [{"Symbol": symbol.upper(), "Current Price": round(last_price, 2),
                    "Model": "Double DQN + Risk Manager", "Episodes": episodes,
                    "Signal": signal, "Best Reward": round(float(max(rewards)), 2),
                    "Avg Reward": round(float(np.mean(rewards)), 2),
                    "Final Reward": round(float(rewards[-1]), 2),
                    **metrics}]
        episode_results = [{"Episode": i+1, "Reward": round(float(r), 2),
                            "Status": "Good" if r > 0 else "Bad"} for i, r in enumerate(rewards)]
        return summary + episode_results
    except Exception as e:
        return [{"Error": str(e)}]

def run_policy_gradient(symbol, episodes):
    try:
        from openbb_forecast.agents.policy_gradient import PolicyGradientAgent
        from openbb_forecast.agents.environment import TradingEnvironment
        from openbb_forecast.backtesting.transaction_costs import TransactionCostModel
        df = fetch_yf(symbol)
        if df is None: return [{"Error": f"No data for {symbol}"}]
        prices = np.array(df["close"].values, dtype=np.float64).flatten()
        prices = prices[~np.isnan(prices)]
        split = int(len(prices) * 0.8)
        cost_model = TransactionCostModel(commission_bps=10)
        env = TradingEnvironment(prices=prices[:split], window_size=30,
                                  initial_capital=10000, cost_model=cost_model)
        agent = PolicyGradientAgent(state_size=env.state_size, action_size=env.action_size)
        rewards = agent.train(env, episodes=episodes)
        state = env.reset()
        action = agent.select_action(state)
        action_map = {0: "HOLD", 1: "BUY", 2: "SELL"}
        signal = action_map.get(int(action), "HOLD")
        last_price = float(prices[-1])
        summary = [{"Symbol": symbol.upper(), "Current Price": round(last_price, 2),
                    "Model": "Policy Gradient (REINFORCE)", "Episodes": episodes,
                    "Signal": signal, "Best Reward": round(float(max(rewards)), 2),
                    "Avg Reward": round(float(np.mean(rewards)), 2)}]
        return summary + [{"Episode": i+1, "Reward": round(float(r), 2),
                           "Status": "Good" if r > 0 else "Bad"} for i, r in enumerate(rewards)]
    except Exception as e:
        return [{"Error": str(e)}]


def run_usx_screener(min_score=7, direction="Long Only"):
    try:
        results = []
        for symbol in HALAL_STOCKS:
            df = fetch_yf(symbol)
            if df is None: continue
            try:
                close = df["close"]
                high  = df["high"]
                low   = df["low"]
                vol   = df["volume"]

                # EMAs
                ema9  = float(ema(close, 9).iloc[-2])
                ema21 = float(ema(close, 21).iloc[-2])

                # Daily EMA trend
                daily_ema21 = float(ema(close, 21).iloc[-1])
                daily_bullish = float(close.iloc[-1]) > daily_ema21

                # RSI
                rsi_s   = rsi(close)
                rsi_val = float(rsi_s.iloc[-2])

                # MACD
                _, _, hist_s = macd(close)
                hist_now  = float(hist_s.iloc[-2])
                hist_prev = float(hist_s.iloc[-3])
                macd_rising = hist_now > 0 and hist_now > hist_prev

                # ATR
                atr_val = float(atr(df).iloc[-1])

                # ADX approximation
                high_s  = df["high"]
                low_s   = df["low"]
                tr      = pd.concat([high_s - low_s,
                                     (high_s - close.shift()).abs(),
                                     (low_s  - close.shift()).abs()], axis=1).max(axis=1)
                atr14   = tr.ewm(alpha=1/14, min_periods=14).mean()
                dm_plus  = (high_s.diff()).clip(lower=0)
                dm_minus = (-low_s.diff()).clip(lower=0)
                di_plus  = (dm_plus.ewm(alpha=1/14, min_periods=14).mean() / atr14 * 100).iloc[-2]
                di_minus = (dm_minus.ewm(alpha=1/14, min_periods=14).mean() / atr14 * 100).iloc[-2]
                adx_approx = abs(di_plus - di_minus) / (di_plus + di_minus + 1e-9) * 100
                adx_ok     = adx_approx >= 20 and di_plus > di_minus

                # Bollinger Band Squeeze
                sma20    = close.rolling(20).mean()
                std20    = close.rolling(20).std()
                bb_upper = sma20 + 2 * std20
                bb_lower = sma20 - 2 * std20
                bb_width = ((bb_upper - bb_lower) / sma20 * 100)
                bb_width_sma = bb_width.rolling(20).mean()
                bb_squeeze = float(bb_width.iloc[-2]) < float(bb_width_sma.iloc[-2])

                # Volume
                vol_ma    = vol.rolling(20).mean()
                vol_ratio = float(vol.iloc[-2] / vol_ma.iloc[-2]) if float(vol_ma.iloc[-2]) > 0 else 1.0
                vol_ok    = vol_ratio >= 1.0

                # Gap protection
                gap_pct = float(((close.iloc[-1] - close.iloc[-2]) / close.iloc[-2]) * 100)
                gap_ok  = 0 <= gap_pct <= 1.5

                # VWAP approximation (cumulative)
                hlc3   = (df["high"] + df["low"] + df["close"]) / 3
                vwap   = (hlc3 * vol).cumsum() / vol.cumsum()
                above_vwap = float(close.iloc[-2]) > float(vwap.iloc[-2])

                # Relative Strength (vs SPY approximation using momentum)
                roc5    = float((close.iloc[-1] - close.iloc[-6]) / close.iloc[-6] * 100) if len(close) >= 6 else 0
                rs_ok   = roc5 > 0

                # 10-Point Scoring (Long Only)
                s1  = 1 if above_vwap else 0          # VWAP
                s2  = 1 if daily_bullish else 0        # Daily trend
                s3  = 1 if ema9 > ema21 else 0        # EMA 9>21
                s4  = 1 if macd_rising else 0          # MACD rising
                s5  = 1 if 40 <= rsi_val <= 65 else 0  # RSI zone
                s6  = 1 if adx_ok else 0               # ADX+DI
                s7  = 1 if bb_squeeze else 0           # BB Squeeze
                s8  = 1 if rs_ok else 0                # RS positive
                s9  = 1 if vol_ok else 0               # Volume
                s10 = 1 if gap_ok else 0               # Gap OK

                score = s1+s2+s3+s4+s5+s6+s7+s8+s9+s10

                if score < min_score:
                    continue

                # Signal
                if score >= 9:   signal = "STRONG BUY"
                elif score >= 7: signal = "BUY"
                elif score >= 6: signal = "NEUTRAL"
                else:            signal = "NO TRADE"

                price = float(close.iloc[-1])

                # Trade Plan
                sl_mult  = 1.5
                tp1_mult = 1.5
                tp2_mult = 2.5
                tp3_mult = 4.0

                sl  = round(price - sl_mult  * atr_val, 2)
                tp1 = round(price + tp1_mult * atr_val, 2)
                tp2 = round(price + tp2_mult * atr_val, 2)
                tp3 = round(price + tp3_mult * atr_val, 2)
                be  = round(price + 1.0 * atr_val, 2)

                # Position sizing
                risk_per_share = price - sl
                risk_capital   = 100000 * 0.01
                qty = int(risk_capital / risk_per_share) if risk_per_share > 0 else 0

                # R:R
                rr1 = round(tp1_mult / sl_mult, 2)
                rr2 = round(tp2_mult / sl_mult, 2)
                rr3 = round(tp3_mult / sl_mult, 2)

                # Score breakdown
                breakdown = []
                if s1: breakdown.append("VWAP")
                if s2: breakdown.append("Daily")
                if s3: breakdown.append("EMA")
                if s4: breakdown.append("MACD")
                if s5: breakdown.append(f"RSI {rsi_val:.0f}")
                if s6: breakdown.append("ADX")
                if s7: breakdown.append("Squeeze")
                if s8: breakdown.append("RS+")
                if s9: breakdown.append(f"Vol {vol_ratio:.1f}x")
                if s10: breakdown.append("Gap OK")

                results.append({
                    "Symbol":      symbol,
                    "Price":       round(price, 2),
                    "Score":       f"{score}/10",
                    "Signal":      signal,
                    "RSI":         round(rsi_val, 1),
                    "ADX":         round(adx_approx, 1),
                    "BB Squeeze":  "Yes" if bb_squeeze else "No",
                    "Vol Ratio":   round(vol_ratio, 2),
                    "Gap %":       round(gap_pct, 2),
                    "Stop Loss":   sl,
                    "TP1 (50%)":   tp1,
                    "TP2 (30%)":   tp2,
                    "TP3 (20%)":   tp3,
                    "Breakeven":   be,
                    "Qty":         qty,
                    "RR1":         rr1,
                    "RR2":         rr2,
                    "RR3":         rr3,
                    "Confluence":  ", ".join(breakdown),
                })
            except Exception as e:
                logger.error(f"USX {symbol}: {e}")
                continue

        results.sort(key=lambda x: int(x["Score"].split("/")[0]), reverse=True)
        return results if results else [{"Message": "No stocks meet USX Pro criteria"}]
    except Exception as e:
        return [{"Error": str(e)}]


def run_consensus(symbol, horizon=5, episodes=10):
    try:
        votes_buy  = 0
        votes_sell = 0
        votes_hold = 0
        details    = []

        df = fetch_yf(symbol)
        if df is None:
            return [{"Error": f"No data for {symbol}"}]
        price = float(df["close"].iloc[-1])
        atr_val = float(atr(df).iloc[-1])

        # --- 1. Halal Screener ---
        try:
            r = analyze(symbol, df)
            if r:
                sig = r["swing_signal"]
                score = r["swing_score"]
                if "STRONG BUY" in sig:
                    votes_buy += 2
                    vote = "BUY"
                elif "BUY" in sig:
                    votes_buy += 1
                    vote = "BUY"
                else:
                    votes_hold += 1
                    vote = "HOLD"
                details.append({"Tool": "Halal Screener", "Signal": sig, "Vote": vote, "Score": score})
        except Exception as e:
            details.append({"Tool": "Halal Screener", "Signal": "ERROR", "Vote": "-", "Score": 0})

        # --- 2. USX Pro ---
        try:
            usx = run_usx_screener(min_score=7)
            usx_sym = [x for x in usx if x.get("Symbol") == symbol]
            if usx_sym:
                usx_sig = usx_sym[0]["Signal"]
                usx_score = usx_sym[0]["Score"]
                if "STRONG BUY" in usx_sig:
                    votes_buy += 2
                    vote = "BUY"
                elif "BUY" in usx_sig:
                    votes_buy += 1
                    vote = "BUY"
                else:
                    votes_hold += 1
                    vote = "HOLD"
                details.append({"Tool": "USX Pro", "Signal": usx_sig, "Vote": vote, "Score": usx_score})
            else:
                votes_hold += 1
                details.append({"Tool": "USX Pro", "Signal": "Below Min Score", "Vote": "HOLD", "Score": 0})
        except Exception as e:
            details.append({"Tool": "USX Pro", "Signal": "ERROR", "Vote": "-", "Score": 0})

        # --- 3. Backtest ---
        try:
            import datetime
            end_date = datetime.date.today().strftime("%Y-%m-%d")
            start_date = (datetime.date.today() - datetime.timedelta(days=365*2)).strftime("%Y-%m-%d")
            bt = run_backtest(symbol, start_date, end_date, 100000, 1.0, 3)
            if bt and len(bt) > 0 and "Return %" in bt[0]:
                ret = float(bt[0]["Return %"])
                win_rate = float(bt[0].get("Win Rate %", 0))
                sharpe = float(bt[0].get("Sharpe Ratio", 0))
                if ret > 5 and win_rate > 50 and sharpe > 0.5:
                    votes_buy += 1
                    vote = "BUY"
                    sig = f"Return {ret:.1f}% WR {win_rate:.0f}%"
                elif ret < -5 or (win_rate < 40 and sharpe < 0):
                    votes_sell += 1
                    vote = "SELL"
                    sig = f"Return {ret:.1f}% WR {win_rate:.0f}%"
                else:
                    votes_hold += 1
                    vote = "HOLD"
                    sig = f"Return {ret:.1f}% WR {win_rate:.0f}%"
                details.append({"Tool": "Backtest 2Y", "Signal": sig, "Vote": vote, "Score": round(ret, 1)})
            else:
                votes_hold += 1
                details.append({"Tool": "Backtest 2Y", "Signal": "No trades", "Vote": "HOLD", "Score": 0})
        except Exception as e:
            details.append({"Tool": "Backtest 2Y", "Signal": "ERROR", "Vote": "-", "Score": 0})

        # --- 4. Monte Carlo ---
        try:
            from openbb_forecast.simulation.monte_carlo import MonteCarloSimulator
            prices = np.array(df["close"].values, dtype=np.float64).flatten()
            prices = prices[~np.isnan(prices)]
            mc = MonteCarloSimulator(seed=42)
            result = mc.simulate(prices, n_simulations=500, forecast_days=horizon)
            prob = float(result["summary"]["prob_profit"])
            exp_price = float(result["summary"]["expected_terminal_price"])
            chg = (exp_price - price) / price * 100
            if prob >= 0.60:
                votes_buy += 1
                vote = "BUY"
            elif prob <= 0.45:
                votes_sell += 1
                vote = "SELL"
            else:
                votes_hold += 1
                vote = "HOLD"
            details.append({"Tool": "Monte Carlo", "Signal": f"Prob {prob*100:.1f}% Exp {chg:+.1f}%", "Vote": vote, "Score": round(prob*100, 1)})
        except Exception as e:
            details.append({"Tool": "Monte Carlo", "Signal": "ERROR", "Vote": "-", "Score": 0})

        # --- 5. LSTM ---
        try:
            from openbb_forecast.models.lstm import LSTMForecaster
            prices2 = np.array(df["close"].values, dtype=np.float64).flatten()
            prices2 = prices2[~np.isnan(prices2)]
            mean_p = prices2.mean(); std_p = prices2.std() + 1e-9
            norm = (prices2 - mean_p) / std_p
            SEQ = 20
            X, y = [], []
            for i in range(len(norm)-SEQ-horizon):
                X.append(norm[i:i+SEQ].reshape(-1,1))
                y.append(norm[i+SEQ:i+SEQ+horizon])
            X = np.array(X); y = np.array(y)
            split = int(len(X)*0.8)
            lstm = LSTMForecaster(hidden_size=64, num_layers=2, epochs=30, patience=5)
            lstm.fit(X[:split], y[:split])
            preds = lstm.predict(X[-1:])
            pred5 = float(preds[0][-1]) * std_p + mean_p
            chg = (pred5 - price) / price * 100
            if chg > 1:
                votes_buy += 1
                vote = "BUY"
            elif chg < -1:
                votes_sell += 1
                vote = "SELL"
            else:
                votes_hold += 1
                vote = "HOLD"
            details.append({"Tool": "LSTM", "Signal": f"{chg:+.1f}%", "Vote": vote, "Score": round(chg, 2)})
        except Exception as e:
            details.append({"Tool": "LSTM", "Signal": "ERROR", "Vote": "-", "Score": 0})

        # --- 6. Transformer ---
        try:
            from openbb_forecast.models.transformer import TransformerForecaster
            prices3 = np.array(df["close"].values, dtype=np.float64).flatten()
            prices3 = prices3[~np.isnan(prices3)]
            mean_p = prices3.mean(); std_p = prices3.std() + 1e-9
            norm = (prices3 - mean_p) / std_p
            SEQ = 20
            X, y = [], []
            for i in range(len(norm)-SEQ-horizon):
                X.append(norm[i:i+SEQ].reshape(-1,1))
                y.append(norm[i+SEQ:i+SEQ+horizon])
            X = np.array(X); y = np.array(y)
            split = int(len(X)*0.8)
            tf = TransformerForecaster(d_model=64, n_heads=4, num_layers=2, epochs=30, patience=5)
            tf.fit(X[:split], y[:split])
            preds = tf.predict(X[-1:])
            pred5 = float(preds[0][-1]) * std_p + mean_p
            chg = (pred5 - price) / price * 100
            if chg > 1:
                votes_buy += 1
                vote = "BUY"
            elif chg < -1:
                votes_sell += 1
                vote = "SELL"
            else:
                votes_hold += 1
                vote = "HOLD"
            details.append({"Tool": "Transformer", "Signal": f"{chg:+.1f}%", "Vote": vote, "Score": round(chg, 2)})
        except Exception as e:
            details.append({"Tool": "Transformer", "Signal": "ERROR", "Vote": "-", "Score": 0})

        # --- 7. Stacking Ensemble ---
        try:
            from openbb_forecast.models.ensemble import StackingForecaster
            prices5 = np.array(df["close"].values, dtype=np.float64).flatten()
            prices5 = prices5[~np.isnan(prices5)]
            mean_p = prices5.mean(); std_p = prices5.std() + 1e-9
            norm = (prices5 - mean_p) / std_p
            SEQ = 20
            X, y = [], []
            for i in range(len(norm)-SEQ-horizon):
                X.append(norm[i:i+SEQ])
                y.append(norm[i+SEQ:i+SEQ+horizon])
            X = np.array(X); y = np.array(y)
            split = int(len(X)*0.8)
            ens = StackingForecaster()
            ens.fit(X[:split], y[:split])
            preds = ens.predict(X[-1:])
            pred5 = float(preds[0][-1]) * std_p + mean_p
            chg = (pred5 - price) / price * 100
            if chg > 1:
                votes_buy += 1
                vote = "BUY"
            elif chg < -1:
                votes_sell += 1
                vote = "SELL"
            else:
                votes_hold += 1
                vote = "HOLD"
            details.append({"Tool": "Ensemble", "Signal": f"{chg:+.1f}%", "Vote": vote, "Score": round(chg, 2)})
        except Exception as e:
            details.append({"Tool": "Ensemble", "Signal": "ERROR", "Vote": "-", "Score": 0})

        # --- 8. Double DQN ---
        try:
            from openbb_forecast.agents.double_dqn import DoubleDQNAgent
            from openbb_forecast.agents.environment import TradingEnvironment
            from openbb_forecast.backtesting.transaction_costs import TransactionCostModel
            prices4 = np.array(df["close"].values, dtype=np.float64).flatten()
            prices4 = prices4[~np.isnan(prices4)]
            split = int(len(prices4)*0.8)
            cost_model = TransactionCostModel(commission_bps=10)
            env = TradingEnvironment(prices=prices4[:split], window_size=30, initial_capital=10000, cost_model=cost_model)
            agent = DoubleDQNAgent(state_size=env.state_size, action_size=env.action_size)
            agent.train(env, episodes=episodes)
            state = env.reset()
            action = agent.select_action(state)
            action_map = {0: "HOLD", 1: "BUY", 2: "SELL"}
            dqn_sig = action_map.get(int(action), "HOLD")
            if dqn_sig == "BUY": votes_buy += 1
            elif dqn_sig == "SELL": votes_sell += 1
            else: votes_hold += 1
            details.append({"Tool": "Double DQN", "Signal": dqn_sig, "Vote": dqn_sig, "Score": 0})
        except Exception as e:
            details.append({"Tool": "Double DQN", "Signal": "ERROR", "Vote": "-", "Score": 0})

        # --- 9. Policy Gradient ---
        try:
            from openbb_forecast.agents.policy_gradient import PolicyGradientAgent
            from openbb_forecast.agents.environment import TradingEnvironment
            from openbb_forecast.backtesting.transaction_costs import TransactionCostModel
            prices6 = np.array(df["close"].values, dtype=np.float64).flatten()
            prices6 = prices6[~np.isnan(prices6)]
            split = int(len(prices6)*0.8)
            cost_model2 = TransactionCostModel(commission_bps=10)
            env2 = TradingEnvironment(prices=prices6[:split], window_size=30, initial_capital=10000, cost_model=cost_model2)
            agent2 = PolicyGradientAgent(state_size=env2.state_size, action_size=env2.action_size)
            agent2.train(env2, episodes=episodes)
            state2 = env2.reset()
            action2 = agent2.select_action(state2)
            action_map2 = {0: "HOLD", 1: "BUY", 2: "SELL"}
            pg_sig = action_map2.get(int(action2), "HOLD")
            if pg_sig == "BUY": votes_buy += 1
            elif pg_sig == "SELL": votes_sell += 1
            else: votes_hold += 1
            details.append({"Tool": "Policy Gradient", "Signal": pg_sig, "Vote": pg_sig, "Score": 0})
        except Exception as e:
            details.append({"Tool": "Policy Gradient", "Signal": "ERROR", "Vote": "-", "Score": 0})

        # --- Final Verdict ---
        total = votes_buy + votes_sell + votes_hold
        if total == 0: total = 1
        confidence = round(max(votes_buy, votes_sell) / total * 100, 1)

        if votes_buy >= 6:
            verdict = "STRONG BUY"
            action_str = "✅ STRONG ENTER"
        elif votes_buy > votes_sell and confidence >= 60:
            verdict = "BUY"
            action_str = "✅ ENTER"
        elif votes_buy > votes_sell and confidence >= 40:
            verdict = "WEAK BUY"
            action_str = "⚠️ WEAK SIGNAL"
        elif votes_sell >= 6:
            verdict = "STRONG SELL"
            action_str = "❌ STRONG AVOID"
        elif votes_sell > votes_buy and confidence >= 60:
            verdict = "SELL"
            action_str = "❌ AVOID"
        elif votes_sell > votes_buy:
            verdict = "WEAK SELL"
            action_str = "⚠️ WEAK SELL"
        else:
            verdict = "NEUTRAL"
            action_str = "⏳ WAIT"

        sl  = round(price - 1.5 * atr_val, 2)
        tp1 = round(price + 1.5 * atr_val, 2)
        tp2 = round(price + 2.5 * atr_val, 2)
        tp3 = round(price + 4.0 * atr_val, 2)

        summary = [{
            "Symbol":       symbol.upper(),
            "Price":        round(price, 2),
            "Verdict":      verdict,
            "Confidence %": confidence,
            "Votes BUY":    votes_buy,
            "Votes SELL":   votes_sell,
            "Votes HOLD":   votes_hold,
            "Total Tools":  len(details),
            "Action":       action_str,
            "Stop Loss":    sl,
            "TP1":          tp1,
            "TP2":          tp2,
            "TP3":          tp3,
        }]

        return summary + details

    except Exception as e:
        return [{"Error": str(e)}]


def run_batch_consensus(min_swing_score=55, horizon=5, episodes=5, max_stocks=10):
    try:
        # الخطوة 1: جلب Active Buy Signals
        screener_results = run_screener()
        buy_signals = [r for r in screener_results if r.get("swing_score", 0) >= min_swing_score]
        
        if not buy_signals:
            return [{"Message": "No active buy signals found"}]
        
        # حد أقصى للسرعة
        buy_signals = buy_signals[:max_stocks]
        
        summary_header = [{
            "Info": f"Scanning {len(buy_signals)} stocks from Active Buy Signals",
            "Min Score": min_swing_score,
            "Horizon": f"{horizon} days",
            "Status": "Running..."
        }]
        
        results = []
        for stock in buy_signals:
            symbol = stock["symbol"]
            try:
                consensus = run_consensus(symbol, horizon=horizon, episodes=episodes)
                if consensus and len(consensus) > 0:
                    row = consensus[0].copy()
                    row["Swing Score"] = stock.get("swing_score", 0)
                    row["ATR Pct"] = stock.get("atr_pct", 0)
                    results.append(row)
            except Exception as e:
                results.append({
                    "Symbol": symbol,
                    "Verdict": "ERROR",
                    "Confidence %": 0,
                    "Votes BUY": 0,
                    "Votes SELL": 0,
                    "Votes HOLD": 0,
                    "Action": f"Error: {str(e)[:50]}",
                    "Swing Score": stock.get("swing_score", 0),
                })
        
        # ترتيب حسب الثقة والتصويت
        results.sort(key=lambda x: (
            x.get("Votes BUY", 0) - x.get("Votes SELL", 0),
            x.get("Confidence %", 0)
        ), reverse=True)
        
        return summary_header + results

    except Exception as e:
        return [{"Error": str(e)}]


RUSSELL_1000_HALAL = ['AAPL', 'MSFT', 'NVDA', 'GOOGL', 'AMZN', 'META', 'TSLA', 'AMD', 'INTC', 'CSCO', 'ORCL', 'IBM', 'QCOM', 'TXN', 'AVGO', 'CRM', 'ADBE', 'NOW', 'SNOW', 'PLTR', 'NET', 'DDOG', 'ZS', 'CRWD', 'PANW', 'FTNT', 'CDNS', 'SNPS', 'KLAC', 'LRCX', 'AMAT', 'MCHP', 'MPWR', 'ENPH', 'FSLR', 'ON', 'SWKS', 'WOLF', 'LITE', 'ENTG', 'ACLS', 'ONTO', 'RMBS', 'CRUS', 'SLAB', 'POWI', 'AMBA', 'COHR', 'FORM', 'GIGA', 'HIMX', 'IMOS', 'IPGP', 'ITRN', 'IXYS', 'KLIC', 'LSCC', 'MKSI', 'MRVL', 'MU', 'NXPI', 'OLED', 'PLAB', 'SMTC', 'SYNA', 'TOWR', 'TSEM', 'UCTT', 'VEEA', 'VICR', 'VLAB', 'VSH', 'WFRD', 'XLNX', 'XPLT', 'YELP', 'YEXT', 'ZI', 'HUBS', 'PAYC', 'VEEV', 'WDAY', 'PCTY', 'COUP', 'MDB', 'ESTC', 'DOMO', 'FROG', 'GTLB', 'IOT', 'JAMF', 'KVYO', 'MANH', 'MNDY', 'NCNO', 'SPSC', 'TOST', 'VRNS', 'JNJ', 'PFE', 'ABBV', 'MRK', 'LLY', 'TMO', 'ABT', 'MDT', 'AMGN', 'GILD', 'REGN', 'VRTX', 'ISRG', 'BSX', 'EW', 'SYK', 'BDX', 'IQV', 'A', 'WAT', 'MTD', 'IDXX', 'PODD', 'DXCM', 'ALGN', 'HOLX', 'ILMN', 'INSP', 'IONS', 'JAZZ', 'LGND', 'MASI', 'MMSI', 'NKTR', 'NVCR', 'NVST', 'OMCL', 'PRGO', 'RARE', 'RCUS', 'RGEN', 'RVMD', 'RXRX', 'SAGE', 'SRPT', 'STAA', 'SUPN', 'TGTX', 'THRM', 'TBPH', 'ACAD', 'ACMR', 'AGIO', 'AKBA', 'AKRO', 'ALEC', 'ALKS', 'ALNY', 'ALXO', 'AMRN', 'AMRX', 'ANAB', 'ANGI', 'ANIK', 'ANIP', 'APLS', 'APOG', 'APRE', 'APTX', 'ARCT', 'ARDX', 'ARGX', 'ARQT', 'ARVN', 'ASND', 'ATEX', 'ATRC', 'ATRI', 'AVAL', 'AVEO', 'AVRO', 'AXNX', 'AXSM', 'AYTU', 'AZTA', 'BCAB', 'BCDA', 'BCRX', 'BDSI', 'BEAM', 'BHVN', 'BIOL', 'BIOP', 'BLUE', 'BMRN', 'BPMC', 'BRMK', 'BTAI', 'BYSI', 'CABA', 'XOM', 'CVX', 'COP', 'EOG', 'SLB', 'OXY', 'PSX', 'VLO', 'MPC', 'HAL', 'DVN', 'FANG', 'APA', 'BKR', 'NOV', 'HP', 'WHD', 'NE', 'CIVI', 'CPE', 'ESTE', 'GPRE', 'HESM', 'MNRL', 'MTDR', 'PDCE', 'PR', 'REPX', 'SBOW', 'SM', 'SWN', 'TALO', 'VTLE', 'XEC', 'ACDC', 'AMPY', 'ARCH', 'AROC', 'BATL', 'CEIX', 'CLMT', 'CNX', 'CONSOL', 'CTRA', 'DKL', 'DK', 'DMLP', 'DNOW', 'DRQ', 'ENLC', 'EXTN', 'FLNG', 'FTI', 'GLP', 'GPRK', 'GRNT', 'HES', 'HLX', 'HRI', 'HTGC', 'IMPP', 'INT', 'INTL', 'ISNS', 'JMPX', 'KOS', 'LBRT', 'LPI', 'MGLD', 'WMT', 'COST', 'TGT', 'HD', 'LOW', 'NKE', 'PG', 'KO', 'PEP', 'CL', 'KMB', 'GIS', 'SYY', 'CMG', 'EL', 'CHD', 'CLX', 'MKC', 'HRL', 'CPB', 'BURL', 'CASY', 'CBRL', 'CHDN', 'CHEF', 'COKE', 'COLM', 'CROX', 'DG', 'DLTR', 'DPZ', 'EAT', 'EBAY', 'ELF', 'ETSY', 'FIVE', 'FIZZ', 'GFF', 'GIII', 'GPRO', 'HAIN', 'HAS', 'HELE', 'HIBB', 'HRB', 'JACK', 'JOUT', 'LEVI', 'LKQ', 'LULU', 'MAR', 'MCD', 'MDLZ', 'MNST', 'MTN', 'NOMD', 'NUS', 'NYT', 'OLLI', 'ONON', 'ORLY', 'POOL', 'QSR', 'ROST', 'SAM', 'SBUX', 'SHAK', 'SHOP', 'SKX', 'SNA', 'TSCO', 'ULTA', 'USFD', 'WGO', 'WING', 'WSM', 'YUM', 'YUMC', 'ZOES', 'GE', 'CAT', 'MMM', 'HON', 'DE', 'EMR', 'ETN', 'PH', 'ROK', 'AME', 'IEX', 'XYL', 'OTIS', 'CARR', 'DOV', 'FTV', 'ROP', 'LDOS', 'SAIC', 'BAH', 'ESAB', 'FELE', 'GNRC', 'GWW', 'HXL', 'HUBB', 'ITT', 'JBTM', 'KAI', 'KAMN', 'MANT', 'MIDD', 'MOOG', 'MWA', 'NPO', 'NVT', 'OSK', 'PRIM', 'PWR', 'RBC', 'RS', 'RTX', 'TDY', 'TEX', 'TNC', 'TR', 'TRS', 'TT', 'TXT', 'UFPI', 'VMI', 'AGCO', 'ALGT', 'ALIT', 'ALMA', 'AMWD', 'AQUA', 'ARHS', 'AROW', 'AXL', 'AZEK', 'BCOR', 'BGSF', 'BJRI', 'BLBD', 'BMBL', 'BNFT', 'BRBR', 'BRLT', 'BXMT', 'CECO', 'NEE', 'DUK', 'SO', 'AEP', 'EXC', 'SRE', 'PPL', 'XEL', 'ES', 'ETR', 'AMT', 'PLD', 'CCI', 'EQIX', 'PSA', 'O', 'WELL', 'DLR', 'AVB', 'EQR', 'ARE', 'BXP', 'CPT', 'EGP', 'EXR', 'FRT', 'HIW', 'HST', 'IRM', 'KIM', 'KRG', 'LTC', 'LXP', 'NNN', 'OFC', 'PEAK', 'PLYM', 'RPT', 'SAFE', 'SHO', 'STAG', 'STOR', 'SUI', 'SWK', 'TCO', 'UDR', 'UE', 'VNO', 'WPC', 'WRE', 'VZ', 'T', 'TMUS', 'LUMN', 'SHEN', 'USM', 'TDS', 'ATN', 'CNSL', 'LBAI', 'ATUS', 'CABO', 'CCOI', 'DISH', 'GSAT', 'IDT', 'LILA', 'LILAK', 'OOMA', 'F', 'GM', 'UBER', 'LYFT', 'RIVN', 'LCID', 'NKLA', 'RIDE', 'FSR', 'BLNK', 'CHPT', 'EVGO', 'FFIE', 'GOEV', 'HYLN', 'LEV', 'MULN', 'NRGV', 'WKHS']

def run_pipeline(min_confidence=40, max_final=15, horizon=5, episodes=5):
    try:
        # المرحلة 1: فلتر سريع
        quick_results = []
        for symbol in RUSSELL_1000_HALAL:
            try:
                df = fetch_yf(symbol)
                if df is None: continue
                close = df["close"]
                ema21_val = float(ema(close, 21).iloc[-1])
                ema9_val  = float(ema(close, 9).iloc[-1])
                rsi_val   = float(rsi(close).iloc[-1])
                vol_ma    = float(df["volume"].rolling(20).mean().iloc[-1])
                vol_ratio = float(df["volume"].iloc[-1] / vol_ma) if vol_ma > 0 else 0
                price     = float(close.iloc[-1])
                score = sum([
                    price > ema21_val,
                    ema9_val > ema21_val,
                    30 <= rsi_val <= 75,
                    vol_ratio >= 0.8
                ])
                if score >= 2:
                    quick_results.append({"symbol": symbol, "price": round(price,2), "rsi": round(rsi_val,1), "quick_score": score})
            except: continue
        quick_results.sort(key=lambda x: x["quick_score"], reverse=True)
        top100 = quick_results[:100]

        # المرحلة 2: Halal Screener
        screener_results = []
        for item in top100:
            try:
                df = fetch_yf(item["symbol"])
                if df is None: continue
                r = analyze(item["symbol"], df)
                if r and r["swing_score"] >= 45:
                    screener_results.append(r)
            except: continue
        screener_results.sort(key=lambda x: x["swing_score"], reverse=True)
        top30 = screener_results[:30]

        # المرحلة 3: USX Pro
        top30_symbols = [r["symbol"] for r in top30]
        try:
            usx_all = run_usx_screener(min_score=5)
            usx_filtered = [x for x in usx_all if x.get("Symbol") in top30_symbols]
            usx_filtered.sort(key=lambda x: int(x.get("Score","0/10").split("/")[0]), reverse=True)
            top15 = [x["Symbol"] for x in usx_filtered[:15]]
        except:
            top15 = top30_symbols[:15]

        # المرحلة 4: AI Consensus
        final_results = []
        for symbol in top15:
            try:
                consensus = run_consensus(symbol, horizon=horizon, episodes=episodes)
                if consensus and len(consensus) > 0:
                    row = consensus[0].copy()
                    screener_info = next((r for r in screener_results if r["symbol"] == symbol), {})
                    usx_info = next((r for r in usx_filtered if r.get("Symbol") == symbol), {}) if "usx_filtered" in dir() else {}
                    row["Swing Score"] = screener_info.get("swing_score", 0)
                    row["USX Score"]   = usx_info.get("Score", "N/A")
                    row["RSI"]         = screener_info.get("rsi", 0)
                    row["Signals"]     = screener_info.get("signals", "")
                    final_results.append(row)
            except Exception as e:
                final_results.append({"Symbol": symbol, "Verdict": "ERROR", "Confidence %": 0, "Action": str(e)[:30], "Swing Score": 0})

        final_results.sort(key=lambda x: (
            x.get("Votes BUY", 0) - x.get("Votes SELL", 0),
            x.get("Confidence %", 0)
        ), reverse=True)

        strong_buy = [r for r in final_results if "STRONG BUY" in r.get("Verdict","")]
        buy        = [r for r in final_results if r.get("Verdict","") == "BUY"]
        weak_buy   = [r for r in final_results if r.get("Verdict","") == "WEAK BUY"]

        header = [{
            "Pipeline":         "Russell 1000 Halal → Full AI Pipeline",
            "Total Scanned":    len(RUSSELL_1000_HALAL),
            "After Quick Filter": len(top100),
            "After Screener":   len(top30),
            "After USX Pro":    len(top15),
            "Final Results":    len(final_results),
            "STRONG BUY":       len(strong_buy),
            "BUY":              len(buy),
            "WEAK BUY":         len(weak_buy),
            "Best Stock":       final_results[0]["Symbol"] if final_results else "None",
            "Best Confidence":  f'{final_results[0].get("Confidence %", 0)}%' if final_results else "0%",
            "Best Action":      final_results[0].get("Action","") if final_results else "",
        }]

        return header + final_results

    except Exception as e:
        return [{"Error": str(e)}]

@app.get("/widgets.json")
async def widgets():
    return {
        "halal_screener": {"name": "Halal Swing Screener", "description": "Multi-confluence swing signals",
                           "category": "Equity", "type": "table", "endpoint": "/screener", "gridData": {"w": 20, "h": 9}},
        "halal_buys": {"name": "Active Buy Signals", "description": "Score >= 55",
                       "category": "Equity", "type": "table", "endpoint": "/buys", "gridData": {"w": 20, "h": 9}},
        "halal_watchlist": {"name": "Halal Watchlist", "description": "Score 35-54",
                            "category": "Equity", "type": "table", "endpoint": "/watchlist", "gridData": {"w": 20, "h": 9}},
        "backtest_widget": {"name": "Backtest + Metrics", "description": "Backtest with Sharpe/Sortino/VaR",
                            "category": "Equity", "type": "table", "endpoint": "/backtest", "gridData": {"w": 20, "h": 9},
                            "params": [{"paramName": "symbol", "value": "AAPL", "label": "Symbol", "type": "text", "show": True},
                                       {"paramName": "start_date", "value": "2022-01-01", "label": "Start Date", "type": "text", "show": True},
                                       {"paramName": "end_date", "value": "2024-12-31", "label": "End Date", "type": "text", "show": True}]},
        "monte_carlo_widget": {"name": "Monte Carlo Forecast", "description": "1000 price scenarios",
                               "category": "Equity", "type": "table", "endpoint": "/monte_carlo", "gridData": {"w": 20, "h": 9},
                               "params": [{"paramName": "symbol", "value": "AAPL", "label": "Symbol", "type": "text", "show": True},
                                          {"paramName": "days", "value": "30", "label": "Days", "type": "text", "show": True},
                                          {"paramName": "simulations", "value": "1000", "label": "Simulations", "type": "text", "show": True}]},
        "lstm_widget": {"name": "LSTM + Walk-Forward", "description": "LSTM with leak-free validation",
                        "category": "Equity", "type": "table", "endpoint": "/lstm", "gridData": {"w": 20, "h": 9},
                        "params": [{"paramName": "symbol", "value": "AAPL", "label": "Symbol", "type": "text", "show": True},
                                   {"paramName": "horizon", "value": "5", "label": "Forecast Days", "type": "text", "show": True}]},
        "transformer_widget": {"name": "Transformer AI Forecast", "description": "Attention-based prediction + Walk-Forward",
                               "category": "Equity", "type": "table", "endpoint": "/transformer", "gridData": {"w": 20, "h": 9},
                               "params": [{"paramName": "symbol", "value": "AAPL", "label": "Symbol", "type": "text", "show": True},
                                          {"paramName": "horizon", "value": "5", "label": "Forecast Days", "type": "text", "show": True}]},
        "ensemble_widget": {"name": "Stacking Ensemble", "description": "XGB+RF+GBR ensemble forecast",
                            "category": "Equity", "type": "table", "endpoint": "/ensemble", "gridData": {"w": 20, "h": 9},
                            "params": [{"paramName": "symbol", "value": "AAPL", "label": "Symbol", "type": "text", "show": True},
                                       {"paramName": "horizon", "value": "5", "label": "Forecast Days", "type": "text", "show": True}]},
        "dqn_widget": {"name": "Double DQN + Risk Manager", "description": "RL agent with risk management",
                       "category": "Equity", "type": "table", "endpoint": "/dqn", "gridData": {"w": 20, "h": 9},
                       "params": [{"paramName": "symbol", "value": "AAPL", "label": "Symbol", "type": "text", "show": True},
                                  {"paramName": "episodes", "value": "20", "label": "Episodes", "type": "text", "show": True}]},
        "usx_pro_widget":{"name":"USX Pro V1.0 Screener","description":"10-point US swing trading system","category":"Equity","type":"table","endpoint":"/usx","gridData":{"w":20,"h":9},"params":[{"paramName":"min_score","value":"7","label":"Min Score (1-10)","type":"text","show":True}]},
        "policy_gradient_widget": {"name": "Policy Gradient Agent", "description": "REINFORCE trading agent",
                                   "category": "Equity", "type": "table", "endpoint": "/policy_gradient", "gridData": {"w": 20, "h": 9},
                                   "params": [{"paramName": "symbol", "value": "AAPL", "label": "Symbol", "type": "text", "show": True},
                                              {"paramName": "episodes", "value": "20", "label": "Episodes", "type": "text", "show": True}]},
    }

@app.get("/screener")
async def screener(): return run_screener()
@app.get("/buys")
async def buys(): return [r for r in run_screener() if r["swing_score"] >= 55]
@app.get("/watchlist")
async def watchlist(): return [r for r in run_screener() if 35 <= r["swing_score"] < 55]
@app.get("/backtest")
async def backtest(symbol: str = "AAPL", start_date: str = "2022-01-01", end_date: str = "2024-12-31", portfolio: float = 100000, risk_pct: float = 1.0, hold_days: int = 3):
    try: return run_backtest(symbol.upper(), start_date, end_date, portfolio, risk_pct, hold_days)
    except Exception as e: return [{"Error": str(e)}]
@app.get("/monte_carlo")
async def monte_carlo(symbol: str = "AAPL", days: int = 30, simulations: int = 1000):
    try: return run_monte_carlo(symbol.upper(), days, simulations)
    except Exception as e: return [{"Error": str(e)}]
@app.get("/lstm")
async def lstm(symbol: str = "AAPL", horizon: int = 5):
    try: return run_lstm(symbol.upper(), horizon)
    except Exception as e: return [{"Error": str(e)}]
@app.get("/transformer")
async def transformer(symbol: str = "AAPL", horizon: int = 5):
    try: return run_transformer(symbol.upper(), horizon)
    except Exception as e: return [{"Error": str(e)}]
@app.get("/ensemble")
async def ensemble(symbol: str = "AAPL", horizon: int = 5):
    try: return run_ensemble(symbol.upper(), horizon)
    except Exception as e: return [{"Error": str(e)}]
@app.get("/dqn")
async def dqn(symbol: str = "AAPL", episodes: int = 20):
    try: return run_dqn(symbol.upper(), episodes)
    except Exception as e: return [{"Error": str(e)}]
@app.get("/policy_gradient")
async def policy_gradient(symbol: str = "AAPL", episodes: int = 20):
    try: return run_policy_gradient(symbol.upper(), episodes)
    except Exception as e: return [{"Error": str(e)}]
@app.get("/usx")
async def usx(min_score:int=7):
    try: return run_usx_screener(min_score=min_score)
    except Exception as e: return [{"Error":str(e)}]
@app.get("/health")
async def health(): return {"status": "ok", "version": "11.0.0", "widgets": 14, "stocks": 505}
if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=5001)
