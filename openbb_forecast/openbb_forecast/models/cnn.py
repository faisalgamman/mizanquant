"""PyTorch CNN forecasters — ported from Stock-Prediction-Models.

Provides CNN-Seq2Seq and Dilated CNN variants for time-series forecasting.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset

from openbb_forecast.models.base import BaseForecaster
from openbb_forecast.models.persistence import default_version, write_metadata


class CNNSeq2SeqNetwork(nn.Module):
    def __init__(self, input_size: int, window_size: int, filters: int = 64, dropout: float = 0.2):
        super().__init__()
        self.conv1 = nn.Conv1d(input_size, filters, kernel_size=3, padding=1)
        self.conv2 = nn.Conv1d(filters, filters, kernel_size=3, padding=1)
        self.pool = nn.MaxPool1d(2)
        self.dropout = nn.Dropout(dropout)
        self.relu = nn.ReLU()
        pooled_len = window_size // 2
        self.fc1 = nn.Linear(filters * pooled_len, 50)
        self.fc2 = nn.Linear(50, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = x.transpose(1, 2)
        x = self.relu(self.conv1(x))
        x = self.pool(x)
        x = self.relu(self.conv2(x))
        x = x.view(x.size(0), -1)
        x = self.dropout(x)
        x = self.relu(self.fc1(x))
        return self.fc2(x)


class DilatedCNNNetwork(nn.Module):
    def __init__(self, input_size: int, filters: int = 64, dropout: float = 0.2):
        super().__init__()
        self.conv1 = nn.Conv1d(input_size, filters, kernel_size=3, dilation=2, padding=2)
        self.conv2 = nn.Conv1d(filters, filters, kernel_size=3, dilation=4, padding=4)
        self.pool = nn.AdaptiveAvgPool1d(1)
        self.dropout = nn.Dropout(dropout)
        self.relu = nn.ReLU()
        self.fc = nn.Linear(filters, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = x.transpose(1, 2)
        x = self.relu(self.conv1(x))
        x = self.relu(self.conv2(x))
        x = self.dropout(x)
        x = self.pool(x).squeeze(-1)
        return self.fc(x)


CNN_VARIANTS = {
    "cnn_seq2seq": CNNSeq2SeqNetwork,
    "dilated_cnn": DilatedCNNNetwork,
}


class CNNForecaster(BaseForecaster):
    name = "cnn"

    def __init__(
        self,
        variant: str = "cnn_seq2seq",
        filters: int = 64,
        epochs: int = 150,
        learning_rate: float = 0.001,
        dropout: float = 0.2,
        batch_size: int = 32,
        patience: int = 15,
        device: str = "auto",
        version: str | None = None,
    ):
        self.version = version or default_version()
        self.variant = variant
        self.filters = filters
        self.epochs = epochs
        self.learning_rate = learning_rate
        self.dropout = dropout
        self.batch_size = batch_size
        self.patience = patience

        if device == "auto":
            self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        else:
            self.device = torch.device(device)

        self._model: nn.Module | None = None
        self._input_size: int | None = None
        self._output_size: int | None = None
        self._window_size: int | None = None

    def _build_model(self, input_size: int, output_size: int) -> nn.Module:
        v = self.variant
        if v not in CNN_VARIANTS:
            raise ValueError(f"Unknown CNN variant: {v}. Choose from: {list(CNN_VARIANTS)}")
        if v == "cnn_seq2seq":
            return CNNSeq2SeqNetwork(input_size, self._window_size or 30, self.filters, self.dropout).to(self.device)
        return DilatedCNNNetwork(input_size, self.filters, self.dropout).to(self.device)

    def reset(self) -> None:
        if self._input_size is not None and self._output_size is not None:
            self._model = self._build_model(self._input_size, self._output_size)

    def fit(self, X_train: np.ndarray, y_train: np.ndarray, **kwargs) -> None:
        self._input_size = X_train.shape[-1]
        self._window_size = X_train.shape[1]
        self._output_size = y_train.shape[-1] if y_train.ndim > 1 else 1
        self._model = self._build_model(self._input_size, self._output_size)

        X_t = torch.FloatTensor(X_train).to(self.device)
        y_t = torch.FloatTensor(y_train).to(self.device)
        if y_t.ndim == 1:
            y_t = y_t.unsqueeze(-1)

        val_split = max(1, int(len(X_t) * 0.85))
        X_tr, X_val = X_t[:val_split], X_t[val_split:]
        y_tr, y_val = y_t[:val_split], y_t[val_split:]

        dataset = TensorDataset(X_tr, y_tr)
        loader = DataLoader(dataset, batch_size=self.batch_size, shuffle=True)

        optimizer = torch.optim.AdamW(self._model.parameters(), lr=self.learning_rate, weight_decay=1e-4)
        scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, mode="min", factor=0.5, patience=5, min_lr=1e-6)
        criterion = nn.MSELoss()

        best_val_loss = float("inf")
        patience_counter = 0
        best_state = None

        self._model.train()
        for epoch in range(self.epochs):
            for X_batch, y_batch in loader:
                optimizer.zero_grad()
                pred = self._model(X_batch)
                loss = criterion(pred, y_batch)
                loss.backward()
                torch.nn.utils.clip_grad_norm_(self._model.parameters(), max_norm=0.5)
                optimizer.step()

            if len(X_val) > 0:
                self._model.eval()
                with torch.no_grad():
                    val_loss = criterion(self._model(X_val), y_val).item()
                self._model.train()

                if val_loss < best_val_loss:
                    best_val_loss = val_loss
                    patience_counter = 0
                    best_state = {k: v.clone() for k, v in self._model.state_dict().items()}
                else:
                    patience_counter += 1
                    if patience_counter >= self.patience:
                        scheduler.step(val_loss)
                        break
                scheduler.step(val_loss)

        if best_state is not None:
            self._model.load_state_dict(best_state)

    def predict(self, X: np.ndarray) -> np.ndarray:
        if self._model is None:
            raise RuntimeError("Model not trained. Call fit() first.")
        self._model.eval()
        X_t = torch.FloatTensor(X).to(self.device)
        with torch.no_grad():
            return self._model(X_t).cpu().numpy()

    def save(self, path: Path) -> None:
        if self._model is None:
            raise RuntimeError("Model not trained. Call fit() before save().")
        payload = {
            "config": {
                "variant": self.variant,
                "filters": self.filters,
                "epochs": self.epochs,
                "learning_rate": self.learning_rate,
                "dropout": self.dropout,
                "batch_size": self.batch_size,
                "patience": self.patience,
                "device": str(self.device),
                "version": self.version,
            },
            "input_size": self._input_size,
            "output_size": self._output_size,
            "window_size": self._window_size,
            "state_dict": self._model.state_dict(),
        }
        torch.save(payload, path)
        write_metadata(path.with_suffix(".meta.json"), {"name": self.name, "version": self.version, "variant": self.variant})

    @classmethod
    def load(cls, path: Path) -> "CNNForecaster":
        payload = torch.load(path, map_location="cpu")
        config = payload.get("config", {})
        model = cls(**config)
        model._input_size = payload["input_size"]
        model._output_size = payload["output_size"]
        model._window_size = payload.get("window_size", 30)
        model._model = model._build_model(model._input_size, model._output_size)
        model._model.load_state_dict(payload["state_dict"])
        model._model.eval()
        return model
