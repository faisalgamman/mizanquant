"""Unit tests for the precision entry gates (no network).

Each gate is verified in isolation with monkeypatched externals (earnings calendar,
market regime). Module-level toggles are forced ON so the test is deterministic
regardless of the environment.
"""

import pandas as pd
import pytest

from app.services import precision_gates as pg


@pytest.fixture(autouse=True)
def _gates_on(monkeypatch):
    for flag in ("GATES_ENABLED", "EARNINGS_GATE_ON", "WEEKLY_TREND_GATE_ON",
                 "SECTOR_CAP_ON", "REGIME_GATE_ON"):
        monkeypatch.setattr(pg, flag, True)


def _df(closes):
    return pd.DataFrame({"close": [float(c) for c in closes]})


# ── earnings blackout ──────────────────────────────────────────────────────

def test_earnings_blackout_blocks_when_imminent(monkeypatch):
    monkeypatch.setattr("app.services.reference_data.business_days_until_earnings", lambda s: 2)
    blocked, why = pg.earnings_blackout("AAA", days=3)
    assert blocked is True and "earnings" in why


def test_earnings_blackout_allows_when_far(monkeypatch):
    monkeypatch.setattr("app.services.reference_data.business_days_until_earnings", lambda s: 10)
    assert pg.earnings_blackout("AAA", days=3) == (False, None)


def test_earnings_blackout_fails_open_when_unknown(monkeypatch):
    monkeypatch.setattr("app.services.reference_data.business_days_until_earnings", lambda s: None)
    assert pg.earnings_blackout("AAA", days=3) == (False, None)


def test_earnings_blackout_off(monkeypatch):
    monkeypatch.setattr(pg, "EARNINGS_GATE_ON", False)
    assert pg.earnings_blackout("AAA") == (False, None)


# ── weekly-trend gate ──────────────────────────────────────────────────────

def test_weekly_trend_ok_for_rising(monkeypatch):
    ok, why = pg.weekly_trend_ok(_df(range(1, 81)))       # steadily rising
    assert ok is True and why is None


def test_weekly_trend_blocks_downtrend(monkeypatch):
    ok, why = pg.weekly_trend_ok(_df(range(120, 40, -1)))  # steadily falling
    assert ok is False and "trend" in why or "avg" in why


def test_weekly_trend_fails_open_on_thin_history():
    ok, _ = pg.weekly_trend_ok(_df(range(10)))            # < 60 bars
    assert ok is True


# ── sector concentration cap ───────────────────────────────────────────────

def test_sector_cap_keeps_top_n_per_sector():
    rows = [
        {"symbol": "A", "sector": "Tech"}, {"symbol": "B", "sector": "Tech"},
        {"symbol": "C", "sector": "Tech"}, {"symbol": "D", "sector": "Energy"},
        {"symbol": "E", "sector": "Tech"}, {"symbol": "F", "sector": "Energy"},
    ]
    out = pg.sector_concentration_cap(rows, max_per_sector=2)
    syms = [r["symbol"] for r in out]
    assert syms == ["A", "B", "D", "F"]        # 2 Tech (top-ranked), 2 Energy; C & E dropped


def test_sector_cap_keeps_unknown_sector():
    rows = [{"symbol": "A", "sector": ""}, {"symbol": "B", "sector": ""},
            {"symbol": "C", "sector": ""}]
    assert len(pg.sector_concentration_cap(rows, max_per_sector=1)) == 3  # unknowns never dropped


def test_sector_cap_off(monkeypatch):
    monkeypatch.setattr(pg, "SECTOR_CAP_ON", False)
    rows = [{"symbol": "A", "sector": "Tech"}, {"symbol": "B", "sector": "Tech"}]
    assert pg.sector_concentration_cap(rows, max_per_sector=1) == rows


# ── regime strictness + combined entry gate ────────────────────────────────

def test_regime_requires_weekly_trend_in_stress(monkeypatch):
    monkeypatch.setattr("app.services.market_context.get_market_status",
                        lambda: {"status": "CREDIT STRESS"})
    r = pg.regime_strictness()
    assert r["require_weekly_trend"] is True and r["composite_penalty"] == 5


def test_regime_lenient_in_risk_on(monkeypatch):
    monkeypatch.setattr("app.services.market_context.get_market_status",
                        lambda: {"status": "RISK ON"})
    r = pg.regime_strictness()
    assert r["require_weekly_trend"] is False and r["composite_penalty"] == 0


def test_entry_gate_failures_combines(monkeypatch):
    monkeypatch.setattr("app.services.reference_data.business_days_until_earnings", lambda s: 1)
    monkeypatch.setattr("app.services.market_context.get_market_status",
                        lambda: {"status": "RISK ON"})
    fails = pg.entry_gate_failures("AAA", _df(range(120, 40, -1)), require_weekly_trend=True)
    assert any("earnings" in f for f in fails)            # earnings imminent
    assert any(("trend" in f or "avg" in f) for f in fails)  # against weekly trend
