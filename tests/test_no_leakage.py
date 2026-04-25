"""Tests for walk-forward leakage protections."""

from __future__ import annotations

import pytest

np = pytest.importorskip("numpy")


def test_scaler_refits_per_walk_forward_fold():
    pytest.importorskip("pandas")
    from openbb_forecast.data.preprocessing import SafeScaler
    from openbb_forecast.data.validation import WalkForwardSplit

    data = np.arange(100, dtype=np.float64).reshape(-1, 1)
    splitter = WalkForwardSplit(n_splits=3, min_train_ratio=0.6)
    means = []
    stds = []

    for train_idx, _test_idx in splitter.split(len(data)):
        scaler = SafeScaler(method="standard").fit(data[train_idx])
        means.append(float(scaler._mean[0]))
        stds.append(float(scaler._std[0]))

    assert len(set(means)) == len(means)
    assert len(set(stds)) == len(stds)
