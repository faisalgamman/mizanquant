"""Tests for feature_store.py"""
import pytest
import numpy as np
import pandas as pd
from pathlib import Path
import tempfile
from openbb_forecast.data.feature_store import (
    FeatureStore, FeatureSchema, FeatureSet, register_default_features
)

class TestFeatureStore:
    @pytest.fixture
    def store(self):
        with tempfile.TemporaryDirectory() as tmp:
            yield FeatureStore(cache_dir=Path(tmp)/"cache", registry_path=Path(tmp)/"reg.json")

    def test_register_feature(self, store):
        schema = FeatureSchema("momentum_5", dtype="float", description="5d momentum", lookback_window=5)
        store.register(schema)
        assert "momentum_5" in store.list_features()
        retrieved = store.get_schema("momentum_5")
        assert retrieved.dtype == "float"
        assert retrieved.lookback_window == 5

    def test_materialize_and_serve(self, store):
        schema = FeatureSchema("test_feat", dtype="float")
        store.register(schema)
        data = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0], name="test_feat")
        v = store.materialize("test_feat", data, version="v1")
        assert v.version == "v1"
        assert v.n_samples == 5
        assert len(v.hash_sha256) == 64

        result = store.get_features(["test_feat"])
        assert len(result) == 5
        assert result["test_feat"].iloc[0] == 1.0

    def test_point_in_time_filter(self, store):
        dates = pd.date_range("2024-01-01", periods=100, freq="D")
        data = pd.Series(np.arange(100), name="test_feat", index=dates)
        store.register(FeatureSchema("test_feat", dtype="float"))
        store.materialize("test_feat", data)

        result = store.get_features(["test_feat"], as_of="2024-01-10")
        assert len(result) <= 10  # up to Jan 10 inclusive

    def test_dependency_check(self, store):
        child = FeatureSchema("child", dtype="float", dependencies=["parent"])
        store.register(child)
        ok, missing = store.check_dependencies_satisfied("child")
        assert not ok
        assert "parent" in missing

        store.register(FeatureSchema("parent", dtype="float"))
        store.materialize("parent", pd.Series([1,2,3]))
        ok, missing = store.check_dependencies_satisfied("child")
        # parent is registered but child not materialized -> need parent materialized
        assert "parent" not in missing or ok

    def test_validation_range(self, store):
        schema = FeatureSchema("rsi", dtype="float", min_val=0.0, max_val=100.0)
        store.register(schema)
        # Should warn but not crash when values exceed range
        store.materialize("rsi", pd.Series([50, 60, 120]))

    def test_default_features_registration(self, store):
        register_default_features(store)
        assert len(store.list_features()) >= 8
        assert "returns" in store.list_features()
        assert "rsi_14" in store.list_features()

    def test_cache_invalidation(self, store):
        store.register(FeatureSchema("temp_feat", dtype="float"))
        store.materialize("temp_feat", pd.Series([1,2,3]))
        assert "temp_feat" in store._versions
        store.invalidate_cache("temp_feat")
        assert "temp_feat" not in store._versions

    def test_get_dependency_graph(self, store):
        store.register(FeatureSchema("A", dtype="float"))
        store.register(FeatureSchema("B", dtype="float", dependencies=["A"]))
        graph = store.get_dependency_graph()
        assert graph["B"] == ["A"]
        assert graph["A"] == []
