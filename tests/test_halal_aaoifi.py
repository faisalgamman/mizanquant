"""AAOIFI (total-assets) halal screening + three-state verdict.

Guards the fix for the Sharia contradictions: a name like ITT fails the AAOIFI debt
screen (debt / total assets) yet passed the legacy DJIM screen (debt / market cap).
Also covers the activity three-state (haram / doubtful / clean) and the screen-version
cache invalidation.
"""
from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd

from app.services import halal_screening as hs


def _profile(mcap, shares, sector="Industrials", industry="Specialty Industrial Machinery"):
    return {"marketCap": mcap, "sharesOutstanding": shares, "sector": sector,
            "industry": industry, "companyName": "TestCo", "price": mcap / shares}


def _bs(debt, assets, cash=0.0, receivables=0.0):
    return [{"totalDebt": debt, "cashAndCashEquivalents": cash, "shortTermInvestments": 0,
             "cashAndShortTermInvestments": cash, "netReceivables": receivables, "totalAssets": assets}]


def _income(revenue=1_000_000_000, interest=0.0):
    return [{"revenue": revenue, "interestIncome": interest, "interestExpense": interest}]


def _run(profile, bs, income, mcap, shares):
    """screen_symbol with FMP mocked + a flat 2y price series (avg_mcap == spot)."""
    idx = pd.date_range("2024-01-01", periods=500, freq="B")
    df = pd.DataFrame({"close": np.full(500, mcap / shares)}, index=idx)
    with patch.object(hs.fmp_client, "get_profile", return_value=profile), \
         patch.object(hs.fmp_client, "get_balance_sheet", return_value=bs), \
         patch.object(hs.fmp_client, "get_income_statement", return_value=income), \
         patch("app.services.market_data.fetch", return_value=df):
        return hs.screen_symbol("TEST")


# ── The ITT case: same debt, opposite verdict by denominator ──────────────────

# 35.6% of total assets, but only ~22.7% of market cap.
_MCAP, _SHARES = 17_450_000_000, 89_500_000
_ASSETS, _DEBT = 11_131_600_000, 3_965_000_000


def test_aaoifi_debt_over_total_assets_is_non_compliant(monkeypatch):
    monkeypatch.setattr(hs, "HALAL_STANDARD", "aaoifi")
    r = _run(_profile(_MCAP, _SHARES), _bs(_DEBT, _ASSETS, cash=5e8, receivables=5e8),
             _income(), _MCAP, _SHARES)
    assert r is not None
    assert r["denominator_basis"] == "total_assets"
    assert r["debt_pass"] is False
    assert 35.0 < r["debt_ratio"] < 36.5
    assert r["halal_verdict"] == "non_compliant"
    assert r["is_halal"] is False
    assert any("الدَّين" in x for x in r["halal_reasons"])


def test_same_name_passes_under_djim(monkeypatch):
    monkeypatch.setattr(hs, "HALAL_STANDARD", "djim")
    monkeypatch.setattr(hs, "HALAL_DEBT_MAX", 33.0)
    monkeypatch.setattr(hs, "HALAL_LIQUIDITY_MAX", 33.0)
    monkeypatch.setattr(hs, "HALAL_RECEIVABLE_MAX", 33.0)
    r = _run(_profile(_MCAP, _SHARES), _bs(_DEBT, _ASSETS, cash=5e8, receivables=5e8),
             _income(), _MCAP, _SHARES)
    assert r["denominator_basis"] == "avg_mcap_24m"
    assert r["debt_pass"] is True           # 22.7% of market cap < 33%
    assert r["halal_verdict"] == "halal"
    assert r["is_halal"] is True


# ── Activity three-state ──────────────────────────────────────────────────────

def test_casino_activity_is_non_compliant(monkeypatch):
    monkeypatch.setattr(hs, "HALAL_STANDARD", "aaoifi")
    prof = _profile(_MCAP, _SHARES, sector="Consumer Cyclical", industry="Resorts & Casinos")
    r = _run(prof, _bs(1e8, _ASSETS, cash=1e7), _income(), _MCAP, _SHARES)
    assert r["activity"] == "haram"
    assert r["halal_verdict"] == "non_compliant" and r["is_halal"] is False


def test_hotel_activity_is_doubtful(monkeypatch):
    monkeypatch.setattr(hs, "HALAL_STANDARD", "aaoifi")
    # Clean financials, but a lodging/travel activity → DOUBTFUL (needs review), not halal.
    prof = _profile(_MCAP, _SHARES, sector="Consumer Cyclical", industry="Travel Services")
    r = _run(prof, _bs(1e8, _ASSETS, cash=1e7, receivables=1e7), _income(), _MCAP, _SHARES)
    assert r["activity"] == "doubtful"
    assert r["halal_verdict"] == "doubtful"
    assert r["is_halal"] is False           # kept out of auto-BUY
    assert r["needs_manual_review"] is True


def test_clean_low_debt_is_halal(monkeypatch):
    monkeypatch.setattr(hs, "HALAL_STANDARD", "aaoifi")
    # Debt 10% of assets, clean industry → fully halal.
    r = _run(_profile(_MCAP, _SHARES), _bs(0.10 * _ASSETS, _ASSETS, cash=1e7, receivables=1e7),
             _income(), _MCAP, _SHARES)
    assert r["debt_pass"] is True
    assert r["halal_verdict"] == "halal" and r["is_halal"] is True
    assert r["standard"] == "AAOIFI"


def test_packaged_foods_and_entertainment_are_clean(monkeypatch):
    # Owner decision: packaged foods + entertainment are CLEAN (halal), not doubtful.
    monkeypatch.setattr(hs, "HALAL_STANDARD", "aaoifi")
    for sector, industry in [("Consumer Defensive", "Packaged Foods"),
                             ("Communication Services", "Entertainment")]:
        prof = _profile(_MCAP, _SHARES, sector=sector, industry=industry)
        r = _run(prof, _bs(0.10 * _ASSETS, _ASSETS, cash=1e7, receivables=1e7), _income(), _MCAP, _SHARES)
        assert r["activity"] == "clean", f"{industry} should be clean"
        assert r["halal_verdict"] == "halal" and r["is_halal"] is True


def test_missing_total_assets_is_doubtful_not_haram(monkeypatch):
    monkeypatch.setattr(hs, "HALAL_STANDARD", "aaoifi")
    r = _run(_profile(_MCAP, _SHARES), _bs(_DEBT, 0.0), _income(), _MCAP, _SHARES)  # no total assets
    assert r["halal_verdict"] == "doubtful"   # absence of data ≠ haram
    assert r["is_halal"] is False


# ── Screen-version cache invalidation (get_halal_status) ──────────────────────

class _FakeQuery:
    def __init__(self, first=None):
        self._first = first

    def filter(self, *a, **k):
        return self

    def first(self):
        return self._first


class _FakeSession:
    def __init__(self, first=None):
        self._first = first

    def query(self, *a, **k):
        return _FakeQuery(self._first)

    def close(self):
        pass


def test_old_screen_version_forces_refresh(monkeypatch):
    # A recent row, but computed under an OLD screen version → must NOT be served; refresh.
    row = SimpleNamespace(
        symbol="ZZZ", last_screened=hs._utc_now() - timedelta(days=1),
        details={"symbol": "ZZZ", "is_halal": True, "screen_version": "old-djim"},
        is_halal=True, debt_ratio=0, interest_ratio=0, liquidity_ratio=0, sector="Tech",
    )
    monkeypatch.setattr(hs, "SessionLocal", lambda: _FakeSession(first=row))
    monkeypatch.setattr(hs.settings, "FMP_API_KEY", "x", raising=False)
    monkeypatch.setattr(hs, "screen_and_store", lambda s: {"symbol": s, "is_halal": False,
                                                           "halal_verdict": "non_compliant"})
    out = hs.get_halal_status("ZZZ")
    assert out["halal_verdict"] == "non_compliant"   # re-screened, not the stale DJIM pass


def test_old_version_not_served_stale_on_failure(monkeypatch):
    row = SimpleNamespace(
        symbol="ZZZ", last_screened=hs._utc_now() - timedelta(days=1),
        details={"symbol": "ZZZ", "is_halal": True, "screen_version": "old-djim"},
        is_halal=True, debt_ratio=0, interest_ratio=0, liquidity_ratio=0, sector="Tech",
    )
    monkeypatch.setattr(hs, "SessionLocal", lambda: _FakeSession(first=row))
    monkeypatch.setattr(hs.settings, "FMP_API_KEY", "x", raising=False)
    monkeypatch.setattr(hs, "screen_and_store", lambda s: None)  # refresh fails
    assert hs.get_halal_status("ZZZ") is None   # never serve an old-standard verdict
