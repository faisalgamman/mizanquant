"""RNN variant models — Vanilla RNN, 2Path, Attention, VAE — ported from Stock-Prediction-Models.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset

from openbb_forecast.models.base import BaseForecaster
from openbb_forecast.models.persistence import default_version, write_metadata


class VanillaRNNNetwork(nn.Module):
    def __init__(self, input_size: int, hidden_size: int, num_layers: int, output_size: int, dropout: float = 0.2, bidirectional: bool = False):
        super().__init__()
        d = 2 if bidirectional else 1
        self.rnn = nn.RNN(input_size, hidden_size, num_layers, batch_first=True, dropout=dropout if num_layers > 1 else 0.0, bidirectional=bidirectional)
        self.dropout = nn.Dropout(dropout)
        self.fc = nn.Linear(hidden_size * d, output_size)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out, _ = self.rnn(x)
        out = self.dropout(out[:, -1, :])
        return self.fc(out)


class RNN2PathNetwork(nn.Module):
    def __init__(self, input_size: int, hidden_size: int, output_size: int, dropout: float = 0.2):
        super().__init__()
        self.rnn1 = nn.RNN(input_size, hidden_size, batch_first=True)
        self.rnn2 = nn.RNN(input_size, hidden_size, batch_first=True)
        self.dropout = nn.Dropout(dropout)
        self.fc = nn.Linear(hidden_size * 2, output_size)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out1, _ = self.rnn1(x)
        out2, _ = self.rnn2(x)
        out = torch.cat([out1[:, -1, :], out2[:, -1, :]], dim=1)
        out = self.dropout(out)
        return self.fc(out)


class LSTM2PathNetwork(nn.Module):
    def __init__(self, input_size: int, hidden_size: int, output_size: int, dropout: float = 0.2):
        super().__init__()
        self.lstm1 = nn.LSTM(input_size, hidden_size, batch_first=True)
        self.lstm2 = nn.LSTM(input_size, hidden_size, batch_first=True)
        self.dropout = nn.Dropout(dropout)
        self.fc = nn.Linear(hidden_size * 2, output_size)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out1, _ = self.lstm1(x)
        out2, _ = self.lstm2(x)
        out = torch.cat([out1[:, -1, :], out2[:, -1, :]], dim=1)
        out = self.dropout(out)
        return self.fc(out)


class LSTMBiSeq2SeqNetwork(nn.Module):
    def __init__(self, input_size: int, hidden_size: int, output_size: int, dropout: float = 0.2):
        super().__init__()
        self.lstm1 = nn.LSTM(input_size, hidden_size, batch_first=True, bidirectional=True)
        self.lstm2 = nn.LSTM(hidden_size * 2, hidden_size, batch_first=True, bidirectional=True)
        self.dropout = nn.Dropout(dropout)
        self.fc = nn.Linear(hidden_size * 2, output_size)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out, _ = self.lstm1(x)
        out, _ = self.lstm2(out)
        out = self.dropout(out[:, -1, :])
        return self.fc(out)


class LSTMSeq2SeqVAENetwork(nn.Module):
    def __init__(self, input_size: int, hidden_size: int, latent_dim: int, output_size: int, dropout: float = 0.2):
        super().__init__()
        self.encoder = nn.LSTM(input_size, hidden_size, batch_first=True)
        self.fc_mu = nn.Linear(hidden_size, latent_dim)
        self.fc_logvar = nn.Linear(hidden_size, latent_dim)
        self.decoder = nn.LSTM(latent_dim, hidden_size, batch_first=True)
        self.dropout = nn.Dropout(dropout)
        self.fc_out = nn.Linear(hidden_size, output_size)

    def reparameterize(self, mu: torch.Tensor, logvar: torch.Tensor) -> torch.Tensor:
        return mu + torch.randn_like(logvar) * torch.exp(0.5 * logvar)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out, _ = self.encoder(x)
        out = out[:, -1, :]
        mu = self.fc_mu(out)
        logvar = self.fc_logvar(out)
        z = self.reparameterize(mu, logvar)
        z = z.unsqueeze(1).repeat(1, x.size(1), 1)
        out, _ = self.decoder(z)
        out = self.dropout(out[:, -1, :])
        return self.fc_out(out)


class AttentionRNNNetwork(nn.Module):
    def __init__(self, input_size: int, hidden_size: int, output_size: int, nhead: int = 2, dropout: float = 0.2):
        super().__init__()
        self.attention = nn.MultiheadAttention(input_size, nhead, batch_first=True)
        self.lstm = nn.LSTM(input_size, hidden_size, batch_first=True)
        self.dropout = nn.Dropout(dropout)
        self.fc = nn.Linear(hidden_size, output_size)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        attn_out, _ = self.attention(x, x, x)
        out, _ = self.lstm(attn_out)
        out = self.dropout(out[:, -1, :])
        return self.fc(out)


RNN_VARIANTS = {
    "vanilla": VanillaRNNNetwork,
    "vanilla_bi": (VanillaRNNNetwork, {"bidirectional": True}),
    "vanilla_2path": RNN2PathNetwork,
    "lstm_2path": LSTM2PathNetwork,
    "bilstm_seq2seq": LSTMBiSeq2SeqNetwork,
    "lstm_vae": LSTMSeq2SeqVAENetwork,
    "attention_rnn": AttentionRNNNetwork,
}


class RNNVariantForecaster(BaseForecaster):
    """Forecaster wrapping Vanilla RNN, BiRNN, 2Path, LSTM-2Path, BiLSTM-Seq2Seq, LSTM-VAE, and Attention-RNN variants.
    Each variant uses the same training loop and persistence format.
    """
    name = "rnn_variant"

    def __init__(
        self,
        variant: str = "vanilla",
        hidden_size: int = 64,
        num_layers: int = 2,
        epochs: int = 150,
        learning_rate: float = 0.001,
        dropout: float = 0.3,
        batch_size: int = 32,
        patience: int = 15,
        latent_dim: int = 16,
        device: str = "auto",
        version: str | None = None,
    ):
        self.version = version or default_version()
        self.variant = variant
        self.hidden_size = hidden_size
        self.num_layers = num_layers
        self.epochs = epochs
        self.learning_rate = learning_rate
        self.dropout = dropout
        self.batch_size = batch_size
        self.patience = patience
        self.latent_dim = latent_dim

        if device == "auto":
            self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        else:
            self.device = torch.device(device)

        self._model: nn.Module | None = None
        self._input_size: int | None = None
        self._output_size: int | None = None

    def _build_model(self, input_size: int, output_size: int) -> nn.Module:
        v = self.variant
        if v == "vanilla":
            return VanillaRNNNetwork(input_size, self.hidden_size, self.num_layers, output_size, self.dropout).to(self.device)
        elif v == "vanilla_bi":
            return VanillaRNNNetwork(input_size, self.hidden_size, self.num_layers, output_size, self.dropout, bidirectional=True).to(self.device)
        elif v == "vanilla_2path":
            return RNN2PathNetwork(input_size, self.hidden_size, output_size, self.dropout).to(self.device)
        elif v == "lstm_2path":
            return LSTM2PathNetwork(input_size, self.hidden_size, output_size, self.dropout).to(self.device)
        elif v == "bilstm_seq2seq":
            return LSTMBiSeq2SeqNetwork(input_size, self.hidden_size, output_size, self.dropout).to(self.device)
        elif v == "lstm_vae":
            return LSTMSeq2SeqVAENetwork(input_size, self.hidden_size, self.latent_dim, output_size, self.dropout).to(self.device)
        elif v == "attention_rnn":
            # Ensure embed_dim is divisible by num_heads
            nhead = min(2, max(1, input_size))
            return AttentionRNNNetwork(input_size, self.hidden_size, output_size, nhead=nhead, dropout=self.dropout).to(self.device)
        else:
            raise ValueError(f"Unknown RNN variant: {v}")

    def reset(self) -> None:
        if self._input_size is not None and self._output_size is not None:
            self._model = self._build_model(self._input_size, self._output_size)

    def fit(self, X_train: np.ndarray, y_train: np.ndarray, **kwargs) -> None:
        self._input_size = X_train.shape[-1]
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
                "hidden_size": self.hidden_size,
                "num_layers": self.num_layers,
                "epochs": self.epochs,
                "learning_rate": self.learning_rate,
                "dropout": self.dropout,
                "batch_size": self.batch_size,
                "patience": self.patience,
                "latent_dim": self.latent_dim,
                "device": str(self.device),
                "version": self.version,
            },
            "input_size": self._input_size,
            "output_size": self._output_size,
            "state_dict": self._model.state_dict(),
        }
        torch.save(payload, path)
        write_metadata(path.with_suffix(".meta.json"), {"name": self.name, "version": self.version, "variant": self.variant})

    @classmethod
    def load(cls, path: Path) -> "RNNVariantForecaster":
        payload = torch.load(path, map_location="cpu")
        config = payload.get("config", {})
        model = cls(**config)
        model._input_size = payload["input_size"]
        model._output_size = payload["output_size"]
        model._model = model._build_model(model._input_size, model._output_size)
        model._model.load_state_dict(payload["state_dict"])
        model._model.eval()
        return model
