"""Run a multi-regime walk-forward backtest and write an HTML report."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from app.services.market_data import fetch
from openbb_forecast.backtesting.engine import WalkForwardBacktester


def _utc_today() -> datetime:
    return datetime.now(timezone.utc)


REGIMES = {
    "rate_hike": ("2018-10-01", "2019-01-31"),
    "covid_crash": ("2020-02-15", "2020-05-15"),
    "bear_2022": ("2022-01-01", "2022-10-15"),
    "recovery_2023": ("2023-01-01", "2023-06-30"),
    "ai_bull_2024": ("2024-01-01", "2024-12-31"),
    "ytd": (f"{_utc_today().year}-01-01", (_utc_today() - timedelta(days=1)).strftime("%Y-%m-%d")),
}


@dataclass(frozen=True)
class ProfileSpec:
    signal_threshold: float
    momentum_window: int
    mean_window: int
    vol_window: int


PROFILE_SPECS = {
    "base": ProfileSpec(signal_threshold=0.0010, momentum_window=7, mean_window=20, vol_window=10),
    "momentum": ProfileSpec(signal_threshold=0.0008, momentum_window=5, mean_window=20, vol_window=10),
    "reversion": ProfileSpec(signal_threshold=0.0006, momentum_window=3, mean_window=15, vol_window=10),
    "ml": ProfileSpec(signal_threshold=0.0012, momentum_window=10, mean_window=30, vol_window=15),
}
DEFAULT_SYMBOLS = ("AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL")


def _load_frame(symbol: str, start: str, end: str) -> pd.DataFrame:
    df = fetch(symbol, start=start, end=end)
    if df is None or len(df) < 60:
        raise ValueError(f"No data for {symbol} {start}..{end}")
    if "date" not in df.columns:
        raise ValueError("Expected a 'date' column in fetched market data.")
    out = df.copy()
    out["date"] = pd.to_datetime(out["date"], utc=True)
    out = out.sort_values("date").reset_index(drop=True)
    return out


def _split_is_oos(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    split_idx = max(30, int(len(df) * 0.75))
    if len(df) - split_idx < 10:
        raise ValueError("Not enough OOS samples for walk-forward split.")
    return df.iloc[:split_idx].copy(), df.iloc[split_idx:].copy()


def _build_predictions(df: pd.DataFrame, profile: str) -> np.ndarray:
    spec = PROFILE_SPECS[profile]
    close = pd.Series(df["close"].astype(float).to_numpy(dtype=np.float64))
    returns = close.pct_change().fillna(0.0)
    momentum = returns.rolling(spec.momentum_window, min_periods=1).mean()
    trend = close.pct_change(spec.mean_window).replace([np.inf, -np.inf], 0.0).fillna(0.0)
    rolling_mean = close.rolling(spec.mean_window, min_periods=2).mean()
    rolling_std = close.rolling(spec.mean_window, min_periods=2).std(ddof=0).replace(0.0, np.nan)
    reversion = -((close - rolling_mean) / rolling_std).replace([np.inf, -np.inf], 0.0).fillna(0.0) * 0.01
    volatility = returns.rolling(spec.vol_window, min_periods=2).std(ddof=0).replace(0.0, np.nan)
    vol_adjusted = (momentum / volatility).replace([np.inf, -np.inf], 0.0).fillna(0.0) * 0.0025

    if profile == "momentum":
        signal = 0.55 * momentum + 0.30 * trend + 0.15 * vol_adjusted
    elif profile == "reversion":
        signal = 0.65 * reversion - 0.15 * trend + 0.20 * vol_adjusted
    elif profile == "ml":
        signal = 0.40 * momentum + 0.35 * trend + 0.25 * vol_adjusted
    else:
        signal = 0.40 * momentum + 0.30 * reversion + 0.30 * trend

    signal = signal.shift(1).fillna(0.0).clip(-0.03, 0.03)
    return (close * (1.0 + signal)).to_numpy(dtype=np.float64)


def _execution_costs(df: pd.DataFrame) -> tuple[float, float, float]:
    recent = df.tail(min(20, len(df)))
    avg_price = max(float(recent["close"].mean()), 1.0)
    order_notional = avg_price * 5.0

    if "volume" in recent.columns:
        adv = float((recent["close"].astype(float) * recent["volume"].astype(float)).mean())
    else:
        adv = order_notional * 1_000

    if {"high", "low", "close"}.issubset(recent.columns):
        spread_proxy = (
            ((recent["high"].astype(float) - recent["low"].astype(float)).abs())
            / recent["close"].astype(float).replace(0.0, np.nan)
        ) * 10_000.0
        spread_bps = max(1.0, float(spread_proxy.replace([np.inf, -np.inf], np.nan).fillna(0.0).median()) * 0.05)
    else:
        spread_bps = 2.0

    commission_bps = (0.005 / avg_price) * 10_000.0
    adv_component = 3.0 * (order_notional / max(adv * 0.001, 1.0))
    slippage_bps = max(1.0, 0.5 * spread_bps) + adv_component
    return commission_bps, slippage_bps, spread_bps


def _trade_distribution(daily_results: list[dict]) -> dict[str, float]:
    counts = Counter(row.get("action", "hold") for row in daily_results)
    total = max(sum(counts.values()), 1)
    return {action: round(count / total * 100.0, 2) for action, count in sorted(counts.items())}


def _signal_expectation(daily_results: list[dict]) -> dict[str, float]:
    mapping = {"buy": "BUY", "sell": "SELL", "hold": "NEUTRAL"}
    counts = Counter(mapping.get(row.get("action", "hold"), "NEUTRAL") for row in daily_results)
    total = max(sum(counts.values()), 1)
    return {bucket: round(count / total * 100.0, 2) for bucket, count in sorted(counts.items())}


def run_one_symbol(profile: str, symbol: str, start: str, end: str) -> dict:
    df = _load_frame(symbol, start, end)
    in_sample, out_sample = _split_is_oos(df)
    predictions = _build_predictions(df, profile)
    oos_predictions = predictions[len(in_sample) :]
    oos_prices = out_sample["close"].astype(float).to_numpy(dtype=np.float64)
    oos_dates = [ts.isoformat() for ts in out_sample["date"]]
    commission_bps, slippage_bps, spread_bps = _execution_costs(out_sample)

    backtester = WalkForwardBacktester(
        commission_bps=commission_bps,
        slippage_bps=slippage_bps,
        spread_bps=spread_bps,
        signal_threshold=PROFILE_SPECS[profile].signal_threshold,
    )
    result = backtester.run(prices=oos_prices, predictions=oos_predictions, dates=oos_dates)
    result["in_sample"] = {
        "start": in_sample["date"].iloc[0].date().isoformat(),
        "end": in_sample["date"].iloc[-1].date().isoformat(),
        "rows": int(len(in_sample)),
    }
    result["out_of_sample"] = {
        "start": out_sample["date"].iloc[0].date().isoformat(),
        "end": out_sample["date"].iloc[-1].date().isoformat(),
        "rows": int(len(out_sample)),
    }
    result["costs_bps"] = {
        "commission": round(float(commission_bps), 4),
        "slippage": round(float(slippage_bps), 4),
        "spread": round(float(spread_bps), 4),
    }
    result["trade_distribution"] = _trade_distribution(result["daily_results"])
    result["signal_expectation"] = _signal_expectation(result["daily_results"])
    return result


def _merge_distribution(items: list[dict[str, float]]) -> dict[str, float]:
    counts = Counter()
    for item in items:
        for key, value in item.items():
            counts[key] += value
    total = max(len(items), 1)
    return {key: round(value / total, 2) for key, value in sorted(counts.items())}


def run_one(profile: str, symbols: list[str], start: str, end: str) -> dict:
    symbol_results = {
        symbol: run_one_symbol(profile, symbol, start, end)
        for symbol in symbols
    }
    summaries = [result["summary"] for result in symbol_results.values()]
    trade_returns = [
        trade_return
        for result in symbol_results.values()
        for trade_return in result.get("trade_returns", [])
    ]
    combined_returns = np.asarray(trade_returns, dtype=np.float64)
    win_rate = float(np.mean(combined_returns > 0)) if len(combined_returns) else 0.0
    profit_factor = _overall_profit_factor(trade_returns)
    summary = {
        "total_return": round(float(np.mean([item["total_return"] for item in summaries])), 4),
        "annualized_return": round(float(np.mean([item["annualized_return"] for item in summaries])), 4),
        "sharpe_ratio": round(float(np.mean([item["sharpe_ratio"] for item in summaries])), 4),
        "sortino_ratio": round(float(np.mean([item["sortino_ratio"] for item in summaries])), 4),
        "max_drawdown": round(float(max(item["max_drawdown"] for item in summaries)), 4),
        "calmar_ratio": round(float(np.mean([item["calmar_ratio"] for item in summaries])), 4),
        "win_rate": round(win_rate, 4),
        "profit_factor": round(profit_factor, 4),
        "total_trades": int(sum(item["total_trades"] for item in summaries)),
        "avg_trade_return": round(float(np.mean(combined_returns)) if len(combined_returns) else 0.0, 4),
        "benchmark_return": round(float(np.mean([item["benchmark_return"] for item in summaries])), 4),
        "alpha": round(float(np.mean([item["alpha"] for item in summaries])), 4),
        "total_commissions": round(float(sum(item["total_commissions"] for item in summaries)), 4),
        "total_slippage": round(float(sum(item["total_slippage"] for item in summaries)), 4),
    }
    first = next(iter(symbol_results.values()))
    return {
        "summary": summary,
        "trade_returns": trade_returns,
        "in_sample": first["in_sample"],
        "out_of_sample": first["out_of_sample"],
        "costs_bps": _merge_distribution([result["costs_bps"] for result in symbol_results.values()]),
        "trade_distribution": _merge_distribution([result["trade_distribution"] for result in symbol_results.values()]),
        "signal_expectation": _merge_distribution([result["signal_expectation"] for result in symbol_results.values()]),
        "symbol_results": symbol_results,
        "symbols": list(symbols),
        "symbol_count": len(symbol_results),
    }


def _overall_profit_factor(trade_returns: list[float]) -> float:
    if not trade_returns:
        return 0.0
    arr = np.asarray(trade_returns, dtype=np.float64)
    wins = arr[arr > 0].sum()
    losses = np.abs(arr[arr < 0].sum())
    if losses == 0:
        return float("inf") if wins > 0 else 0.0
    return float(wins / losses)


def _gate(results: dict[str, dict]) -> dict:
    sharpe_passes = sum(1 for result in results.values() if result["summary"]["sharpe_ratio"] >= 0.8)
    dd_failures = [
        name for name, result in results.items()
        if result["summary"]["max_drawdown"] > 0.15
    ]
    all_trade_returns = [
        trade_return
        for result in results.values()
        for trade_return in result.get("trade_returns", [])
    ]
    overall_win_rate = float(np.mean(np.asarray(all_trade_returns) > 0)) if all_trade_returns else 0.0
    overall_profit_factor = _overall_profit_factor(all_trade_returns)
    return {
        "passed": (
            sharpe_passes >= min(5, len(results))
            and not dd_failures
            and overall_win_rate >= 0.48
            and overall_profit_factor >= 1.3
        ),
        "sharpe_passes": sharpe_passes,
        "dd_failures": dd_failures,
        "overall_win_rate": round(overall_win_rate, 4),
        "overall_profit_factor": round(overall_profit_factor, 4),
    }


def _html_report(symbols: list[str], profile: str, results: dict[str, dict], gate: dict) -> str:
    metric_rows = []
    detail_blocks = []
    for regime_name, result in results.items():
        summary = result["summary"]
        metric_rows.append(
            "<tr>"
            f"<td>{regime_name}</td>"
            f"<td>{result['in_sample']['start']} → {result['in_sample']['end']}</td>"
            f"<td>{result['out_of_sample']['start']} → {result['out_of_sample']['end']}</td>"
            f"<td>{summary['total_return']:.4f}</td>"
            f"<td>{summary['sharpe_ratio']:.4f}</td>"
            f"<td>{summary['max_drawdown']:.4f}</td>"
            f"<td>{summary['win_rate']:.4f}</td>"
            f"<td>{summary['profit_factor']:.4f}</td>"
            "</tr>"
        )
        detail_blocks.append(
            "<section style='margin-top:18px'>"
            f"<h2>{regime_name}</h2>"
            f"<p>Symbols: {', '.join(result.get('symbols', []))}</p>"
            f"<p>Costs (bps): {json.dumps(result['costs_bps'], sort_keys=True)}</p>"
            f"<p>Action distribution: {json.dumps(result['trade_distribution'], sort_keys=True)}</p>"
            f"<p>Signal expectation: {json.dumps(result['signal_expectation'], sort_keys=True)}</p>"
            "</section>"
        )

    status = "PASS" if gate["passed"] else "FAIL"
    dd_failures = ", ".join(gate["dd_failures"]) if gate["dd_failures"] else "none"
    return f"""
    <html>
      <head>
        <title>Backtest Report</title>
        <meta charset="utf-8" />
      </head>
      <body style="font-family:Segoe UI, sans-serif; margin:24px;">
        <h1>Backtest Report: {profile}</h1>
        <p>Universe: {', '.join(symbols)}</p>
        <p>Promotion gate: <strong>{status}</strong></p>
        <p>Sharpe passes: {gate['sharpe_passes']} | DD failures: {dd_failures} | Overall win rate: {gate['overall_win_rate']:.4f} | Overall profit factor: {gate['overall_profit_factor']:.4f}</p>
        <table border="1" cellpadding="6" cellspacing="0">
          <tr>
            <th>Regime</th>
            <th>In-Sample</th>
            <th>Out-of-Sample</th>
            <th>Total Return</th>
            <th>Sharpe</th>
            <th>Max DD</th>
            <th>Win Rate</th>
            <th>Profit Factor</th>
          </tr>
          {''.join(metric_rows)}
        </table>
        {''.join(detail_blocks)}
      </body>
    </html>
    """


def _write_signal_expectation(profile: str, results: dict[str, dict]) -> Path:
    expectation_path = Path("calibration") / "signal_expectation.json"
    expectation_path.parent.mkdir(parents=True, exist_ok=True)
    aggregated = Counter()
    for result in results.values():
        for bucket, pct in result["signal_expectation"].items():
            aggregated[bucket] += pct
    count = max(len(results), 1)
    payload = {}
    if expectation_path.exists():
        payload = json.loads(expectation_path.read_text(encoding="utf-8"))
    payload[profile] = {
        bucket: round(total / count, 2)
        for bucket, total in sorted(aggregated.items())
    }
    expectation_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return expectation_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default="momentum", choices=sorted(PROFILE_SPECS))
    parser.add_argument("--regimes", default="all")
    parser.add_argument("--symbol", default=None)
    parser.add_argument("--symbols", nargs="+", default=None)
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--write-signal-expectation", action="store_true")
    args = parser.parse_args()
    symbols = args.symbols or ([args.symbol] if args.symbol else list(DEFAULT_SYMBOLS))

    if args.smoke:
        selected = {"smoke": REGIMES["recovery_2023"]}
    elif args.regimes == "all":
        selected = REGIMES
    else:
        selected = {args.regimes: REGIMES[args.regimes]}

    results = {
        regime_name: run_one(args.profile, symbols, start, end)
        for regime_name, (start, end) in selected.items()
    }
    gate = _gate(results)
    report_dir = Path("reports")
    report_dir.mkdir(parents=True, exist_ok=True)
    report_date = _utc_today().strftime("%Y%m%d")
    out_path = report_dir / f"backtest_{report_date}_{args.profile}.html"
    out_path.write_text(_html_report(symbols, args.profile, results, gate), encoding="utf-8")

    expectation_path = None
    if args.write_signal_expectation:
        expectation_path = _write_signal_expectation(args.profile, results)

    for regime_name, result in results.items():
        print(
            f"{regime_name}: sharpe={result['summary']['sharpe_ratio']:.4f} "
            f"max_dd={result['summary']['max_drawdown']:.4f} "
            f"win_rate={result['summary']['win_rate']:.4f}"
        )
    extra = f" signal_expectation={expectation_path}" if expectation_path else ""
    print(f"gate_passed={gate['passed']} report={out_path}{extra}")


if __name__ == "__main__":
    main()
