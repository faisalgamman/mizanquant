"""Walk-forward threshold calibration for consensus profiles."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from app.core.config import app_cfg
from app.services.market_data import fetch
from openbb_forecast.data.validation import WalkForwardSplit


PROFILES = ("base", "momentum", "reversion", "ml")
THRESHOLDS = list(range(30, 81, 5))
CALIBRATION_DIR = Path("calibration")
SYMBOLS = ("AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL")
LEGACY_MIN_CONFIDENCE = {
    "base": 45.0,
    "momentum": 45.0,
    "reversion": 40.0,
    "ml": 50.0,
}


def _utc_today() -> datetime:
    return datetime.now(timezone.utc)


def _load_prices(symbol: str) -> np.ndarray | None:
    df = fetch(symbol, start="2018-01-01", end=(_utc_today() - timedelta(days=1)).strftime("%Y-%m-%d"))
    if df is None or len(df) < 260:
        return None
    return df["close"].astype(float).to_numpy(dtype=np.float64)


def _profile_confidence(prices: np.ndarray, profile: str) -> np.ndarray:
    returns = np.diff(prices, prepend=prices[0]) / np.where(prices != 0, prices, 1.0)
    if profile == "momentum":
        signal = np.convolve(returns, np.ones(5) / 5, mode="same")
    elif profile == "reversion":
        signal = -np.convolve(returns, np.ones(3) / 3, mode="same")
    elif profile == "ml":
        signal = np.convolve(returns, np.ones(10) / 10, mode="same") + returns
    else:
        signal = np.convolve(returns, np.ones(7) / 7, mode="same")
    scaled = np.clip(np.abs(signal) * 2500.0, 0.0, 100.0)
    return scaled


def _simulate_profile(prices: np.ndarray, profile: str, threshold: float) -> dict:
    splitter = WalkForwardSplit(n_splits=5, min_train_ratio=0.75)
    fold_returns = []
    trade_count = 0
    for _train_idx, test_idx in splitter.split(len(prices)):
        conf = _profile_confidence(prices[: test_idx[-1] + 2], profile)
        for idx in test_idx[:-1]:
            if conf[idx] < threshold:
                continue
            ret = (prices[idx + 1] - prices[idx]) / max(prices[idx], 1e-9)
            if profile == "reversion":
                ret *= -1
            fold_returns.append(ret)
            trade_count += 1

    if not fold_returns:
        return {
            "sharpe": 0.0,
            "hit_rate": 0.0,
            "expectancy": 0.0,
            "profit_factor": 0.0,
            "max_dd": 0.0,
            "trade_count": 0,
            "trades_per_year": 0.0,
        }

    returns = np.asarray(fold_returns, dtype=np.float64)
    equity = np.cumprod(1.0 + returns)
    running_peak = np.maximum.accumulate(equity)
    drawdown = (equity / np.where(running_peak != 0, running_peak, 1.0)) - 1.0
    wins = returns[returns > 0]
    losses = returns[returns <= 0]
    sharpe = 0.0 if returns.std() == 0 else float(np.sqrt(252) * returns.mean() / returns.std())
    profit_factor = float(wins.sum() / abs(losses.sum())) if len(losses) and losses.sum() != 0 else 0.0
    days = max(len(prices), 1)
    trades_per_year = float(trade_count / (days / 252.0))
    return {
        "sharpe": round(sharpe, 4),
        "hit_rate": round(float((returns > 0).mean() * 100.0), 2),
        "expectancy": round(float(returns.mean() * 100.0), 4),
        "profit_factor": round(profit_factor, 4),
        "max_dd": round(float(abs(drawdown.min()) * 100.0), 4),
        "trade_count": int(trade_count),
        "trades_per_year": round(trades_per_year, 2),
    }


def _aggregate_profile(profile: str) -> dict:
    universe = [prices for symbol in SYMBOLS if (prices := _load_prices(symbol)) is not None]
    if not universe:
        raise RuntimeError("No market data available for calibration.")

    sweeps = []
    for threshold in THRESHOLDS:
        metrics = [_simulate_profile(prices, profile, threshold) for prices in universe]
        merged = {
            key: round(float(np.mean([metric[key] for metric in metrics])), 4)
            for key in metrics[0]
        }
        merged["threshold"] = threshold
        sweeps.append(merged)

    eligible = [
        row for row in sweeps
        if row["trades_per_year"] >= 50 and row["max_dd"] <= 15.0
    ]
    best = max(eligible or sweeps, key=lambda row: row["sharpe"])
    legacy_threshold = LEGACY_MIN_CONFIDENCE.get(profile, 45.0)
    legacy = next((row for row in sweeps if row["threshold"] == legacy_threshold), None)
    if legacy is None:
        legacy = best

    legacy_sharpe = float(legacy.get("sharpe", 0.0))
    best_sharpe = float(best.get("sharpe", 0.0))
    if legacy_sharpe > 0:
        improvement_pct = ((best_sharpe - legacy_sharpe) / legacy_sharpe) * 100.0
        beats_legacy = improvement_pct >= 5.0
    else:
        improvement_pct = (best_sharpe - legacy_sharpe) * 100.0
        beats_legacy = best_sharpe >= legacy_sharpe + 0.05

    selected = best["threshold"] if beats_legacy else legacy["threshold"]
    return {
        "selected": selected,
        "sweep": sweeps,
        "best": best,
        "legacy": legacy,
        "accepted": beats_legacy,
        "sharpe_improvement_pct": round(float(improvement_pct), 4),
    }


def main():
    CALIBRATION_DIR.mkdir(parents=True, exist_ok=True)
    thresholds = dict(LEGACY_MIN_CONFIDENCE)
    sweeps = {}
    for profile in PROFILES:
        result = _aggregate_profile(profile)
        thresholds[profile] = result["selected"]
        sweeps[profile] = result

    version = _utc_today().strftime("%Y%m%d")
    payload = {
        "generated_at": _utc_today().isoformat(),
        "thresholds": {
            "min_confidence": thresholds,
            "strong_buy_votes": app_cfg.thresholds.strong_buy_votes,
            "buy_votes_margin": app_cfg.thresholds.buy_votes_margin,
            "min_buy_confidence": app_cfg.thresholds.min_buy_confidence,
            "ml_tools_enabled": app_cfg.thresholds.ml_tools_enabled,
        },
        "sweeps": sweeps,
        "legacy_thresholds": dict(LEGACY_MIN_CONFIDENCE),
    }
    out_path = CALIBRATION_DIR / f"calibration_{version}.json"
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"output": str(out_path), "thresholds": thresholds}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
