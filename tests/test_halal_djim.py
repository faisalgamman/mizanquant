"""Tests for DJIM halal screening — full standard with 24-month avg market cap.

The system default is now AAOIFI (total-assets denominator); this suite pins the
legacy DJIM path (market-cap denominator, 33% thresholds) to keep covering that math.
"""
from __future__ import annotations

import math
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _force_djim(monkeypatch):
    from app.services import halal_screening as hs
    monkeypatch.setattr(hs, "HALAL_STANDARD", "djim")
    monkeypatch.setattr(hs, "HALAL_DEBT_MAX", 33.0)
    monkeypatch.setattr(hs, "HALAL_LIQUIDITY_MAX", 33.0)
    monkeypatch.setattr(hs, "HALAL_RECEIVABLE_MAX", 33.0)
    monkeypatch.setattr(hs, "HALAL_INTEREST_MAX", 5.0)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_profile(mcap=1_000_000_000, shares=10_000_000, sector="Technology", industry="Software"):
    return {
        "marketCap": mcap,
        "sharesOutstanding": shares,
        "sector": sector,
        "industry": industry,
        "companyName": "TestCo",
        "price": mcap / shares if shares else 100,
    }


def _make_bs(debt=100_000_000, cash=50_000_000, receivables=80_000_000):
    return [{
        "totalDebt": debt,
        "cashAndCashEquivalents": cash,
        "shortTermInvestments": 0,
        "cashAndShortTermInvestments": cash,
        "netReceivables": receivables,
    }]


def _make_income(revenue=500_000_000, interest=5_000_000):
    return [{"revenue": revenue, "interestIncome": interest, "interestExpense": interest}]


# ---------------------------------------------------------------------------
# Test 1: avg_market_cap_24m helper — injected price series
# ---------------------------------------------------------------------------

def test_avg_market_cap_24m_uses_price_history(monkeypatch):
    """When 2y prices available, avg_mcap = mean(close) * shares."""
    import pandas as pd
    import numpy as np
    from app.services.halal_screening import _avg_market_cap_24m

    # 500 trading days, close price = 100 constant
    idx = pd.date_range("2024-01-01", periods=500, freq="B")
    df = pd.DataFrame({"close": np.full(500, 100.0)}, index=idx)

    with patch("app.services.market_data.fetch", return_value=df):
        avg, basis = _avg_market_cap_24m("TEST", shares=1_000_000, spot_mcap=95_000_000)

    assert basis == "avg_24m"
    assert math.isclose(avg, 100_000_000, rel_tol=0.01)


def test_avg_market_cap_24m_falls_back_on_empty(monkeypatch):
    """When price fetch returns empty df, falls back to spot."""
    import pandas as pd
    from app.services.halal_screening import _avg_market_cap_24m

    with patch("app.services.market_data.fetch", return_value=pd.DataFrame()):
        avg, basis = _avg_market_cap_24m("TEST", shares=1_000_000, spot_mcap=50_000_000)

    assert basis == "spot_fallback"
    assert avg == 50_000_000


# ---------------------------------------------------------------------------
# Test 2: All 3 DJIM ratios computed on avg_mcap denominator
# ---------------------------------------------------------------------------

def test_screen_symbol_djim_all_pass(monkeypatch):
    """All ratios < 33% -> is_halal=True, standard=DJIM."""
    import pandas as pd
    import numpy as np
    from app.services import halal_screening as hs

    mcap = 1_000_000_000
    shares = 10_000_000
    profile = _make_profile(mcap=mcap, shares=shares)
    bs = _make_bs(debt=200_000_000, cash=50_000_000, receivables=100_000_000)  # all < 33%
    income = _make_income(revenue=500_000_000, interest=5_000_000)

    idx = pd.date_range("2024-01-01", periods=500, freq="B")
    df = pd.DataFrame({"close": np.full(500, mcap / shares)}, index=idx)

    with patch.object(hs.fmp_client, "get_profile", return_value=profile), \
         patch.object(hs.fmp_client, "get_balance_sheet", return_value=bs), \
         patch.object(hs.fmp_client, "get_income_statement", return_value=income), \
         patch("app.services.market_data.fetch", return_value=df):
        result = hs.screen_symbol("TEST")

    assert result is not None
    assert result["is_halal"] is True
    assert result["standard"] == "DJIM"
    assert result["mcap_basis"] == "avg_24m"
    assert result["screens_total"] == 5
    assert result["debt_pass"] is True
    assert result["liquidity_pass"] is True
    assert result["receivable_pass"] is True


def test_screen_symbol_receivable_ratio_fails(monkeypatch):
    """Receivables > 33% of avg_mcap -> is_halal=False even if debt+liquidity pass."""
    import pandas as pd
    import numpy as np
    from app.services import halal_screening as hs

    mcap = 1_000_000_000
    shares = 10_000_000
    profile = _make_profile(mcap=mcap, shares=shares)
    bs = _make_bs(debt=100_000_000, cash=50_000_000, receivables=400_000_000)  # 40% > 33%
    income = _make_income(revenue=500_000_000, interest=0)

    idx = pd.date_range("2024-01-01", periods=500, freq="B")
    df = pd.DataFrame({"close": np.full(500, mcap / shares)}, index=idx)

    with patch.object(hs.fmp_client, "get_profile", return_value=profile), \
         patch.object(hs.fmp_client, "get_balance_sheet", return_value=bs), \
         patch.object(hs.fmp_client, "get_income_statement", return_value=income), \
         patch("app.services.market_data.fetch", return_value=df):
        result = hs.screen_symbol("TEST")

    assert result is not None
    assert result["is_halal"] is False
    assert result["receivable_pass"] is False
    assert result["receivable_ratio"] > 33.0


def test_screen_symbol_reit_sector_excluded(monkeypatch):
    """Real estate sector -> haram_pass=False -> is_halal=False."""
    import pandas as pd
    from app.services import halal_screening as hs

    mcap = 1_000_000_000
    profile = _make_profile(mcap=mcap, sector="Real Estate", industry="REIT—Diversified")
    bs = _make_bs(debt=100_000_000, cash=50_000_000, receivables=10_000_000)
    income = _make_income()

    idx = pd.date_range("2024-01-01", periods=500, freq="B")
    df = pd.DataFrame({"close": [100.0] * 500}, index=idx)

    with patch.object(hs.fmp_client, "get_profile", return_value=profile), \
         patch.object(hs.fmp_client, "get_balance_sheet", return_value=bs), \
         patch.object(hs.fmp_client, "get_income_statement", return_value=income), \
         patch("app.services.market_data.fetch", return_value=df):
        result = hs.screen_symbol("VNO")

    assert result is not None
    assert result["is_halal"] is False
    assert result["haram_pass"] is False


def test_screen_symbol_debt_fails_on_avg_mcap(monkeypatch):
    """Debt passes on spot mcap but fails on avg_24m -> is_halal=False (DJIM strictness)."""
    import pandas as pd
    import numpy as np
    from app.services import halal_screening as hs

    spot_mcap = 1_000_000_000
    avg_mcap = 700_000_000  # lower avg -> higher ratio
    shares = 10_000_000
    avg_price = avg_mcap / shares  # ~70 while spot is 100

    profile = _make_profile(mcap=spot_mcap, shares=shares)
    bs = _make_bs(debt=250_000_000, cash=10_000_000, receivables=10_000_000)
    # debt/spot = 25% (pass), debt/avg = 250/700 = 35.7% (fail)
    income = _make_income()

    idx = pd.date_range("2024-01-01", periods=500, freq="B")
    df = pd.DataFrame({"close": np.full(500, avg_price)}, index=idx)

    with patch.object(hs.fmp_client, "get_profile", return_value=profile), \
         patch.object(hs.fmp_client, "get_balance_sheet", return_value=bs), \
         patch.object(hs.fmp_client, "get_income_statement", return_value=income), \
         patch("app.services.market_data.fetch", return_value=df):
        result = hs.screen_symbol("TEST")

    assert result is not None
    assert result["debt_pass"] is False
    assert result["is_halal"] is False
    assert result["mcap_basis"] == "avg_24m"
