"""Offline factor lab — get the ANSWER now instead of waiting months for the forward
paper ledger to fill. Everything here is look-ahead-safe (uses bars strictly UP TO each
rebalance date) and PRICE-ONLY (point-in-time clean: no future fundamentals/halal/
sentiment leak). It ESTIMATES the weekly's technical edge honestly and fast; the live
ledger still confirms — this just tells us where to look today.

Two engines, both reusing ``backtest_engine._aligned_closes`` for the price panel and the
SAME formulas the live pipeline uses (EMA20 = ewm(span20), RS = factor_rs_vs_spy) so the
replay is faithful:

  • ``gate_ab_replay`` — counterfactual A/B of the with-trend entry gate over history:
    across every candidate on every rebalance date, does the gate-PASS set beat the
    gate-FAIL set (and the pooled book)? Calls the LIVE ``_weekly_entry_ok`` so the gate
    logic (and its env thresholds) are identical. Answers "would the gate have raised the
    weekly's alpha, and by how much?" with hundreds of observations — today.

  • ``cross_sectional_ic`` — per-date rank correlation (Spearman) of a factor with the
    forward return (the Information Coefficient). One observation per DATE from dozens of
    names, so statistical power accrues per-day rather than per-closed-trade. Reports mean
    IC, the IC information-ratio (mean/std·√n ≈ a t-stat), and % of dates positive.
"""

from __future__ import annotations

import logging
import threading
import time

import numpy as np

logger = logging.getLogger("screener")


def _ema_last(closes, span: int = 20):
    """Last EMA value — identical formula to smart_exit.compute_exit_indicators
    (``ewm(span, adjust=False)``), so the historical trend flag matches live."""
    if closes is None or len(closes) < span:
        return None
    import pandas as pd
    return float(pd.Series(closes, dtype=float).ewm(span=span, adjust=False).mean().iloc[-1])


def _asof_gate_parts(closes, spy_closes) -> dict:
    """The gate-relevant entry factors (wk_rs, wk_above_ema20) as of the last bar of
    ``closes`` — replicates _weekly_signal_parts' math exactly for the two fields the
    with-trend gate actually reads."""
    from app.services.backtest_engine import factor_rs_vs_spy
    ema20 = _ema_last(closes, 20)
    c = float(closes[-1])
    rs = factor_rs_vs_spy(closes, spy_closes, 63)
    return {
        "wk_rs": round(float(rs) * 100, 2) if rs is not None else None,
        "wk_above_ema20": 1 if (ema20 and c > ema20) else 0,
    }


def _arm(rets: list) -> dict:
    a = np.asarray(rets, dtype=float)
    n = len(a)
    if n == 0:
        return {"n": 0, "mean_ret_pct": None, "win_rate_pct": None}
    return {"n": n, "mean_ret_pct": round(float(a.mean()) * 100, 3),
            "win_rate_pct": round(100.0 * float((a > 0).mean()), 1)}


def _welch_t(a: list, b: list):
    """Two-sample (Welch) t of mean(a) − mean(b); |t| ≳ 2 ⇒ the two arms differ."""
    x, y = np.asarray(a, float), np.asarray(b, float)
    if len(x) < 2 or len(y) < 2:
        return None
    vx, vy = x.var(ddof=1), y.var(ddof=1)
    se = np.sqrt(vx / len(x) + vy / len(y))
    if se <= 0:
        return None
    return round(float((x.mean() - y.mean()) / se), 2)


# ── ① counterfactual A/B of the with-trend entry gate ────────────────────────

def gate_ab_replay(symbols, *, spy: str = "SPY", period: str = "2y",
                   rebalance_days: int = 5, hold_days: int = 20,
                   warmup: int = 252, _mat=None) -> dict:
    """Would the weekly with-trend gate have improved outcomes? Look-ahead-safe replay:
    at each rebalance, for every candidate, compute the gate factors from bars up to that
    date, ask the LIVE ``_weekly_entry_ok``, then take the forward ``hold_days`` return.
    Compare gate-PASS vs gate-FAIL vs the pooled book, all vs SPY over the same windows."""
    from app.services.backtest_engine import _aligned_closes
    from app.services.paper_validation import _weekly_entry_ok

    mat = _mat if _mat is not None else _aligned_closes(list(symbols) + [spy], period)
    if mat is None or spy not in mat.columns:
        return {"error": "no data"}
    universe = [s for s in symbols if s in mat.columns and s != spy]
    dates = mat.index
    if len(dates) < warmup + hold_days + rebalance_days:
        return {"error": "insufficient history", "rows": len(dates)}

    spy_col = mat[spy].values
    pass_r, fail_r, all_r = [], [], []
    pass_alpha, all_alpha = [], []
    n_rebal = 0
    i = warmup
    while i + hold_days < len(dates):
        n_rebal += 1
        spy_hist = mat[spy].iloc[:i + 1].dropna().values
        s0, s1 = spy_col[i], spy_col[i + hold_days]
        spy_fwd = float(s1 / s0 - 1.0) if (np.isfinite(s0) and np.isfinite(s1) and s0 > 0) else 0.0
        for s in universe:
            col = mat[s].iloc[:i + 1].dropna().values
            if len(col) < warmup:
                continue
            p0 = mat[s].iloc[i]
            p1 = mat[s].iloc[i + hold_days]
            if not (np.isfinite(p0) and np.isfinite(p1) and p0 > 0):
                continue
            r = float(p1 / p0 - 1.0)
            ok, _ = _weekly_entry_ok(_asof_gate_parts(col, spy_hist))
            all_r.append(r)
            all_alpha.append(r - spy_fwd)
            if ok:
                pass_r.append(r)
                pass_alpha.append(r - spy_fwd)
            else:
                fail_r.append(r)
        i += rebalance_days

    pa = float(np.mean(pass_alpha)) * 100 if pass_alpha else None
    aa = float(np.mean(all_alpha)) * 100 if all_alpha else None
    return {
        "pass": _arm(pass_r), "fail": _arm(fail_r), "all": _arm(all_r),
        "pass_alpha_pct": round(pa, 3) if pa is not None else None,
        "all_alpha_pct": round(aa, 3) if aa is not None else None,
        "alpha_uplift_pct": round(pa - aa, 3) if (pa is not None and aa is not None) else None,
        "t_pass_vs_fail": _welch_t(pass_r, fail_r),
        "rebalances": n_rebal, "hold_days": hold_days,
        "note": ("Look-ahead-safe, price-only replay across ALL candidates. alpha_uplift = "
                 "gate-PASS alpha − pooled alpha (how much the gate would lift the weekly). "
                 "t_pass_vs_fail: |t|≳2 ⇒ PASS and FAIL forward returns genuinely differ."),
    }


# ── ② cross-sectional Information Coefficient ────────────────────────────────

def cross_sectional_ic(symbols, factor_fn, *, spy: str = "SPY", period: str = "2y",
                       rebalance_days: int = 5, hold_days: int = 20,
                       warmup: int = 252, _mat=None) -> dict:
    """Per-date Spearman(factor, forward return) = the Information Coefficient. One obs
    per date from the whole cross-section ⇒ fast statistical power. Returns mean IC, the
    IC information-ratio (mean/std·√n ≈ t), and % of dates with positive IC."""
    from app.services.backtest_engine import _aligned_closes
    from app.services.signal_calibration import _spearman

    mat = _mat if _mat is not None else _aligned_closes(list(symbols) + [spy], period)
    if mat is None or spy not in mat.columns:
        return {"error": "no data"}
    universe = [s for s in symbols if s in mat.columns and s != spy]
    dates = mat.index
    if len(dates) < warmup + hold_days + rebalance_days:
        return {"error": "insufficient history", "rows": len(dates)}

    ics = []
    i = warmup
    while i + hold_days < len(dates):
        spy_hist = mat[spy].iloc[:i + 1].dropna().values
        fac, fwd = [], []
        for s in universe:
            col = mat[s].iloc[:i + 1].dropna().values
            if len(col) < warmup:
                continue
            p0 = mat[s].iloc[i]
            p1 = mat[s].iloc[i + hold_days]
            if not (np.isfinite(p0) and np.isfinite(p1) and p0 > 0):
                continue
            try:
                v = factor_fn(col, spy_hist)
            except Exception:
                v = None
            if v is not None and np.isfinite(v):
                fac.append(float(v))
                fwd.append(float(p1 / p0 - 1.0))
        if len(fac) >= 5:
            ic = _spearman(fac, fwd)
            if ic is not None:
                ics.append(ic)
        i += rebalance_days

    n = len(ics)
    if n == 0:
        return {"n_dates": 0, "mean_ic": None, "ic_ir": None, "pct_positive": None}
    a = np.asarray(ics, dtype=float)
    sd = float(a.std(ddof=1)) if n > 1 else 0.0
    ir = round(float(a.mean()) / (sd / np.sqrt(n)), 2) if sd > 0 else None
    return {
        "n_dates": n,
        "mean_ic": round(float(a.mean()), 4),
        "ic_ir": ir,   # information ratio ≈ t-stat of the IC
        "pct_positive": round(100.0 * float((a > 0).mean()), 1),
        "hold_days": hold_days,
    }


# ── combined report (cached — the replay is heavy) ───────────────────────────

_CACHE: dict = {"at": 0.0, "data": None}
_TTL = 6 * 3600.0
_COMPUTING = threading.Lock()


def factor_lab_cached(*, warm: bool = False):
    """Return the cached report if fresh, else None — NEVER computes inline (the replay is
    heavy; callers on a request path must not block on it). If ``warm`` and the cache is
    cold, kick a single-flight background compute so a later read is populated."""
    now = time.time()
    data = _CACHE.get("data")
    if data is not None and (now - _CACHE["at"]) < _TTL:
        return data
    if warm and _COMPUTING.acquire(blocking=False):
        def _run():
            try:
                factor_lab_report(force=True)
            except Exception as e:
                logger.debug("factor_lab warm failed: %s", e)
            finally:
                _COMPUTING.release()
        threading.Thread(target=_run, daemon=True, name="factor-lab-warm").start()
    return None


def factor_lab_report(symbols=None, *, force: bool = False) -> dict:
    """Headline: the gate A/B uplift + the IC of RS and 12-1 momentum. Cached 6h."""
    now = time.time()
    if not force and _CACHE["data"] is not None and (now - _CACHE["at"]) < _TTL:
        return _CACHE["data"]

    if not symbols:
        try:
            from app.services.halal_screener import build_halal_candidates
            symbols = list(build_halal_candidates() or [])[:120]
        except Exception:
            symbols = []
    symbols = list(dict.fromkeys(symbols or []))

    from app.services.backtest_engine import _aligned_closes, factor_rs_vs_spy, factor_momentum_12_1
    mat = _aligned_closes(symbols + ["SPY"], "2y") if symbols else None

    out: dict = {"universe": len(symbols)}
    try:
        out["gate_ab"] = gate_ab_replay(symbols, _mat=mat)
    except Exception as e:
        logger.debug("gate_ab_replay failed: %s", e)
        out["gate_ab"] = {"error": str(e)}
    try:
        out["ic_rs"] = cross_sectional_ic(symbols, factor_rs_vs_spy, _mat=mat)
        out["ic_mom"] = cross_sectional_ic(symbols, factor_momentum_12_1, _mat=mat)
    except Exception as e:
        logger.debug("cross_sectional_ic failed: %s", e)
    out["caveat"] = ("Price-only, look-ahead-safe ESTIMATE (no PIT fundamentals/halal/"
                     "sentiment). Guides the decision fast; the live ledger still confirms.")
    _CACHE.update(at=now, data=out)
    return out


__all__ = ["gate_ab_replay", "cross_sectional_ic", "factor_lab_report", "factor_lab_cached"]
