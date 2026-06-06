"""Standalone backtest engine — no halal_screener dependency."""
from app.services.execution_costs import apply_costs as _apply_costs, BACKTEST_COST_BPS
from app.services.market_data import fetch as fetch_market_data
from app.services.technical import ema, rsi, macd, atr, calc_metrics, score_series


def run_backtest(symbol, start_date, end_date, portfolio, risk_pct, hold_days):
    """Walk-forward backtest with NO same-bar look-ahead and transaction costs.

    Key invariants (Chan Ch.2):
      - Signals computed using close[i] are acted on at OPEN of bar i+1.
      - All fills pay BACKTEST_COST_BPS in slippage/commission per leg.
      - Stop-loss / take-profit checked using next bar's high/low after entry.
    """
    try:
        df = fetch_market_data(symbol, start=start_date, end=end_date)
        if df is None:
            return [{"Error": f"No data for {symbol}"}]
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
        df["score"] = score_series(df)
        trades = []
        port = portfolio
        in_trade = False
        entry_idx = entry_price = sl = tp = pos_size = 0
        trade_returns = []
        n = len(df)
        for i in range(n):
            row = df.iloc[i]
            if in_trade:
                days = i - entry_idx
                if float(row["low"]) <= sl:
                    exit_price = _apply_costs(sl, "sell")
                    pnl = (exit_price - entry_price) * pos_size
                    port += pnl
                    ret = (exit_price - entry_price) / entry_price
                    trade_returns.append(ret)
                    trades.append({"Entry Date": str(df.iloc[entry_idx]["date"])[:10], "Exit Date": str(row["date"])[:10], "Entry": round(entry_price, 2), "Exit": round(exit_price, 2), "Result": "Stop Loss", "PnL": round(pnl, 2), "Portfolio": round(port, 2), "Days": days, "Score": int(df.iloc[entry_idx]["score"])})
                    in_trade = False
                elif float(row["high"]) >= tp:
                    exit_price = _apply_costs(tp, "sell")
                    pnl = (exit_price - entry_price) * pos_size
                    port += pnl
                    ret = (exit_price - entry_price) / entry_price
                    trade_returns.append(ret)
                    trades.append({"Entry Date": str(df.iloc[entry_idx]["date"])[:10], "Exit Date": str(row["date"])[:10], "Entry": round(entry_price, 2), "Exit": round(exit_price, 2), "Result": "Take Profit", "PnL": round(pnl, 2), "Portfolio": round(port, 2), "Days": days, "Score": int(df.iloc[entry_idx]["score"])})
                    in_trade = False
                elif days >= hold_days:
                    exit_price = _apply_costs(float(row["close"]), "sell")
                    pnl = (exit_price - entry_price) * pos_size
                    port += pnl
                    ret = (exit_price - entry_price) / entry_price
                    trade_returns.append(ret)
                    trades.append({"Entry Date": str(df.iloc[entry_idx]["date"])[:10], "Exit Date": str(row["date"])[:10], "Entry": round(entry_price, 2), "Exit": round(exit_price, 2), "Result": "Time Stop", "PnL": round(pnl, 2), "Portfolio": round(port, 2), "Days": days, "Score": int(df.iloc[entry_idx]["score"])})
                    in_trade = False
            if not in_trade and i + 1 < n and float(df.iloc[i]["score"]) >= 55:
                next_open_raw = float(df.iloc[i + 1].get("open", df.iloc[i + 1]["close"]))
                entry_price = _apply_costs(next_open_raw, "buy")
                atr_v = float(df.iloc[i]["atr_v"])
                sl = entry_price - atr_v
                tp = entry_price + (2 * atr_v)
                risk = entry_price - sl
                pos_size = int((port * (risk_pct / 100)) / risk) if risk > 0 else 0
                if pos_size > 0:
                    in_trade = True
                    entry_idx = i + 1
        if not trades:
            return [{"Symbol": symbol, "Message": "No trades found"}]
        for t in trades:
            if t["PnL"] > 0:
                t["Classification"] = "WIN"
            elif t["PnL"] < 0:
                t["Classification"] = "LOSS"
            else:
                t["Classification"] = "BREAKEVEN"
        wins = [t for t in trades if t["Classification"] == "WIN"]
        losses = [t for t in trades if t["Classification"] == "LOSS"]
        ann = max(252 // max(hold_days, 1), 12)
        metrics = calc_metrics(trade_returns, annualization=ann)
        try:
            from app.services.backtest_qc import deflated_sharpe, permutation_pvalue
            dsr = deflated_sharpe(trade_returns, n_trials=1, annualization=ann)
            pval = permutation_pvalue(trade_returns, n_perm=200)
        except Exception:
            dsr = 0.0
            pval = 1.0
        total_closed = len(trades)
        summary = [{"Symbol": symbol, "Period": f"{start_date} to {end_date}",
                    "Total Trades": total_closed, "Winners": len(wins), "Losers": len(losses),
                    "Win Rate %": round(len(wins) / total_closed * 100, 1) if total_closed else 0,
                    "Total PnL": round(sum(t["PnL"] for t in trades), 2),
                    "Final Portfolio": round(port, 2),
                    "Return %": round((port - portfolio) / portfolio * 100, 2),
                    "Avg Win": round(sum(t["PnL"] for t in wins) / len(wins), 2) if wins else 0,
                    "Avg Loss": round(sum(t["PnL"] for t in losses) / len(losses), 2) if losses else 0,
                    "Deflated Sharpe": round(dsr, 3),
                    "Permutation p-value": round(pval, 3),
                    **metrics}]
        # ── Equity Curve ──────────────────────────────────────────
        equity_curve = [{"date": start_date, "portfolio": portfolio}]
        for t in trades:
            equity_curve.append({"date": t["Exit Date"], "portfolio": t["Portfolio"]})

        # ── Monthly Returns ────────────────────────────────────────
        from collections import defaultdict as _dd
        monthly_pnl = _dd(float)
        monthly_start = {}
        running = portfolio
        for t in trades:
            mk = t["Exit Date"][:7]
            if mk not in monthly_start:
                monthly_start[mk] = running
            monthly_pnl[mk] += t["PnL"]
            running = t["Portfolio"]
        monthly_returns = {
            m: round(pnl / monthly_start[m] * 100, 2)
            for m, pnl in sorted(monthly_pnl.items())
            if monthly_start.get(m)
        }

        # ── Benchmark (SPY buy & hold) ─────────────────────────────
        benchmark_return_pct = 0.0
        benchmark_equity = []
        try:
            spy = fetch_market_data("SPY", start=start_date, end=end_date)
            if spy is not None and len(spy) >= 2:
                spy_ratio = float(spy["close"].iloc[-1]) / float(spy["close"].iloc[0])
                benchmark_return_pct = round((spy_ratio - 1) * 100, 2)
                benchmark_equity = [
                    {"date": start_date, "portfolio": portfolio},
                    {"date": end_date,   "portfolio": round(portfolio * spy_ratio, 2)},
                ]
        except Exception:
            pass

        # ── Extended Stats ─────────────────────────────────────────
        max_cw = max_cl = cw = cl = 0
        for t in trades:
            if t["Classification"] == "WIN":
                cw += 1; cl = 0
            else:
                cl += 1; cw = 0
            max_cw = max(max_cw, cw); max_cl = max(max_cl, cl)

        wr  = summary[0].get("Win Rate %", 0) / 100
        awv = summary[0].get("Avg Win",  0) or 0
        alv = summary[0].get("Avg Loss", 0) or 0
        exp = round(wr * awv + (1 - wr) * alv, 2)
        mdd = summary[0].get("Max Drawdown %", 0) or -0.001
        rf  = round(summary[0].get("Return %", 0) / abs(mdd), 3) if mdd else 0

        # ── Regime Breakdown ──────────────────────────────────────────
        regime_breakdown: dict = {}
        try:
            from app.services.backtest_regime_filter import (
                tag_trades, compute_per_regime_breakdown,
            )
            # Reuse the SPY data already fetched for the benchmark — this
            # gives point-in-time accuracy for every historical trade date.
            spy_for_regime = spy if (spy is not None and len(spy) >= 2) else None
            # trades use "Entry Date" key (Title Case)
            tag_trades(trades, spy_df=spy_for_regime)
            regime_breakdown = compute_per_regime_breakdown(trades)
        except Exception:
            pass

        summary[0].update({
            "Benchmark Return %": benchmark_return_pct,
            "Max Consec Wins":    max_cw,
            "Max Consec Losses":  max_cl,
            "Expectancy ($)":     exp,
            "Recovery Factor":    rf,
            "equity_curve":       equity_curve,
            "monthly_returns":    monthly_returns,
            "benchmark_equity":   benchmark_equity,
            "regime_breakdown":   regime_breakdown,
        })

        return summary + trades
    except Exception as e:
        return [{"Error": str(e)}]
