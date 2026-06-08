"""Tests for IBKR config validation — invalid ports rejected, defaults unified.

Root-cause regression guard for the Railway misconfiguration where
IBKR_PORT=4004 (invalid) and a stopped-service host went unnoticed.
"""
from __future__ import annotations

from app.services.broker.ibkr_config import (
    get_ibkr_config,
    DEFAULT_IBKR_PORT,
    DEFAULT_IBKR_HOST,
    VALID_IBKR_PORTS,
)


def test_invalid_port_rejected(monkeypatch):
    """A genuinely invalid port must be rejected and replaced with the default 4002.

    NOTE: 4004 is NOT invalid — it is the gnzsnz/ib-gateway socat-relay paper port and is
    REQUIRED in production (cross-container connections reach the API via socat on 4003/4004,
    not the localhost-only direct 4002). Verified live: connecting on 4004 returns connected.
    So we guard against an actually-bogus port here, not 4004.
    """
    monkeypatch.setenv("IBKR_PORT", "1234")  # not in VALID_IBKR_PORTS
    cfg = get_ibkr_config()
    assert cfg["port"] == DEFAULT_IBKR_PORT
    assert cfg["port"] != 1234


def test_valid_ports_accepted(monkeypatch):
    """All legitimate IBKR ports must pass through unchanged — including the
    socat-relay ports 4003/4004 used by the gnzsnz/ib-gateway image on Railway."""
    for port in (4001, 4002, 4003, 4004, 7496, 7497):
        monkeypatch.setenv("IBKR_PORT", str(port))
        assert get_ibkr_config()["port"] == port


def test_non_numeric_port_falls_back(monkeypatch):
    """A non-numeric IBKR_PORT must fall back to the default, not crash."""
    monkeypatch.setenv("IBKR_PORT", "abc")
    assert get_ibkr_config()["port"] == DEFAULT_IBKR_PORT


def test_default_when_port_missing(monkeypatch):
    """When IBKR_PORT is unset, the unified default is 4002 (not 7497)."""
    monkeypatch.delenv("IBKR_PORT", raising=False)
    assert get_ibkr_config()["port"] == 4002


def test_host_passthrough(monkeypatch):
    """A valid host value must pass through unchanged."""
    monkeypatch.setenv("IBKR_HOST", "ibgateway.railway.internal")
    assert get_ibkr_config()["host"] == "ibgateway.railway.internal"


def test_default_host_when_missing(monkeypatch):
    """When IBKR_HOST is unset, fall back to localhost."""
    monkeypatch.delenv("IBKR_HOST", raising=False)
    assert get_ibkr_config()["host"] == DEFAULT_IBKR_HOST


def test_empty_host_falls_back(monkeypatch):
    """An empty/whitespace IBKR_HOST must fall back to the default."""
    monkeypatch.setenv("IBKR_HOST", "   ")
    assert get_ibkr_config()["host"] == DEFAULT_IBKR_HOST


def test_mode_label_matches_port(monkeypatch):
    """The 'mode' label must describe the resolved port."""
    monkeypatch.setenv("IBKR_PORT", "4002")
    assert get_ibkr_config()["mode"] == VALID_IBKR_PORTS[4002]
    monkeypatch.setenv("IBKR_PORT", "4001")
    assert get_ibkr_config()["mode"] == VALID_IBKR_PORTS[4001]


def test_client_id_non_numeric_falls_back(monkeypatch):
    """A non-numeric IBKR_CLIENT_ID must fall back to 1."""
    monkeypatch.setenv("IBKR_CLIENT_ID", "xyz")
    assert get_ibkr_config()["client_id"] == 1


def test_client_id_passthrough(monkeypatch):
    """A valid IBKR_CLIENT_ID must pass through."""
    monkeypatch.setenv("IBKR_CLIENT_ID", "11")
    assert get_ibkr_config()["client_id"] == 11
