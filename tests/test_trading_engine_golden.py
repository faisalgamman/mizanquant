"""Golden-master for trading_engine.execute_buy (guards the M-D refactor).

execute_buy sizes and submits real (paper) orders, so the M-D consolidation of
the 5 identical shadow-sizing layers must NOT change a single observable output.
This test pins, for a fixed input and fixed multipliers:
  * the final live ``qty`` and the submitted order payload, and
  * every ``sizing`` shadow-diagnostic key the layers write.

It mocks only the external boundaries (broker, halal gate, the 9 sizing
services, order submit/record/notify) so the function's own control flow runs
unchanged. One case exercises the shadow path (flags OFF → qty unchanged,
shadow keys recorded); one exercises the live-apply path (one flag ON).
"""
from __future__ import annotations

import pytest

from app.services import trading_engine as te
from app.config import settings


_MULTS = {  # deterministic per-layer multipliers
    "wavelet": 1.1, "kalman": 1.05, "mr": 1.2, "sentiment": 0.9,
    "factor": 1.0, "garch": 1.0, "cov": 1.0,
}


@pytest.fixture
def wired(monkeypatch):
    """Wire every execute_buy boundary to a deterministic stub.

    Returns the live ``sizing`` dict (mutated in place by the layers) plus a
    one-slot list capturing the submitted order payload.
    """
    captured: dict = {}
    sizing = {"qty": 50, "position_value": 5000.0, "risk_amount": 150.0,
              "risk_pct": 1.5, "regime": "NEUTRAL"}

    # ── trading flags ──
    monkeypatch.setattr(settings, "AUTO_TRADE_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "KILL_SWITCH", False, raising=False)
    monkeypatch.setattr(settings, "SWING_EXIT_ENABLED", False, raising=False)
    monkeypatch.setattr(settings, "TRAILING_STOP_ENABLED", False, raising=False)
    for flag in ("XGB_SIGNAL_GATE_LIVE", "WAVELET_SIZING_LIVE", "KALMAN_SIZING_LIVE",
                 "GARCH_SIZING_LIVE", "PORTFOLIO_COV_SIZING_LIVE", "MR_QUALITY_SIZING_LIVE",
                 "SENTIMENT_SIZING_LIVE", "FACTOR_SIZING_LIVE"):
        monkeypatch.setattr(settings, flag, False, raising=False)

    # ── broker + gates ──
    monkeypatch.setattr(te, "alpaca_get_account", lambda strategy_id=None: {"equity": "100000", "cash": "100000"})
    monkeypatch.setattr(te, "alpaca_get_positions", lambda strategy_id=None: [])
    monkeypatch.setattr(te, "check_trade_eligibility",
                        lambda **kw: {"eligible": True, "sizing": sizing, "guards": []})
    monkeypatch.setattr("halal_screener.verify_halal", lambda s: (True, "halal ok"))
    monkeypatch.setattr("app.services.portfolio_stop.check_drawdown", lambda: {"tier": "none", "message": ""})

    # ── the 9 sizing services ──
    monkeypatch.setattr("app.services.signal_classifier.classify_buy_signal",
                        lambda **kw: {"probability": 0.5, "model_available": False, "pass_gate": True})
    monkeypatch.setattr("app.services.wavelet_denoise.get_wavelet_adjustment", lambda s: _MULTS["wavelet"])
    monkeypatch.setattr("app.services.kalman_filter.get_kalman_adjustment", lambda s: _MULTS["kalman"])
    monkeypatch.setattr("app.services.garch_volatility.garch_vol_multiplier", lambda s: _MULTS["garch"])
    monkeypatch.setattr("app.services.portfolio_optimizer.get_strategy_multiplier", lambda sid: _MULTS["cov"])
    monkeypatch.setattr("app.services.mean_reversion_util.get_mr_multiplier", lambda s: _MULTS["mr"])
    monkeypatch.setattr("app.services.sentiment_engine.get_sentiment_multiplier", lambda s: _MULTS["sentiment"])
    monkeypatch.setattr("app.services.factor_exposure.get_factor_multiplier", lambda s: _MULTS["factor"])
    monkeypatch.setattr("app.services.execution_cost_estimator.estimate_execution_cost",
                        lambda *a, **k: {"blocked": False, "impact_bps": 1.0, "est_cost_bps": 2.0})
    monkeypatch.setattr("app.services.smart_execution.should_route", lambda qty: False)

    # ── order submission / side effects ──
    def _fake_submit(payload, strategy_id=None):
        captured["payload"] = payload
        return {"id": "ord-1", "client_order_id": payload.get("client_order_id", "")}

    monkeypatch.setattr(te, "_submit_order", _fake_submit)
    monkeypatch.setattr(te, "_record_trade", lambda *a, **k: None)
    monkeypatch.setattr(te, "_notify_trade", lambda *a, **k: None)
    monkeypatch.setattr(te, "_arm_bracket", lambda *a, **k: True)
    monkeypatch.setattr(te, "log_trade_event", lambda *a, **k: None)

    return sizing, captured


def _buy():
    return te.execute_buy(symbol="AAPL", price=100.0, stop_loss=98.0,
                          take_profit=106.0, confidence=80.0, signal_details={"score": 70})


def test_shadow_path_qty_unchanged_and_diagnostics_recorded(wired):
    sizing, captured = wired
    result = _buy()

    assert result["executed"] is True
    assert result["qty"] == 50                      # flags OFF → live qty untouched
    assert captured["payload"]["qty"] == "50"
    assert captured["payload"]["time_in_force"] == "gtc"
    assert captured["payload"]["order_class"] == "bracket"
    assert captured["payload"]["stop_loss"]["stop_price"] == "98.0"
    assert captured["payload"]["take_profit"]["limit_price"] == "106.0"

    # Each non-1.0 layer records a shadow qty = max(1, int(50 * mult)); 1.0 layers do not.
    assert sizing["wavelet_multiplier"] == 1.1 and sizing["wavelet_shadow_qty"] == 55
    assert sizing["kalman_multiplier"] == 1.05 and sizing["kalman_shadow_qty"] == 52
    assert sizing["mr_multiplier"] == 1.2 and sizing["mr_shadow_qty"] == 60
    assert sizing["sentiment_multiplier"] == 0.9 and sizing["sentiment_shadow_qty"] == 45
    assert sizing["factor_multiplier"] == 1.0 and "factor_shadow_qty" not in sizing


def test_live_apply_path_threads_qty_through_layers(wired, monkeypatch):
    sizing, captured = wired
    monkeypatch.setattr(settings, "WAVELET_SIZING_LIVE", True, raising=False)

    result = _buy()

    # Wavelet live → qty = int(50*1.1) = 55; later (shadow) layers size off 55.
    assert result["qty"] == 55
    assert captured["payload"]["qty"] == "55"
    assert sizing["qty"] == 55
    assert "Wavelet adjusted" in sizing.get("note", "")
    assert sizing["kalman_shadow_qty"] == int(55 * 1.05)   # 57
    assert sizing["mr_shadow_qty"] == int(55 * 1.2)        # 66
