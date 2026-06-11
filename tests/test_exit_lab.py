"""Tests for exit_lab — simulator + gate + no-lookahead."""
import numpy as np
import pandas as pd

from scripts.exit_lab import (
    evaluate_adoption_exit,
    simulate_exit,
)


def _post_bars(closes, lows=None, highs=None):
    """Build a minimal post-signal DataFrame with OHLCV from close prices."""
    n = len(closes)
    closes_a = np.asarray(closes, dtype=float)
    lows_a = np.asarray(lows, dtype=float) if lows is not None else closes_a * 0.98
    highs_a = np.asarray(highs, dtype=float) if highs is not None else closes_a * 1.02
    return pd.DataFrame({
        "open": closes_a, "high": highs_a, "low": lows_a,
        "close": closes_a, "volume": np.full(n, 1_000_000.0),
    })


class TestSimulator:

    def test_stop_hit(self):
        """Bar low hits stop on day 3 → return ≈ -stop_pct."""
        entry = 100.0
        stop_pct = 10.0
        # Day 0: 101, Day 1: 102, Day 2: 95 (penetrates 90 stop on day 3)
        post = _post_bars([101, 102, 85], lows=[100, 101, 85])
        result = simulate_exit(post, entry, stop_pct, 20, trailing=False)
        assert result is not None
        ret, day = result
        assert day == 3
        assert abs(ret - (-stop_pct)) < 1.0

    def test_time_exit(self):
        """No stop hit → exit at hold_days close."""
        entry = 100.0
        post = _post_bars([101, 102, 103, 104, 105, 106, 107, 108, 109, 110])
        result = simulate_exit(post, entry, 15, 10, trailing=False)
        assert result is not None
        ret, day = result
        assert day == 10
        assert abs(ret - 10.0) < 0.1

    def test_trailing_locks_gains(self):
        """Rally then pullback → trailing stop locks gains above entry."""
        entry = 100.0
        stop_pct = 10.0
        # Rally to 120, then drops to 105
        post = _post_bars(
            [102, 105, 110, 115, 120, 118, 115, 110, 108, 105],
            lows=[101, 104, 109, 114, 119, 117, 114, 109, 107, 104],
        )
        result = simulate_exit(post, entry, stop_pct, 20, trailing=True)
        assert result is not None
        ret, _ = result
        # With trailing, stop ratchets to 120*0.9 = 108 → exit at 108 when low ≤ 108
        # The day 8 low is 107 which is ≤ 108 → exit at ~108 = +8%
        assert ret > 5.0, f"Trailing should lock positive gains: {ret}"

    def test_insufficient_bars_none(self):
        """Fewer than 2 bars → None."""
        post = _post_bars([100])
        result = simulate_exit(post, 100, 10, 20)
        assert result is None

    def test_early_time_exit(self):
        """Short hold_days → exit at that bar's close."""
        entry = 100.0
        post = _post_bars([101, 102, 103, 104, 105])
        result = simulate_exit(post, entry, 15, 3, trailing=False)
        assert result is not None
        ret, day = result
        assert day == 3
        assert abs(ret - 3.0) < 0.1


class TestGate:

    def test_pass(self):
        assert evaluate_adoption_exit(3.0, 2.0, 65, 60, 0.01, 0.8) == "PASS"

    def test_fail_pf(self):
        assert evaluate_adoption_exit(1.5, 2.0, 65, 60, 0.01, 0.8) == "KEEP_OPTION_A"

    def test_fail_wr(self):
        assert evaluate_adoption_exit(3.0, 2.0, 56, 60, 0.01, 0.8) == "KEEP_OPTION_A"

    def test_fail_pval(self):
        assert evaluate_adoption_exit(3.0, 2.0, 65, 60, 0.10, 0.8) == "KEEP_OPTION_A"

    def test_fail_dsr(self):
        assert evaluate_adoption_exit(3.0, 2.0, 65, 60, 0.01, 0.3) == "KEEP_OPTION_A"

    def test_wr_boundary_ok(self):
        """WR within 2 points of baseline → allowed."""
        assert evaluate_adoption_exit(3.0, 2.0, 58, 60, 0.01, 0.8) == "PASS"

    def test_wr_boundary_fail(self):
        """WR > 2 points below baseline → fail."""
        assert evaluate_adoption_exit(3.0, 2.0, 57, 60, 0.01, 0.8) == "KEEP_OPTION_A"


# ── INSUFFICIENT_DATA guard test ────────────────────────────────────────────

def test_insufficient_data_single_month(monkeypatch):
    """All rows share one month → verdict must be INSUFFICIENT_DATA, never KEEP_*."""
    from scripts.exit_lab import run_exit_lab

    # Mock load_matured_signals → single-month signals (>100)
    def _mock_load(days=365):
        return [{"symbol": "AAPL", "created_at": "2026-05-01T10:00:00", "price": 150.0},
                {"symbol": "MSFT", "created_at": "2026-05-15T10:00:00", "price": 400.0}] * 60  # 120 signals > 100
    monkeypatch.setattr("scripts.exit_lab.load_matured_signals", _mock_load)
    monkeypatch.setattr("scripts.exit_lab.apply_signal_filter", lambda s, d: (s, None))

    # Mock price data → synthetic dataframe with date index
    n = 260
    closes = np.linspace(100, 120, n)
    dates = pd.date_range("2026-01-01", periods=n, freq="B")
    df_sym = pd.DataFrame({
        "open": closes, "high": closes * 1.01, "low": closes * 0.99,
        "close": closes, "volume": np.full(n, 1_000_000.0),
    }, index=dates)
    # Also add 'date' column for merge-based lookups
    df_sym["date"] = dates
    monkeypatch.setattr("scripts.exit_lab.fetch_prices_for_signals",
                        lambda s: {"AAPL": df_sym, "MSFT": df_sym})

    result = run_exit_lab(days=365)
    assert result["verdict"] == "INSUFFICIENT_DATA", \
        f"Expected INSUFFICIENT_DATA, got {result.get('verdict')}"
    assert result["months"] == 1 if "months" in result else result["counts"]["months"] == 1
