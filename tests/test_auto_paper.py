"""Auto-paper executor — the safety guards (opt-in, paper-only, kill-switch, cap, dedupe)
and the placement loop. The broker + submit + record are injected so no socket/DB is needed."""
import app.services.auto_paper as ap

_PAPER = {"mode": "IB Gateway — Paper (socat relay)", "port": 4004}
_LIVE = {"mode": "IB Gateway — Live (direct)", "port": 4001}


class _FakeBroker:
    def __init__(self, held=()):
        self._held = list(held)

    def get_positions(self, strategy_id=None):
        return [{"symbol": s} for s in self._held]


def _picks(n):
    return [{"symbol": "S%d" % i, "entry": 100.0, "stop": 85.0, "tp": 130.0, "shares": 5}
            for i in range(n)]


def _ok_submit(p):
    return {"success": True, "order_id": "OID-" + p["symbol"], "status": "submitted"}


def test_disabled_when_flag_off(monkeypatch):
    monkeypatch.setenv("AUTO_PAPER_TRADE", "false")
    assert ap.run_auto_paper("weekly")["status"] == "disabled"


def test_aborts_when_broker_not_paper(monkeypatch):
    monkeypatch.setenv("AUTO_PAPER_TRADE", "true")
    monkeypatch.setattr("app.services.broker.ibkr_config.get_ibkr_config", lambda: _LIVE)
    out = ap.run_auto_paper("weekly", _broker=_FakeBroker(),
                            _picks_fn=lambda s: _picks(3), _submit_fn=_ok_submit)
    assert out["status"] == "aborted" and out["reason"] == "not_paper"


def test_kill_switch_halts(monkeypatch):
    monkeypatch.setenv("AUTO_PAPER_TRADE", "true")
    monkeypatch.setenv("KILL_SWITCH", "true")
    monkeypatch.setattr("app.services.broker.ibkr_config.get_ibkr_config", lambda: _PAPER)
    out = ap.run_auto_paper("weekly", _broker=_FakeBroker(),
                            _picks_fn=lambda s: _picks(3), _submit_fn=_ok_submit)
    assert out["status"] == "halted"


def test_places_up_to_cap_and_dedupes_held(monkeypatch):
    monkeypatch.setenv("AUTO_PAPER_TRADE", "true")
    monkeypatch.setenv("KILL_SWITCH", "false")
    monkeypatch.setenv("AUTO_PAPER_MAX_NEW", "2")
    monkeypatch.setattr("app.services.broker.ibkr_config.get_ibkr_config", lambda: _PAPER)
    monkeypatch.setattr(ap, "_today_manual_symbols", lambda: set())
    recorded = []
    monkeypatch.setattr(ap, "_record", lambda p, oid, scn: recorded.append((p["symbol"], oid)))
    # S0 is already held → skipped; cap 2 → only S1, S2 placed of S0..S4.
    out = ap.run_auto_paper("weekly", _broker=_FakeBroker(held=["S0"]),
                            _picks_fn=lambda s: _picks(5), _submit_fn=_ok_submit)
    assert out["status"] == "ok" and out["cap"] == 2
    assert out["placed"] == ["S1", "S2"] and len(recorded) == 2


def test_broker_offline_aborts(monkeypatch):
    monkeypatch.setenv("AUTO_PAPER_TRADE", "true")
    monkeypatch.setenv("KILL_SWITCH", "false")
    monkeypatch.setattr("app.services.broker.ibkr_config.get_ibkr_config", lambda: _PAPER)
    monkeypatch.setattr(ap, "_default_broker", lambda: None)
    out = ap.run_auto_paper("weekly", _picks_fn=lambda s: _picks(3), _submit_fn=_ok_submit)
    assert out["status"] == "aborted" and out["reason"] == "broker_offline"


def test_skips_already_auto_placed_today(monkeypatch):
    monkeypatch.setenv("AUTO_PAPER_TRADE", "true")
    monkeypatch.setenv("KILL_SWITCH", "false")
    monkeypatch.setenv("AUTO_PAPER_MAX_NEW", "5")
    monkeypatch.setattr("app.services.broker.ibkr_config.get_ibkr_config", lambda: _PAPER)
    monkeypatch.setattr(ap, "_today_manual_symbols", lambda: {"S1"})   # already placed today
    monkeypatch.setattr(ap, "_record", lambda p, oid, scn: None)
    out = ap.run_auto_paper("weekly", _broker=_FakeBroker(),
                            _picks_fn=lambda s: _picks(3), _submit_fn=_ok_submit)
    assert "S1" not in out["placed"] and "S0" in out["placed"] and "S2" in out["placed"]


# ── smart-exit monitor (IBKR-paper position manager) ─────────────────────────



class _FakeExitBroker:
    """Broker double whose positions are the ACTUAL holdings (symbol/qty/avg cost)."""
    def __init__(self, positions=(), orders=()):
        self._positions = [dict(p) for p in positions]
        self._orders = list(orders)
        self.canceled, self.closed, self.sold = [], [], []

    def get_positions(self, strategy_id=None):
        return [dict(p) for p in self._positions]

    def get_orders(self, status="all", strategy_id=None):
        return list(self._orders)

    def cancel_order(self, order_id, strategy_id=None):
        self.canceled.append(order_id); return True

    def close_position(self, symbol, strategy_id=None):
        self.closed.append(symbol); return {"id": "close-" + symbol}

    def submit_order(self, payload, strategy_id=None):
        self.sold.append(payload); return {"id": "sell-" + payload["symbol"]}


def _exit_bars(rows):
    import pandas as pd
    idx = pd.date_range("2024-06-03", periods=len(rows), freq="B")
    return pd.DataFrame([{"close": c, "high": h, "low": l} for c, h, l in rows], index=idx)


def _pos(sym, qty, avg):
    return {"symbol": sym, "qty": str(qty), "avg_entry_price": str(avg), "side": "long" if qty >= 0 else "short"}


_RUNNER = [(102, 103, 101), (108, 109, 107), (115, 116, 114), (120, 121, 119), (113, 114, 112)]  # +13%, off a +21% peak
_STOPPED = [(98, 99, 97), (84, 86, 83)]  # low 83 <= 85 catastrophe stop


def _no_db(monkeypatch):
    monkeypatch.setattr(ap, "_record_broker_exit", lambda *a, **k: None)
    monkeypatch.setattr(ap, "_recent_partial_taken", lambda *a, **k: False)


def test_smart_exit_monitor_disabled(monkeypatch):
    monkeypatch.setenv("AUTO_PAPER_TRADE", "false")
    assert ap.run_smart_exit_monitor()["status"] == "disabled"


def test_smart_exit_monitor_aborts_when_not_paper(monkeypatch):
    monkeypatch.setenv("AUTO_PAPER_TRADE", "true")
    monkeypatch.setattr("app.services.broker.ibkr_config.get_ibkr_config", lambda: _LIVE)
    out = ap.run_smart_exit_monitor(
        _broker=_FakeExitBroker(positions=[_pos("AAA", 100, 100)]),
        _entries_fn=lambda: {}, _bars_fn=lambda s: _exit_bars(_RUNNER))
    assert out["status"] == "aborted" and out["reason"] == "not_paper"


def test_partial_taken_on_broker_winner(monkeypatch):
    # A legacy broker winner (avg cost 100, now +13%, no ledger row) → sell half.
    monkeypatch.setenv("AUTO_PAPER_TRADE", "true")
    monkeypatch.setattr("app.services.broker.ibkr_config.get_ibkr_config", lambda: _PAPER)
    _no_db(monkeypatch)
    br = _FakeExitBroker(positions=[_pos("AAA", 100, 100)])
    out = ap.run_smart_exit_monitor(_broker=br, _entries_fn=lambda: {}, _bars_fn=lambda s: _exit_bars(_RUNNER))
    assert out["partial"] == 1 and out["closed"] == 0
    assert br.sold and int(br.sold[0]["qty"]) == 50 and br.sold[0]["side"] == "sell"
    assert br.closed == []                       # partial only — not flattened


def test_full_trailing_exit_when_partial_already_taken(monkeypatch):
    monkeypatch.setenv("AUTO_PAPER_TRADE", "true")
    monkeypatch.setattr("app.services.broker.ibkr_config.get_ibkr_config", lambda: _PAPER)
    monkeypatch.setattr(ap, "_record_broker_exit", lambda *a, **k: None)
    monkeypatch.setattr(ap, "_recent_partial_taken", lambda *a, **k: True)   # already scaled out
    br = _FakeExitBroker(positions=[_pos("AAA", 100, 100)], orders=[{"id": "O1", "symbol": "AAA"}])
    out = ap.run_smart_exit_monitor(_broker=br, _entries_fn=lambda: {}, _bars_fn=lambda s: _exit_bars(_RUNNER))
    assert out["closed"] == 1 and out["exits"][0]["reason"] == "trailing"
    assert br.canceled == ["O1"] and br.closed == ["AAA"]


def test_short_positions_are_skipped(monkeypatch):
    monkeypatch.setenv("AUTO_PAPER_TRADE", "true")
    monkeypatch.setattr("app.services.broker.ibkr_config.get_ibkr_config", lambda: _PAPER)
    _no_db(monkeypatch)
    br = _FakeExitBroker(positions=[_pos("SHT", -50, 100)])
    out = ap.run_smart_exit_monitor(_broker=br, _entries_fn=lambda: {}, _bars_fn=lambda s: _exit_bars(_RUNNER))
    assert out["shorts"] == 1 and out["closed"] == 0 and out["partial"] == 0
    assert br.sold == [] and br.closed == []      # a short is never touched


def test_legacy_position_acts_on_catastrophe_stop(monkeypatch):
    # No ledger row → no broker bracket → the monitor DOES honour the stop.
    monkeypatch.setenv("AUTO_PAPER_TRADE", "true")
    monkeypatch.setattr("app.services.broker.ibkr_config.get_ibkr_config", lambda: _PAPER)
    _no_db(monkeypatch)
    br = _FakeExitBroker(positions=[_pos("AAA", 100, 100)])
    out = ap.run_smart_exit_monitor(_broker=br, _entries_fn=lambda: {}, _bars_fn=lambda s: _exit_bars(_STOPPED))
    assert out["closed"] == 1 and out["exits"][0]["reason"] == "stop" and br.closed == ["AAA"]


def test_bracketed_position_skips_catastrophe_stop(monkeypatch):
    # Ledger row present → auto_paper bracket owns the stop → monitor must NOT flatten.
    monkeypatch.setenv("AUTO_PAPER_TRADE", "true")
    monkeypatch.setattr("app.services.broker.ibkr_config.get_ibkr_config", lambda: _PAPER)
    _no_db(monkeypatch)
    br = _FakeExitBroker(positions=[_pos("AAA", 100, 100)])
    out = ap.run_smart_exit_monitor(
        _broker=br, _entries_fn=lambda: {"AAA": (100.0, None)}, _bars_fn=lambda s: _exit_bars(_STOPPED))
    assert out["closed"] == 0 and br.closed == []
