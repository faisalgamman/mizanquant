"""C2 — lock the live signal-display invariant.

The manual trader executes off the EXACT numbers shown in the Telegram alert,
so the displayed stop/hold must always match the validated Option-A exit policy
(fixed wide catastrophe stop = SWING_TRAIL_PCT, 20-day time exit). This test
fails loudly if a future change drifts the display away from config.
"""
from __future__ import annotations

import pytest

pytest.importorskip("pandas")


def test_swing_signal_display_matches_config(monkeypatch):
    from app.config import settings
    from app.services import signals_advisor as sa

    monkeypatch.setattr(settings, "SWING_EXIT_ENABLED", True)
    monkeypatch.setattr(settings, "SWING_TRAIL_PCT", 15.0)
    monkeypatch.setattr(settings, "SWING_MAX_HOLD_DAYS", 20)

    row = {
        "Symbol": "AAPL", "Price": 200.0, "Confidence %": 80, "Verdict": "STRONG BUY",
        "Votes BUY": 6, "Votes SELL": 1, "Votes HOLD": 1, "__strategy_label": "A",
    }
    msg, plan = sa._format_signal(
        row, usx_score=78, usx_breakdown={}, account_usd=100000, risk_pct=0.02
    )

    # Displayed stop == fixed 15% catastrophe stop from entry (not a trailing stop).
    assert plan["sl"] == round(200.0 * (1 - 0.15), 2)  # 170.00
    assert "fixed catastrophe" in msg            # not "trailing"
    assert "15.0%" in msg
    # Time-exit framing must be present so the trader holds through normal noise.
    assert "20 trading days" in msg
    assert "don't sell on normal pullbacks" in msg


def test_non_swing_display_has_no_swing_framing(monkeypatch):
    from app.config import settings
    from app.services import signals_advisor as sa

    monkeypatch.setattr(settings, "SWING_EXIT_ENABLED", False)
    row = {
        "Symbol": "MSFT", "Price": 300.0, "Confidence %": 75, "Verdict": "STRONG BUY",
        "Votes BUY": 5, "Votes SELL": 1, "Votes HOLD": 1, "__strategy_label": "A",
    }
    msg, _plan = sa._format_signal(
        row, usx_score=70, usx_breakdown={}, account_usd=100000, risk_pct=0.02
    )
    assert "fixed catastrophe" not in msg
    assert "time exit" not in msg
