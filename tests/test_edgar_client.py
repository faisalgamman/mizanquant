"""Unit tests for the SEC EDGAR fundamentals parser (no network).

Exercises the pure XBRL-extraction helpers against a synthetic ``us-gaap`` facts dict,
covering: most-recent-annual selection, debt summation, the short-term-investments MAX,
and revenue tag-fallback (filers that report ``Revenues`` not the contract-revenue tag).
"""

from app.services import edgar_client as ed


def _instant(val, end, form="10-K"):
    return {"end": end, "val": val, "form": form, "fy": int(end[:4]), "fp": "FY"}


def _duration(val, start, end, form="10-K"):
    return {"start": start, "end": end, "val": val, "form": form, "fy": int(end[:4]), "fp": "FY"}


def _usd(entries):
    return {"units": {"USD": entries}}


def _facts():
    return {
        "Assets": _usd([_instant(1000, "2023-12-31"), _instant(1200, "2024-12-31")]),
        "LongTermDebtNoncurrent": _usd([_instant(300, "2024-12-31")]),
        "DebtCurrent": _usd([_instant(100, "2024-12-31")]),
        "CashAndCashEquivalentsAtCarryingValue": _usd([_instant(150, "2024-12-31")]),
        "MarketableSecuritiesCurrent": _usd([_instant(50, "2024-12-31")]),
        "AvailableForSaleSecuritiesCurrent": _usd([_instant(80, "2024-12-31")]),
        "AccountsReceivableNetCurrent": _usd([_instant(90, "2024-12-31")]),
        # Revenue reported under the legacy tag, not the contract-revenue tag.
        "Revenues": _usd([_duration(2000, "2024-01-01", "2024-12-31")]),
        "InterestExpense": _usd([_duration(12, "2024-01-01", "2024-12-31")]),
    }


def test_latest_annual_picks_most_recent_10k():
    f = _facts()
    assert ed._latest_fact(f, ed._TAGS_ASSETS, annual=True) == 1200  # 2024 over 2023


def test_sum_debt_lt_plus_current():
    f = _facts()
    assert ed._sum_debt(f) == 400  # 300 + 100


def test_short_term_investments_uses_max_not_first():
    f = _facts()
    sti = max(ed._latest_fact(f, [t], annual=True) or 0.0 for t in ed._TAGS_STI)
    assert sti == 80  # AvailableForSale (80) > MarketableSecurities (50); never summed to 130


def test_revenue_tag_fallback():
    f = _facts()
    # The contract-revenue tag is absent → falls through to "Revenues".
    assert ed._latest_fact(f, ed._TAGS_REVENUE, annual=True) == 2000


def test_annual_prefers_10k_over_later_10q():
    # A later-dated 10-Q must NOT outrank the annual 10-K when annual=True.
    f = {"Assets": _usd([
        _instant(1200, "2024-12-31", form="10-K"),
        _instant(1300, "2025-03-31", form="10-Q"),
    ])}
    assert ed._latest_fact(f, ["Assets"], annual=True) == 1200


def test_missing_tag_returns_none():
    assert ed._latest_fact({}, ["Assets"], annual=True) is None
