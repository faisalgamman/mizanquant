"""Tests for centralized app_cfg behavior."""
from __future__ import annotations

from app.services import risk_manager as rm


def _account(equity=10_000, cash=10_000, last_equity=10_000):
    return {
        "equity": equity,
        "cash": cash,
        "last_equity": last_equity,
        "trading_blocked": False,
        "account_blocked": False,
    }


def test_daily_loss_limit_reads_app_cfg(monkeypatch):
    monkeypatch.setattr(rm.app_cfg.risk, "daily_loss_limit_pct", 2.0)
    monkeypatch.setattr(rm.app_cfg.risk, "use_modular_guards", True)
    monkeypatch.setattr(rm, "is_market_open", lambda: True)

    result = rm.check_trade_eligibility(
        symbol="AAPL",
        side="buy",
        price=100.0,
        stop_loss=95.0,
        account=_account(equity=9_790, last_equity=10_000),
        open_positions=[],
    )

    assert result["eligible"] is False
    assert "daily_loss" in result["reason"]
