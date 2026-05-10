"""Tests for scheduler metrics module."""
from __future__ import annotations

from app.services.scheduler_metrics import SchedulerMetrics


def test_health_returns_defaults_when_no_cycles_recorded():
    m = SchedulerMetrics()
    h = m.health()
    assert h["total_runs"] == 0
    assert h["total_success"] == 0
    assert h["total_failures"] == 0
    assert h["success_rate_pct"] == 100.0
    assert h["uptime"] is None
    assert h["failing_cycles"] == []


def test_snapshot_includes_all_cycle_types():
    m = SchedulerMetrics()
    s = m.snapshot()
    assert "cycles" in s
    expected = {
        "pre_market", "market_scan", "post_market", "signals_scan",
        "optimizer", "train_models", "signal_audit", "reference_data",
    }
    assert set(s["cycles"].keys()) == expected


def test_record_cycle_tracks_success():
    m = SchedulerMetrics()
    m.record_cycle_start("pre_market")
    m.record_cycle_end("pre_market", success=True)
    c = m.snapshot()["cycles"]["pre_market"]
    assert c["total_runs"] == 1
    assert c["total_success"] == 1
    assert c["total_failures"] == 0
    assert c["last_success"] is True
    assert c["last_start"] is not None
    assert c["last_end"] is not None
    assert c["last_duration_s"] is not None


def test_record_cycle_tracks_failure():
    m = SchedulerMetrics()
    m.record_cycle_start("market_scan")
    m.record_cycle_end("market_scan", success=False)
    c = m.snapshot()["cycles"]["market_scan"]
    assert c["total_runs"] == 1
    assert c["total_success"] == 0
    assert c["total_failures"] == 1
    assert c["last_success"] is False


def test_record_cycle_stores_error_message():
    m = SchedulerMetrics()
    m.record_cycle_start("post_market")
    m.record_cycle_end("post_market", success=False, error="Connection refused")
    c = m.snapshot()["cycles"]["post_market"]
    assert c["last_error"] == "Connection refused"


def test_record_cycle_unknown_cycle_is_noop():
    m = SchedulerMetrics()
    m.record_cycle_start("nonexistent")
    m.record_cycle_end("nonexistent", success=True)
    s = m.snapshot()
    assert "nonexistent" not in s["cycles"]


def test_lifecycle_mark_started_stopped():
    m = SchedulerMetrics()
    assert m.snapshot()["uptime"] is None
    m.mark_started()
    assert m.snapshot()["uptime"] is not None
    m.mark_stopped()
    assert m.snapshot()["uptime"] is None


def test_recent_errors_ring_buffer():
    m = SchedulerMetrics()
    for i in range(60):
        m.record_cycle_start("market_scan")
        m.record_cycle_end("market_scan", success=False, error=f"error_{i}")
    assert len(m.snapshot()["recent_errors"]) == 50
    assert m.snapshot()["recent_errors"][0]["error"] == "error_10"


def test_run_cycle_wraps_success():
    m = SchedulerMetrics()
    result = m.run_cycle("optimizer", lambda x: x + 1, 41)
    assert result == 42
    c = m.snapshot()["cycles"]["optimizer"]
    assert c["total_runs"] == 1
    assert c["total_success"] == 1


def test_run_cycle_wraps_failure():
    m = SchedulerMetrics()

    def will_fail():
        raise ValueError("boom")

    try:
        m.run_cycle("signal_audit", will_fail)
    except ValueError:
        pass
    c = m.snapshot()["cycles"]["signal_audit"]
    assert c["total_runs"] == 1
    assert c["total_failures"] == 1
    assert c["last_error"] == "boom"


def test_health_reports_failing_cycles():
    m = SchedulerMetrics()
    m.record_cycle_start("market_scan")
    m.record_cycle_end("market_scan", success=False)
    m.record_cycle_start("post_market")
    m.record_cycle_end("post_market", success=True)
    h = m.health()
    assert "market_scan" in h["failing_cycles"]
    assert "post_market" not in h["failing_cycles"]
