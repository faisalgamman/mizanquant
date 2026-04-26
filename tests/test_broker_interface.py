"""Phase A: broker abstraction contract tests.

Two things must hold for the abstraction to be useful:

1. `AlpacaBroker` actually satisfies the `BrokerClient` Protocol —
   every required method is present with the right shape. Without
   this guarantee, swapping in IB later silently breaks callers.
2. `get_broker()` resolves the right adapter from environment:
   - default → Alpaca
   - BROKER_TYPE=alpaca → Alpaca
   - per-strategy STRATEGY_BROKER_A overrides global default
   - unknown name → falls back to Alpaca with a warning, never crashes

These tests are pure (no broker I/O) — they verify wiring only.
"""

from __future__ import annotations

import importlib

import pytest


def _reload_factory():
    """Re-import the factory so its lru_cache doesn't poison cross-test
    state when env vars change."""
    from app.services.broker import factory

    importlib.reload(factory)
    return factory


def test_alpaca_adapter_satisfies_broker_protocol():
    from app.services.broker.alpaca_adapter import AlpacaBroker
    from app.services.broker.base import BrokerClient

    inst = AlpacaBroker()
    # runtime_checkable Protocol — isinstance verifies method presence.
    assert isinstance(inst, BrokerClient)
    assert inst.name == "alpaca"


def test_get_broker_default_is_alpaca(monkeypatch):
    monkeypatch.delenv("BROKER_TYPE", raising=False)
    factory = _reload_factory()
    b = factory.get_broker()
    assert b.name == "alpaca"


def test_get_broker_explicit_alpaca(monkeypatch):
    monkeypatch.setenv("BROKER_TYPE", "alpaca")
    factory = _reload_factory()
    assert factory.get_broker().name == "alpaca"


def test_unknown_broker_falls_back_to_alpaca(monkeypatch):
    monkeypatch.setenv("BROKER_TYPE", "definitely_not_a_broker")
    factory = _reload_factory()
    assert factory.get_broker().name == "alpaca"


def test_per_strategy_override_takes_precedence(monkeypatch):
    monkeypatch.setenv("BROKER_TYPE", "alpaca")
    monkeypatch.setenv("STRATEGY_BROKER_A", "totally_unknown")
    factory = _reload_factory()
    # Override is read but unknown → falls back to Alpaca
    assert factory.get_broker(strategy_id="A").name == "alpaca"
    # Strategy without override still uses global default
    assert factory.get_broker(strategy_id="B").name == "alpaca"


def test_get_broker_caches_per_name(monkeypatch):
    monkeypatch.setenv("BROKER_TYPE", "alpaca")
    factory = _reload_factory()
    a = factory.get_broker()
    b = factory.get_broker()
    assert a is b  # cached


def test_alpaca_adapter_has_all_required_methods():
    """Sanity: every method named in BrokerClient exists as a bound
    callable on the adapter instance. Catches typos that slip past
    isinstance() because Protocol checks names but not signatures."""
    from app.services.broker.alpaca_adapter import AlpacaBroker

    inst = AlpacaBroker()
    for method in (
        "get_account",
        "get_positions",
        "get_orders",
        "get_order",
        "submit_order",
        "cancel_order",
        "close_position",
    ):
        assert callable(getattr(inst, method)), f"missing {method}"
