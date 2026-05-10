"""Stacking ensemble — replaces stacking/stack-rnn-arima-xgb.ipynb and stack-encoder-ensemble-xgb.ipynb.

Key improvements over original:
  - Proper out-of-fold (OOF) predictions instead of training on full data
  - Configurable base learners: XGBoost, RandomForest, GBM, ARIMA, AdaBoost, ExtraTrees
  - Ridge or XGBoost meta-learner (original used XGBoost)
  - Optional auxiliary predictions from pre-trained models as stack features
  - No autoencoder (adds complexity without value; causes leakage on full-data fit)
  - Uses engineered features from preprocessing.create_features() instead
"""

from __future__ import annotations

import pickle
import warnings
from pathlib import Path

import numpy as np
from sklearn.ensemble import (
    AdaBoostRegressor,
    ExtraTreesRegressor,
    GradientBoostingRegressor,
    RandomForestRegressor,
)
from sklearn.linear_model import Ridge
from sklearn.model_selection import TimeSeriesSplit
from xgboost import XGBRegressor

from openbb_forecast.models.base import BaseForecaster
from openbb_forecast.models.persistence import default_version, write_metadata


# Base learner factory: maps short names to (class, default_params)
_BASE_LEARNER_REGISTRY: dict[str, tuple[type, dict]] = {
    "xgb": (XGBRegressor, {
        "n_estimators": 100, "max_depth": 3, "learning_rate": 0.03,
        "subsample": 0.7, "colsample_bytree": 0.7,
        "reg_alpha": 0.1, "reg_lambda": 0.1, "min_child_weight": 5,
    }),
    "rf": (RandomForestRegressor, {
        "n_estimators": 100, "max_depth": 5, "min_samples_leaf": 10,
        "min_samples_split": 10, "max_features": "sqrt",
    }),
    "gbr": (GradientBoostingRegressor, {
        "n_estimators": 100, "max_depth": 3, "learning_rate": 0.03,
        "subsample": 0.7, "min_samples_leaf": 10,
    }),
    "ada": (AdaBoostRegressor, {
        "n_estimators": 100, "learning_rate": 0.1,
    }),
    "et": (ExtraTreesRegressor, {
        "n_estimators": 100, "max_depth": 5, "min_samples_leaf": 5,
        "max_features": "sqrt",
    }),
}

BASE_LEARNER_NAMES = list(_BASE_LEARNER_REGISTRY.keys())


class StackingForecaster(BaseForecaster):
    """Two-level stacking ensemble with time-series aware OOF predictions.

    Level 1 (base learners): configurable set of sklearn regressors + optional ARIMA.
    Level 2 (meta-learner): Ridge regression or XGBoost on OOF predictions.

    The meta-learner trains ONLY on OOF predictions from level 1,
    not on predictions from models that saw the data being predicted.

    To replicate the original Stack-RNN-ARIMA-XGBoost approach:
      1. Train an RNN/LSTM/GRU model separately (e.g., via train_models.py)
      2. Pass its predictions as `aux_predictions` to fit()
      3. Set base_learners=["arima", "xgb", "rf"] and meta_learner="xgb"

    Args:
        n_inner_splits: Number of inner CV splits for generating OOF predictions.
        base_learners: List of base learner names from {xgb, rf, gbr, ada, et, arima}.
        arima_enabled: Shortcut to add "arima" to base_learners (if not already present).
        meta_learner: Meta-learner type ("ridge" or "xgb").
        xgb_params: XGBoost hyperparameters override (base learner).
        rf_params: RandomForest hyperparameters override.
        gbr_params: GradientBoosting hyperparameters override.
        ada_params: AdaBoost hyperparameters override.
        et_params: ExtraTrees hyperparameters override.
        arima_order: ARIMA (p,d,q) order tuple.
        ridge_alpha: Regularization strength for Ridge meta-learner.
        xgb_meta_params: XGBoost meta-learner hyperparameters.
        version: Artifact version string.
    """

    name = "ensemble"

    def __init__(
        self,
        n_inner_splits: int = 5,
        base_learners: list[str] | None = None,
        arima_enabled: bool = False,
        meta_learner: str = "ridge",
        xgb_params: dict | None = None,
        rf_params: dict | None = None,
        gbr_params: dict | None = None,
        ada_params: dict | None = None,
        et_params: dict | None = None,
        arima_order: tuple[int, int, int] = (5, 1, 0),
        ridge_alpha: float = 5.0,
        xgb_meta_params: dict | None = None,
        version: str | None = None,
    ):
        self.version = version or default_version()
        self.n_inner_splits = n_inner_splits
        self.meta_learner_type = meta_learner
        self.ridge_alpha = ridge_alpha
        self.arima_order = arima_order

        # Resolve base learner list
        if base_learners is not None:
            self._base_learner_names = list(base_learners)
        else:
            self._base_learner_names = ["xgb", "rf", "gbr"]

        if arima_enabled and "arima" not in self._base_learner_names:
            self._base_learner_names.append("arima")

        # Validate
        for name in self._base_learner_names:
            if name != "arima" and name not in _BASE_LEARNER_REGISTRY:
                raise ValueError(
                    f"Unknown base learner '{name}'. "
                    f"Available: {sorted(BASE_LEARNER_NAMES)} + 'arima'"
                )

        # Store hyperparameters
        self._xgb_params = xgb_params or dict(_BASE_LEARNER_REGISTRY["xgb"][1])
        self._rf_params = rf_params or dict(_BASE_LEARNER_REGISTRY["rf"][1])
        self._gbr_params = gbr_params or dict(_BASE_LEARNER_REGISTRY["gbr"][1])
        self._ada_params = ada_params or dict(_BASE_LEARNER_REGISTRY["ada"][1])
        self._et_params = et_params or dict(_BASE_LEARNER_REGISTRY["et"][1])

        # XGBoost meta-learner params
        self._xgb_meta_params = xgb_meta_params or {
            "n_estimators": 200,
            "max_depth": 3,
            "learning_rate": 0.05,
            "subsample": 0.8,
            "colsample_bytree": 0.8,
            "reg_alpha": 0.1,
            "reg_lambda": 1.0,
            "min_child_weight": 5,
        }

        self._base_models: list[object] = []
        self._meta_model: object | None = None

    def reset(self) -> None:
        """Reset all models for a fresh fold."""
        self._base_models = []
        self._meta_model = None

    def _flatten_input(self, X: np.ndarray) -> np.ndarray:
        if X.ndim == 3:
            return X.reshape(X.shape[0], -1)
        return X

    def _flatten_target(self, y: np.ndarray) -> np.ndarray:
        if y.ndim == 2 and y.shape[1] > 1:
            return y[:, 0]
        return y.ravel()

    def _build_base_configs(self) -> list[tuple[str, type | None, dict | None]]:
        """Build list of (name, model_cls_or_None, params) for each base learner."""
        configs = []
        for name in self._base_learner_names:
            if name == "arima":
                configs.append(("arima", None, {"order": self.arima_order}))
            else:
                cls, _ = _BASE_LEARNER_REGISTRY[name]
                params = getattr(self, f"_{name}_params", {})
                configs.append((name, cls, params))
        return configs

    def _fit_arima_oof(
        self,
        series: np.ndarray,
        fold_train_idx: np.ndarray,
        fold_val_idx: np.ndarray,
    ) -> np.ndarray:
        """Fit ARIMA on fold_train series, predict on fold_val."""
        from statsmodels.tsa.arima.model import ARIMA as ARIMAModel

        train_series = series[fold_train_idx]
        val_series = series[fold_val_idx]
        predictions = np.zeros(len(val_series))

        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                model = ARIMAModel(train_series, order=self.arima_order)
                fitted = model.fit(method_kwargs={"disp": False})
                predictions[:len(val_series)] = fitted.forecast(steps=len(val_series))
        except Exception:
            predictions.fill(np.mean(train_series[-20:]) if len(train_series) >= 20 else 0.0)

        return predictions

    def _predict_arima(self, series: np.ndarray, n_steps: int) -> np.ndarray:
        """Fit ARIMA on full series and forecast n_steps ahead."""
        from statsmodels.tsa.arima.model import ARIMA as ARIMAModel

        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                model = ARIMAModel(series, order=self.arima_order)
                fitted = model.fit(method_kwargs={"disp": False})
                return np.asarray(fitted.forecast(steps=n_steps), dtype=np.float64)
        except Exception:
            return np.full(n_steps, series[-1] if len(series) > 0 else 0.0)

    def fit(self, X_train: np.ndarray, y_train: np.ndarray, aux_predictions: np.ndarray | None = None) -> None:
        """Train stacking ensemble with OOF predictions.

        1. Generate OOF predictions using inner time-series CV
        2. Train final base models on full training data
        3. Train meta-learner on OOF predictions

        Args:
            X_train: Input sequences, shape (n_samples, sequence_length, n_features).
            y_train: Target values, shape (n_samples, forecast_horizon).
            aux_predictions: Optional pre-computed predictions from external models.
                             Shape (n_samples,) or (n_samples, n_aux_models).
                             These are concatenated with base predictions for meta-learner.
        """
        X_flat = self._flatten_input(X_train)
        y_flat = self._flatten_target(y_train)

        base_configs = self._build_base_configs()
        n_base = len(base_configs)
        n_aux = aux_predictions.shape[1] if aux_predictions is not None and aux_predictions.ndim > 1 else (aux_predictions.shape[0] if aux_predictions is not None else 0)
        n_total_features = n_base + (1 if aux_predictions is not None and aux_predictions.ndim == 1 else n_aux)

        # Generate OOF predictions for meta-learner training
        n_inner = min(self.n_inner_splits, max(2, len(X_flat) // 20))
        tscv = TimeSeriesSplit(n_splits=n_inner)

        oof_predictions = np.zeros((len(X_flat), n_total_features))
        oof_mask = np.zeros(len(X_flat), dtype=bool)

        for fold_train_idx, fold_val_idx in tscv.split(X_flat):
            col = 0
            for name, model_cls, params in base_configs:
                if name == "arima":
                    # ARIMA operates on the target series directly
                    oof_predictions[fold_val_idx, col] = self._fit_arima_oof(
                        y_flat, fold_train_idx, fold_val_idx
                    )
                else:
                    model = model_cls(**params) if model_cls is not None else None
                    if model is not None:
                        model.fit(X_flat[fold_train_idx], y_flat[fold_train_idx])
                        oof_predictions[fold_val_idx, col] = model.predict(X_flat[fold_val_idx])
                col += 1

            # Add auxiliary predictions if provided
            if aux_predictions is not None:
                if aux_predictions.ndim == 1:
                    oof_predictions[fold_val_idx, col] = aux_predictions[fold_val_idx]
                else:
                    for k in range(aux_predictions.shape[1]):
                        oof_predictions[fold_val_idx, col + k] = aux_predictions[fold_val_idx, k]

            oof_mask[fold_val_idx] = True

        # Train final base models on full training data
        self._base_models = []
        for name, model_cls, params in base_configs:
            if name == "arima":
                self._base_models.append("arima")
            else:
                model = model_cls(**params) if model_cls is not None else None
                if model is not None:
                    model.fit(X_flat, y_flat)
                    self._base_models.append(model)

        # Store auxiliary flag
        self._has_aux = aux_predictions is not None
        self._aux_ndim = aux_predictions.ndim if aux_predictions is not None else 0

        # Store training targets for ARIMA base learner prediction
        self._train_y = y_flat

        # Train meta-learner on OOF predictions only
        valid_oof = oof_predictions[oof_mask]
        valid_y = y_flat[oof_mask]

        if self.meta_learner_type == "xgb":
            self._meta_model = XGBRegressor(**self._xgb_meta_params)
        else:
            self._meta_model = Ridge(alpha=self.ridge_alpha)

        self._meta_model.fit(valid_oof, valid_y)

        # Store output dimension for multi-step prediction
        self._output_dim = y_train.shape[1] if y_train.ndim > 1 else 1

    def predict(self, X: np.ndarray, aux_predictions: np.ndarray | None = None) -> np.ndarray:
        """Generate stacked predictions.

        1. Each base model predicts independently
        2. Meta-learner combines base predictions

        Args:
            X: Input sequences, shape (n_samples, sequence_length, n_features).
            aux_predictions: Optional auxiliary predictions (same as during fit).
        """
        if not self._base_models or self._meta_model is None:
            raise RuntimeError("Model not trained. Call fit() first.")

        X_flat = self._flatten_input(X)

        # Collect base predictions
        base_pred_list = []
        for i, name in enumerate(self._base_learner_names):
            if name == "arima":
                # ARIMA: extrapolate from the training series
                if hasattr(self, "_train_y"):
                    arima_preds = self._predict_arima(self._train_y, len(X_flat))
                    base_pred_list.append(arima_preds)
                else:
                    base_pred_list.append(np.zeros(len(X_flat)))
            else:
                model = self._base_models[i]
                if hasattr(model, "predict"):
                    base_pred_list.append(model.predict(X_flat))
                else:
                    base_pred_list.append(np.zeros(len(X_flat)))

        base_preds = np.column_stack(base_pred_list)

        # Add auxiliary predictions
        if aux_predictions is not None and self._has_aux:
            if aux_predictions.ndim == 1:
                base_preds = np.column_stack([base_preds, aux_predictions])
            else:
                for k in range(aux_predictions.shape[1]):
                    base_preds = np.column_stack([base_preds, aux_predictions[:, k]])

        meta_preds = self._meta_model.predict(base_preds)

        if self._output_dim > 1:
            return np.tile(meta_preds.reshape(-1, 1), (1, self._output_dim))
        return meta_preds.reshape(-1, 1)

    def save(self, path: Path) -> None:
        if not self._base_models or self._meta_model is None:
            raise RuntimeError("Model not trained. Call fit() before save().")
        payload = {
            "config": {
                "n_inner_splits": self.n_inner_splits,
                "base_learners": self._base_learner_names,
                "meta_learner": self.meta_learner_type,
                "xgb_params": self._xgb_params,
                "rf_params": self._rf_params,
                "gbr_params": self._gbr_params,
                "ada_params": self._ada_params,
                "et_params": self._et_params,
                "arima_order": self.arima_order,
                "ridge_alpha": self.ridge_alpha,
                "xgb_meta_params": self._xgb_meta_params,
                "version": self.version,
                "has_aux": getattr(self, "_has_aux", False),
                "aux_ndim": getattr(self, "_aux_ndim", 0),
            },
            "base_models": self._base_models,
            "base_learner_names": self._base_learner_names,
            "meta_model": self._meta_model,
            "output_dim": getattr(self, "_output_dim", 1),
        }
        with path.open("wb") as fh:
            pickle.dump(payload, fh)
        write_metadata(
            path.with_suffix(".meta.json"),
            {"name": self.name, "version": self.version, "trained_on": default_version()},
        )

    @classmethod
    def load(cls, path: Path) -> "StackingForecaster":
        with path.open("rb") as fh:
            payload = pickle.load(fh)
        config = payload.get("config", {})
        # Strip non-constructor keys from persisted config
        valid_keys = {
            "n_inner_splits", "base_learners", "arima_enabled", "meta_learner",
            "xgb_params", "rf_params", "gbr_params", "ada_params", "et_params",
            "arima_order", "ridge_alpha", "xgb_meta_params", "version",
        }
        filtered = {k: v for k, v in config.items() if k in valid_keys}
        model = cls(**filtered)
        model._base_models = payload["base_models"]
        model._base_learner_names = payload.get("base_learner_names", ["xgb", "rf", "gbr"])
        model._meta_model = payload["meta_model"]
        model._output_dim = payload.get("output_dim", 1)
        model._has_aux = config.get("has_aux", False)
        model._aux_ndim = config.get("aux_ndim", 0)
        return model
