"""Tests for the probability-calibration primitives."""

from __future__ import annotations

import numpy as np

from app.services.calibration import (
    brier_score,
    calibration_report,
    expected_calibration_error,
    reliability_curve,
)


def test_perfectly_calibrated_has_low_ece():
    # outcome ~ Bernoulli(predicted) → the prediction is, by construction, calibrated.
    rng = np.random.default_rng(0)
    p = rng.uniform(0, 1, 5000)
    y = (rng.uniform(0, 1, 5000) < p).astype(float)
    ece = expected_calibration_error(p, y, n_bins=10)
    assert ece is not None and ece < 0.06
    rep = calibration_report(p, y)
    assert abs(rep["mean_pred"] - rep["base_rate"]) < 0.03   # unbiased on average


def test_overconfident_flagged():
    # Always claims 90% but the truth is a coin flip → big calibration gap.
    rng = np.random.default_rng(1)
    p = np.full(2000, 0.9)
    y = (rng.uniform(0, 1, 2000) < 0.5).astype(float)
    assert expected_calibration_error(p, y) > 0.3
    assert brier_score(p, y) > 0.2


def test_reliability_curve_bins_and_counts():
    curve = reliability_curve([0.05, 0.15, 0.15, 0.95], [0, 0, 1, 1], n_bins=10)
    b = next(x for x in curve if x["lo"] <= 0.15 < x["hi"])
    assert b["n"] == 2 and b["mean_actual"] == 0.5


def test_empty_and_report_shape():
    assert brier_score([], []) is None
    assert expected_calibration_error([], []) is None
    rep = calibration_report([0.6, 0.4], [1, 0])
    assert rep["n"] == 2
    for k in ("n", "base_rate", "mean_pred", "brier", "ece", "curve"):
        assert k in rep


def test_mismatched_lengths_are_truncated():
    # defensive: unequal arrays shouldn't raise
    assert calibration_report([0.5, 0.6, 0.7], [1, 0])["n"] == 2
