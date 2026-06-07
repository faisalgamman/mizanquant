"""Pins the /api/v1/trade/plan JSON-serialization fix.

The endpoint embeds the strategy `details` dict, which can carry numpy scalars
(notably numpy.bool_). FastAPI's jsonable_encoder cannot serialize those and 500s
(seen 4x on CLX in the 2026-06-07 prod log). `_to_jsonable` coerces numpy/pandas
scalars to JSON-native types so the response serializes.
"""
from __future__ import annotations

import json

import pytest

np = pytest.importorskip("numpy")

from app.api.v1.scoring import _to_jsonable  # noqa: E402


def test_numpy_scalars_become_native():
    assert _to_jsonable(np.bool_(True)) is True
    assert _to_jsonable(np.bool_(False)) is False
    assert isinstance(_to_jsonable(np.int64(7)), int) and _to_jsonable(np.int64(7)) == 7
    out = _to_jsonable(np.float64(1.5))
    assert isinstance(out, float) and out == 1.5


def test_nan_and_inf_become_none():
    assert _to_jsonable(np.float64("nan")) is None
    assert _to_jsonable(float("inf")) is None
    assert _to_jsonable(float("nan")) is None


def test_ndarray_and_nested_containers():
    assert _to_jsonable(np.array([1, 2, 3])) == [1, 2, 3]
    nested = {"a": np.bool_(True), "b": [np.int64(1), {"c": np.float64(2.0)}]}
    assert _to_jsonable(nested) == {"a": True, "b": [1, {"c": 2.0}]}


def test_plan_with_numpy_bool_details_serializes():
    # Mirrors the real bug: a plan dict whose `details` carries a numpy.bool_.
    plan = {
        "entry": np.float64(100.0),
        "stop_loss": 85.0,
        "details": {"ema_bullish": np.bool_(True), "rsi": np.float64(55.3)},
        "symbol": "CLX",
    }
    clean = _to_jsonable(plan)
    # json.dumps (no default=) must succeed → proves it is JSON-native.
    json.dumps(clean)
    assert clean["details"]["ema_bullish"] is True
    assert clean["entry"] == 100.0


def test_native_values_pass_through():
    assert _to_jsonable({"x": 1, "y": "s", "z": None, "b": True}) == {
        "x": 1, "y": "s", "z": None, "b": True,
    }
