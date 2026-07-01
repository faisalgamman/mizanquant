"""Event-driven, look-ahead-safe backtest for a PRICE factor — the judge that tests
a selection rule on history BEFORE it goes live.

We can only faithfully replay factors computable from price bars (RS, momentum,
volatility, technicals): point-in-time fundamentals/halal/sentiment aren't stored
historically, so a full-composite replay would leak the future. Price is PIT-clean.

At each (non-overlapping) rebalance the engine ranks the universe by the factor
using bars UP TO that date, longs the top-N equal-weight, holds `hold_days`, and
records the forward return. It reports PF / annualised Sharpe / max drawdown /
win-rate and — crucially — the alpha vs SPY with a t-stat and a permutation
p-value, so a lucky curve isn't mistaken for edge.
"""

from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger("screener")


# ── factor library (price-only, PIT) ─────────────────────────────────────────

def factor_rs_vs_spy(closes, spy_closes, lookback: int = 63):
    """Relative strength: the stock's return minus SPY's over `lookback` days."""
    if closes is None or spy_closes is None or len(closes) < lookback + 1 or len(spy_closes) < lookback + 1:
        return None
    return (closes[-1] / closes[-lookback - 1] - 1.0) - (spy_closes[-1] / spy_closes[-lookback - 1] - 1.0)


def factor_momentum_12_1(closes, spy_closes=None):
    """Classic 12-1 momentum: 12-month return excluding the most recent month."""
    if closes is None or len(closes) < 252:
        return None
    return closes[-21] / closes[-252] - 1.0


def factor_lowvol(closes, spy_closes=None, lookback: int = 63):
    """Low volatility preferred → negative of realised daily-return std."""
    if closes is None or len(closes) < lookback + 1:
        return None
    rets = np.diff(np.log(closes[-lookback - 1:]))
    sd = float(np.std(rets))
    return -sd if np.isfinite(sd) else None


def factor_random(closes, spy_closes=None):
    """No-skill baseline — random ranking."""
    return float(np.random.random())


# ── metrics ──────────────────────────────────────────────────────────────────

def _metrics(port, spy, rebalance_days: int) -> dict:
    p = np.asarray(port, dtype=float)
    s = np.asarray(spy, dtype=float)
    n = len(p)
    if n == 0:
        return {"n": 0}
    per_yr = 252.0 / max(rebalance_days, 1)
    mean = float(p.mean())
    sd = float(p.std(ddof=1)) if n > 1 else 0.0
    sharpe = round(mean / sd * np.sqrt(per_yr), 2) if sd > 0 else None
    equity = np.cumprod(1.0 + p)
    peak = np.maximum.accumulate(equity)
    max_dd = float((equity / peak - 1.0).min())
    wins = p[p > 0]
    losses = p[p <= 0]
    pf = round(float(wins.sum() / -losses.sum()), 2) if (len(losses) and losses.sum() < 0) else None
    alpha = p - s
    a_mean = float(alpha.mean())
    a_sd = float(alpha.std(ddof=1)) if n > 1 else 0.0
    a_t = round(a_mean / (a_sd / np.sqrt(n)), 2) if (n > 1 and a_sd > 0) else None
    return {
        "n": n,
        "mean_ret_pct": round(mean * 100, 3),
        "ann_sharpe": sharpe,
        "max_drawdown_pct": round(max_dd * 100, 1),
        "win_rate_pct": round(100.0 * len(wins) / n, 1),
        "profit_factor": pf,
        "total_return_pct": round((equity[-1] - 1.0) * 100, 1),
        "mean_alpha_pct": round(a_mean * 100, 3),
        "alpha_t": a_t,
    }


def _permutation_pvalue(port, spy, iters: int = 500, seed: int = 7) -> float | None:
    """Is the mean alpha better than shuffling which forward return each pick got?
    Reassign returns at random and see how often the shuffled mean alpha ≥ actual."""
    p = np.asarray(port, dtype=float)
    s = np.asarray(spy, dtype=float)
    n = len(p)
    if n < 5:
        return None
    actual = float((p - s).mean())
    rng = np.random.default_rng(seed)
    ge = 0
    for _ in range(iters):
        if float((rng.permutation(p) - s).mean()) >= actual:
            ge += 1
    return round((ge + 1) / (iters + 1), 4)


# ── data + engine ────────────────────────────────────────────────────────────

def _aligned_closes(symbols, period: str = "2y"):
    import pandas as pd
    from app.services.market_data import fetch
    frames = {}
    for s in symbols:
        try:
            df = fetch(s, period=period)
            if df is None or len(df) < 120 or "date" not in df.columns:
                continue
            ser = pd.Series(df["close"].astype(float).values,
                            index=pd.to_datetime(df["date"], utc=True))
            frames[s] = ser[~ser.index.duplicated(keep="last")]
        except Exception:
            continue
    if not frames:
        return None
    return pd.DataFrame(frames).sort_index()


def run_backtest(symbols, factor_fn, *, spy="SPY", rebalance_days: int = 20,
                 hold_days: int = 20, top_n: int = 10, warmup: int = 252,
                 period: str = "2y", _mat=None) -> dict:
    """Backtest `factor_fn(closes, spy_closes) -> score` (higher = preferred).
    Non-overlapping by default (rebalance_days == hold_days) so the stats are honest."""
    mat = _mat if _mat is not None else _aligned_closes(list(symbols) + [spy], period)
    if mat is None or spy not in mat.columns:
        return {"error": "no data"}
    universe = [s for s in symbols if s in mat.columns and s != spy]
    dates = mat.index
    if len(dates) < warmup + hold_days + rebalance_days:
        return {"error": "insufficient history", "rows": len(dates)}

    spy_col = mat[spy].values
    port, spy_ret, n_rebal = [], [], 0
    i = warmup
    while i + hold_days < len(dates):
        n_rebal += 1
        scores = {}
        spy_hist = mat[spy].iloc[:i + 1].dropna().values
        for s in universe:
            col = mat[s].iloc[:i + 1].dropna().values
            if len(col) >= warmup:
                try:
                    sc = factor_fn(col, spy_hist)
                except Exception:
                    sc = None
                if sc is not None and np.isfinite(sc):
                    scores[s] = sc
        if scores:
            top = sorted(scores, key=scores.get, reverse=True)[:top_n]
            rets = []
            for s in top:
                p0 = mat[s].iloc[i]
                p1 = mat[s].iloc[i + hold_days]
                if np.isfinite(p0) and np.isfinite(p1) and p0 > 0:
                    rets.append(p1 / p0 - 1.0)
            if rets:
                port.append(float(np.mean(rets)))
                s0, s1 = spy_col[i], spy_col[i + hold_days]
                spy_ret.append(float(s1 / s0 - 1.0) if s0 > 0 else 0.0)
        i += rebalance_days

    rep = _metrics(port, spy_ret, rebalance_days)
    rep["rebalances"] = n_rebal
    rep["perm_pvalue"] = _permutation_pvalue(port, spy_ret)
    return rep


__all__ = ["run_backtest", "_metrics", "factor_rs_vs_spy", "factor_momentum_12_1",
           "factor_lowvol", "factor_random"]
