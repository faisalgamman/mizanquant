"""Unit tests for BLOCK System levels and guard."""
from __future__ import annotations

import pytest

from app.services.guards.block_system import get_block_level, guard, BLOCK_LEVELS
from app.services.guards.base import GuardContext


class TestGetBlockLevel:
    def _mock_market_context(self, spy_regime="bull", vix=18, credit_class="ok"):
        """Helper to mock market_context.get_market_context at source."""
        import app.services.market_context as mc_mod
        return lambda: {
            "spy_regime": {"regime": spy_regime},
            "vix": {"vix": vix},
            "credit": {"classification": credit_class},
        }

    def test_normal_when_all_good(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.market_context.get_market_context",
            self._mock_market_context(),
        )
        assert get_block_level() == "NORMAL"

    def test_bear_when_spy_below_ema200(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.market_context.get_market_context",
            self._mock_market_context(spy_regime="bear"),
        )
        assert get_block_level() == "BEAR"

    def test_vix_when_vix_above_25(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.market_context.get_market_context",
            self._mock_market_context(vix=30),
        )
        assert get_block_level() == "VIX"

    def test_credit_when_credit_stress(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.market_context.get_market_context",
            self._mock_market_context(credit_class="stress"),
        )
        assert get_block_level() == "CREDIT"

    def test_bear_takes_precedence_over_vix(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.market_context.get_market_context",
            self._mock_market_context(spy_regime="bear", vix=30, credit_class="stress"),
        )
        assert get_block_level() == "BEAR"

    def test_vix_takes_precedence_over_credit(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.market_context.get_market_context",
            self._mock_market_context(vix=30, credit_class="stress"),
        )
        assert get_block_level() == "VIX"


def _make_guard_ctx(**overrides) -> GuardContext:
    """Build a valid GuardContext with sensible defaults."""
    from datetime import datetime, timezone
    from app.services.regime import RegimeSnapshot
    defaults = dict(
        strategy_id="A",
        symbol="AAPL",
        side="buy",
        price=100.0,
        stop_loss=95.0,
        qty=10,
        account={"equity": 100000, "cash": 50000},
        positions=[],
        regime=RegimeSnapshot(state="NEUTRAL", vix=18, vix_pctile=50.0, spy_ema_slope_pct=0.1, yield_spread_bps=30, computed_at=datetime.now(timezone.utc), changed=False),
    )
    defaults.update(overrides)
    return GuardContext(**defaults)


class TestGuard:
    def test_normal_passes(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.market_context.get_market_context",
            lambda: {
                "spy_regime": {"regime": "bull"},
                "vix": {"vix": 18},
                "credit": {"classification": "ok"},
            },
        )
        ctx = _make_guard_ctx()
        result = guard(ctx)
        assert result.passed is True
        assert result.code == "BLOCK_OK"

    def test_bear_blocks(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.market_context.get_market_context",
            lambda: {
                "spy_regime": {"regime": "bear"},
                "vix": {"vix": 18},
                "credit": {"classification": "ok"},
            },
        )
        ctx = _make_guard_ctx()
        result = guard(ctx)
        assert result.passed is False
        assert result.code == "BLOCK_BEAR"

    def test_vix_blocks(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.market_context.get_market_context",
            lambda: {
                "spy_regime": {"regime": "bull"},
                "vix": {"vix": 30},
                "credit": {"classification": "ok"},
            },
        )
        ctx = _make_guard_ctx()
        result = guard(ctx)
        assert result.passed is False
        assert result.code == "BLOCK_VIX"

    def test_credit_blocks(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.market_context.get_market_context",
            lambda: {
                "spy_regime": {"regime": "bull"},
                "vix": {"vix": 18},
                "credit": {"classification": "stress"},
            },
        )
        ctx = _make_guard_ctx()
        result = guard(ctx)
        assert result.passed is False
        assert result.code == "BLOCK_CREDIT"

    def test_guard_result_has_required_fields(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.market_context.get_market_context",
            lambda: {
                "spy_regime": {"regime": "bull"},
                "vix": {"vix": 18},
                "credit": {"classification": "ok"},
            },
        )
        ctx = _make_guard_ctx()
        result = guard(ctx)
        assert result.name == "block_system"
        assert isinstance(result.passed, bool)
        assert isinstance(result.blocking, bool)
        assert isinstance(result.reason, str)
        assert isinstance(result.code, str)


def test_block_levels_ordering():
    """BLOCK levels must maintain ascending severity order."""
    assert BLOCK_LEVELS == ["NORMAL", "CREDIT", "VIX", "BEAR"]
