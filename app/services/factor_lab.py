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
import os
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
    ir = round(float(a.mean() / (sd / np.sqrt(n))), 2) if sd > 0 else None  # plain float (JSON-safe)
    return {
        "n_dates": n,
        "mean_ic": round(float(a.mean()), 4),
        "ic_ir": ir,   # information ratio ≈ t-stat of the IC
        "pct_positive": round(100.0 * float((a > 0).mean()), 1),
        "hold_days": hold_days,
    }


# ── ①② self-calibrating gate: threshold-grid sweep + OOS judge ───────────────

def _collect_gate_records(mat, spy: str = "SPY", *, rebalance_days: int = 5,
                          hold_days: int = 20, warmup: int = 252) -> list:
    """Compute (rebal_ordinal, wk_rs, above_ema20, fwd_ret, spy_fwd) for EVERY
    (rebalance, candidate) ONCE — so a whole grid of gate thresholds can be scored on the
    same look-ahead-safe records instead of re-replaying per threshold."""
    universe = [s for s in mat.columns if s != spy]
    dates = mat.index
    spy_col = mat[spy].values
    recs, i, di = [], warmup, 0
    while i + hold_days < len(dates):
        spy_hist = mat[spy].iloc[:i + 1].dropna().values
        s0, s1 = spy_col[i], spy_col[i + hold_days]
        spy_fwd = float(s1 / s0 - 1.0) if (np.isfinite(s0) and np.isfinite(s1) and s0 > 0) else 0.0
        for s in universe:
            col = mat[s].iloc[:i + 1].dropna().values
            if len(col) < warmup:
                continue
            p0, p1 = mat[s].iloc[i], mat[s].iloc[i + hold_days]
            if not (np.isfinite(p0) and np.isfinite(p1) and p0 > 0):
                continue
            parts = _asof_gate_parts(col, spy_hist)
            recs.append((di, parts.get("wk_rs"), parts.get("wk_above_ema20"),
                         float(p1 / p0 - 1.0), spy_fwd))
        i += rebalance_days
        di += 1
    return recs


def _eval_gate(recs, min_rs: float, require_ema20: bool = True) -> dict:
    """Apply ONE candidate gate to precomputed records → pass/fail/uplift/t. Uplift =
    gate-PASS alpha − pooled alpha (how much this threshold would lift the weekly)."""
    pass_r, fail_r, pass_a, all_a = [], [], [], []
    for _di, rs, above, r, spy in recs:
        allow = True
        if require_ema20 and above == 0:
            allow = False
        if allow and rs is not None and float(rs) < min_rs:
            allow = False
        all_a.append(r - spy)
        if allow:
            pass_r.append(r)
            pass_a.append(r - spy)
        else:
            fail_r.append(r)
    pa = float(np.mean(pass_a)) * 100 if pass_a else None
    aa = float(np.mean(all_a)) * 100 if all_a else None
    return {
        "min_rs": min_rs, "require_ema20": require_ema20,
        "n_pass": len(pass_r), "n_fail": len(fail_r),
        "pass_ret_pct": round(float(np.mean(pass_r)) * 100, 3) if pass_r else None,
        "uplift_pct": round(pa - aa, 3) if (pa is not None and aa is not None) else None,
        "t_pass_vs_fail": _welch_t(pass_r, fail_r),
    }


_GRID_DEFAULT = [-10, -8, -6, -5, -4, -3, -2, -1, 0, 1, 2]


def gate_threshold_sweep(symbols, *, grid=None, spy: str = "SPY", period: str = "2y",
                         rebalance_days: int = 5, hold_days: int = 20,
                         warmup: int = 252, _mat=None) -> dict:
    """① Score a GRID of MIN_RS thresholds on one look-ahead-safe pass; sorted by uplift."""
    from app.services.backtest_engine import _aligned_closes
    grid = grid or _GRID_DEFAULT
    mat = _mat if _mat is not None else _aligned_closes(list(symbols) + [spy], period)
    if mat is None or spy not in mat.columns:
        return {"error": "no data"}
    if len(mat.index) < warmup + hold_days + rebalance_days:
        return {"error": "insufficient history", "rows": len(mat.index)}
    recs = _collect_gate_records(mat, spy, rebalance_days=rebalance_days,
                                 hold_days=hold_days, warmup=warmup)
    results = [_eval_gate(recs, m) for m in grid]
    results.sort(key=lambda r: (r["uplift_pct"] if r["uplift_pct"] is not None else -1e9), reverse=True)
    return {"grid": results, "best": results[0] if results else None, "n_records": len(recs)}


def _gate_recommendation(best_oos, current: float, min_delta: float = 0.5, pbo=None) -> dict:
    """Turn the best OOS-robust threshold into a plain-language proposal vs the current
    one. Conservative: proposes a change only if a robust winner beats current by
    ≥ min_delta % uplift on the OOS (test) half AND the grid search isn't likely overfit
    (⑤ PBO ≤ 0.5)."""
    if best_oos is None:
        return {"action": "keep", "min_rs": current,
                "reason": "لا عتبة تتفوّق خارج العيّنة بدلالة — أبقِ الحالية."}
    cand = float(best_oos["min_rs"])
    te = best_oos["test"]["uplift_pct"] or 0.0
    if abs(cand - current) < 1e-9:
        return {"action": "keep", "min_rs": current,
                "reason": f"العتبة الحالية ({current}) هي الأفضل خارج العيّنة أصلاً."}
    pbo_v = (pbo or {}).get("pbo") if isinstance(pbo, dict) else None
    if pbo_v is not None and pbo_v > 0.5:
        return {"action": "keep", "min_rs": current, "pbo": pbo_v,
                "reason": (f"عتبة {cand}% تبدو أفضل ظاهرياً، لكن احتمال فرط التخصيص "
                           f"PBO={pbo_v} مرتفع — لا تُغيّر بناءً على بحث شبكة غير موثوق.")}
    trust_note = f" · موثوقية PBO={pbo_v}" if pbo_v is not None else ""
    return {
        "action": "raise" if cand > current else "lower",
        "min_rs": cand, "from": current,
        "expected_uplift_pct": te,
        "train_t": best_oos["train"]["t_pass_vs_fail"],
        "test_t": best_oos["test"]["t_pass_vs_fail"],
        "pbo": pbo_v,
        "reason": (f"الدليل يدعم ضبط MIN_RS إلى {cand}% — رفع متوقّع "
                   f"+{te}%/صفقة خارج العيّنة (t تدريب {best_oos['train']['t_pass_vs_fail']}, "
                   f"t اختبار {best_oos['test']['t_pass_vs_fail']}){trust_note}."),
    }


_GATE_CAL_CACHE: dict = {"at": 0.0, "data": None}


def gate_calibration(symbols=None, *, grid=None, min_t: float = 2.0,
                     period: str = "2y", force: bool = False, _mat=None, _cache: bool = True) -> dict:
    """② OOS judge. Split the records train(first half)/test(second half); a threshold is
    ROBUST only if uplift>0 AND t≥min_t in BOTH halves (no overfit). Recommend the robust
    threshold with the best OOS uplift vs the current live gate. Cached 6h."""
    now = time.time()
    if _cache and not force and _GATE_CAL_CACHE["data"] is not None and (now - _GATE_CAL_CACHE["at"]) < _TTL:
        return _GATE_CAL_CACHE["data"]

    from app.services.backtest_engine import _aligned_closes
    grid = grid or _GRID_DEFAULT
    if _mat is not None:
        mat = _mat
    else:
        if not symbols:
            try:
                from app.services.universe import build_halal_candidates
                symbols = list(build_halal_candidates(cap=int(os.environ.get("FACTOR_LAB_UNIVERSE", "60"))) or [])
            except Exception:
                symbols = []
        mat = _aligned_closes(list(symbols) + ["SPY"], period) if symbols else None
    if mat is None or "SPY" not in getattr(mat, "columns", []) or len(mat.index) < 300:
        out = {"error": "insufficient data", "grid": [], "best_oos": None}
        if _cache:
            _GATE_CAL_CACHE.update(at=now, data=out)
        return out

    recs = _collect_gate_records(mat, "SPY")
    if not recs:
        out = {"error": "no records", "grid": [], "best_oos": None}
        if _cache:
            _GATE_CAL_CACHE.update(at=now, data=out)
        return out
    max_di = max(r[0] for r in recs)
    mid = max_di // 2
    train = [r for r in recs if r[0] <= mid]
    test = [r for r in recs if r[0] > mid]

    rows = []
    for m in grid:
        tr, te = _eval_gate(train, m), _eval_gate(test, m)
        robust = ((tr["uplift_pct"] or -1) > 0 and (te["uplift_pct"] or -1) > 0
                  and (tr["t_pass_vs_fail"] or 0) >= min_t and (te["t_pass_vs_fail"] or 0) >= min_t)
        rows.append({"min_rs": m, "train": tr, "test": te, "robust": robust})

    robust_rows = [r for r in rows if r["robust"]]
    best = max(robust_rows, key=lambda r: (r["test"]["uplift_pct"] or -1e9)) if robust_rows else None

    try:
        from app.services.gate_config import get_min_rs
        current = float(get_min_rs())
    except Exception:
        current = float(os.environ.get("WEEKLY_MIN_RS", "-2"))

    # ⑤ PBO/CSCV — is the grid search overfit? Build a (rebalance × threshold) matrix of
    # per-date gate-PASS alpha and run CSCV, then stamp the recommendation with a trust.
    pbo = None
    try:
        from app.services.overfitting import pbo_cscv
        dts = sorted({r[0] for r in recs})
        di_ix = {d: i for i, d in enumerate(dts)}
        by_date: dict = {}
        for di, rs, above, r, spy in recs:
            by_date.setdefault(di, []).append((rs, above, r, spy))
        M = np.full((len(dts), len(grid)), np.nan)
        for d, rd in by_date.items():
            for j, m in enumerate(grid):
                pa = [(r - spy) for (rs, above, r, spy) in rd
                      if above != 0 and (rs is None or float(rs) >= m)]
                if pa:
                    M[di_ix[d], j] = float(np.mean(pa))
        pbo = pbo_cscv(M, s_splits=10)
    except Exception as e:
        logger.debug("gate PBO failed: %s", e)

    out = {
        "grid": rows, "best_oos": best, "current_min_rs": current,
        "n_records": len(recs), "split": {"train": len(train), "test": len(test)},
        "pbo": pbo,
        "recommendation": _gate_recommendation(best, current, pbo=pbo),
        "caveat": ("Out-of-sample validated (train/test split), price-only, look-ahead-safe. "
                   "Gates the PAPER ledger only — never a real order."),
    }
    if _cache:
        _GATE_CAL_CACHE.update(at=now, data=out)
    return out


def gate_calibration_cached():
    """Cache-only read of gate_calibration (never computes on a request path)."""
    now = time.time()
    d = _GATE_CAL_CACHE.get("data")
    return d if (d is not None and (now - _GATE_CAL_CACHE["at"]) < _TTL) else None


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
            from app.services.universe import build_halal_candidates
            # A bounded, liquid sample — enough for a cross-sectional estimate, and the
            # engine drops thin-history names anyway. Keeps the cold-cache warm from
            # hammering Alpaca (the full micro-cap universe 429-throttles for minutes).
            cap = int(os.environ.get("FACTOR_LAB_UNIVERSE", "60"))
            symbols = list(build_halal_candidates(cap=cap) or [])
        except Exception as e:
            logger.debug("factor_lab universe load failed: %s", e)
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
    try:  # ② self-calibrating gate — OOS-validated threshold recommendation
        out["gate_calibration"] = gate_calibration(symbols=symbols, force=force)
    except Exception as e:
        logger.debug("gate_calibration failed: %s", e)
        out["gate_calibration"] = {"error": str(e)}
    try:  # ④ HMM regime — computed here (heavy) so the scorecard reads it from cache
        from app.services.regime_hmm import regime_probabilities
        spy_closes = mat["SPY"].dropna().values if (mat is not None and "SPY" in mat.columns) else None
        out["regime"] = regime_probabilities(spy_closes) if spy_closes is not None else None
    except Exception as e:
        logger.debug("regime_hmm in report failed: %s", e)
    out["caveat"] = ("Price-only, look-ahead-safe ESTIMATE (no PIT fundamentals/halal/"
                     "sentiment). Guides the decision fast; the live ledger still confirms.")
    _CACHE.update(at=now, data=out)
    return out


__all__ = ["gate_ab_replay", "cross_sectional_ic", "factor_lab_report", "factor_lab_cached"]
