"""PyTorch Transformer forecaster — replaces deep-learning/16.attention-is-all-you-need.ipynb.

Key differences from original:
  - Uses PyTorch's nn.TransformerEncoder (well-tested) instead of hand-rolled TF1 attention
  - Sinusoidal positional encoding (same concept, cleaner implementation)
  - Proper causal masking to prevent attention to future positions
  - Early stopping and gradient clipping for stable training
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset

from openbb_forecast.models.base import BaseForecaster
from openbb_forecast.models.persistence import default_version, write_metadata


class PositionalEncoding(nn.Module):
    """Sinusoidal positional encoding for sequence position awareness."""

    def __init__(self, d_model: int, max_len: int = 5000, dropout: float = 0.1):
        super().__init__()
        self.dropout = nn.Dropout(p=dropout)

        pe = torch.zeros(max_len, d_model)
        position = torch.arange(0, max_len, dtype=torch.float).unsqueeze(1)
        div_term = torch.exp(torch.arange(0, d_model, 2).float() * (-math.log(10000.0) / d_model))
        pe[:, 0::2] = torch.sin(position * div_term)
        if d_model > 1:
            pe[:, 1::2] = torch.cos(position * div_term[: d_model // 2])
        pe = pe.unsqueeze(0)  # (1, max_len, d_model)
        self.register_buffer("pe", pe)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x: (batch, seq_len, d_model)
        x = x + self.pe[:, : x.size(1)]
        return self.dropout(x)


class TimeSeriesTransformer(nn.Module):
    """Encoder-only Transformer for time-series forecasting.

    Architecture:
        Linear(input_size -> d_model) -> PositionalEncoding ->
        N x TransformerEncoderLayer(d_model, nhead, dim_ff, dropout) ->
        Linear(d_model -> output_size)

    Uses causal attention mask so each position can only attend to
    itself and earlier positions (no future leakage within the sequence).
    """

    def __init__(
        self,
        input_size: int,
        d_model: int = 128,
        n_heads: int = 8,
        num_layers: int = 2,
        dim_feedforward: int = 256,
        output_size: int = 1,
        dropout: float = 0.1,
    ):
        super().__init__()
        self.d_model = d_model
        self.input_projection = nn.Linear(input_size, d_model)
        self.pos_encoder = PositionalEncoding(d_model, dropout=dropout)

        encoder_layer = nn.TransformerEncoderLayer(
            d_model=d_model,
            nhead=n_heads,
            dim_feedforward=dim_feedforward,
            dropout=dropout,
            batch_first=True,
            norm_first=True,
        )
        self.transformer_encoder = nn.TransformerEncoder(encoder_layer, num_layers=num_layers, enable_nested_tensor=False)
        self.output_projection = nn.Linear(d_model, output_size)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x: (batch, seq_len, input_size)
        seq_len = x.size(1)

        # Causal mask: prevent attention to future positions
        mask = nn.Transformer.generate_square_subsequent_mask(seq_len, device=x.device)

        x = self.input_projection(x) * math.sqrt(self.d_model)
        x = self.pos_encoder(x)
        x = self.transformer_encoder(x, mask=mask)

        # Use last position output for prediction
        return self.output_projection(x[:, -1, :])


class TransformerForecaster(BaseForecaster):
    """Transformer forecaster with walk-forward validation support.

    Args:
        d_model: Transformer model dimension.
        n_heads: Number of attention heads.
        num_layers: Number of encoder layers.
        dim_feedforward: Feed-forward network dimension.
        epochs: Maximum training epochs.
        learning_rate: Adam optimizer learning rate.
        dropout: Dropout rate.
        batch_size: Training batch size.
        patience: Early stopping patience.
        device: 'cuda', 'cpu', or 'auto'.
    """

    name = "transformer"

    def __init__(
        self,
        d_model: int = 64,
        n_heads: int = 4,
        num_layers: int = 2,
        dim_feedforward: int = 128,
        epochs: int = 150,
        learning_rate: float = 0.0005,
        dropout: float = 0.2,
        batch_size: int = 32,
        patience: int = 15,
        device: str = "auto",
        version: str | None = None,
    ):
        self.version = version or default_version()
        self.d_model = d_model
        self.n_heads = n_heads
        self.num_layers = num_layers
        self.dim_feedforward = dim_feedforward
        self.epochs = epochs
        self.learning_rate = learning_rate
        self.dropout = dropout
        self.batch_size = batch_size
        self.patience = patience

        if device == "auto":
            self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        else:
            self.device = torch.device(device)

        self._model: TimeSeriesTransformer | None = None
        self._input_size: int | None = None
        self._output_size: int | None = None

    def _build_model(self, input_size: int, output_size: int) -> TimeSeriesTransformer:
        return TimeSeriesTransformer(
            input_size=input_size,
            d_model=self.d_model,
            n_heads=self.n_heads,
            num_layers=self.num_layers,
            dim_feedforward=self.dim_feedforward,
            output_size=output_size,
            dropout=self.dropout,
        ).to(self.device)

    def reset(self) -> None:
        if self._input_size is not None and self._output_size is not None:
            self._model = self._build_model(self._input_size, self._output_size)

    def fit(self, X_train: np.ndarray, y_train: np.ndarray, **kwargs) -> None:
        """Train Transformer on one fold's training data with early stopping."""
        self._input_size = X_train.shape[-1]
        self._output_size = y_train.shape[-1] if y_train.ndim > 1 else 1
        self._model = self._build_model(self._input_size, self._output_size)

        X_t = torch.FloatTensor(X_train).to(self.device)
        y_t = torch.FloatTensor(y_train).to(self.device)
        if y_t.ndim == 1:
            y_t = y_t.unsqueeze(-1)

        # Validation split for early stopping
        val_split = max(1, int(len(X_t) * 0.85))
        X_tr, X_val = X_t[:val_split], X_t[val_split:]
        y_tr, y_val = y_t[:val_split], y_t[val_split:]

        dataset = TensorDataset(X_tr, y_tr)
        loader = DataLoader(dataset, batch_size=self.batch_size, shuffle=True)

        optimizer = torch.optim.AdamW(
            self._model.parameters(), lr=self.learning_rate, weight_decay=1e-5
        )
        scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=self.epochs)
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
                torch.nn.utils.clip_grad_norm_(self._model.parameters(), max_norm=1.0)
                optimizer.step()

            scheduler.step()

            # Validation
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

        if best_state is not None:
            self._model.load_state_dict(best_state)

    def predict(self, X: np.ndarray) -> np.ndarray:
        if self._model is None:
            raise RuntimeError("Model not trained. Call fit() first.")

        self._model.eval()
        X_t = torch.FloatTensor(X).to(self.device)
        with torch.no_grad():
            preds = self._model(X_t).cpu().numpy()
        return preds

    def save(self, path: Path) -> None:
        if self._model is None:
            raise RuntimeError("Model not trained. Call fit() before save().")
        payload = {
            "config": {
                "d_model": self.d_model,
                "n_heads": self.n_heads,
                "num_layers": self.num_layers,
                "dim_feedforward": self.dim_feedforward,
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
            "state_dict": self._model.state_dict(),
        }
        torch.save(payload, path)
        write_metadata(
            path.with_suffix(".meta.json"),
            {"name": self.name, "version": self.version, "trained_on": default_version()},
        )

    @classmethod
    def load(cls, path: Path) -> "TransformerForecaster":
        payload = torch.load(path, map_location="cpu")
        config = payload.get("config", {})
        model = cls(**config)
        model._input_size = payload["input_size"]
        model._output_size = payload["output_size"]
        model._model = model._build_model(model._input_size, model._output_size)
        model._model.load_state_dict(payload["state_dict"])
        model._model.eval()
        return model
