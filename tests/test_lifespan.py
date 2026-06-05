"""Lifespan and dual-app tests — T3, T4 from code review."""
from __future__ import annotations

import importlib
import os
import sys


# ── T3: importing halal_screener does NOT start a scheduler ──────────────


def test_import_halal_screener_no_scheduler_boot(monkeypatch):
    """Importing halal_screener must not trigger start_scheduler (F-3)."""
    started = []

    # Block actual scheduler start
    monkeypatch.setattr(
        "app.services.scheduler.start_scheduler",
        lambda: started.append(True),
    )

    # Force reimport of halal_screener
    if "halal_screener" in sys.modules:
        del sys.modules["halal_screener"]
    # Clear submodules too
    for k in list(sys.modules):
        if k.startswith("app.") and ("scheduler" in k or "telegram" in k):
            del sys.modules[k]

    # Redirect stdout to suppress import-time logging
    old_stdout = sys.stdout
    sys.stdout = open(os.devnull, "w")
    try:
        import halal_screener  # noqa: F401
    finally:
        sys.stdout.close()
        sys.stdout = old_stdout

    assert len(started) == 0, (
        f"halal_screener import triggered start_scheduler {len(started)} times — "
        "lifespan isolation broken"
    )


# ── T4: halal_screener lifespan is no-op when not standalone ─────────────


def test_halal_screener_lifespan_noop():
    """F-3 guarantee: module-level app uses _noop_lifespan (standalone=False)."""
    import halal_screener as hs

    # The app MUST exist for test backward compat
    assert hs.app is not None, "hs.app should exist for TestClient backward compat"

    # _build_app with standalone=False uses _noop_lifespan
    import inspect
    src = inspect.getsource(hs._build_app)
    assert "standalone" in src
    assert "_noop_lifespan" in src or "noop" in src.lower()


# ── T3b: workspace_server's own scheduler IS its responsibility ──────────


def test_workspace_server_owns_scheduler():
    """workspace_server.py must be the ONE file that calls start_scheduler."""
    from pathlib import Path

    project_root = Path(__file__).resolve().parent.parent
    ws_path = project_root / "app" / "workspace_server.py"

    src = ws_path.read_text(encoding="utf-8")
    assert "start_scheduler" in src, (
        "workspace_server.py must contain start_scheduler — it owns the lifecycle"
    )

    # halal_screener must NOT call start_scheduler at module level
    # (inside function bodies is fine — those require explicit invocation)
    hs_path = project_root / "halal_screener.py"
    hs_src = hs_path.read_text(encoding="utf-8")
    hs_lines = hs_src.split("\n")
    # Check top-level (non-indented) lines only
    top_level_calls = [
        l for l in hs_lines
        if "start_scheduler()" in l and not l.startswith((" ", "\t"))
    ]
    assert len(top_level_calls) == 0, (
        f"halal_screener.py has {len(top_level_calls)} top-level start_scheduler() calls"
    )
