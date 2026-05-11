"""Nightly model retraining and artifact promotion."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

from app.services.market_data import fetch
from app.services.notify import notify
from openbb_forecast.data.preprocessing import SafeScaler, create_features, create_sequences
from openbb_forecast.models.persistence import artifact_path, default_version, resolve_latest, write_latest
from openbb_forecast.models.factory import create_model, MODEL_NAMES
from openbb_forecast.risk.metrics import sharpe_ratio


MODEL_FACTORIES = {name: name for name in MODEL_NAMES}

PSI_RETRAIN_THRESHOLD = float(os.environ.get("DRIFT_RETRAIN_THRESHOLD", "0.25"))
PSI_HALT_THRESHOLD = float(os.environ.get("DRIFT_HALT_THRESHOLD", "0.35"))

FEATURE_COLS = [
    "returns", "volatility_5", "volatility_10", "volatility_20",
    "momentum_5", "momentum_10", "momentum_20",
    "rsi_14", "macd", "macd_signal", "volume_ratio",
    "high_low_range", "open_close_range",
]


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


def _load_features_df(symbol: str) -> pd.DataFrame:
    """Fetch OHLCV data and engineer multi-feature DataFrame."""
    df = fetch(symbol, period="5y")
    if df is None or len(df) < 260:
        raise ValueError(f"Insufficient history for {symbol}")
    return create_features(df)


def _build_feature_matrix(df: pd.DataFrame) -> np.ndarray:
    """Build feature matrix from engineered columns, using close as the first column."""
    close_col = df["close"].values.reshape(-1, 1)
    feat_cols = [c for c in FEATURE_COLS if c in df.columns]
    features = df[feat_cols].values
    return np.concatenate([close_col, features], axis=1)


def _train_one(name: str, symbol: str, version: str) -> dict:
    df = _load_features_df(symbol)
    feature_matrix = _build_feature_matrix(df)
    n_features = feature_matrix.shape[1]
    close_prices = df["close"].values
    holdout = _holdout_window(close_prices)
    train_val_len = len(close_prices) - holdout
    split = int(train_val_len * 0.8)

    train_features = feature_matrix[:split]
    val_features = feature_matrix[split - 30 : train_val_len]
    test_features = feature_matrix[train_val_len - 30 :]

    train_close = close_prices[:split]
    val_close = close_prices[split - 30 : train_val_len]
    test_close = close_prices[train_val_len - 30 :]

    price_scaler = SafeScaler(method="standard").fit(train_close.reshape(-1, 1))
    feature_scaler = SafeScaler(method="standard").fit(train_features)

    train_scaled = feature_scaler.transform(train_features)
    val_scaled = feature_scaler.transform(val_features)
    test_scaled = feature_scaler.transform(test_features)

    X_train, y_train_full = create_sequences(train_scaled, sequence_length=30, forecast_horizon=1)
    X_val, y_val_full = create_sequences(val_scaled, sequence_length=30, forecast_horizon=1)
    X_test, y_test_full = create_sequences(test_scaled, sequence_length=30, forecast_horizon=1)
    # Target is close price only (column 0), not all 14 features
    y_train = y_train_full[:, :, 0]
    y_val = y_val_full[:, :, 0]
    y_test = y_test_full[:, :, 0]

    model = create_model(name, version=version)
    model.fit(X_train, y_train)
    val_sharpe, val_directional_acc = _score_predictions(model.predict(X_val), y_val, price_scaler)
    test_sharpe, test_directional_acc = _score_predictions(model.predict(X_test), y_test, price_scaler)

    from openbb_forecast.models.factory import get_model_suffix
    suffix = get_model_suffix(name)
    artifact = artifact_path(name, version, suffix)
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
            "feature_list_hash": "multi_feature_v2",
            "n_features": n_features,
        }
    )
    meta_path.write_text(json.dumps(meta, indent=2, sort_keys=True), encoding="utf-8")
    _store_feature_references(df, symbol)
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
        from openbb_forecast.models.factory import get_model_suffix
        suffix = get_model_suffix(name)
        latest = resolve_latest(name, suffix)
        meta_path = latest.with_suffix(".meta.json")
        if meta_path.exists():
            return float(json.loads(meta_path.read_text(encoding="utf-8")).get("val_sharpe"))
    except Exception:
        return None
    return None


def _store_feature_references(df: pd.DataFrame, symbol: str):
    """Store training feature distributions as drift references."""
    from app.services.drift_monitor import DriftMonitor
    monitor = DriftMonitor()
    for col in FEATURE_COLS:
        if col in df.columns:
            values = df[col].dropna().values.tolist()
            if values:
                monitor.set_reference(f"{symbol}_{col}", values)


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
