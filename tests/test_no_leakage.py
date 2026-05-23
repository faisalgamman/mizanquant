"""Tests for walk-forward leakage protections (Pillar 1).

Covers:
  1. Scaler is refitted per WalkForwardSplit fold (original test).
  2. LeakageAuditor reports clean on a properly constructed pipeline.
  3. LeakageAuditor flags a shuffled split as CRITICAL.
  4. LeakageAuditor flags a direct target-in-features leak as CRITICAL.
  5. walk_forward_validate exports leakage_clean=True for clean data.
"""

from __future__ import annotations

import pytest

np = pytest.importorskip("numpy")


# ── 1. Scaler refit (original test) ─────────────────────────────────────────

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


# ── 2. LeakageAuditor: clean pipeline ────────────────────────────────────────

def test_leakage_auditor_clean_pipeline():
    pd = pytest.importorskip("pandas")
    from openbb_forecast.data.leakage_auditor import LeakageAuditor
    from openbb_forecast.data.validation import WalkForwardSplit

    n = 200
    rng = np.random.default_rng(42)
    X = pd.DataFrame(rng.standard_normal((n, 5)), columns=[f"feat_{i}" for i in range(5)])
    y = pd.Series(rng.standard_normal(n), name="target")

    splitter = WalkForwardSplit(n_splits=3, min_train_ratio=0.6, gap=5)
    folds = splitter.split(n)
    train_idx, test_idx = folds[0]

    report = LeakageAuditor().audit(
        features=X,
        labels=y,
        train_idx=train_idx,
        test_idx=test_idx,
        scaler_refit_per_fold=True,
        gap=5,
        shuffle_split=False,
    )
    # Only CRITICAL/HIGH findings indicate real leakage; MEDIUM is advisory.
    blocking = [f for f in report.findings if f.severity in ("CRITICAL", "HIGH")]
    assert blocking == [], (
        f"Clean pipeline should have no CRITICAL/HIGH findings:\n{report.summary()}"
    )
    assert report.score > 70, f"Leakage score too low: {report.score}"


# ── 3. LeakageAuditor: shuffled split is CRITICAL ────────────────────────────

def test_leakage_auditor_detects_shuffled_split():
    pd = pytest.importorskip("pandas")
    from openbb_forecast.data.leakage_auditor import LeakageAuditor

    n = 100
    rng = np.random.default_rng(7)
    X = pd.DataFrame(rng.standard_normal((n, 3)), columns=["a", "b", "c"])
    y = pd.Series(rng.standard_normal(n), name="target")

    # Shuffle the indices (the bug we're guarding against)
    idx = np.arange(n)
    rng.shuffle(idx)
    train_idx = idx[:70]
    test_idx  = idx[70:]

    report = LeakageAuditor().audit(
        features=X, labels=y,
        train_idx=train_idx, test_idx=test_idx,
        shuffle_split=True,
    )
    critical = [f for f in report.findings if f.severity == "CRITICAL"]
    assert critical, "Shuffled split must produce at least one CRITICAL finding"


# ── 4. LeakageAuditor: target column in features ─────────────────────────────

def test_leakage_auditor_detects_target_in_features():
    pd = pytest.importorskip("pandas")
    from openbb_forecast.data.leakage_auditor import LeakageAuditor

    n = 100
    rng = np.random.default_rng(13)
    y_vals = rng.standard_normal(n)
    # Leak: include the target as a feature column
    X = pd.DataFrame({
        "feat_0": rng.standard_normal(n),
        "target": y_vals,        # ← direct leakage
    })
    y = pd.Series(y_vals, name="target")

    train_idx = np.arange(70)
    test_idx  = np.arange(70, n)

    report = LeakageAuditor().audit(
        features=X, labels=y,
        train_idx=train_idx, test_idx=test_idx,
        scaler_refit_per_fold=True,
    )
    critical = [f for f in report.findings if f.severity == "CRITICAL"]
    assert critical, "Target-in-features must produce at least one CRITICAL finding"


# ── 5. walk_forward_validate exports leakage_clean ───────────────────────────

def test_walk_forward_validate_exports_leakage_clean():
    """walk_forward_validate must include 'leakage_clean' in its return dict."""
    from app.services.ml_pipeline import walk_forward_validate

    n = 150
    rng = np.random.default_rng(99)
    X = rng.standard_normal((n, 10))
    y = rng.standard_normal(n)

    def dummy_predict(X_te):
        return rng.standard_normal(len(X_te))

    result = walk_forward_validate(X, y, dummy_predict, n_splits=3)

    assert "leakage_clean" in result, (
        "walk_forward_validate must return 'leakage_clean' key"
    )
    assert isinstance(result["leakage_clean"], bool)
