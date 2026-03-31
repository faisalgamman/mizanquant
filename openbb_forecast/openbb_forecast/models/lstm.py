"""PyTorch LSTM forecaster — replaces the TF1 LSTM from deep-learning/1.lstm.ipynb.

Key differences from original:
  - PyTorch instead of TF1 (no tf.Session, tf.placeholder, tf.contrib)
  - Proper dropout (applied between LSTM layers, not to output)
  - reset() reinitializes weights between walk-forward folds
  - Early stopping on validation loss to prevent overfitting within each fold
"""

from __future__ import annotations

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset

from openbb_forecast.models.base import BaseForecaster


class LSTMNetwork(nn.Module):
    """Multi-layer LSTM with dropout for time-series forecasting.

    Architecture:
        Input(n_features) -> LSTM(hidden_size, num_layers, dropout) -> Linear -> Output(forecast_horizon)
    """

    def __init__(
        self,
        input_size: int,
        hidden_size: int,
        num_layers: int,
        output_size: int,
        dropout: float = 0.2,
    ):
        super().__init__()
        self.hidden_size = hidden_size
        self.num_layers = num_layers

        self.lstm = nn.LSTM(
            input_size=input_size,
            hidden_size=hidden_size,
            num_layers=num_layers,
            dropout=dropout if num_layers > 1 else 0.0,
            batch_first=True,
        )
        self.dropout = nn.Dropout(dropout)
        self.fc = nn.Linear(hidden_size, output_size)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x shape: (batch, sequence_length, input_size)
        lstm_out, _ = self.lstm(x)
        # Use last time step output
        last_output = lstm_out[:, -1, :]
        out = self.dropout(last_output)
        return self.fc(out)


class LSTMForecaster(BaseForecaster):
    """LSTM forecaster with walk-forward validation support.

    Args:
        hidden_size: Number of LSTM hidden units.
        num_layers: Number of stacked LSTM layers.
        epochs: Maximum training epochs per fold.
        learning_rate: Adam optimizer learning rate.
        dropout: Dropout rate between layers.
        batch_size: Training batch size.
        patience: Early stopping patience (epochs without improvement).
        device: 'cuda', 'cpu', or 'auto'.
    """

    def __init__(
        self,
        hidden_size: int = 128,
        num_layers: int = 2,
        epochs: int = 100,
        learning_rate: float = 0.001,
        dropout: float = 0.2,
        batch_size: int = 32,
        patience: int = 10,
        device: str = "auto",
    ):
        self.hidden_size = hidden_size
        self.num_layers = num_layers
        self.epochs = epochs
        self.learning_rate = learning_rate
        self.dropout = dropout
        self.batch_size = batch_size
        self.patience = patience

        if device == "auto":
            self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        else:
            self.device = torch.device(device)

        self._model: LSTMNetwork | None = None
        self._input_size: int | None = None
        self._output_size: int | None = None

    def _build_model(self, input_size: int, output_size: int) -> LSTMNetwork:
        return LSTMNetwork(
            input_size=input_size,
            hidden_size=self.hidden_size,
            num_layers=self.num_layers,
            output_size=output_size,
            dropout=self.dropout,
        ).to(self.device)

    def reset(self) -> None:
        """Reinitialize model weights for a fresh fold."""
        if self._input_size is not None and self._output_size is not None:
            self._model = self._build_model(self._input_size, self._output_size)

    def fit(self, X_train: np.ndarray, y_train: np.ndarray, **kwargs) -> None:
        """Train LSTM on one fold's training data with early stopping.

        Args:
            X_train: shape (n_samples, sequence_length, n_features)
            y_train: shape (n_samples, forecast_horizon)
        """
        self._input_size = X_train.shape[-1]
        self._output_size = y_train.shape[-1] if y_train.ndim > 1 else 1
        self._model = self._build_model(self._input_size, self._output_size)

        X_t = torch.FloatTensor(X_train).to(self.device)
        y_t = torch.FloatTensor(y_train).to(self.device)
        if y_t.ndim == 1:
            y_t = y_t.unsqueeze(-1)

        # Split last 15% of training data for early stopping validation
        val_split = max(1, int(len(X_t) * 0.85))
        X_tr, X_val = X_t[:val_split], X_t[val_split:]
        y_tr, y_val = y_t[:val_split], y_t[val_split:]

        dataset = TensorDataset(X_tr, y_tr)
        loader = DataLoader(dataset, batch_size=self.batch_size, shuffle=True)

        optimizer = torch.optim.Adam(self._model.parameters(), lr=self.learning_rate)
        criterion = nn.MSELoss()

        best_val_loss = float("inf")
        patience_counter = 0
        best_state = None

        self._model.train()
        for epoch in range(self.epochs):
            epoch_loss = 0.0
            for X_batch, y_batch in loader:
                optimizer.zero_grad()
                pred = self._model(X_batch)
                loss = criterion(pred, y_batch)
                loss.backward()
                torch.nn.utils.clip_grad_norm_(self._model.parameters(), max_norm=1.0)
                optimizer.step()
                epoch_loss += loss.item()

            # Validation for early stopping
            if len(X_val) > 0:
                self._model.eval()
                with torch.no_grad():
                    val_pred = self._model(X_val)
                    val_loss = criterion(val_pred, y_val).item()
                self._model.train()

                if val_loss < best_val_loss:
                    best_val_loss = val_loss
                    patience_counter = 0
                    best_state = {k: v.clone() for k, v in self._model.state_dict().items()}
                else:
                    patience_counter += 1
                    if patience_counter >= self.patience:
                        break

        # Restore best model
        if best_state is not None:
            self._model.load_state_dict(best_state)

    def predict(self, X: np.ndarray) -> np.ndarray:
        """Generate predictions from trained LSTM.

        Args:
            X: shape (n_samples, sequence_length, n_features)

        Returns:
            Predictions, shape (n_samples, forecast_horizon)
        """
        if self._model is None:
            raise RuntimeError("Model not trained. Call fit() first.")

        self._model.eval()
        X_t = torch.FloatTensor(X).to(self.device)
        with torch.no_grad():
            preds = self._model(X_t).cpu().numpy()
        return preds
