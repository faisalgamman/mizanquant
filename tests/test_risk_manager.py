"""Unit tests for app.services.risk_manager position sizing & eligibility."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services import risk_manager as rm


def _account(equity=10_000, cash=10_000, last_equity=10_000, blocked=False):
    return {
        "equity": equity,
        "cash": cash,
        "last_equity": last_equity,
        "trading_blocked": blocked,
        "account_blocked": False,
    }


class TestPositionSize:
    def test_invalid_price_returns_zero(self):
        out = rm.calculate_position_size(price=0, stop_loss=1, available_cash=1000, total_equity=1000)
        assert out["qty"] == 0

    def test_stop_above_price_returns_zero(self):
        out = rm.calculate_position_size(price=100, stop_loss=110, available_cash=1000, total_equity=1000)
        assert out["qty"] == 0

    def test_sizing_respects_risk_budget(self):
        # 1% of 10k = $100 budget, risk/share = 5 -> max 20 shares; cap at cash
        out = rm.calculate_position_size(price=100, stop_loss=95, available_cash=10_000, total_equity=10_000)
        assert out["qty"] >= 1
        # risk amount must not exceed budget by more than a rounding share
        assert out["risk_amount"] <= (10_000 * (rm.settings.TRADE_RISK_PCT / 100)) + 100

    def test_sizing_capped_by_cash(self):
        out = rm.calculate_position_size(price=200, stop_loss=190, available_cash=500, total_equity=10_000)
        # Only $500 cash -> 2 shares max
        assert out["qty"] <= 2

    def test_cash_shortfall_rejected(self):
        out = rm.calculate_position_size(price=1000, stop_loss=990, available_cash=100, total_equity=10_000)
        assert out["qty"] == 0


class TestEligibility:
    def test_market_closed_blocks(self):
        with patch.object(rm, "is_market_open", return_value=False):
            r = rm.check_trade_eligibility("AAPL", "buy", 100, 95, _account(), [])
            assert not r["eligible"]
            assert "Market is closed" in r["reason"]

    def test_blocked_account_blocks(self):
        with patch.object(rm, "is_market_open", return_value=True):
            r = rm.check_trade_eligibility("AAPL", "buy", 100, 95, _account(blocked=True), [])
            assert not r["eligible"]
            assert "blocked" in r["reason"].lower()

    def test_daily_loss_limit_blocks(self):
        with patch.object(rm, "is_market_open", return_value=True):
            acct = _account(equity=9_500, last_equity=10_000)  # -5% day
            r = rm.check_trade_eligibility("AAPL", "buy", 100, 95, acct, [])
            assert not r["eligible"]
            assert "loss" in r["reason"].lower()

    def test_max_positions_blocks(self):
        with patch.object(rm, "is_market_open", return_value=True):
            open_pos = [{"symbol": f"T{i}", "market_value": 100} for i in range(10)]
            r = rm.check_trade_eligibility("AAPL", "buy", 100, 95, _account(), open_pos)
            assert not r["eligible"]
            assert "Max positions" in r["reason"]

    def test_already_holding_blocks(self):
        with patch.object(rm, "is_market_open", return_value=True):
            open_pos = [{"symbol": "AAPL", "market_value": 100}]
            r = rm.check_trade_eligibility("AAPL", "buy", 100, 95, _account(), open_pos)
            assert not r["eligible"]
            assert "Already holding" in r["reason"]

    def test_happy_path(self):
        with patch.object(rm, "is_market_open", return_value=True):
            r = rm.check_trade_eligibility("AAPL", "buy", 100, 95, _account(), [])
            assert r["eligible"]
            assert r["sizing"]["qty"] >= 1


class TestPDT:
    def test_day_trade_counter_decays(self):
        rm._day_trades.clear()
        rm.record_day_trade()
        rm.record_day_trade()
        assert rm.get_day_trade_count() == 2
        # Push all recorded timestamps beyond the 7-day window
        from datetime import datetime, timedelta, timezone
        rm._day_trades[:] = [datetime.now(timezone.utc) - timedelta(days=10)] * 2
        assert rm.get_day_trade_count() == 0

    def test_pdt_guard(self):
        rm._day_trades.clear()
        for _ in range(rm.MAX_DAY_TRADES):
            rm.record_day_trade()
        assert not rm.can_day_trade()
        rm._day_trades.clear()
