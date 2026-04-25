"""Helpers for model artifact layout and latest-version pointers."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

MODELS_DIR = Path("models")


def default_version() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def model_dir(name: str) -> Path:
    path = MODELS_DIR / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def artifact_path(name: str, version: str, suffix: str) -> Path:
    return model_dir(name) / f"{version}{suffix}"


def latest_pointer_path(name: str) -> Path:
    return model_dir(name) / "latest.txt"


def write_latest(name: str, version: str) -> None:
    latest_pointer_path(name).write_text(version, encoding="utf-8")


def resolve_latest(name: str, suffix: str) -> Path:
    pointer = latest_pointer_path(name)
    if pointer.exists():
        version = pointer.read_text(encoding="utf-8").strip()
        candidate = artifact_path(name, version, suffix)
        if candidate.exists():
            return candidate
    candidates = sorted(model_dir(name).glob(f"*{suffix}"))
    if not candidates:
        raise FileNotFoundError(f"No persisted artifact found for model '{name}'")
    return candidates[-1]


def write_metadata(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
