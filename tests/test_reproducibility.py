"""Tests for reproducibility utilities."""
from __future__ import annotations

import numpy as np
import pytest

from app.core.reproducibility import (
    code_hash,
    config_hash,
    data_hash,
    git_sha,
    is_dirty,
    reproducibility_seal,
)


class TestGitSha:
    def test_returns_string(self):
        sha = git_sha()
        assert isinstance(sha, str)
        assert len(sha) > 0

    def test_short_returns_7_chars(self):
        sha = git_sha(short=True)
        assert len(sha) == 7

    def test_is_dirty_returns_bool(self):
        assert isinstance(is_dirty(), bool)


class TestDataHash:
    def test_deterministic(self):
        a = np.array([1.0, 2.0, 3.0])
        h1 = data_hash(a)
        h2 = data_hash(a)
        assert h1 == h2

    def test_different_for_different_data(self):
        a = np.array([1.0, 2.0, 3.0])
        b = np.array([1.0, 2.0, 4.0])
        assert data_hash(a) != data_hash(b)

    def test_returns_16_char_hex(self):
        h = data_hash(np.array([1.0]))
        assert len(h) == 16
        assert all(c in "0123456789abcdef" for c in h)


class TestCodeHash:
    def test_returns_string(self):
        h = code_hash()
        assert isinstance(h, str)
        assert len(h) == 16

    def test_cached(self):
        h1 = code_hash()
        h2 = code_hash()
        assert h1 == h2


class TestConfigHash:
    def test_deterministic(self):
        cfg = {"a": 1, "b": 2}
        h1 = config_hash(cfg)
        h2 = config_hash(cfg)
        assert h1 == h2

    def test_sorted_keys(self):
        assert config_hash({"a": 1, "b": 2}) == config_hash({"b": 2, "a": 1})

    def test_different_configs_differ(self):
        assert config_hash({"a": 1}) != config_hash({"a": 2})


class TestReproducibilitySeal:
    def test_contains_all_keys(self):
        prices = np.array([100.0, 101.0, 102.0])
        seal = reproducibility_seal(prices=prices)
        assert set(seal.keys()) == {"git_sha", "is_dirty", "data_hash", "code_hash", "config_hash"}

    def test_data_hash_missing_when_no_prices(self):
        seal = reproducibility_seal()
        assert seal["data_hash"] == "no_data"

    def test_seal_is_serializable(self):
        seal = reproducibility_seal()
        import json
        dumped = json.dumps(seal)
        assert isinstance(dumped, str)
