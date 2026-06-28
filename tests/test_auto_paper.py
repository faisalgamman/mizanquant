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
