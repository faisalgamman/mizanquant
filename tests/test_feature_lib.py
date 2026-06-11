"""Tests for the shared feature library (feature_lib.py)."""
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from app.services.feature_lib import FEATURE_ORDER, compute_features


def _utcnow():
    return datetime.now(timezone.utc)


def _make_df(n_days=300, trend=0.0005, noise=0.015, seed=42):
    rng = np.random.default_rng(seed)
    close = 100.0
    closes = []
    for _ in range(n_days):
        ret = trend + rng.normal(0, noise)
        close = close * (1 + ret)
        closes.append(close)
    dates = pd.date_range(end=datetime.now(), periods=n_days, freq="D")
    closes = np.array(closes)
    return pd.DataFrame({
        "open": closes, "high": closes * 1.01, "low": closes * 0.99,
        "close": closes, "volume": np.full(n_days, 1_000_000.0),
    }, index=dates)


def test_returns_all_feature_order_keys():
    """compute_features returns ALL 15 keys from FEATURE_ORDER."""
    df = _make_df(300)
    spy = _make_df(300, seed=99)
    asof = df.index[-1]
    result = compute_features(df, spy, asof)
    assert result is not None
    for key in FEATURE_ORDER:
        assert key in result, f"Missing key: {key}"
    # Extra keys not in FEATURE_ORDER (none expected, but verify shape)
    extra = set(result.keys()) - set(FEATURE_ORDER)
    assert not extra, f"Unexpected extra keys: {extra}"


def test_tz_aware_asof_with_naive_index():
    """Aware asof vs naive price index must NOT fail (regression for tz bug)."""
    n = 200
    naive_idx = pd.DatetimeIndex(pd.date_range(end=datetime.now(), periods=n, freq="D"))
    closes = np.linspace(100, 120, n)
    df = pd.DataFrame({
        "open": closes, "high": closes * 1.01, "low": closes * 0.99,
        "close": closes, "volume": np.full(n, 1_000_000.0),
    }, index=naive_idx)
    asof = _utcnow()  # tz-aware
    result = compute_features(df, df, asof)
    assert result is not None
    assert "rsi14" in result


def test_less_than_60_bars_returns_none():
    """Fewer than 60 bars → None."""
    df = _make_df(50)
    asof = df.index[-1]
    result = compute_features(df, df, asof)
    assert result is None


def test_no_spy_still_works():
    """spy_df=None returns valid features with default RS values."""
    df = _make_df(300)
    asof = df.index[-1]
    result = compute_features(df, None, asof)
    assert result is not None
    assert result["rs_spy_20"] == 1.0
    assert result["rs_spy_63"] == 1.0
