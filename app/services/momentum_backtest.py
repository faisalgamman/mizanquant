"""Cross-sectional momentum backtest (research only — not wired to trading).

Tests the single most documented anomaly honestly: each month-end, rank the
universe by 12-1 momentum (trailing `lookback` days return, EXCLUDING the most
recent `skip` days), hold the top-N equal-weighted for one month, rebalance, pay
transaction costs on turnover, and compare to SPY buy-and-hold — then gate any
"edge" claim behind the Deflated Sharpe + permutation p-value.

HONESTY: the result is a survivorship-biased UPPER BOUND — the universe is the
CURRENT halal set (plus known delisted names), not point-in-time. The real,
live-tradeable result is almost always worse. This module is pure (no I/O) so the
logic is unit-tested; the data fetching lives in scripts/momentum_backtest.py.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _month_end_positions(index: pd.DatetimeIndex) -> list[int]:
    """Positional index of the last trading day of each month."""
    periods = index.to_period("M")
    pos = pd.Series(np.arange(len(index)), index=index)
    return [int(p) for p in pos.groupby(periods).max().values]


def _ann_metrics(monthly: np.ndarray) -> dict:
    n = int(monthly.size)
    if n == 0:
        return {"cagr_pct": 0.0, "sharpe": 0.0, "max_dd_pct": 0.0, "total_return_pct": 0.0, "months": 0}
    eq = np.cumprod(1.0 + monthly)
    years = n / 12.0
    cagr = (eq[-1] ** (1.0 / years) - 1.0) if (years > 0 and eq[-1] > 0) else 0.0
    mean = float(monthly.mean())
    std = float(monthly.std(ddof=1)) if n > 1 else 0.0
    sharpe = (mean / std * np.sqrt(12)) if std > 0 else 0.0
    peak = np.maximum.accumulate(eq)
    max_dd = float(((eq - peak) / peak).min())
    return {
        "cagr_pct": round(cagr * 100, 2),
        "sharpe": round(sharpe, 2),
        "max_dd_pct": round(max_dd * 100, 2),
        "total_return_pct": round((eq[-1] - 1.0) * 100, 2),
        "months": n,
    }


def cross_sectional_momentum_backtest(
    prices: pd.DataFrame,
    spy: pd.Series,
    top_n: int = 15,
    lookback: int = 252,
    skip: int = 21,
    cost_bps: float = 20.0,
    n_trials: int = 1,
) -> dict:
    """Monthly-rebalanced top-N 12-1 momentum vs SPY. Returns metrics + verdict.

    Args:
        prices: [dates x symbols] adjusted daily closes (date-indexed, sorted).
        spy: SPY adjusted daily close (date-indexed); reindexed onto `prices`.
        top_n: number of names to hold.
        lookback / skip: momentum window = return from t-lookback to t-skip.
        cost_bps: one-way transaction cost in bps, charged on turnover each rebalance.
        n_trials: strategy variants searched (multiple-testing honesty for DSR).
    """
    from app.services.backtest_qc import deflated_sharpe, permutation_pvalue

    if prices is None or prices.shape[1] < top_n:
        return {"error": "insufficient breadth"}
    # Be robust to a non-datetime index (e.g. loaded from CSV / fetch RangeIndex).
    if not isinstance(prices.index, pd.DatetimeIndex):
        prices = prices.copy()
        prices.index = pd.to_datetime(prices.index, errors="coerce")
        prices = prices[~prices.index.isna()]
    if getattr(prices.index, "tz", None) is not None:
        prices = prices.copy()
        prices.index = prices.index.tz_localize(None)  # avoid tz drop warning in to_period
    if prices.shape[0] < lookback + 25:
        return {"error": "insufficient price history"}

    prices = prices.sort_index()
    spy = spy.reindex(prices.index).ffill()
    rebal = [p for p in _month_end_positions(prices.index) if p >= lookback]
    if len(rebal) < 6:
        return {"error": "not enough rebalance dates"}

    cost = cost_bps / 10_000.0
    port_rets: list[float] = []
    spy_rets: list[float] = []
    dates: list = []
    turnovers: list[float] = []
    prev: set[str] = set()

    pvals = prices.values
    cols = list(prices.columns)
    spv = spy.values

    for i in range(len(rebal) - 1):
        pos, nxt = rebal[i], rebal[i + 1]
        past = pvals[pos - skip]
        base = pvals[pos - lookback]
        with np.errstate(divide="ignore", invalid="ignore"):
            mom = past / base - 1.0
        entry = pvals[pos]
        exit_ = pvals[nxt]
        # valid = finite momentum AND finite entry+exit prices
        valid = np.isfinite(mom) & np.isfinite(entry) & np.isfinite(exit_) & (entry > 0)
        if valid.sum() < 1:
            continue
        order = np.argsort(np.where(valid, mom, -np.inf))[::-1]
        sel_idx = order[: top_n]
        sel = {cols[j] for j in sel_idx}
        rets = exit_[sel_idx] / entry[sel_idx] - 1.0
        gross = float(np.nanmean(rets))
        # turnover = fraction of names replaced vs last month (sell+buy = 2 sides)
        turn = (len(sel - prev) / max(len(sel), 1)) if prev else 1.0
        net = gross - cost * 2.0 * turn
        spy_ret = (spv[nxt] / spv[pos] - 1.0) if (np.isfinite(spv[pos]) and spv[pos] > 0) else 0.0

        port_rets.append(net)
        spy_rets.append(float(spy_ret))
        turnovers.append(turn)
        dates.append(str(prices.index[nxt])[:10])
        prev = sel

    pr = np.array(port_rets, dtype=float)
    sr = np.array(spy_rets, dtype=float)
    port_m = _ann_metrics(pr)
    spy_m = _ann_metrics(sr)
    dsr = deflated_sharpe(pr, n_trials=n_trials, annualization=12)
    pval = permutation_pvalue(pr, n_perm=500)

    beats_spy = port_m["sharpe"] > spy_m["sharpe"] and port_m["cagr_pct"] > spy_m["cagr_pct"]
    has_edge = bool(beats_spy and dsr >= 0.60 and pval < 0.05)
    verdict = (
        "EDGE: beats SPY net + survives DSR/permutation" if has_edge
        else "NO PROVEN EDGE — do not trade this; prefer a halal index"
    )

    return {
        "strategy": f"top-{top_n} cross-sectional 12-1 momentum, monthly",
        "period": f"{dates[0]} → {dates[-1]}" if dates else "—",
        "momentum_portfolio": port_m,
        "spy_buy_hold": spy_m,
        "dsr": round(dsr, 3),
        "permutation_p": round(pval, 3),
        "avg_monthly_turnover_pct": round(float(np.mean(turnovers) * 100), 1) if turnovers else 0.0,
        "cost_bps_per_side": cost_bps,
        "has_edge": has_edge,
        "verdict": verdict,
        "survivorship_warning": True,
        "point_in_time": False,
        "monthly_returns": [round(x, 4) for x in pr.tolist()],
        "spy_monthly_returns": [round(x, 4) for x in sr.tolist()],
        "dates": dates,
    }
