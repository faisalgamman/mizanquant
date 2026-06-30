"""Tests for pairs_strategy (Phase 3B) — halal long-only relative value.

Verifies:
1. evaluate_pair: strongly negative spread → BUY y (long_y).
2. evaluate_pair: strongly positive spread → BUY x (long_x).
3. evaluate_pair: spread at mean → EXIT.
4. NEVER-SHORT invariant: action ∈ {long_y, long_x, exit, hold}; long_symbol
   is always y, x, or None — no short side is ever produced.
5. Entry stop-loss < entry < take-profit (long-only bracket geometry).
6. run_pairs_cycle: entry signal calls execute_buy on the correct leg.
7. run_pairs_cycle: exit signal on a held leg calls execute_sell.
8. run_pairs_cycle: PAIRS_TRADING_ENABLED=false → no execution.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.services.cointegration import hedge_ratio
from app.services.pairs_scanner import PairReport


# ── synthetic OHLC builders ───────────────────────────────────────────────────

def _ohlc(close: np.ndarray) -> pd.DataFrame:
    idx = pd.date_range("2021-01-04", periods=len(close), freq="B")
    close = np.asarray(close, dtype=float)
    return pd.DataFrame(
        {"close": close, "high": close * 1.01, "low": close * 0.99, "volume": 1_000_000.0},
        index=idx,
    )


def _forced_pair(z_target: float, n: int = 400, seed: int = 7):
    """Build a cointegrated (y, x) whose final spread sits ≈ z_target sigmas
    from its trailing mean, so evaluate_pair sees that z."""
    rng = np.random.default_rng(seed)
    x = np.cumsum(rng.normal(0.0, 1.0, n)) + 120.0
    eps = np.empty(n)
    eps[0] = 0.0
    for t in range(1, n):
        eps[t] = 0.8 * eps[t - 1] + rng.normal(0.0, 1.0)
    beta_true, alpha_true = 1.0, 5.0
    y = alpha_true + beta_true * x + eps

    beta, alpha = hedge_ratio(y, x)
    spread = y - beta * x - alpha
    mu = float(np.mean(spread[-60:]))
    sigma = float(np.std(spread[-60:], ddof=1))
    # Set the last spread value to mu + z_target * sigma
    y = y.copy()
    y[-1] = (mu + z_target * sigma) + beta * x[-1] + alpha
    return y, x


def _report(y_sym="AA", x_sym="BB") -> PairReport:
    return PairReport(
        y_symbol=y_sym, x_symbol=x_sym, sector="TECH", pvalue=0.01,
        hedge_ratio=1.0, intercept=5.0, half_life=10.0, zscore=0.0,
    )


# ── evaluate_pair ─────────────────────────────────────────────────────────────

def test_negative_spread_buys_y():
    from app.services.pairs_strategy import evaluate_pair

    # z=-3.0: past entry (2.0), comfortably below the breakdown cut (4.0).
    y, x = _forced_pair(z_target=-3.0)
    sig = evaluate_pair(_report(), _ohlc(y), _ohlc(x))
    assert sig is not None
    assert sig.zscore < 0, f"expected negative z, got {sig.zscore}"
    assert sig.action == "long_y", f"expected long_y, got {sig.action}"
    assert sig.long_symbol == "AA"


def test_positive_spread_buys_x():
    from app.services.pairs_strategy import evaluate_pair

    y, x = _forced_pair(z_target=+3.0)
    sig = evaluate_pair(_report(), _ohlc(y), _ohlc(x))
    assert sig is not None
    assert sig.zscore > 0, f"expected positive z, got {sig.zscore}"
    assert sig.action == "long_x", f"expected long_x, got {sig.action}"
    assert sig.long_symbol == "BB"


def test_spread_at_mean_exits():
    from app.services.pairs_strategy import evaluate_pair

    y, x = _forced_pair(z_target=0.0)
    sig = evaluate_pair(_report(), _ohlc(y), _ohlc(x))
    assert sig is not None
    assert sig.action == "exit", f"expected exit at mean, got {sig.action} (z={sig.zscore})"
    assert sig.long_symbol is None


def test_entry_bracket_geometry_long_only():
    """Long entry: stop < entry < take-profit (never an inverted/short bracket)."""
    from app.services.pairs_strategy import evaluate_pair

    y, x = _forced_pair(z_target=-3.0)
    sig = evaluate_pair(_report(), _ohlc(y), _ohlc(x))
    assert sig.action == "long_y"
    assert sig.stop_loss < sig.entry < sig.take_profit, (
        f"long bracket geometry violated: sl={sig.stop_loss} entry={sig.entry} tp={sig.take_profit}"
    )


# ── breakdown stop ────────────────────────────────────────────────────────────

def test_pair_breakdown_blowout_on_z_alone():
    """|z| ≥ PAIRS_BREAKDOWN_Z trips the breakdown from z alone (no series)."""
    from app.services import pairs_strategy as ps

    broke, why = ps.pair_breakdown(ps.PAIRS_BREAKDOWN_Z + 0.5)
    assert broke is True and "blowout" in (why or "")
    # within the band → no breakdown
    assert ps.pair_breakdown(ps.PAIRS_ENTRY_Z + 0.1)[0] is False


def test_blown_out_spread_exits_not_enters():
    """A spread blown out past the breakdown band must EXIT (cut), never enter."""
    from app.services.pairs_strategy import evaluate_pair, PAIRS_BREAKDOWN_Z

    y, x = _forced_pair(z_target=-(PAIRS_BREAKDOWN_Z + 1.0))
    sig = evaluate_pair(_report(), _ohlc(y), _ohlc(x))
    assert sig is not None
    assert sig.action == "exit", f"expected breakdown exit, got {sig.action} (z={sig.zscore})"
    assert sig.long_symbol is None
    assert sig.reason.startswith("breakdown")


def test_breakdown_pvalue_decay_cuts(monkeypatch):
    """Even at a moderate z, a decayed cointegration p-value trips the stop."""
    from app.services import pairs_strategy as ps

    # Force the recent-window Engle–Granger p-value above the decay threshold.
    monkeypatch.setattr(
        "app.services.cointegration.engle_granger_pvalue",
        lambda y, x: ps.PAIRS_BREAKDOWN_PVAL + 0.2,
    )
    y = list(range(100, 200))           # ≥60 points each so the test runs
    x = list(range(300, 400))
    broke, why = ps.pair_breakdown(ps.PAIRS_ENTRY_Z + 0.2, y, x)
    assert broke is True and "decay" in (why or "")


def test_never_emits_short(monkeypatch):
    """Across many random z values, no short side is ever produced."""
    from app.services.pairs_strategy import evaluate_pair

    rng = np.random.default_rng(0)
    for _ in range(25):
        z = float(rng.uniform(-5, 5))
        y, x = _forced_pair(z_target=z, seed=int(abs(z) * 1000) + 1)
        sig = evaluate_pair(_report(), _ohlc(y), _ohlc(x))
        assert sig is not None
        assert sig.action in ("long_y", "long_x", "exit", "hold")
        assert sig.long_symbol in ("AA", "BB", None)
        # The very concept of "short" must never appear
        assert "short" not in sig.action.lower()


# ── run_pairs_cycle ──────────────────────────────────────────────────────────

def _make_signal(action, long_symbol, pair="AA/BB"):
    from app.services.pairs_strategy import PairSignal
    return PairSignal(
        pair=pair, action=action, long_symbol=long_symbol,
        entry=100.0, stop_loss=96.0, take_profit=106.0,
        zscore=-2.5 if action == "long_y" else (2.5 if action == "long_x" else 0.1),
        hedge_ratio=1.0, half_life=10.0, confidence=85.0, reason="test",
    )


def test_cycle_entry_calls_execute_buy(monkeypatch):
    from app.services import pairs_strategy as ps
    import app.services.trading_engine as te

    monkeypatch.setattr(ps, "PAIRS_TRADING_ENABLED", True)
    monkeypatch.setattr(ps, "compute_signals", lambda max_pairs=None: [_make_signal("long_y", "AA")])
    monkeypatch.setattr(ps, "_pairs_held_symbols", lambda: set())  # nothing held

    buys = []
    monkeypatch.setattr(te, "execute_buy", lambda **kw: (buys.append(kw) or {"executed": True, "order_id": "X1"}))
    monkeypatch.setattr(te, "execute_sell", lambda *a, **k: {"executed": False})

    summary = ps.run_pairs_cycle()
    assert summary["entries"] == 1, summary
    assert len(buys) == 1
    assert buys[0]["symbol"] == "AA"
    assert buys[0]["strategy_id"] == ps.PAIRS_STRATEGY_ID
    # long-only bracket
    assert buys[0]["stop_loss"] < buys[0]["price"] < buys[0]["take_profit"]


def test_cycle_exit_calls_execute_sell(monkeypatch):
    from app.services import pairs_strategy as ps
    import app.services.trading_engine as te

    monkeypatch.setattr(ps, "PAIRS_TRADING_ENABLED", True)
    monkeypatch.setattr(ps, "compute_signals", lambda max_pairs=None: [_make_signal("exit", None)])
    # AA is currently held by the pairs strategy
    monkeypatch.setattr(ps, "_pairs_held_symbols", lambda: {"AA"})

    sells = []
    monkeypatch.setattr(te, "execute_sell", lambda *a, **k: (sells.append((a, k)) or {"executed": True}))
    monkeypatch.setattr(te, "execute_buy", lambda **kw: {"executed": False})

    summary = ps.run_pairs_cycle()
    assert summary["exits"] == 1, summary
    assert len(sells) == 1
    # execute_sell(symbol, price, confidence, strategy_id=...)
    assert sells[0][0][0] == "AA"


def test_cycle_disabled_does_not_execute(monkeypatch):
    from app.services import pairs_strategy as ps
    import app.services.trading_engine as te

    monkeypatch.setattr(ps, "PAIRS_TRADING_ENABLED", False)
    monkeypatch.setattr(ps, "compute_signals", lambda max_pairs=None: [_make_signal("long_y", "AA")])
    monkeypatch.setattr(ps, "_pairs_held_symbols", lambda: set())

    called = {"buy": 0, "sell": 0}
    monkeypatch.setattr(te, "execute_buy", lambda **kw: called.__setitem__("buy", called["buy"] + 1))
    monkeypatch.setattr(te, "execute_sell", lambda *a, **k: called.__setitem__("sell", called["sell"] + 1))

    summary = ps.run_pairs_cycle()
    assert summary["entries"] == 0
    assert summary["exits"] == 0
    assert called == {"buy": 0, "sell": 0}, "no orders may be placed when disabled"
    # Signals are still computed/returned for observability
    assert summary["pairs_scanned"] == 1
