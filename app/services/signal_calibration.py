"""Read-only signal calibration from the paper-validation ledgers.

Answers the central intelligence question — **do higher scanner scores actually produce
higher forward returns?** — by reading CLOSED paper trades (TradeHistory: ``confidence``
= the entry score, ``pnl_pct`` = the realized return under the real exit policy) and
bucketing them by score band.

This is PURE MEASUREMENT: no trade-path impact, no live re-weighting. It exists so the
scoring can be trusted (or retuned) on evidence, not on the human-guessed weights baked
into the scanners. Component attribution reads the score parts stored on each trade's
``signal_details`` (populated going forward for the monthly composite) and correlates each
part with the realized return, so we learn which inputs actually carry signal.

Honesty: small samples are not conclusive. The report flags insufficient samples and the
correlations are directional, not proof — the paper ledger must accumulate.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("screener")

# scanner label -> paper-validation ledger strategy id (see paper_validation.py)
_STRATEGY = {"weekly": "PV", "monthly": "PVM", "pairs": "PVP"}

# Score bands per scanner — aligned with each scanner's own signal thresholds so the
# buckets mean something (e.g. weekly BUY = swing_score >= 55; monthly STRONG BUY = 72).
_BANDS: dict[str, list[tuple[int, int, str]]] = {
    "weekly":  [(0, 35, "NO TRADE"), (35, 55, "WATCH"), (55, 75, "BUY"), (75, 101, "STRONG BUY")],
    "monthly": [(0, 38, "AVOID"), (38, 55, "WATCH"), (55, 72, "BUY"), (72, 101, "STRONG BUY")],
    "pairs":   [(0, 40, "LOW"), (40, 60, "MED"), (60, 101, "HIGH")],
}

# Composite component fields the monthly ledger stores (after the recording enrichment).
_MONTHLY_PARTS = ("score_tech", "score_fund", "score_sentiment", "score_halal", "conviction_score")


def _closed_trades(strategy_id: str) -> list[dict]:
    """Closed paper trades for a ledger → [{score, ret, symbol, details}]."""
    from app.db.database import SessionLocal
    from app.db.models import TradeHistory
    db = SessionLocal()
    try:
        rows = (db.query(TradeHistory.confidence, TradeHistory.pnl_pct,
                         TradeHistory.symbol, TradeHistory.signal_details)
                  .filter(TradeHistory.strategy_id == strategy_id,
                          TradeHistory.pnl_pct.isnot(None)).all())
        return [{"score": float(r[0] or 0), "ret": float(r[1]),
                 "symbol": r[2], "details": r[3] or {}} for r in rows]
    except Exception as e:
        logger.debug("calibration read failed for %s: %s", strategy_id, e)
        return []
    finally:
        db.close()


def _stats(rets: list[float]) -> dict:
    n = len(rets)
    if n == 0:
        return {"n": 0, "win_rate": None, "avg_ret": None, "median_ret": None}
    wins = sum(1 for x in rets if x > 0)
    s = sorted(rets)
    med = s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2
    return {"n": n, "win_rate": round(wins / n * 100, 1),
            "avg_ret": round(sum(rets) / n, 2), "median_ret": round(med, 2)}


def _spearman(xs: list[float], ys: list[float]) -> float | None:
    """Rank correlation (tie-naive, no scipy). +1 → higher x tracks higher y."""
    n = len(xs)
    if n < 5:
        return None
    import numpy as np
    rx = np.argsort(np.argsort(np.asarray(xs, dtype=float)))
    ry = np.argsort(np.argsort(np.asarray(ys, dtype=float)))
    if rx.std() == 0 or ry.std() == 0:
        return None
    return round(float(np.corrcoef(rx, ry)[0, 1]), 3)


def _interpret(n: int, min_n: int, rank_corr: float | None,
               monotonic: bool | None, overall: dict) -> str:
    if n < min_n:
        return (f"Only {n} closed trades — insufficient to judge calibration "
                f"(need ≥{min_n}). Let the paper ledger accumulate.")
    msgs: list[str] = []
    if rank_corr is None:
        msgs.append("Rank correlation unavailable.")
    elif rank_corr >= 0.15:
        msgs.append(f"Score is POSITIVELY calibrated (rank corr {rank_corr}): "
                    "higher scores tend to higher returns.")
    elif rank_corr <= -0.15:
        msgs.append(f"Score is INVERTED (rank corr {rank_corr}): higher scores tend to "
                    "LOWER returns — the scoring needs review.")
    else:
        msgs.append(f"Score shows ~NO link to return (rank corr {rank_corr}): the scoring "
                    "is roughly uninformative — recalibrate the weights.")
    if monotonic is True:
        msgs.append("Average return rises across score bands (good).")
    elif monotonic is False:
        msgs.append("Average return is NOT monotonic across bands (some higher bands "
                    "underperform lower ones).")
    if overall.get("win_rate") is not None:
        msgs.append(f"Overall win rate {overall['win_rate']}% on {n} trades "
                    f"(avg {overall['avg_ret']}%).")
    return " ".join(msgs)


def calibration_report(scanner: str = "weekly", min_n: int = 30) -> dict:
    """Score→return calibration for one scanner's paper ledger.

    Returns per-band win-rate/avg-return, the overall record, the score↔return rank
    correlation, a monotonicity flag, and an honest interpretation + sample caveat.
    """
    key = str(scanner).lower()
    strat = _STRATEGY.get(key)
    if not strat:
        return {"error": f"unknown scanner '{scanner}' (use weekly|monthly|pairs)"}

    trades = _closed_trades(strat)
    n = len(trades)

    bands_out = []
    for lo, hi, label in _BANDS.get(key, []):
        rets = [t["ret"] for t in trades if lo <= t["score"] < hi]
        bands_out.append({"band": f"{lo}-{hi if hi <= 100 else '100+'}",
                          "label": label, "score_range": [lo, min(hi, 100)], **_stats(rets)})

    overall = _stats([t["ret"] for t in trades])
    rank_corr = _spearman([t["score"] for t in trades], [t["ret"] for t in trades])
    avgs = [b["avg_ret"] for b in bands_out if b["n"] and b["avg_ret"] is not None]
    monotonic = all(avgs[i] <= avgs[i + 1] for i in range(len(avgs) - 1)) if len(avgs) >= 2 else None

    return {
        "scanner": key,
        "strategy_id": strat,
        "closed_trades": n,
        "overall": overall,
        "bands": bands_out,
        "score_return_rank_corr": rank_corr,   # +1 = high score → high return
        "higher_score_better": monotonic,      # avg return increases across bands
        "sufficient_sample": n >= min_n,
        "interpretation": _interpret(n, min_n, rank_corr, monotonic, overall),
        "caveat": ("Read-only measurement from the SIMULATED paper ledger (real prices + the "
                   "real exit policy). Small samples are NOT conclusive — treat <100 closed "
                   "trades as directional, not proof. No live weights are changed by this."),
    }


def component_attribution(scanner: str = "monthly", min_n: int = 30) -> dict:
    """Per-component rank correlation with the realized return.

    Reads the score parts stored on each closed trade's ``signal_details`` and correlates
    each with ``pnl_pct`` — i.e. which inputs actually carry forward signal. Monthly stores
    the composite parts (tech/fund/sentiment/halal/conviction) after the recording
    enrichment; until enough trades carry them it reports "accumulating".
    """
    key = str(scanner).lower()
    strat = _STRATEGY.get(key)
    if not strat:
        return {"error": f"unknown scanner '{scanner}'"}
    if key != "monthly":
        return {"scanner": key, "status": "unsupported",
                "note": "Component attribution is available for the monthly composite only "
                        "(weekly swing_score has no stored sub-scores)."}

    trades = _closed_trades(strat)
    out = {}
    for part in _MONTHLY_PARTS:
        pairs = [(float(t["details"].get(part)), t["ret"]) for t in trades
                 if isinstance(t["details"], dict) and isinstance(t["details"].get(part), (int, float))]
        if len(pairs) >= min_n:
            out[part] = {"n": len(pairs),
                         "rank_corr": _spearman([p[0] for p in pairs], [p[1] for p in pairs])}
        else:
            out[part] = {"n": len(pairs), "rank_corr": None, "status": "accumulating"}

    have = [p for p, v in out.items() if v.get("rank_corr") is not None]
    return {
        "scanner": key,
        "strategy_id": strat,
        "closed_trades": len(trades),
        "components": out,
        "ready_components": have,
        "note": ("Components with a positive rank_corr carry forward signal and deserve MORE "
                 "weight; near-zero or negative ones are candidates to down-weight. "
                 "'accumulating' = not enough closed trades yet carry that part."),
        "caveat": "Directional measurement, not proof. No live weights are changed by this.",
    }
