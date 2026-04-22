"""Nightly model retraining and artifact promotion."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from app.services.market_data import fetch
from app.services.notify import notify
from openbb_forecast.data.preprocessing import SafeScaler, create_sequences
from openbb_forecast.models.ensemble import StackingForecaster
from openbb_forecast.models.lstm import LSTMForecaster
from openbb_forecast.models.persistence import artifact_path, default_version, resolve_latest, write_latest
from openbb_forecast.models.transformer import TransformerForecaster
from openbb_forecast.risk.metrics import sharpe_ratio


MODEL_FACTORIES = {
    "lstm": LSTMForecaster,
    "transformer": TransformerForecaster,
    "ensemble": StackingForecaster,
}


def _holdout_window(prices: np.ndarray) -> int:
    return min(63, max(30, len(prices) // 12))


def _score_predictions(
    preds_scaled: np.ndarray,
    y_scaled: np.ndarray,
    scaler: SafeScaler,
) -> tuple[float, float]:
    preds = scaler.inverse_transform(np.asarray(preds_scaled).reshape(-1, 1)).ravel()
    actuals = scaler.inverse_transform(np.asarray(y_scaled).reshape(-1, 1)).ravel()
    size = min(len(preds), len(actuals))
    if size == 0:
        return 0.0, 0.0
    preds = preds[:size]
    actuals = actuals[:size]
    signal_returns = (preds - actuals) / np.where(actuals != 0, actuals, 1.0)
    val = sharpe_ratio(signal_returns) if len(signal_returns) > 1 else 0.0
    direction = np.sign(np.diff(preds))
    realized = np.sign(np.diff(actuals))
    acc = float((direction == realized).mean()) if len(direction) and len(realized) else 0.0
    return float(val), acc


def _load_prices(symbol: str) -> np.ndarray:
    df = fetch(symbol, period="5y")
    if df is None or len(df) < 260:
        raise ValueError(f"Insufficient history for {symbol}")
    return df["close"].astype(float).to_numpy(dtype=np.float64)


def _train_one(name: str, symbol: str, version: str) -> dict:
    prices = _load_prices(symbol)
    holdout = _holdout_window(prices)
    train_val_prices = prices[:-holdout]
    split = int(len(train_val_prices) * 0.8)
    train_prices = train_val_prices[:split]
    val_prices = train_val_prices[split - 30 :]
    test_prices = prices[-(holdout + 30) :]

    scaler = SafeScaler(method="standard").fit(train_prices.reshape(-1, 1))
    train_scaled = scaler.transform(train_prices.reshape(-1, 1))
    val_scaled = scaler.transform(val_prices.reshape(-1, 1))
    test_scaled = scaler.transform(test_prices.reshape(-1, 1))
    X_train, y_train = create_sequences(train_scaled, sequence_length=30, forecast_horizon=1)
    X_val, y_val = create_sequences(val_scaled, sequence_length=30, forecast_horizon=1)
    X_test, y_test = create_sequences(test_scaled, sequence_length=30, forecast_horizon=1)

    model = MODEL_FACTORIES[name](version=version)
    model.fit(X_train, y_train)
    val_sharpe, val_directional_acc = _score_predictions(model.predict(X_val), y_val, scaler)
    test_sharpe, test_directional_acc = _score_predictions(model.predict(X_test), y_test, scaler)

    artifact = artifact_path(name, version, ".pt" if name != "ensemble" else ".pkl")
    model.save(artifact)
    meta_path = artifact.with_suffix(".meta.json")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta.update(
        {
            "trained_on": symbol,
            "val_sharpe": round(float(val_sharpe), 4),
            "val_acc": round(float(val_directional_acc), 4),
            "test_sharpe": round(float(test_sharpe), 4),
            "test_acc": round(float(test_directional_acc), 4),
            "test_window_days": int(holdout),
            "feature_list_hash": "close_only_v1",
        }
    )
    meta_path.write_text(json.dumps(meta, indent=2, sort_keys=True), encoding="utf-8")
    return {
        "artifact": artifact,
        "val_sharpe": float(val_sharpe),
        "val_acc": float(val_directional_acc),
        "test_sharpe": float(test_sharpe),
        "test_acc": float(test_directional_acc),
        "test_window_days": int(holdout),
    }


def _previous_sharpe(name: str) -> float | None:
    try:
        suffix = ".pt" if name != "ensemble" else ".pkl"
        latest = resolve_latest(name, suffix)
        meta_path = latest.with_suffix(".meta.json")
        if meta_path.exists():
            return float(json.loads(meta_path.read_text(encoding="utf-8")).get("val_sharpe"))
    except Exception:
        return None
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="+", default=["AAPL"])
    parser.add_argument("--models", nargs="+", default=list(MODEL_FACTORIES))
    parser.add_argument("--version", default=default_version())
    args = parser.parse_args()

    reports = {}
    for model_name in args.models:
        best_result = None
        for symbol in args.symbols:
            result = _train_one(model_name, symbol, args.version)
            if best_result is None or result["val_sharpe"] > best_result["val_sharpe"]:
                best_result = result

        previous = _previous_sharpe(model_name)
        promote = previous is None or best_result["val_sharpe"] >= previous - 0.5
        if promote:
            write_latest(model_name, args.version)
        else:
            notify(
                "critical_error",
                dedup_key=f"train-models-rollback:{model_name}:{args.version}",
                message=(
                    f"Model rollback kept previous {model_name} artifact: "
                    f"new_sharpe={best_result['val_sharpe']:.4f}, previous={previous:.4f}"
                ),
            )
        reports[model_name] = {
            "version": args.version,
            "val_sharpe": best_result["val_sharpe"],
            "val_acc": best_result["val_acc"],
            "test_sharpe": best_result["test_sharpe"],
            "test_acc": best_result["test_acc"],
            "test_window_days": best_result["test_window_days"],
            "promoted": promote,
            "previous_sharpe": previous,
            "artifact": str(best_result["artifact"]),
        }

    print(json.dumps(reports, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
