"""PVP pairs paper-ledger — strategy mapping, signal→row mapping, no-op record, status.

The existing test_pairs_strategy / test_cointegration cover the strategy + math; this
covers the NEW PVP validation ledger (mirror of PV/PVM). DB-write + mature paths are
exercised live by the scheduled cycle (no real broker orders).
"""
import app.services.paper_validation as pv


def test_strategy_for_pairs():
    assert pv.PV_PAIRS == "PVP"
    assert pv._strategy_for("pairs") == pv.PV_PAIRS
    assert pv._strategy_for("Pairs Scanner") == pv.PV_PAIRS
    assert pv._strategy_for("weekly") == pv.PV_WEEKLY
    assert pv._strategy_for("monthly") == pv.PV_MONTHLY


class _FakeSig:
    pair = "KO/PEP"
    action = "long_y"
    long_symbol = "KO"
    entry = 80.0
    stop_loss = 76.0
    take_profit = 88.0
    zscore = -2.3
    hedge_ratio = 0.9
    half_life = 12.0
    confidence = 60.0
    reason = "spread z<-2"


def test_pairs_row_from_signal_maps_long_leg():
    row = pv._pairs_row_from_signal(_FakeSig())
    assert row["strategy_id"] == "PVP"
    assert row["symbol"] == "KO"          # the long (undervalued) leg, not the pair
    assert row["side"] == "buy"
    assert row["entry_price"] == 80.0
    assert row["stop_loss"] == 76.0
    assert row["status"] == "open"
    assert row["qty"] > 0                 # nominal sizing so pnl is displayable
    d = row["signal_details"]
    assert d["pair"] == "KO/PEP" and d["long_symbol"] == "KO"
    assert d["source"] == "pairs_paper"


def test_record_pairs_no_entries(monkeypatch):
    # No actionable entries → no DB writes, clean no-op.
    monkeypatch.setattr("app.services.pairs_strategy.compute_signals", lambda **k: [])
    out = pv.record_pairs_signals()
    assert out.get("recorded") == 0


def test_record_pairs_skips_non_entry_actions(monkeypatch):
    class _Exit(_FakeSig):
        action = "exit"
    monkeypatch.setattr("app.services.pairs_strategy.compute_signals", lambda **k: [_Exit()])
    out = pv.record_pairs_signals()
    assert out.get("recorded") == 0  # only long_y/long_x are recorded as entries


def test_paper_ledger_status_pairs_label():
    st = pv.paper_ledger_status(pv.PV_PAIRS)
    assert st["scanner"] == "pairs"
    assert st["strategy_id"] == "PVP"
    assert "graduation" in st
    assert "open" in st and "closed" in st
