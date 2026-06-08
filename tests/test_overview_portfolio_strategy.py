"""Test that _get_portfolio reads DASHBOARD_STRATEGY (default MANUAL)."""

import asyncio

import pytest


class TestGetPortfolioStrategy:
    def test_default_manual(self, monkeypatch):
        """Without env var, _get_portfolio passes strategy_id='MANUAL' to get_broker."""
        from app.api.v1.overview import _get_portfolio

        called_sid = []

        class FakeBroker:
            name = "ibkr"
            def get_account(self, strategy_id=None):
                called_sid.append(strategy_id)
                return {"equity": 990869, "cash": 100000, "buying_power": 400000,
                        "portfolio_value": 990869, "last_equity": 990000}
            def get_positions(self, strategy_id=None):
                return []

        def fake_get_broker(strategy_id=None):
            called_sid.append(strategy_id)
            return FakeBroker()

        monkeypatch.setattr(
            "app.services.broker.factory.get_broker", fake_get_broker,
        )

        result = asyncio.run(_get_portfolio())

        # strategy_id should be "MANUAL"
        assert any(s == "MANUAL" for s in called_sid), f"Expected MANUAL in {called_sid}"
        assert result["equity"] == 990869
        assert result["broker_type"] == "ibkr"

    def test_env_override(self, monkeypatch):
        """With DASHBOARD_STRATEGY=A, _get_portfolio reads 'A' instead."""
        from app.api.v1.overview import _get_portfolio

        monkeypatch.setenv("DASHBOARD_STRATEGY", "A")

        called_sid = []

        class FakeBroker:
            name = "alpaca"
            def get_account(self, strategy_id=None):
                called_sid.append(strategy_id)
                return {"equity": 98313, "cash": 50000, "buying_power": 100000,
                        "portfolio_value": 98313, "last_equity": 98000}
            def get_positions(self, strategy_id=None):
                return []

        def fake_get_broker(strategy_id=None):
            called_sid.append(strategy_id)
            return FakeBroker()

        monkeypatch.setattr(
            "app.services.broker.factory.get_broker", fake_get_broker,
        )

        result = asyncio.run(_get_portfolio())

        assert any(s == "A" for s in called_sid), f"Expected 'A' in {called_sid}"
        assert result["equity"] == 98313
        assert result["broker_type"] == "alpaca"
