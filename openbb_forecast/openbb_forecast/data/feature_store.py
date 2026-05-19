"""Versioned feature store with point-in-time correctness.

Features:
  - Immutable feature definitions with schema validation
  - Dependency tracking (feature A depends on feature B)
  - Hash-based cache invalidation
  - Point-in-time feature serving (no future data)
  - Materialization with versioning

Usage:
    store = FeatureStore()
    store.register("momentum_5", schema={"type": "float", "min": -1.0, "max": 1.0})
    store.materialize("momentum_5", df, version="v1")
    features = store.get_features(["momentum_5", "volatility_10"], as_of="2024-06-15")
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd

logger = logging.getLogger("feature_store")


# ------------------------------------------------------------------
# Core domain objects
# ------------------------------------------------------------------

@dataclass
class FeatureSchema:
    """Definition of a single feature with its schema."""
    name: str
    dtype: Literal["float", "int", "bool", "category", "datetime"]
    description: str = ""
    min_val: float | None = None
    max_val: float | None = None
    categories: list[str] | None = None  # for category dtype
    lookback_window: int = 0  # how many past periods needed
    category: str = "uncategorized"  # e.g., "momentum", "volatility", "volume", "trend"
    dependencies: list[str] = field(default_factory=list)  # other feature names this depends on
    source_table: str = "ohlcv"  # which raw table this derives from
    is_ratio: bool = False  # whether the feature is a ratio (needs special handling)
    tags: list[str] = field(default_factory=list)


@dataclass
class FeatureVersion:
    """A specific version of a materialized feature."""
    feature_name: str
    version: str  # semantic version like "v1.0.0" or hash
    created_at: str  # ISO timestamp
    n_samples: int
    hash_sha256: str  # content hash for cache validation
    stats: dict = field(default_factory=dict)  # mean, std, min, max, etc.
    metadata: dict = field(default_factory=dict)


@dataclass
class FeatureSet:
    """A named collection of features."""
    name: str  # e.g., "momentum_suite", "volume_indicators"
    features: list[str]
    description: str = ""


# ------------------------------------------------------------------
# Feature Store
# ------------------------------------------------------------------

class FeatureStore:
    """Central feature store managing feature schemas, versions, and serving.

    Maintains a registry of feature schemas and materialized feature data
    with point-in-time correct serving.

    Args:
        cache_dir: Directory for cached materializations.
        registry_path: Path to the schema registry JSON file.
    """

    def __init__(
        self,
        cache_dir: str | Path | None = None,
        registry_path: str | Path | None = None,
    ):
        self._cache_dir = Path(cache_dir or "feature_store_cache")
        self._registry_path = Path(registry_path or "feature_registry.json")
        self._cache_dir.mkdir(parents=True, exist_ok=True)

        # In-memory caches
        self._schemas: dict[str, FeatureSchema] = {}
        self._versions: dict[str, list[FeatureVersion]] = {}  # feature_name -> versions
        self._data_cache: dict[str, pd.DataFrame] = {}  # version_hash -> DataFrame (lazy)

        # Load persisted registry
        self._load_registry()

    # ------------------------------------------------------------------
    # Registry management
    # ------------------------------------------------------------------

    def register(self, feature: FeatureSchema) -> None:
        """Register a new feature schema or update existing."""
        existing = self._schemas.get(feature.name)
        if existing is not None:
            logger.info("Updating existing feature schema for '%s'", feature.name)
        self._schemas[feature.name] = feature
        self._save_registry()
        logger.info("Registered feature: %s (category=%s, lookback=%d)", feature.name, feature.category, feature.lookback_window)

    def get_schema(self, feature_name: str) -> FeatureSchema | None:
        """Retrieve a feature's schema."""
        return self._schemas.get(feature_name)

    def list_features(self, category: str | None = None) -> list[str]:
        """List all registered features, optionally filtered by category."""
        if category:
            return [name for name, s in self._schemas.items() if s.category == category]
        return sorted(self._schemas.keys())

    def get_dependency_graph(self) -> dict[str, list[str]]:
        """Return {feature_name: [dependencies]} for all features."""
        return {name: list(s.dependencies) for name, s in self._schemas.items()}

    # ------------------------------------------------------------------
    # Materialization
    # ------------------------------------------------------------------

    def materialize(
        self,
        feature_name: str,
        data: pd.DataFrame | pd.Series,
        version: str | None = None,
        timestamp_index: pd.DatetimeIndex | None = None,
    ) -> FeatureVersion:
        """Materialize (persist) a feature with versioning.

        Args:
            feature_name: Name matching a registered schema.
            data: The feature data (DataFrame or Series).
            version: Version string (auto-generated if None).
            timestamp_index: Time index for point-in-time serving.

        Returns:
            FeatureVersion with hash and stats.
        """
        if feature_name not in self._schemas:
            raise KeyError(f"Feature '{feature_name}' not registered. Call register() first.")

        schema = self._schemas[feature_name]

        # Convert to Series
        if isinstance(data, pd.DataFrame):
            if feature_name in data.columns:
                series = data[feature_name]
            else:
                series = data.iloc[:, 0]
        else:
            series = pd.Series(data)

        # Validate schema
        self._validate_against_schema(series, schema)

        # Compute stats
        stats = {
            "mean": float(series.mean()),
            "std": float(series.std()),
            "min": float(series.min()),
            "max": float(series.max()) if not schema.is_ratio else min(float(series.max()), float("inf")),
            "n_missing": int(series.isna().sum()),
            "n_total": len(series),
        }

        # Compute content hash
        content_hash = self._compute_hash(series)

        # Generate version
        if version is None:
            version = f"v_{content_hash[:8]}"

        fv = FeatureVersion(
            feature_name=feature_name,
            version=version,
            created_at=datetime.now(timezone.utc).isoformat(),
            n_samples=len(series),
            hash_sha256=content_hash,
            stats=stats,
            metadata={"schema_dtype": schema.dtype},
        )

        # Save to disk
        cache_file = self._cache_dir / f"{feature_name}_{version}.parquet"
        series.to_frame(name=feature_name).to_parquet(cache_file)

        # Track version
        if feature_name not in self._versions:
            self._versions[feature_name] = []
        self._versions[feature_name].append(fv)
        self._save_registry()

        logger.info("Materialized %s v%s: %d samples, hash=%s", feature_name, version, len(series), content_hash[:8])
        return fv

    def get_features(
        self,
        feature_names: list[str],
        as_of: str | pd.Timestamp | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        """Get features with point-in-time correctness.

        Args:
            feature_names: List of feature names to retrieve.
            as_of: Point-in-time cutoff. No data after this timestamp is returned.
            start_date: Optional start date filter.
            end_date: Optional end date filter.

        Returns:
            DataFrame with requested features.
        """
        if not feature_names:
            return pd.DataFrame()

        # Gather all features
        dfs = []
        seen_hashes = set()

        for fname in feature_names:
            versions = self._versions.get(fname, [])
            if not versions:
                raise KeyError(f"Feature '{fname}' has no materialized versions. Materialize first.")

            # Get latest version
            latest = versions[-1]
            cache_file = self._cache_dir / f"{fname}_{latest.version}.parquet"

            if not cache_file.exists():
                raise FileNotFoundError(f"Cached data missing for '{fname}' v{latest.version}. "
                                       f"Re-run materialize().")

            df = pd.read_parquet(cache_file)
            dfs.append(df)

        if not dfs:
            return pd.DataFrame()

        result = pd.concat(dfs, axis=1)

        # Apply point-in-time filter
        if as_of is not None:
            as_of_ts = pd.Timestamp(as_of) if isinstance(as_of, str) else as_of
            if isinstance(result.index, pd.DatetimeIndex):
                result = result.loc[result.index <= as_of_ts]

        # Apply date range
        if isinstance(result.index, pd.DatetimeIndex):
            if start_date is not None:
                result = result.loc[result.index >= pd.Timestamp(start_date)]
            if end_date is not None:
                result = result.loc[result.index <= pd.Timestamp(end_date)]

        return result

    def check_dependencies_satisfied(self, feature_name: str) -> tuple[bool, list[str]]:
        """Check if a feature's dependencies are all materialized.

        Returns:
            (all_satisfied, [missing_dependency_names])
        """
        schema = self._schemas.get(feature_name)
        if schema is None:
            return False, []

        missing = [dep for dep in schema.dependencies if dep not in self._versions]
        return len(missing) == 0, missing

    def invalidate_cache(self, feature_name: str | None = None):
        """Invalidate cached data for a feature (or all if None).

        Removes parquet files and version records from memory.
        """
        if feature_name:
            self._data_cache.pop(feature_name, None)
            # Remove cache files
            for f in self._cache_dir.glob(f"{feature_name}_*.parquet"):
                f.unlink()
            self._versions.pop(feature_name, None)
            logger.info("Invalidated cache for '%s'", feature_name)
        else:
            self._data_cache.clear()
            for f in self._cache_dir.glob("*.parquet"):
                f.unlink()
            self._versions.clear()
            logger.info("Invalidated all feature caches (%d files)", len(list(self._cache_dir.glob("*.parquet"))))

    # ------------------------------------------------------------------
    # Pre-defined feature sets
    # ------------------------------------------------------------------

    @classmethod
    def create_momentum_suite(cls) -> FeatureSet:
        """Standard momentum features."""
        return FeatureSet(
            name="momentum_suite",
            features=["momentum_5", "momentum_10", "momentum_20", "rsi_14", "macd", "macd_signal"],
            description="Price momentum indicators across multiple timeframes",
        )

    @classmethod
    def create_volatility_suite(cls) -> FeatureSet:
        """Standard volatility features."""
        return FeatureSet(
            name="volatility_suite",
            features=["volatility_5", "volatility_10", "volatility_20", "returns"],
            description="Rolling volatility and return features",
        )

    @classmethod
    def create_volume_suite(cls) -> FeatureSet:
        """Standard volume features."""
        return FeatureSet(
            name="volume_suite",
            features=["volume_ratio"],
            description="Volume-based indicators",
        )

    @classmethod
    def create_all_features(cls) -> FeatureSet:
        """Combined default feature set matching create_features()."""
        ms = cls.create_momentum_suite()
        vs = cls.create_volatility_suite()
        vol = cls.create_volume_suite()
        return FeatureSet(
            name="all_features",
            features=ms.features + vs.features + vol.features + ["high_low_range", "open_close_range"],
            description="All standard OHLCV-derived features",
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _validate_against_schema(self, series: pd.Series, schema: FeatureSchema):
        """Validate a series against its registered schema."""
        # Type check
        expected_dtype = {"float": np.float64, "int": np.int64, "bool": np.bool_,
                         "category": object, "datetime": "datetime64[ns]"}.get(schema.dtype)
        if expected_dtype is not None and expected_dtype != "datetime64[ns]":
            if not pd.api.types.is_numeric_dtype(series) and schema.dtype in ("float", "int"):
                raise TypeError(f"Feature '{schema.name}': expected {schema.dtype}, got {series.dtype}")

        # Range check
        if schema.min_val is not None and series.min() < schema.min_val:
            logger.warning("Feature '%s': %d values below min=%.4f (min=%.4f)",
                          schema.name, (series < schema.min_val).sum(), schema.min_val, series.min())
        if schema.max_val is not None and series.max() > schema.max_val:
            logger.warning("Feature '%s': %d values above max=%.4f (max=%.4f)",
                          schema.name, (series > schema.max_val).sum(), schema.max_val, series.max())

    @staticmethod
    def _compute_hash(series: pd.Series) -> str:
        """Compute deterministic hash of a series."""
        # Use first 100 + last 100 + stats for hash (fast, avoids full scan)
        head = series.head(100).to_numpy()
        tail = series.tail(100).to_numpy()
        stats = np.array([series.mean(), series.std(), len(series)])
        data = np.concatenate([head, tail, stats])
        # Handle NaN
        data = np.nan_to_num(data, nan=0.0)
        return hashlib.sha256(data.tobytes()).hexdigest()

    def _load_registry(self):
        """Load persisted schemas and version history from disk."""
        if not self._registry_path.exists():
            logger.debug("No registry file at %s — starting fresh", self._registry_path)
            return

        try:
            with open(self._registry_path) as fh:
                data = json.load(fh)

            # Load schemas
            for name, sdata in data.get("schemas", {}).items():
                self._schemas[name] = FeatureSchema(**sdata)

            # Load versions
            for name, vlist in data.get("versions", {}).items():
                self._versions[name] = [FeatureVersion(**v) for v in vlist]

            logger.info("Loaded registry: %d schemas, %d versioned features",
                        len(self._schemas), len(self._versions))
        except Exception as exc:
            logger.warning("Failed to load registry: %s — starting fresh", exc)

    def _save_registry(self):
        """Persist schemas and version history to disk."""
        data = {
            "schemas": {name: {
                "name": s.name,
                "dtype": s.dtype,
                "description": s.description,
                "min_val": s.min_val,
                "max_val": s.max_val,
                "categories": s.categories,
                "lookback_window": s.lookback_window,
                "category": s.category,
                "dependencies": s.dependencies,
                "source_table": s.source_table,
                "is_ratio": s.is_ratio,
                "tags": s.tags,
            } for name, s in self._schemas.items()},
            "versions": {name: [{
                "feature_name": v.feature_name,
                "version": v.version,
                "created_at": v.created_at,
                "n_samples": v.n_samples,
                "hash_sha256": v.hash_sha256,
                "stats": v.stats,
                "metadata": v.metadata,
            } for v in vlist] for name, vlist in self._versions.items()},
        }

        with open(self._registry_path, "w") as fh:
            json.dump(data, fh, indent=2, default=str)

    def __repr__(self):
        n_features = len(self._schemas)
        n_materialized = len([f for f in self._schemas if f in self._versions])
        return f"FeatureStore({n_features} registered, {n_materialized} materialized)"


# ------------------------------------------------------------------
# Pre-configured default feature schemas
# ------------------------------------------------------------------

def register_default_features(store: FeatureStore) -> None:
    """Register the standard OHLCV-derived feature schemas."""
    defaults = {
        "returns": FeatureSchema("returns", dtype="float", category="momentum",
                                  description="Log return ln(close_t/close_{t-1})",
                                  lookback_window=1, source_table="ohlcv"),
        "volatility_5": FeatureSchema("volatility_5", dtype="float", category="volatility",
                                       description="5-period rolling std of returns",
                                       lookback_window=5, dependencies=["returns"]),
        "volatility_10": FeatureSchema("volatility_10", dtype="float", category="volatility",
                                        description="10-period rolling std of returns",
                                        lookback_window=10, dependencies=["returns"]),
        "volatility_20": FeatureSchema("volatility_20", dtype="float", category="volatility",
                                        description="20-period rolling std of returns",
                                        lookback_window=20, dependencies=["returns"]),
        "momentum_5": FeatureSchema("momentum_5", dtype="float", category="momentum",
                                     description="5-period price momentum",
                                     lookback_window=5, is_ratio=True),
        "momentum_10": FeatureSchema("momentum_10", dtype="float", category="momentum",
                                      description="10-period price momentum",
                                      lookback_window=10, is_ratio=True),
        "momentum_20": FeatureSchema("momentum_20", dtype="float", category="momentum",
                                      description="20-period price momentum",
                                      lookback_window=20, is_ratio=True),
        "rsi_14": FeatureSchema("rsi_14", dtype="float", category="momentum",
                                description="14-period Relative Strength Index",
                                lookback_window=14, min_val=0.0, max_val=100.0),
        "macd": FeatureSchema("macd", dtype="float", category="trend",
                               description="MACD line (EMA12 - EMA26)"),
        "macd_signal": FeatureSchema("macd_signal", dtype="float", category="trend",
                                      description="MACD signal line (9-period EMA of MACD)"),
        "volume_ratio": FeatureSchema("volume_ratio", dtype="float", category="volume",
                                       description="Volume / 20-period mean volume",
                                       min_val=0.0),
        "high_low_range": FeatureSchema("high_low_range", dtype="float", category="volatility",
                                         description="(high - low) / close",
                                         min_val=0.0, is_ratio=True),
        "open_close_range": FeatureSchema("open_close_range", dtype="float", category="momentum",
                                           description="(close - open) / open",
                                           is_ratio=True),
    }

    for name, schema in defaults.items():
        store.register(schema)
    logger.info("Registered %d default feature schemas", len(defaults))
