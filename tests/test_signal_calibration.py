"""Unit tests for the read-only signal-calibration measurement (no DB).

Monkeypatches the closed-trade reader with synthetic ledgers so the calibration math
(banding, win-rate, rank correlation, monotonicity, sample-size honesty) is verified
deterministically.
"""

from app.services import signal_calibration as sc


def _patch_trades(monkeypatch, trades):
    monkeypatch.setattr(sc, "_closed_trades", lambda strat: trades)


def test_spearman_monotonic_is_one():
    xs = [1, 2, 3, 4, 5, 6]
    ys = [10, 20, 30, 40, 50, 60]
    assert sc._spearman(xs, ys) == 1.0


def test_spearman_inverted_is_minus_one():
    assert sc._spearman([1, 2, 3, 4, 5, 6], [6, 5, 4, 3, 2, 1]) == -1.0


def test_spearman_too_few_returns_none():
    assert sc._spearman([1, 2, 3], [1, 2, 3]) is None


def test_stats_win_rate_and_avg():
    s = sc._stats([10.0, -5.0, 20.0, -2.0])
    assert s["n"] == 4
    assert s["win_rate"] == 50.0           # 2 of 4 positive
    assert s["avg_ret"] == 5.75


def test_calibration_positive_when_high_scores_win(monkeypatch):
    # Higher score → higher realized return: rank corr should be strongly positive.
    trades = []
    for score, ret in [(80, 8), (78, 6), (70, 5), (60, 3), (50, 1), (45, -1),
                       (40, -2), (36, -3), (30, -4), (20, -6)] * 4:
        trades.append({"score": float(score), "ret": float(ret), "symbol": "X", "details": {}})
    _patch_trades(monkeypatch, trades)
    rep = sc.calibration_report("weekly", min_n=10)
    assert rep["closed_trades"] == 40
    assert rep["sufficient_sample"] is True
    assert rep["score_return_rank_corr"] > 0.5
    assert rep["higher_score_better"] is True
    # The STRONG BUY band (75+) must out-win the NO TRADE band (<35).
    by_label = {b["label"]: b for b in rep["bands"]}
    assert by_label["STRONG BUY"]["win_rate"] == 100.0
    assert by_label["NO TRADE"]["win_rate"] == 0.0


def test_calibration_flags_insufficient_sample(monkeypatch):
    _patch_trades(monkeypatch, [{"score": 70.0, "ret": 5.0, "symbol": "X", "details": {}}])
    rep = sc.calibration_report("monthly", min_n=30)
    assert rep["sufficient_sample"] is False
    assert "insufficient" in rep["interpretation"].lower()


def test_unknown_scanner_errors(monkeypatch):
    assert "error" in sc.calibration_report("daily")


def test_component_attribution_accumulating_then_ready(monkeypatch):
    # Trades carrying a 'score_fund' part that tracks return → positive corr once n>=min_n.
    trades = [{"score": 60.0, "ret": float(i % 7 - 3), "symbol": "X",
               "details": {"score_fund": float(i % 7 - 3) + 10}} for i in range(40)]
    _patch_trades(monkeypatch, trades)
    rep = sc.component_attribution("monthly", min_n=30)
    assert rep["components"]["score_fund"]["n"] == 40
    assert rep["components"]["score_fund"]["rank_corr"] is not None
    # A part with no data stays 'accumulating'.
    assert rep["components"]["score_tech"].get("status") == "accumulating"
