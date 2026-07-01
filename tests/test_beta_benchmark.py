"""Alpha-vs-SPY scorecard math (pure, no DB/network)."""

from datetime import datetime

from app.services.beta_benchmark import compute_alpha


def _spy_flat(ts):
    return 100.0   # SPY unchanged → alpha equals the trade's own return


def test_alpha_equals_trade_return_when_spy_flat():
    trades = [(5.0, datetime(2024, 1, 1), datetime(2024, 1, 20)),
              (-3.0, datetime(2024, 2, 1), datetime(2024, 2, 20))]
    r = compute_alpha(trades, _spy_flat)
    assert r["n"] == 2
    assert r["mean_spy_ret"] == 0.0
    assert r["mean_alpha"] == 1.0          # (+5 −3)/2
    assert r["pct_beat_spy"] == 50.0


def test_zero_alpha_when_trade_matches_spy():
    def spy_at(ts):
        return 100.0 if ts.day < 15 else 105.0     # +5% over the window
    trades = [(5.0, datetime(2024, 1, 1), datetime(2024, 1, 20))]   # trade also +5%
    r = compute_alpha(trades, spy_at)
    assert r["mean_alpha"] == 0.0 and r["pct_beat_spy"] == 0.0


def test_beating_spy_is_positive_alpha():
    def spy_at(ts):
        return 100.0 if ts.day < 15 else 102.0     # SPY +2%
    trades = [(8.0, datetime(2024, 1, 1), datetime(2024, 1, 20))]   # trade +8% → alpha +6
    r = compute_alpha(trades, spy_at)
    assert r["mean_alpha"] == 6.0 and r["pct_beat_spy"] == 100.0


def test_empty_and_missing_spy_are_safe():
    assert compute_alpha([], _spy_flat)["n"] == 0
    # a None SPY close for a leg → that trade is skipped
    assert compute_alpha([(5.0, datetime(2024, 1, 1), datetime(2024, 1, 2))],
                         lambda ts: None)["n"] == 0
