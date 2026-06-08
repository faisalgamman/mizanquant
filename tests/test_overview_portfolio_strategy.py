"""Test that _get_portfolio reads DASHBOARD_STRATEGY (default MANUAL)."""

import asyncio

from app.api.v1.overview import _get_portfolio


class TestGetPortfolioStrategy:
    def test_default_manual(self, monkeypatch):
        """Without env var, _get_portfolio passes strategy_id='MANUAL' to get_broker."""

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

    def test_ibkr_empty_falls_back_to_alpaca_a(self, monkeypatch):
        """IBKR returns degraded account → Alpaca fallback with strategy 'A' + ibkr_offline=True."""

        called_alpaca_sid = []

        class FakeIbkrBroker:
            name = "ibkr"
            def get_account(self, strategy_id=None):
                return {"equity": 0, "cash": 0, "buying_power": 0,
                        "portfolio_value": 0, "last_equity": 0}
            def get_positions(self, strategy_id=None):
                return []

        class FakeAlpacaBroker:
            name = "alpaca"
            def get_account(self, strategy_id=None):
                called_alpaca_sid.append(strategy_id)
                return {"equity": 98313, "cash": 50000, "buying_power": 100000,
                        "portfolio_value": 98313, "last_equity": 98000}
            def get_positions(self, strategy_id=None):
                return []

        def fake_get_broker(strategy_id=None):
            # Default MANUAL → IBKR
            return FakeIbkrBroker()

        def fake_build(name):
            assert name == "alpaca"
            return FakeAlpacaBroker()

        monkeypatch.setattr(
            "app.services.broker.factory.get_broker", fake_get_broker,
        )
        monkeypatch.setattr(
            "app.services.broker.factory._build", fake_build,
        )

        result = asyncio.run(_get_portfolio())

        assert result["ibkr_offline"] is True
        assert result["broker_type"] == "alpaca (ibkr offline)"
        assert result["equity"] == 98313
        assert "A" in called_alpaca_sid
