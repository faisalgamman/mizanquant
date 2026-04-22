"""Tests for cached reference-data refresh behavior."""

from __future__ import annotations

import json
import uuid
from datetime import timedelta
from pathlib import Path

from app.services import reference_data as rd


def test_assets_cache_refreshes_when_stale(monkeypatch):
    artifacts_dir = Path.cwd() / "test_artifacts"
    artifacts_dir.mkdir(exist_ok=True)
    cache_file = artifacts_dir / f"tradable_{uuid.uuid4().hex}.json"
    stale_at = (rd._utc_now() - timedelta(days=2)).isoformat()
    cache_file.write_text(json.dumps({"updated_at": stale_at, "symbols": ["OLD"]}), encoding="utf-8")

    monkeypatch.setattr(rd, "TRADABLE_SYMBOLS_FILE", cache_file)
    monkeypatch.setattr(rd, "refresh_tradable_assets", lambda: {"AAPL", "MSFT"})

    assert rd.get_tradable_symbols() == {"AAPL", "MSFT"}
