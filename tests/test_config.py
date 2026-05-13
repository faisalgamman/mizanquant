"""Tests for app.config fail-fast guard."""
from __future__ import annotations

import pytest

from app.config import ConfigurationError, Settings, assert_ready_for_auto_trade, settings


def _make(**overrides) -> Settings:
    base = dict(
        ALPACA_API_KEY="",
        ALPACA_SECRET_KEY="",
        ALPACA_API_KEY_A="",
        ALPACA_SECRET_KEY_A="",
        ALPACA_API_KEY_B="",
        ALPACA_SECRET_KEY_B="",
        ALPACA_API_KEY_C="",
        ALPACA_SECRET_KEY_C="",
        ALPACA_BASE_URL="https://paper-api.alpaca.markets",
        AUTO_TRADE_ENABLED=True,
        TELEGRAM_BOT_TOKEN="x",
        TELEGRAM_CHAT_ID="1",
        API_KEY="secret",
        TRADE_RISK_PCT=1.5,
        DAILY_LOSS_LIMIT_PCT=3.0,
        MAX_POSITION_PCT=15.0,
        MAX_OPEN_POSITIONS=6,
        LIVE_WHITELIST="AAPL,MSFT",
    )
    base.update(overrides)
    return Settings(**base)


def test_no_keys_is_invalid():
    s = _make()
    errors = s.validate_for_live()
    assert any("Alpaca credentials" in e for e in errors)


def test_live_url_is_blocked_by_default():
    s = _make(
        ALPACA_API_KEY_A="k", ALPACA_SECRET_KEY_A="s",
        ALPACA_BASE_URL="https://api.alpaca.markets",
    )
    errors = s.validate_for_live()
    assert any("Live endpoints are blocked" in e for e in errors)


def test_missing_telegram_flagged():
    s = _make(
        ALPACA_API_KEY_A="k", ALPACA_SECRET_KEY_A="s",
        TELEGRAM_BOT_TOKEN="",
    )
    errors = s.validate_for_live()
    assert any("TELEGRAM" in e for e in errors)


def test_missing_api_key_flagged_when_auto_trade_enabled():
    s = _make(
        ALPACA_API_KEY_A="k", ALPACA_SECRET_KEY_A="s",
        API_KEY="",
    )
    errors = s.validate_for_live()
    assert any("API_KEY missing" in e for e in errors)


def test_risk_bounds():
    s = _make(ALPACA_API_KEY_A="k", ALPACA_SECRET_KEY_A="s", TRADE_RISK_PCT=50)
    errors = s.validate_for_live()
    assert any("TRADE_RISK_PCT" in e for e in errors)


def test_empty_whitelist_flagged():
    s = _make(
        ALPACA_API_KEY_A="k", ALPACA_SECRET_KEY_A="s",
        LIVE_WHITELIST="",
    )
    errors = s.validate_for_live()
    assert any("LIVE_WHITELIST" in e for e in errors)


def test_live_whitelist_property():
    s = _make(LIVE_WHITELIST=" aapl , MSFT , googl ")
    assert s.live_whitelist == ["AAPL", "MSFT", "GOOGL"]


def test_live_whitelist_empty_when_not_set():
    s = _make(LIVE_WHITELIST="")
    assert s.live_whitelist == []


def test_happy_path_empty_errors():
    s = _make(ALPACA_API_KEY_A="k", ALPACA_SECRET_KEY_A="s", LIVE_CONFIRMED=True)
    assert s.validate_for_live() == []


def test_redacted_masks_secrets():
    s = _make(ALPACA_API_KEY_A="PKTHISISASECRET", ALPACA_SECRET_KEY_A="BrSecretValue")
    red = s.redacted()
    assert "SECRET" not in str(red["ALPACA_API_KEY_A"]).upper() or red["ALPACA_API_KEY_A"].startswith("***")
    assert red["ALPACA_API_KEY_A"].endswith("CRET")


def test_guard_noop_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "AUTO_TRADE_ENABLED", False)
    assert_ready_for_auto_trade()  # returns without raising


def test_guard_raises_when_invalid(monkeypatch):
    monkeypatch.setattr(settings, "AUTO_TRADE_ENABLED", True)
    monkeypatch.setattr(settings, "ALPACA_API_KEY", "")
    monkeypatch.setattr(settings, "ALPACA_SECRET_KEY", "")
    for sid in ("A", "B", "C"):
        monkeypatch.setattr(settings, f"ALPACA_API_KEY_{sid}", "")
        monkeypatch.setattr(settings, f"ALPACA_SECRET_KEY_{sid}", "")
    with pytest.raises(ConfigurationError):
        assert_ready_for_auto_trade()
    # restore off
    monkeypatch.setattr(settings, "AUTO_TRADE_ENABLED", False)
