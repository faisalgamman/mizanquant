"""Abstract base class for all forecasters with built-in walk-forward validation.

The walk_forward_predict method enforces proper time-series validation:
  - Scaler is re-fit on each fold's training data (no data leakage)
  - Model weights are reset between folds (no weight leakage)
  - Results are collected only from out-of-sample test periods
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np

from openbb_forecast.data.preprocessing import SafeScaler, create_sequences
from openbb_forecast.data.validation import WalkForwardSplit


@dataclass
class FoldResult:
    """Results from a single walk-forward fold."""

    fold: int
    train_size: int
    test_size: int
    predictions: np.ndarray  # shape: (test_size, forecast_horizon)
    actuals: np.ndarray  # shape: (test_size, forecast_horizon)
    test_indices: np.ndarray  # indices into original data


class BaseForecaster(ABC):
    """Abstract base for all forecasting models.

    Subclasses must implement fit(), predict(), and reset().
    The walk_forward_predict() pipeline handles data splitting, scaling,
    and sequence creation automatically.
    """

    @abstractmethod
    def fit(self, X_train: np.ndarray, y_train: np.ndarray, **kwargs) -> None:
        """Train on a single fold's training data.

        Args:
            X_train: Input sequences, shape (n_samples, sequence_length, n_features).
            y_train: Target values, shape (n_samples, forecast_horizon).
        """

    @abstractmethod
    def predict(self, X: np.ndarray) -> np.ndarray:
        """Generate predictions.

        Args:
            X: Input sequences, shape (n_samples, sequence_length, n_features).

        Returns:
            Predictions, shape (n_samples, forecast_horizon).
        """

    @abstractmethod
    def reset(self) -> None:
        """Reset model to fresh state. Called between walk-forward folds
        to prevent weight leakage across folds."""

    def walk_forward_predict(
        self,
        data: np.ndarray,
        sequence_length: int = 30,
        forecast_horizon: int = 5,
        n_splits: int = 5,
        min_train_ratio: float = 0.6,
        gap: int = 0,
        scaler_method: str = "minmax",
        verbose: bool = False,
    ) -> list[FoldResult]:
        """Full walk-forward validation pipeline.

        For each fold:
          1. Split into train/test indices
          2. Fit scaler on TRAINING data only
          3. Transform both train and test with that scaler
          4. Create sequences from scaled data
          5. Reset and train model on training sequences
          6. Predict on test sequences
          7. Inverse-transform predictions and actuals
          8. Collect results

        Args:
            data: Raw price/feature data, shape (n_samples,) or (n_samples, n_features).
            sequence_length: Input window size.
            forecast_horizon: Number of steps to predict.
            n_splits: Number of walk-forward folds.
            min_train_ratio: Minimum training data fraction.
            gap: Embargo gap between train and test.
            scaler_method: 'minmax' or 'standard'.
            verbose: Print progress.

        Returns:
            List of FoldResult, one per fold.
        """
        data = np.asarray(data, dtype=np.float32)
        if data.ndim == 1:
            data = data.reshape(-1, 1)

        splitter = WalkForwardSplit(
            n_splits=n_splits,
            min_train_ratio=min_train_ratio,
            gap=gap,
        )
        folds = splitter.split(len(data))
        results = []

        for i, (train_idx, test_idx) in enumerate(folds):
            if verbose:
                print(
                    f"Fold {i + 1}/{len(folds)}: "
                    f"train=[0..{train_idx[-1]}], test=[{test_idx[0]}..{test_idx[-1]}]"
                )

            # 1. Extract train/test raw data
            train_raw = data[train_idx]
            # Include sequence_length points before test start to form first test sequence
            test_start = max(0, test_idx[0] - sequence_length)
            test_raw = data[test_start : test_idx[-1] + forecast_horizon + 1]

            # 2. Fit scaler on training data ONLY
            scaler = SafeScaler(method=scaler_method)
            scaler.fit(train_raw)

            # 3. Transform
            train_scaled = scaler.transform(train_raw)
            test_scaled = scaler.transform(test_raw)

            # 4. Create sequences
            X_train, y_train = create_sequences(train_scaled, sequence_length, forecast_horizon)
            X_test, y_test = create_sequences(test_scaled, sequence_length, forecast_horizon)

            if len(X_train) == 0 or len(X_test) == 0:
                continue

            # 5. Reset and train
            self.reset()
            self.fit(X_train, y_train)

            # 6. Predict
            preds_scaled = self.predict(X_test)

            # 7. Inverse transform — handle multi-feature case
            if preds_scaled.ndim == 1:
                preds_scaled = preds_scaled.reshape(-1, 1)
            if y_test.ndim == 1:
                y_test = y_test.reshape(-1, 1)

            # For multi-step forecasts, inverse transform each step
            preds_real = scaler.inverse_transform(preds_scaled)
            actuals_real = scaler.inverse_transform(y_test)

            # Trim to only the test indices we care about
            n_test_points = min(len(preds_real), len(test_idx))
            preds_real = preds_real[:n_test_points]
            actuals_real = actuals_real[:n_test_points]

            results.append(
                FoldResult(
                    fold=i + 1,
                    train_size=len(train_idx),
                    test_size=n_test_points,
                    predictions=preds_real,
                    actuals=actuals_real,
                    test_indices=test_idx[:n_test_points],
                )
            )

        return results


def compute_forecast_metrics(fold_results: list[FoldResult]) -> dict:
    """Aggregate metrics across all walk-forward folds.

    Returns a dict matching ForecastSummary fields.
    """
    all_preds = []
    all_actuals = []
    direction_correct = 0
    direction_total = 0

    for fold in fold_results:
        preds = fold.predictions
        actuals = fold.actuals

        all_preds.append(preds)
        all_actuals.append(actuals)

        # Directional accuracy: did we predict the right direction of change?
        if preds.ndim == 2 and preds.shape[1] >= 1:
            for j in range(len(preds)):
                if j > 0:
                    # Predicted direction: does the model think price goes up or down from previous actual?
                    pred_direction = preds[j, 0] - actuals[j - 1, 0]
                    # Actual direction: did the price actually go up or down?
                    actual_direction = actuals[j, 0] - actuals[j - 1, 0]
                    if actual_direction != 0:
                        direction_total += 1
                        if (pred_direction > 0) == (actual_direction > 0):
                            direction_correct += 1

    if not all_preds:
        return {"directional_accuracy": 0, "mae": 0, "rmse": 0, "mape": 0}

    all_preds = np.concatenate(all_preds, axis=0)
    all_actuals = np.concatenate(all_actuals, axis=0)

    errors = all_preds - all_actuals
    abs_errors = np.abs(errors)
    pct_errors = abs_errors / np.where(np.abs(all_actuals) > 1e-8, np.abs(all_actuals), 1.0)

    return {
        "directional_accuracy": direction_correct / max(direction_total, 1),
        "mae": float(np.mean(abs_errors)),
        "rmse": float(np.sqrt(np.mean(errors**2))),
        "mape": float(np.mean(pct_errors)),
    }
