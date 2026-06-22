"""Tests for the advisory weekly swing-picks report.

Pins the Option-A plan math + cap-aware sizing, the funnel enrichment/filtering,
and the read-only (no order-placing) guarantee. All network/DB boundaries are
injected so the suite is deterministic and offline.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.services import weekly_report as wr


# ── Option-A plan ─────────────────────────────────────────────────────────────

def test_option_a_plan_core_values():
    p = wr.build_option_a_plan(
        100.0, 100_000.0,
        risk_pct=1.5, max_pos_pct=15.0, stop_pct=15.0, hold_days=20,
        asof=date(2026, 6, 8),
    )
    assert p["entry"] == 100.0
    assert p["catastrophe_stop"] == 85.0          # fixed 15% below entry
    assert p["far_take_profit"] == 145.0          # entry*(1+3*15%)
    assert p["risk_per_share"] == 15.0
    # Risk budget binds: int(100000*1.5% / 15) = 100 (position cap 150 is looser).
    assert p["shares"] == 100
    assert p["position_value"] == 10_000.0
    assert p["risk_amount"] == 1_500.0


def test_time_exit_is_about_20_trading_days_ahead_and_a_weekday():
    p = wr.build_option_a_plan(100.0, 100_000.0, asof=date(2026, 6, 8))
    exit_d = date.fromisoformat(p["time_exit_date"])
    delta = (exit_d - date(2026, 6, 8)).days
    assert 26 <= delta <= 30          # ~20 business days ≈ 28 calendar days
    assert exit_d.weekday() < 5       # lands on a weekday


@pytest.mark.parametrize("price,account,stop_pct", [
    (0.0, 100_000.0, 15.0),    # bad price
    (100.0, 0.0, 15.0),        # no account
    (100.0, 100_000.0, 0.0),   # zero stop -> zero risk_per_share
])
def test_plan_guards_return_zero_shares(price, account, stop_pct):
    p = wr.build_option_a_plan(price, account, stop_pct=stop_pct)
    assert p["shares"] == 0


# ── Weekly report orchestration ───────────────────────────────────────────────

def _fake_funnel():
    # header + two buys (one STRONG, one BUY) + a HOLD + a low-confidence BUY
    return [
        {"Pipeline": "header row, no Symbol"},
        {"Symbol": "AAA", "Verdict": "BUY", "Confidence %": 60,
         "Price": 200.0, "Votes BUY": 8, "Votes SELL": 2, "Votes HOLD": 4},
        {"Symbol": "BBB", "Verdict": "STRONG BUY", "Confidence %": 75,
         "Price": 50.0, "Votes BUY": 11, "Votes SELL": 1, "Votes HOLD": 2},
        {"Symbol": "CCC", "Verdict": "HOLD", "Confidence %": 90, "Price": 10.0},
        {"Symbol": "DDD", "Verdict": "BUY", "Confidence %": 20, "Price": 30.0},
    ]


def _fake_forward_pf():
    return [{"Message": "No evaluated signals in last 90 days."}]


def _fake_graduation():
    return {"graduated": False, "reason": "n_trades=0<30", "n_trades": 0}


def test_report_enriches_filters_and_sorts(monkeypatch):
    # Hermetic: neutralize the USX overlay (identity). Without this the test
    # depended on LIVE/cached price data for ticker "AAA" (a real symbol), so
    # its order assertion silently changed whenever USX weights changed (v1→v2).
    # USX scoring itself is covered by tests/test_usx_layer.py.
    import app.services.usx_layer as _usx
    monkeypatch.setattr(_usx, "enrich_picks_with_usx", lambda picks, **kw: picks)

    rep = wr.build_weekly_report(
        10_000.0, top=15, min_confidence=45.0,
        _funnel_fn=_fake_funnel, _forward_pf_fn=_fake_forward_pf,
        _graduation_fn=_fake_graduation, asof=date(2026, 6, 8),
    )
    picks = rep["picks"]
    # HOLD and the 20%-confidence BUY are dropped; STRONG BUY sorts first
    # (the report pre-sorts by verdict/confidence; the neutralized USX sort is stable).
    assert [p["symbol"] for p in picks] == ["BBB", "AAA"]
    bbb = picks[0]
    assert bbb["verdict"] == "STRONG BUY"
    assert bbb["catastrophe_stop"] == round(50.0 * 0.85, 2)   # 42.5
    assert bbb["shares"] > 0
    assert bbb["votes"] == "11/1/2"
    # Status block present and honest.
    assert rep["graduation"]["graduated"] is False
    assert "ADVISORY ONLY" in rep["advisory_banner"]
    assert rep["forward_pf"] == _fake_forward_pf()


def test_empty_funnel_yields_no_picks():
    rep = wr.build_weekly_report(
        10_000.0, _funnel_fn=lambda: [{"Pipeline": "x"}],
        _forward_pf_fn=_fake_forward_pf, _graduation_fn=_fake_graduation,
    )
    assert rep["picks"] == []
    assert "لا توصيات" in wr.format_report(rep)


def test_funnel_exception_is_contained():
    def boom():
        raise RuntimeError("data outage")
    rep = wr.build_weekly_report(
        10_000.0, _funnel_fn=boom,
        _forward_pf_fn=_fake_forward_pf, _graduation_fn=_fake_graduation,
    )
    assert rep["picks"] == []          # degrades gracefully, no crash


def test_format_report_renders_table():
    rep = wr.build_weekly_report(
        10_000.0, _funnel_fn=_fake_funnel, _forward_pf_fn=_fake_forward_pf,
        _graduation_fn=_fake_graduation, asof=date(2026, 6, 8),
    )
    text = wr.format_report(rep)
    assert "WEEKLY SWING PICKS" in text
    assert "BBB" in text and "AAA" in text
    assert "Option-A" in text                 # exit policy stated in banner


def test_report_module_does_not_touch_live_trading():
    """Guard: the advisory report must not depend on the order-placing engine."""
    import inspect
    src = inspect.getsource(wr)
    assert "trading_engine" not in src
    assert "execute_buy" not in src


# ── /weekly_picks endpoint ────────────────────────────────────────────────────

_CANNED = {
    "picks": [{"symbol": "AAPL", "verdict": "STRONG BUY", "confidence": 72.0,
               "entry": 100.0, "catastrophe_stop": 85.0, "time_exit_date": "2026-07-06",
               "shares": 10, "risk_amount": 150.0, "votes": "10/1/3"}],
    "advisory_banner": "ADVISORY ONLY - Option-A exit", "asof": "2026-06-08",
    "account": 10000.0, "risk_pct": 1.5, "max_pos_pct": 15.0,
    "market_note": "market regime: NEUTRAL",
    "graduation": {"graduated": False, "reason": "n_trades=0<30"},
    "forward_pf": [{"Message": "none"}],
}


def test_weekly_picks_endpoint(monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import halal_screener as hs
    from app.services import weekly_report as wrmod
    from app.routers.consensus import router

    # Run the cache wrapper synchronously and stub the heavy funnel build.
    monkeypatch.setattr(
        hs, "_serve_or_compute",
        lambda key, func, args=(), kwargs=None, msg="": func(*args, **(kwargs or {})),
    )
    monkeypatch.setattr(wrmod, "build_weekly_report", lambda *a, **k: _CANNED)

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    r = client.get("/weekly_picks?account=10000&top=5")
    assert r.status_code == 200
    assert r.json()["picks"][0]["symbol"] == "AAPL"

    rt = client.get("/weekly_picks?account=10000&format=text")
    assert rt.status_code == 200
    assert "WEEKLY SWING PICKS" in rt.text and "AAPL" in rt.text

    # Out-of-range account is rejected by validate_range (HTTP 400).
    assert client.get("/weekly_picks?account=1").status_code == 400


# ── Swing funnel (Fly fix: record from the swing screener, not the AI pipeline) ──

def test_swing_funnel_maps_and_filters(monkeypatch):
    """funnel='swing' sources run_screener (same as /buys) and keeps only STRONG BUY/BUY."""
    import halal_screener as hs
    fake = [
        {"symbol": "AAA", "swing_signal": "STRONG BUY", "swing_score": 80, "price": 100.0},
        {"symbol": "BBB", "swing_signal": "BUY",        "swing_score": 60, "price": 50.0},
        {"symbol": "CCC", "swing_signal": "WATCH",      "swing_score": 40, "price": 20.0},
        {"symbol": "DDD", "swing_signal": "NO TRADE",   "swing_score": 10, "price": 8.0},
    ]
    monkeypatch.setattr(hs, "run_screener", lambda: fake, raising=False)

    mapped = wr._swing_funnel()
    assert {r["Symbol"] for r in mapped} == {"AAA", "BBB", "CCC", "DDD"}
    assert all(r["Confidence %"] == r["Swing Score"] for r in mapped)

    rep = wr.build_weekly_report(
        100_000.0, funnel="swing", _forward_pf_fn=lambda: {}, _graduation_fn=lambda: {},
    )
    # WATCH (40) and NO TRADE dropped; only the two BUY-verdict names remain.
    assert {p["symbol"] for p in rep["picks"]} == {"AAA", "BBB"}
    assert all(p["shares"] >= 0 for p in rep["picks"])
