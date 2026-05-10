"""Reproducibility utilities — hashes for git SHA, data, code, and config.

Every backtest and training run emits a "reproducibility seal" containing
these four hashes so results can be pinpointed to an exact code + data
snapshot.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional


def _git_root() -> Optional[Path]:
    """Return the repository root, or None if we are not in a git repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=Path(__file__).resolve().parent,
        )
        if result.returncode == 0:
            root = result.stdout.strip()
            return Path(root) if root else None
    except Exception:
        pass
    return None


GIT_ROOT = _git_root()


# ── Git SHA ──


def git_sha(short: bool = False) -> str:
    """Return the current git commit SHA.

    Returns "unknown" if git is unavailable or we are not in a repo.
    """
    if GIT_ROOT is None:
        return "unknown"
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=str(GIT_ROOT),
        )
        if result.returncode != 0:
            return "unknown"
        sha = result.stdout.strip()
        return sha[:7] if short else sha
    except Exception:
        return "unknown"


def git_diff() -> str:
    """Return the staged + unstaged diff as a string (for dirty-repo detection).

    Returns an empty string if the working tree is clean or git is unavailable.
    """
    if GIT_ROOT is None:
        return ""
    try:
        result = subprocess.run(
            ["git", "diff", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=str(GIT_ROOT),
        )
        return result.stdout if result.returncode == 0 else ""
    except Exception:
        return ""


def is_dirty() -> bool:
    """True when the working tree has uncommitted changes."""
    diff = git_diff()
    return len(diff) > 0 if diff else False


# ── Data hash ──


def data_hash(prices) -> str:
    """Deterministic hash of a price DataFrame/array.

    Uses the byte representation of the underlying NumPy array so the
    hash is stable across machines given the same data.
    """
    import numpy as np

    arr = np.asarray(prices, dtype=np.float64)
    return hashlib.sha256(arr.tobytes()).hexdigest()[:16]


# ── Code hash ──


def _walk_py_files(root: Path) -> list[Path]:
    """Recursively collect all .py files under *root* (max 5 levels)."""
    files = []
    try:
        for i, entry in enumerate(root.rglob("*.py")):
            if i > 5000:
                break
            files.append(entry)
    except Exception:
        pass
    return sorted(files)


_CODE_HASH_CACHE: Optional[str] = None


def code_hash(invalidate_cache: bool = False) -> str:
    """SHA-256 of all Python source files under the repo root.

    Results are cached per-process because the source tree does not
    change during a run.  Pass ``invalidate_cache=True`` to re-read.
    """
    global _CODE_HASH_CACHE
    if _CODE_HASH_CACHE is not None and not invalidate_cache:
        return _CODE_HASH_CACHE

    root = GIT_ROOT or Path(__file__).resolve().parent.parent.parent
    hasher = hashlib.sha256()
    for py_file in _walk_py_files(root):
        try:
            hasher.update(py_file.read_bytes())
        except Exception:
            pass
    _CODE_HASH_CACHE = hasher.hexdigest()[:16]
    return _CODE_HASH_CACHE


# ── Config hash ──


def config_hash(config: Optional[dict[str, Any]] = None) -> str:
    """SHA-256 of a config dict (sorted keys for stability).

    If *config* is None, tries to hash the current pydantic settings.
    """
    if config is None:
        try:
            from app.config import settings as _s
            config = _s.model_dump() if hasattr(_s, "model_dump") else _s.dict()
        except Exception:
            config = {}
    raw = json.dumps(config, sort_keys=True, default=str).encode()
    return hashlib.sha256(raw).hexdigest()[:16]


# ── Composite seal ──


def reproducibility_seal(prices=None, config: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Return a full reproducibility seal dict.

    Example::

        {
            "git_sha": "a1b2c3d",
            "is_dirty": False,
            "data_hash": "e5f6a7b8c9d0e1f2",
            "code_hash": "a1b2c3d4e5f6a7b8",
            "config_hash": "c9d0e1f2a1b2c3d4",
        }
    """
    return {
        "git_sha": git_sha(short=True),
        "is_dirty": is_dirty(),
        "data_hash": data_hash(prices) if prices is not None else "no_data",
        "code_hash": code_hash(),
        "config_hash": config_hash(config),
    }
