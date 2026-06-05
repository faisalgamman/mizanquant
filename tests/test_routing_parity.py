"""Route parity tests — verify dashboard endpoints exist after extraction (A5)."""
from __future__ import annotations

import os
import sys

import pytest
from fastapi.testclient import TestClient


# ── helpers ──────────────────────────────────────────────────────────────


def _get_ws_client():
    """Return TestClient for workspace_server app, suppressing import-time noise."""
    old_stdout = sys.stdout
    sys.stdout = open(os.devnull, "w")
    try:
        from app.workspace_server import app
    finally:
        sys.stdout.close()
        sys.stdout = old_stdout
    return TestClient(app)


# ── T2: dashboard API routes still reachable ─────────────────────────────


@pytest.mark.parametrize("route,expected_status", [
    ("/api/dashboard/performance-summary", 200),
    ("/api/dashboard/equity-curve", 200),
    ("/api/dashboard/strategies-stats", 200),
    ("/api/dashboard/options-flow", 200),
    ("/api/dashboard/whale-portfolio", 200),
    ("/api/dashboard/macro-calendar", 200),
])
def test_dashboard_api_routes_present(route, expected_status):
    """Every extracted dashboard route must respond (200 or at least not 404)."""
    client = _get_ws_client()
    resp = client.get(route)
    assert resp.status_code != 404, (
        f"Route {route} returned 404 — extraction may have dropped an endpoint"
    )


def test_dashboard_routes_not_duplicated_in_workspace_server():
    """Inline @app.get decorators for dashboard APIs must NOT exist in ws."""
    import ast
    from pathlib import Path

    ws_path = Path(__file__).resolve().parent.parent / "app" / "workspace_server.py"
    src = ws_path.read_text(encoding="utf-8")

    for ep in [
        "dashboard_performance_summary",
        "dashboard_equity_curve",
        "dashboard_strategies_stats",
        "dashboard_options_flow",
        "dashboard_whale_portfolio",
        "dashboard_macro_calendar",
    ]:
        assert ep not in src, (
            f"Function '{ep}' still defined in workspace_server.py — move to dashboard_api.py"
        )


def test_dashboard_api_router_importable():
    """The extracted router module must be importable without error."""
    from app.api.dashboard_api import router
    assert router is not None
