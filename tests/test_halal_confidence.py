"""Tests for halal reliability v2 — data-confidence flag + purification %.

Verifies that:
1. Full data → halal_confidence == "high", no reasons.
2. spot_fallback mcap → halal_confidence == "partial" with reason.
3. purification_pct == round(interest_ratio, 2).
4. Verdict math is UNCHANGED — same inputs, same is_halal regardless of confidence.
"""

import math

# ── helpers (mirror test_halal_djim.py) ──────────────────────────────

def _make_profile(mcap=1_000_000_000, shares=10_000_000, sector="Technology",
                  industry="Software"):
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
    return [{"revenue": revenue, "interestIncome": interest,
             "interestExpense": interest}]


# ── tests ───────────────────────────────────────────────────────────

class TestConfidenceHigh:
    """Full data → high confidence."""

    def test_full_data_yields_high_confidence(self, monkeypatch):
        """All data sources present → halal_confidence == 'high'."""
        from app.services.halal_screening import screen_symbol

        # Patch FMP client methods
        from app.services.fmp_client import fmp_client
        monkeypatch.setattr(fmp_client, "get_profile",
                            lambda s: _make_profile())
        monkeypatch.setattr(fmp_client, "get_balance_sheet",
                            lambda s, limit=1: _make_bs())
        monkeypatch.setattr(fmp_client, "get_income_statement",
                            lambda s, limit=1: _make_income())

        # Patch _avg_market_cap_24m to return 24m_avg basis
        import app.services.halal_screening as hs
        monkeypatch.setattr(hs, "_avg_market_cap_24m",
                            lambda s, sh, mc: (1_000_000_000, "avg_24m"))

        result = screen_symbol("TEST")

        assert result is not None
        assert result["halal_confidence"] == "high"
        assert result["confidence_reasons"] == []

    def test_confident_with_high_debt_but_full_data(self, monkeypatch):
        """Even a NON-COMPLIANT verdict can have high confidence."""
        from app.services.halal_screening import screen_symbol

        from app.services.fmp_client import fmp_client
        monkeypatch.setattr(fmp_client, "get_profile",
                            lambda s: _make_profile(mcap=1_000_000_000))
        monkeypatch.setattr(fmp_client, "get_balance_sheet",
                            lambda s, limit=1: _make_bs(debt=900_000_000))
        monkeypatch.setattr(fmp_client, "get_income_statement",
                            lambda s, limit=1: _make_income())

        import app.services.halal_screening as hs
        monkeypatch.setattr(hs, "_avg_market_cap_24m",
                            lambda s, sh, mc: (1_000_000_000, "avg_24m"))

        result = screen_symbol("TEST")

        assert result is not None
        assert result["is_halal"] is False  # debt > 33% → NON-COMPLIANT
        assert result["halal_confidence"] == "high"  # but data is complete
        assert result["confidence_reasons"] == []


class TestConfidencePartial:
    """spot_fallback → partial confidence; verdict math unchanged."""

    def test_spot_fallback_yields_partial(self, monkeypatch):
        """spot_fallback basis → halal_confidence == 'partial'."""
        from app.services.halal_screening import screen_symbol

        from app.services.fmp_client import fmp_client
        monkeypatch.setattr(fmp_client, "get_profile",
                            lambda s: _make_profile())
        monkeypatch.setattr(fmp_client, "get_balance_sheet",
                            lambda s, limit=1: _make_bs())
        monkeypatch.setattr(fmp_client, "get_income_statement",
                            lambda s, limit=1: _make_income())

        # Force spot_fallback
        import app.services.halal_screening as hs
        monkeypatch.setattr(hs, "_avg_market_cap_24m",
                            lambda s, sh, mc: (mc, "spot_fallback"))

        result = screen_symbol("TEST")

        assert result is not None
        assert result["halal_confidence"] == "partial"
        assert len(result["confidence_reasons"]) >= 1
        assert any("spot mcap" in r.lower() for r in result["confidence_reasons"])

    def test_verdict_unchanged_with_same_inputs(self, monkeypatch):
        """Same inputs, different mcap_basis → same is_halal verdict."""
        from app.services.halal_screening import screen_symbol
        from app.services.fmp_client import fmp_client

        def _setup(basis_fn):
            monkeypatch.setattr(fmp_client, "get_profile",
                                lambda s: _make_profile())
            monkeypatch.setattr(fmp_client, "get_balance_sheet",
                                lambda s, limit=1: _make_bs())
            monkeypatch.setattr(fmp_client, "get_income_statement",
                                lambda s, limit=1: _make_income())
            import app.services.halal_screening as hs
            monkeypatch.setattr(hs, "_avg_market_cap_24m", basis_fn)

        # Run with avg_24m (full data)
        _setup(lambda s, sh, mc: (1_000_000_000, "avg_24m"))
        result_full = screen_symbol("TEST")
        assert result_full is not None
        assert result_full["halal_confidence"] == "high"

        # Run with spot_fallback — same numbers, different basis
        _setup(lambda s, sh, mc: (1_000_000_000, "spot_fallback"))
        result_partial = screen_symbol("TEST")
        assert result_partial is not None
        assert result_partial["halal_confidence"] == "partial"

        # THE TEST: verdict math must be IDENTICAL
        assert result_full["is_halal"] == result_partial["is_halal"]
        assert result_full["debt_ratio"] == result_partial["debt_ratio"]
        assert result_full["interest_ratio"] == result_partial["interest_ratio"]
        assert result_full["screens_passed"] == result_partial["screens_passed"]


class TestPurificationPct:
    """purification_pct equals interest_ratio."""

    def test_purification_matches_interest_ratio(self, monkeypatch):
        """purification_pct == round(interest_ratio, 2)."""
        from app.services.halal_screening import screen_symbol
        from app.services.fmp_client import fmp_client

        monkeypatch.setattr(fmp_client, "get_profile",
                            lambda s: _make_profile())
        monkeypatch.setattr(fmp_client, "get_balance_sheet",
                            lambda s, limit=1: _make_bs())
        monkeypatch.setattr(fmp_client, "get_income_statement",
                            lambda s, limit=1: _make_income(
                                revenue=500_000_000, interest=12_500_000))

        import app.services.halal_screening as hs
        monkeypatch.setattr(hs, "_avg_market_cap_24m",
                            lambda s, sh, mc: (500_000_000, "avg_24m"))

        result = screen_symbol("TEST")

        assert result is not None
        # interest = 12.5M / revenue 500M = 2.5%
        assert math.isclose(result["interest_ratio"], 2.5, rel_tol=0.01)
        assert result["purification_pct"] == result["interest_ratio"]
        assert result["purification_note"] is not None
        assert "طهّر" in result["purification_note"]
