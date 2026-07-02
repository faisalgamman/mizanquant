"""Tests for the isolated paper-validation ledger.

Uses an in-memory SQLite DB (SessionLocal monkeypatched) plus an injected funnel
and price fetch, so it is deterministic and offline. Verifies: picks become OPEN
PV trades (deduped), and the Option-A maturation closes a stopped trade with a
written pnl_pct while leaving an immature trade open.
"""
from __future__ import annotations

from datetime import datetime

import pytest

pd = pytest.importorskip("pandas")
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.db.database import Base  # noqa: E402
import app.db.models  # noqa: E402,F401  — register ORM tables on Base.metadata
from app.db.models import TradeHistory  # noqa: E402
from app.services import paper_validation as pv  # noqa: E402


@pytest.fixture
def tdb(monkeypatch):
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    TestSession = sessionmaker(bind=eng)
    monkeypatch.setattr(pv, "SessionLocal", TestSession)
    return TestSession


@pytest.fixture(autouse=True)
def _stub_weekly_signal_parts(monkeypatch):
    """Keep the record_weekly_picks tests offline and independent of the Phase-2
    with-trend entry gate: default the entry signals to empty so the gate fails OPEN
    (records as before). Tests that exercise the gate override this with their own stub."""
    monkeypatch.setattr(pv, "_weekly_signal_parts", lambda s: {})


def _pick(sym, entry=100.0, shares=10, verdict="BUY", conf=60.0):
    return {
        "symbol": sym, "entry": entry, "catastrophe_stop": round(entry * 0.85, 2),
        "far_take_profit": round(entry * 1.45, 2), "shares": shares,
        "position_value": round(shares * entry, 2), "risk_amount": round(shares * entry * 0.15, 2),
        "risk_pct_realized": 1.5, "confidence": conf, "verdict": verdict,
        "hold_days": 20, "stop_pct": 15.0, "time_exit_date": "2026-07-06", "votes": "8/0/5",
    }


def test_paper_row_from_pick():
    row = pv._paper_row_from_pick(_pick("AAA", entry=100.0, shares=10))
    assert row["strategy_id"] == "PV" and row["side"] == "buy"
    assert row["stop_loss"] == 85.0 and row["qty"] == 10.0
    assert row["status"] == "open" and row["signal_details"]["source"] == "paper_validation"


def test_record_inserts_open_and_dedups(tdb, monkeypatch):
    report = {"picks": [_pick("AAA"), _pick("BBB"), _pick("CCC", shares=0)]}
    monkeypatch.setattr("app.services.weekly_report.build_weekly_report", lambda *a, **k: report)
    _bull = lambda: {"known": True, "spy_bearish": False}

    r1 = pv.record_weekly_picks(account=10000, _regime_fn=_bull)
    assert r1["recorded"] == 2 and r1["skipped"] == 1   # CCC has 0 shares → skipped

    r2 = pv.record_weekly_picks(account=10000, _regime_fn=_bull)  # AAA/BBB open → deduped
    assert r2["recorded"] == 0

    db = tdb()
    try:
        n_open = db.query(TradeHistory).filter(
            TradeHistory.strategy_id == "PV", TradeHistory.pnl_pct.is_(None)).count()
        assert n_open == 2
        t = db.query(TradeHistory).filter(TradeHistory.symbol == "AAA").first()
        assert t.status == "open" and t.pnl_pct is None and t.stop_loss == 85.0
    finally:
        db.close()


def test_record_also_persists_swing_signal_history(tdb, monkeypatch):
    """C4: each inserted weekly pick is ALSO recorded to SignalHistory as a
    'swing' signal (with the usx breakdown when present), and the PV open-trade
    dedup prevents duplicate SignalHistory rows on a second run."""
    p_a = _pick("AAA")
    p_a.update({"usx_score": 72.0, "usx_pass": True,
                "usx_signals": ["MACD+", "RS20"], "usx_version": "v2-2026-06",
                "usx_shadow": {"v1_score": 55.0, "v2_score": None, "active": "v2"}})
    report = {"picks": [p_a, _pick("BBB"), _pick("CCC", shares=0)]}
    monkeypatch.setattr("app.services.weekly_report.build_weekly_report", lambda *a, **k: report)
    _bull = lambda: {"known": True, "spy_bearish": False}

    calls = []
    import app.background.cache_manager as cm
    monkeypatch.setattr(cm, "record_signal", lambda **kw: calls.append(kw))

    r1 = pv.record_weekly_picks(account=10000, _regime_fn=_bull)
    assert r1["recorded"] == 2
    assert len(calls) == 2, "record_signal must run once per INSERTED pick (not skipped ones)"
    assert all(c["signal_type"] == "swing" for c in calls)
    by_sym = {c["symbol"]: c for c in calls}
    assert by_sym["AAA"]["breakdown"]["usx_score"] == 72.0
    assert by_sym["AAA"]["breakdown"]["usx_version"] == "v2-2026-06"
    assert by_sym["AAA"]["breakdown"]["usx_shadow"]["v1_score"] == 55.0
    assert by_sym["AAA"]["details"]["source"] == "weekly_scanner"
    assert "usx_score" not in (by_sym["BBB"]["breakdown"] or {})  # BBB carries no usx fields

    pv.record_weekly_picks(account=10000, _regime_fn=_bull)   # AAA/BBB open → PV dedup
    assert len(calls) == 2, "second run must not duplicate SignalHistory rows"


# ── Weekly broad-market regime gate (don't pile swing longs into a downtrend) ──

def test_weekly_record_skipped_when_spy_bearish(tdb, monkeypatch):
    report = {"picks": [_pick("AAA"), _pick("BBB")]}
    monkeypatch.setattr("app.services.weekly_report.build_weekly_report", lambda *a, **k: report)
    out = pv.record_weekly_picks(
        account=10000, _regime_fn=lambda: {"known": True, "spy_bearish": True})
    assert out["recorded"] == 0 and out["reason"] == "spy_bearish"
    db = tdb()
    try:
        n = db.query(TradeHistory).filter(
            TradeHistory.strategy_id == "PV", TradeHistory.pnl_pct.is_(None)).count()
        assert n == 0           # nothing recorded into the downtrend
    finally:
        db.close()


def test_weekly_record_runs_when_spy_bullish(tdb, monkeypatch):
    report = {"picks": [_pick("AAA"), _pick("BBB")]}
    monkeypatch.setattr("app.services.weekly_report.build_weekly_report", lambda *a, **k: report)
    out = pv.record_weekly_picks(
        account=10000, _regime_fn=lambda: {"known": True, "spy_bearish": False})
    assert out["recorded"] == 2


def test_weekly_record_unknown_regime_fails_open(tdb, monkeypatch):
    report = {"picks": [_pick("AAA")]}
    monkeypatch.setattr("app.services.weekly_report.build_weekly_report", lambda *a, **k: report)
    out = pv.record_weekly_picks(account=10000, _regime_fn=lambda: {"known": False})
    assert out["recorded"] == 1   # absence of a regime signal must not block


def test_weekly_record_gate_off_records_in_downtrend(tdb, monkeypatch):
    monkeypatch.setenv("WEEKLY_REGIME_GATE", "false")
    report = {"picks": [_pick("AAA")]}
    monkeypatch.setattr("app.services.weekly_report.build_weekly_report", lambda *a, **k: report)
    out = pv.record_weekly_picks(
        account=10000, _regime_fn=lambda: {"known": True, "spy_bearish": True})
    assert out["recorded"] == 1   # gate disabled → records despite the downtrend


def _seed_open(tdb, symbol="AAA", entry=100.0, qty=10):
    db = tdb()
    try:
        db.add(TradeHistory(strategy_id="PV", symbol=symbol, side="buy", qty=qty,
                            entry_price=entry, stop_loss=round(entry * 0.85, 2), status="open",
                            created_at=datetime(2024, 1, 1)))
        db.commit()
    finally:
        db.close()


def test_mature_closes_stopped_trade(tdb, monkeypatch):
    _seed_open(tdb, "AAA", entry=100.0, qty=10)
    idx = pd.date_range("2024-01-02", periods=5, freq="D")
    bars = pd.DataFrame({"low": [99, 84, 90, 90, 90], "high": [101, 100, 99, 96, 97],
                         "close": [99, 80, 90, 90, 95]}, index=idx)  # day2 low 84 ≤ 90 → 10% safety-net stop
    monkeypatch.setattr("app.services.market_data.fetch", lambda *a, **k: bars)

    out = pv.mature_open_paper_trades()
    assert out["closed"] == 1

    db = tdb()
    try:
        t = db.query(TradeHistory).filter(TradeHistory.symbol == "AAA").first()
        assert t.status == "closed"
        # Loss discipline: stop is now capped at the −10% safety net (was a fixed −15%).
        assert t.pnl_pct == -10.0 and t.exit_price == 90.0
        assert t.pnl == round((90.0 - 100.0) * 10, 2) and t.closed_at is not None
    finally:
        db.close()


def test_mature_leaves_immature_open(tdb, monkeypatch):
    _seed_open(tdb, "BBB", entry=100.0, qty=10)
    idx = pd.date_range("2024-01-02", periods=5, freq="D")
    bars = pd.DataFrame({"low": [99, 98, 99, 100, 101], "high": [101, 102, 103, 104, 105],
                         "close": [100, 101, 102, 103, 104]}, index=idx)  # no stop, < 20 days
    monkeypatch.setattr("app.services.market_data.fetch", lambda *a, **k: bars)

    out = pv.mature_open_paper_trades()
    assert out["closed"] == 0

    db = tdb()
    try:
        t = db.query(TradeHistory).filter(TradeHistory.symbol == "BBB").first()
        assert t.status == "open" and t.pnl_pct is None
    finally:
        db.close()


# ── Monthly composite ledger (PVM) — rebalance ────────────────────────────────

def _mpick(sym, price, score):
    return {"symbol": sym, "price": price, "score": score}


def _seed_open_pvm(tdb, symbol, entry, qty=5):
    db = tdb()
    try:
        db.add(TradeHistory(strategy_id=pv.PV_MONTHLY, symbol=symbol, side="buy", qty=qty,
                            entry_price=entry, status="open", created_at=datetime(2024, 1, 1)))
        db.commit()
    finally:
        db.close()


def test_monthly_row_from_pick_equal_weight():
    row = pv._monthly_row_from_pick(_mpick("AAA", 100.0, 88), account=10000.0, top_n=10)
    # equal-weight: budget = 10000/10 = 1000 → floor(1000/100) = 10 shares, no stop
    assert row["strategy_id"] == "PVM" and row["qty"] == 10.0
    assert row["entry_price"] == 100.0 and row.get("stop_loss") is None
    assert row["signal_details"]["source"] == "paper_validation_monthly"


def test_rebalance_opens_top_n_on_empty_ledger(tdb):
    picks = [_mpick("AAA", 100, 90), _mpick("BBB", 50, 80), _mpick("CCC", 25, 70)]
    out = pv.rebalance_monthly(top_n=2, account=10000.0, _picks_fn=lambda n: picks,
                               _vol_fn=lambda s: None)
    assert out == {"target": 2, "opened": 2, "closed": 0, "held": 0, "stopped": 0}

    db = tdb()
    try:
        syms = {r.symbol for r in db.query(TradeHistory).filter(
            TradeHistory.strategy_id == "PVM", TradeHistory.pnl_pct.is_(None)).all()}
        assert syms == {"AAA", "BBB"}      # CCC ranked 3rd → outside top-2
    finally:
        db.close()


def test_rebalance_closes_dropouts_opens_entrants_keeps_held(tdb):
    # Held: AAA (stays in top-N) and DDD (fell out → must close at current price).
    _seed_open_pvm(tdb, "AAA", entry=100.0, qty=5)
    _seed_open_pvm(tdb, "DDD", entry=40.0, qty=10)
    # New ranking top-2 = AAA, BBB. DDD priced at 50 (was 40) → +25% on close.
    picks = [_mpick("AAA", 110, 95), _mpick("BBB", 60, 85),
             _mpick("CCC", 30, 60), _mpick("DDD", 50, 30)]
    out = pv.rebalance_monthly(top_n=2, account=10000.0, _picks_fn=lambda n: picks,
                               _vol_fn=lambda s: None)
    assert out == {"target": 2, "opened": 1, "closed": 1, "held": 1, "stopped": 0}

    db = tdb()
    try:
        ddd = db.query(TradeHistory).filter(TradeHistory.symbol == "DDD").first()
        assert ddd.status == "closed" and ddd.pnl_pct == 25.0
        assert ddd.exit_price == 50.0 and ddd.pnl == round((50.0 - 40.0) * 10, 2)
        aaa = db.query(TradeHistory).filter(TradeHistory.symbol == "AAA").first()
        assert aaa.status == "open" and aaa.pnl_pct is None   # kept (still top-N)
        bbb = db.query(TradeHistory).filter(TradeHistory.symbol == "BBB").first()
        assert bbb is not None and bbb.status == "open"        # new entrant opened
    finally:
        db.close()


def test_rebalance_no_picks_is_noop(tdb):
    out = pv.rebalance_monthly(top_n=2, _picks_fn=lambda n: [])
    assert out["opened"] == 0 and out["closed"] == 0


# ── Hysteresis exit band (Phase 2 — cut churn / whipsaw) ──────────────────────

def test_rebalance_hysteresis_keeps_name_just_outside_topn(tdb):
    # Held EEE sits at rank 3 with top_n=2 → inside the 1.5×N buffer (exit_rank=3) → KEPT,
    # not churned. Without hysteresis it would close the moment it left the top-2.
    _seed_open_pvm(tdb, "AAA", entry=100.0, qty=5)
    _seed_open_pvm(tdb, "EEE", entry=40.0, qty=10)
    picks = [_mpick("AAA", 110, 95), _mpick("BBB", 60, 85),
             _mpick("EEE", 45, 70), _mpick("CCC", 30, 60)]
    out = pv.rebalance_monthly(top_n=2, account=10000.0, _picks_fn=lambda n: picks,
                               _vol_fn=lambda s: None)
    assert out["closed"] == 0          # EEE (rank 3) within buffer → no churn
    db = tdb()
    try:
        eee = db.query(TradeHistory).filter(TradeHistory.symbol == "EEE").first()
        assert eee.status == "open" and eee.pnl_pct is None
    finally:
        db.close()


def test_rebalance_buffer_1_0_is_strict_topn(tdb, monkeypatch):
    # MONTHLY_EXIT_BUFFER=1.0 → exit_rank == top_n → no hysteresis (strict top-N exit).
    monkeypatch.setenv("MONTHLY_EXIT_BUFFER", "1.0")
    _seed_open_pvm(tdb, "EEE", entry=40.0, qty=10)
    picks = [_mpick("AAA", 110, 95), _mpick("BBB", 60, 85), _mpick("EEE", 45, 70)]
    pv.rebalance_monthly(top_n=2, account=10000.0, _picks_fn=lambda n: picks,
                         _vol_fn=lambda s: None)
    db = tdb()
    try:
        eee = db.query(TradeHistory).filter(TradeHistory.symbol == "EEE").first()
        assert eee.status == "closed"  # rank 3 > exit_rank(2) → closed at +12.5%
        assert eee.pnl_pct == 12.5
    finally:
        db.close()


# ── Loose catastrophe stop overlay (Phase 2 — in-rank blowup safety net) ──────

def test_catastrophe_stop_closes_in_rank_crash(tdb):
    # AAA is still rank-1 (inside the buffer) but has crashed 35% from entry → the loose
    # stop fires anyway (the rank exit alone would have kept it).
    _seed_open_pvm(tdb, "AAA", entry=100.0, qty=5)
    picks = [_mpick("AAA", 65, 95), _mpick("BBB", 60, 85)]   # AAA 100→65 = -35%
    out = pv.rebalance_monthly(top_n=2, account=10000.0, _picks_fn=lambda n: picks,
                               _vol_fn=lambda s: None)
    assert out["stopped"] == 1
    db = tdb()
    try:
        aaa = db.query(TradeHistory).filter(TradeHistory.symbol == "AAA").first()
        assert aaa.status == "closed" and aaa.pnl_pct == -35.0
    finally:
        db.close()


def test_catastrophe_stop_is_loose_ignores_small_drawdown(tdb):
    # 10% down and in-rank → the wide 30% stop must NOT fire (no weekly-style whipsaw).
    _seed_open_pvm(tdb, "AAA", entry=100.0, qty=5)
    picks = [_mpick("AAA", 90, 95), _mpick("BBB", 60, 85)]
    out = pv.rebalance_monthly(top_n=2, account=10000.0, _picks_fn=lambda n: picks,
                               _vol_fn=lambda s: None)
    assert out["stopped"] == 0
    db = tdb()
    try:
        aaa = db.query(TradeHistory).filter(TradeHistory.symbol == "AAA").first()
        assert aaa.status == "open"
    finally:
        db.close()


def test_catastrophe_stop_disabled_by_env(tdb, monkeypatch):
    monkeypatch.setenv("MONTHLY_CAT_STOP_PCT", "0")
    _seed_open_pvm(tdb, "AAA", entry=100.0, qty=5)
    picks = [_mpick("AAA", 50, 95), _mpick("BBB", 60, 85)]   # -50% but stop off
    out = pv.rebalance_monthly(top_n=2, account=10000.0, _picks_fn=lambda n: picks,
                               _vol_fn=lambda s: None)
    assert out["stopped"] == 0
    db = tdb()
    try:
        aaa = db.query(TradeHistory).filter(TradeHistory.symbol == "AAA").first()
        assert aaa.status == "open"   # stop disabled → kept despite the crash
    finally:
        db.close()


# ── Component-attribution data pipeline (sub-scores must reach the ledger) ─────

def test_normalize_picks_preserves_existing_parts():
    # _default_monthly_picks normalizes, then rebalance_monthly normalizes AGAIN — the
    # second pass must NOT wipe the sub-scores (the n=0 component_attribution bug).
    once = pv._normalize_picks([{"symbol": "AAA", "price": 100, "composite_score": 80,
                                 "score_tech": 70, "score_fund": 60, "conviction_score": 5}])
    assert once[0]["parts"] == {"score_tech": 70, "score_fund": 60, "conviction_score": 5}
    twice = pv._normalize_picks(once)
    assert twice[0]["parts"] == once[0]["parts"]   # preserved through re-normalization


def test_monthly_row_stores_parts_in_signal_details():
    pick = {"symbol": "AAA", "price": 100, "score": 80,
            "parts": {"score_tech": 70, "score_fund": 60}}
    row = pv._monthly_row_from_pick(pick, account=10000.0, top_n=10)
    assert row["signal_details"]["score_tech"] == 70
    assert row["signal_details"]["score_fund"] == 60


# ── Conviction × inverse-vol weighting (Phase 1 — raise risk-adjusted return) ──

def test_vol_weights_tilt_to_lower_volatility():
    # equal score → pure risk-parity: the lower-vol name gets the larger weight
    picks = [_mpick("AAA", 100, 80), _mpick("BBB", 100, 80)]
    vols = {"AAA": 0.20, "BBB": 0.40}
    w = pv._conviction_vol_weights(picks, top_n=2, vol_fn=lambda s: vols[s])
    assert abs(sum(w.values()) - 1.0) < 1e-9
    assert w["AAA"] > w["BBB"]


def test_vol_weights_equal_when_flag_off(monkeypatch):
    monkeypatch.setenv("MONTHLY_VOL_WEIGHT", "false")
    picks = [_mpick("AAA", 100, 90), _mpick("BBB", 100, 50)]
    w = pv._conviction_vol_weights(picks, top_n=2, vol_fn=lambda s: 0.2)
    assert w == {"AAA": 0.5, "BBB": 0.5}


def test_vol_weights_fallback_equal_when_vol_unknown():
    picks = [_mpick("AAA", 100, 90), _mpick("BBB", 100, 50)]
    w = pv._conviction_vol_weights(picks, top_n=2, vol_fn=lambda s: None)
    assert w == {"AAA": 0.5, "BBB": 0.5}


def test_vol_weights_clamp_prevents_domination():
    # extreme spread (low-vol high-score AAA): without the clamp AAA ≈ 0.99; the soft
    # cap pulls it well down and the floor lifts the dust names off ~0.
    picks = [_mpick("AAA", 100, 99), _mpick("BBB", 100, 1), _mpick("CCC", 100, 1)]
    vols = {"AAA": 0.05, "BBB": 0.9, "CCC": 0.9}
    w = pv._conviction_vol_weights(picks, top_n=3, vol_fn=lambda s: vols[s])
    assert abs(sum(w.values()) - 1.0) < 1e-9
    assert w["AAA"] == max(w.values()) and w["AAA"] < 0.8     # clamped down from ~0.99
    assert w["BBB"] == w["CCC"] and w["BBB"] > 0.05           # floor lifted them


def test_weekly_row_stores_factor_parts():
    """Weekly picks carry entry-time factor signals into signal_details so
    component_attribution can measure the per-factor edge (Phase 1: instrument)."""
    from app.services.paper_validation import _paper_row_from_pick
    row = _paper_row_from_pick({
        "symbol": "AAA", "shares": 10, "entry": 100.0, "verdict": "BUY",
        "parts": {"wk_rs": 5.2, "wk_rsi": 58.0, "wk_above_ema20": 1, "wk_atr_pct": 2.1},
    })
    sd = row["signal_details"]
    assert sd["source"] == "paper_validation"
    assert sd["wk_rs"] == 5.2 and sd["wk_above_ema20"] == 1 and sd["wk_atr_pct"] == 2.1


# --- Phase 2: with-trend entry gate -------------------------------------------------

def test_entry_gate_passes_with_trend(monkeypatch):
    monkeypatch.setenv("WEEKLY_ENTRY_FILTER", "true")
    ok, _ = pv._weekly_entry_ok({"wk_above_ema20": 1, "wk_rs": 3.0})
    assert ok is True


def test_entry_gate_rejects_below_ema20(monkeypatch):
    monkeypatch.setenv("WEEKLY_ENTRY_FILTER", "true")
    ok, why = pv._weekly_entry_ok({"wk_above_ema20": 0, "wk_rs": 3.0})
    assert ok is False and "EMA20" in why


def test_entry_gate_rejects_deeply_lagging_rs(monkeypatch):
    monkeypatch.setenv("WEEKLY_ENTRY_FILTER", "true")
    monkeypatch.setenv("WEEKLY_MIN_RS", "-2")
    ok, why = pv._weekly_entry_ok({"wk_above_ema20": 1, "wk_rs": -5.0})
    assert ok is False and "RS" in why


def test_entry_gate_fails_open_on_missing_signals(monkeypatch):
    monkeypatch.setenv("WEEKLY_ENTRY_FILTER", "true")
    assert pv._weekly_entry_ok({})[0] is True           # no signals → allow
    assert pv._weekly_entry_ok({"wk_rsi": 55})[0] is True  # rs/above absent → allow


def test_entry_gate_off_bypasses(monkeypatch):
    monkeypatch.setenv("WEEKLY_ENTRY_FILTER", "false")
    ok, _ = pv._weekly_entry_ok({"wk_above_ema20": 0, "wk_rs": -20.0})
    assert ok is True


def test_record_filters_counter_trend_pick(tdb, monkeypatch):
    """A below-EMA20 pick is NOT recorded to the ledger; a with-trend one is."""
    monkeypatch.setenv("WEEKLY_ENTRY_FILTER", "true")
    monkeypatch.setattr("app.services.weekly_report.build_weekly_report",
                        lambda *a, **k: {"picks": [_pick("UPP"), _pick("DWN")]})
    monkeypatch.setattr(pv, "_weekly_signal_parts",
                        lambda s: {"wk_above_ema20": 1 if s == "UPP" else 0, "wk_rs": 2.0})
    res = pv.record_weekly_picks(_regime_fn=lambda: {"known": True, "spy_bearish": False})
    assert res["recorded"] == 1 and res["filtered"] == 1
    assert res["filtered_syms"] == ["DWN"]
    db = tdb()
    try:
        syms = {r[0] for r in db.query(TradeHistory.symbol).all()}
        assert "UPP" in syms and "DWN" not in syms
    finally:
        db.close()
