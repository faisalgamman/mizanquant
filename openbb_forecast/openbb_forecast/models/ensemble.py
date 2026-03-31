"""Stacking ensemble — replaces stacking/stack-encoder-ensemble-xgb.ipynb.

Key differences from original:
  - Proper out-of-fold (OOF) predictions instead of training on full data
  - Ridge meta-learner instead of XGBoost (prevents meta-learner overfitting)
  - No autoencoder for feature extraction (adds complexity without value;
    the original's autoencoder was fit on all data, causing leakage)
  - Uses engineered features from preprocessing.create_features() instead
"""

from __future__ import annotations

import numpy as np
from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor
from sklearn.linear_model import Ridge
from sklearn.model_selection import TimeSeriesSplit
from xgboost import XGBRegressor

from openbb_forecast.models.base import BaseForecaster


class StackingForecaster(BaseForecaster):
    """Two-level stacking ensemble with time-series aware OOF predictions.

    Level 1 (base learners):
        - XGBRegressor
        - RandomForestRegressor
        - GradientBoostingRegressor

    Level 2 (meta-learner):
        - Ridge regression on out-of-fold predictions

    The meta-learner trains ONLY on OOF predictions from level 1,
    not on predictions from models that saw the data being predicted.

    Args:
        n_inner_splits: Number of inner CV splits for generating OOF predictions.
        xgb_params: XGBoost hyperparameters override.
        rf_params: RandomForest hyperparameters override.
        gbr_params: GradientBoosting hyperparameters override.
        ridge_alpha: Regularization strength for meta-learner.
    """

    def __init__(
        self,
        n_inner_splits: int = 3,
        xgb_params: dict | None = None,
        rf_params: dict | None = None,
        gbr_params: dict | None = None,
        ridge_alpha: float = 1.0,
    ):
        self.n_inner_splits = n_inner_splits
        self.ridge_alpha = ridge_alpha

        self._xgb_params = xgb_params or {
            "n_estimators": 200,
            "max_depth": 5,
            "learning_rate": 0.05,
            "subsample": 0.8,
            "colsample_bytree": 0.8,
        }
        self._rf_params = rf_params or {
            "n_estimators": 200,
            "max_depth": 10,
            "min_samples_leaf": 5,
        }
        self._gbr_params = gbr_params or {
            "n_estimators": 200,
            "max_depth": 5,
            "learning_rate": 0.05,
            "subsample": 0.8,
        }

        self._base_models: list[object] = []
        self._meta_model: Ridge | None = None

    def reset(self) -> None:
        """Reset all models for a fresh fold."""
        self._base_models = []
        self._meta_model = None

    def _flatten_input(self, X: np.ndarray) -> np.ndarray:
        """Flatten 3D sequence input to 2D for tree-based models.

        Input shape: (n_samples, sequence_length, n_features)
        Output shape: (n_samples, sequence_length * n_features)
        """
        if X.ndim == 3:
            return X.reshape(X.shape[0], -1)
        return X

    def _flatten_target(self, y: np.ndarray) -> np.ndarray:
        """Flatten multi-step target to single value (mean) for base learners."""
        if y.ndim == 2 and y.shape[1] > 1:
            return y[:, 0]  # Predict first step; ensemble stacking over multi-step is noisy
        return y.ravel()

    def fit(self, X_train: np.ndarray, y_train: np.ndarray, **kwargs) -> None:
        """Train stacking ensemble with OOF predictions.

        1. Generate OOF predictions using inner time-series CV
        2. Train final base models on full training data
        3. Train meta-learner on OOF predictions
        """
        X_flat = self._flatten_input(X_train)
        y_flat = self._flatten_target(y_train)

        # Define base learners
        base_configs = [
            ("xgb", XGBRegressor, self._xgb_params),
            ("rf", RandomForestRegressor, self._rf_params),
            ("gbr", GradientBoostingRegressor, self._gbr_params),
        ]

        # Generate OOF predictions for meta-learner training
        n_inner = min(self.n_inner_splits, max(2, len(X_flat) // 20))
        tscv = TimeSeriesSplit(n_splits=n_inner)

        oof_predictions = np.zeros((len(X_flat), len(base_configs)))
        oof_mask = np.zeros(len(X_flat), dtype=bool)

        for fold_train_idx, fold_val_idx in tscv.split(X_flat):
            for j, (name, model_cls, params) in enumerate(base_configs):
                model = model_cls(**params)
                model.fit(X_flat[fold_train_idx], y_flat[fold_train_idx])
                oof_predictions[fold_val_idx, j] = model.predict(X_flat[fold_val_idx])
            oof_mask[fold_val_idx] = True

        # Train final base models on full training data
        self._base_models = []
        for name, model_cls, params in base_configs:
            model = model_cls(**params)
            model.fit(X_flat, y_flat)
            self._base_models.append(model)

        # Train meta-learner on OOF predictions only
        valid_oof = oof_predictions[oof_mask]
        valid_y = y_flat[oof_mask]

        self._meta_model = Ridge(alpha=self.ridge_alpha)
        self._meta_model.fit(valid_oof, valid_y)

        # Store output dimension for multi-step prediction
        self._output_dim = y_train.shape[1] if y_train.ndim > 1 else 1

    def predict(self, X: np.ndarray) -> np.ndarray:
        """Generate stacked predictions.

        1. Each base model predicts independently
        2. Meta-learner combines base predictions
        """
        if not self._base_models or self._meta_model is None:
            raise RuntimeError("Model not trained. Call fit() first.")

        X_flat = self._flatten_input(X)
        base_preds = np.column_stack([m.predict(X_flat) for m in self._base_models])
        meta_preds = self._meta_model.predict(base_preds)

        # Reshape to match expected output
        if self._output_dim > 1:
            # Repeat single prediction across horizon (ensemble predicts first step)
            return np.tile(meta_preds.reshape(-1, 1), (1, self._output_dim))
        return meta_preds.reshape(-1, 1)
