"""Weekly-ledger inception reset.

The weekly (PV) ledger's pre-2026-06-27 rows were a single corrupt batch (duplicate
picks recorded 06-11/12 right before a market drop, every one a loss). The fix is a
NON-destructive reset: an inception cutoff so those rows are excluded from the reported
stats + graduation, while the monthly (PVM) and pairs (PVP) ledgers keep full history.
These pin the cutoff logic; the DB-filter wiring is exercised live by the dashboard.
"""
from datetime import datetime

import app.services.paper_validation as pv


def test_weekly_has_default_inception():
    inc = pv.ledger_inception(pv.PV_WEEKLY)
    assert isinstance(inc, datetime)
    assert inc.date().isoformat() == "2026-06-27"


def test_monthly_and_pairs_have_no_cutoff():
    # only the weekly ledger was reset — the others report their full history
    assert pv.ledger_inception(pv.PV_MONTHLY) is None
    assert pv.ledger_inception(pv.PV_PAIRS) is None


def test_env_overrides_inception(monkeypatch):
    monkeypatch.setenv("PV_WEEKLY_INCEPTION", "2026-07-01")
    assert pv.ledger_inception(pv.PV_WEEKLY).date().isoformat() == "2026-07-01"


def test_empty_env_disables_cutoff(monkeypatch):
    monkeypatch.setenv("PV_WEEKLY_INCEPTION", "")
    assert pv.ledger_inception(pv.PV_WEEKLY) is None


def test_bad_env_is_ignored(monkeypatch):
    monkeypatch.setenv("PV_WEEKLY_INCEPTION", "not-a-date")
    assert pv.ledger_inception(pv.PV_WEEKLY) is None
