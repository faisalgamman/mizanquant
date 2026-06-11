#!/usr/bin/env python
"""Walk-forward Ridge regression fitter for the technical score v2.

Expanding-window by calendar month. Fits sklearn Ridge on reconstructed
technical features (feature_lib), evaluates OOS rank-IC + decile spread vs
the recorded-score baseline, runs permutation + DSR on top-decile OOS returns,
and writes an adoption-gated artifact.

CLI: python scripts/fit_composite_weights.py --days 365 [--out reports/tech_v2_fit.md]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd

_PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ not in sys.path:
    sys.path.insert(0, _PROJ)

from app.services.backtest_qc import deflated_sharpe, permutation_pvalue  # noqa: E402
from app.services.feature_lib import FEATURE_ORDER, compute_features  # noqa: E402
from scripts.signal_attribution import (  # noqa: E402
    apply_signal_filter,
    fetch_prices_for_signals,
    load_matured_signals,
)

ALPHAS = [0.3, 1.0, 3.0]
N_TRIALS = 3  # honestly counted — one per alpha
MIN_TRAIN_MONTHS = 3


def _utcnow():
    return datetime.now(timezone.utc)


# ── adoption gate (pure, unit-testable) ─────────────────────────────────────

def evaluate_adoption(
    ic_v2: float,
    ic_base: float,
    spread_v2: float,
    spread_base: float,
    pval: float,
    dsr: float,
) -> str:
    """Return 'PASS' only when ALL conditions hold."""
    if ic_v2 > ic_base and spread_v2 > spread_base and pval < 0.05 and dsr >= 0.6:
        return "PASS"
    return "KEEP_V1"


# ── main fitter ─────────────────────────────────────────────────────────────

def fit_composite_weights(days: int = 365) -> dict:
    """Run the walk-forward fit. Returns a result dict ready for reporting."""

    print("Loading matured signals...")
    signals = load_matured_signals(days=days)
    print(f"  Loaded {len(signals)} signals")

    signals, _ = apply_signal_filter(signals, "buy")
    print(f"  Buy-side only: {len(signals)} signals")

    if len(signals) < 100:
        return {
            "verdict": "KEEP_V1",
            "error": f"too few buy signals ({len(signals)})",
        }

    print("Fetching price data...")
    price_data = fetch_prices_for_signals(signals)
    print(f"  Got {len(price_data)} symbols")

    spy_df = price_data.get("SPY")

    # Build feature matrix + metadata
    print("Reconstructing features...")
    rows = []
    skipped = 0
    for sig in signals:
        df_sym = price_data.get(sig["symbol"])
        if df_sym is None:
            skipped += 1
            continue
        created = sig["created_at"]
        if created is None:
            skipped += 1
            continue
        feats = compute_features(df_sym, spy_df, created)
        if feats is None:
            skipped += 1
            continue
        row = {
            "symbol": sig["symbol"],
            "created": created,
            "outcome": sig["outcome_return_pct"],
            "score_recorded": sig.get("score", 0),
        }
        row.update(feats)
        rows.append(row)
    print(f"  {len(rows)} feature rows, {skipped} skipped")

    df = pd.DataFrame(rows)
    if len(df) < 60:
        return {"verdict": "KEEP_V1", "error": f"too few feature rows ({len(df)})"}

    df["month"] = pd.to_datetime(df["created"]).dt.to_period("M")
    months = sorted(df["month"].unique())
    y = df["outcome"].values.astype(float)
    X = df[FEATURE_ORDER].values.astype(float)
    score_base = df["score_recorded"].values.astype(float)

    # Drop rows with NaN features
    mask = ~np.isnan(X).any(axis=1) & ~np.isinf(X).any(axis=1)
    df = df.loc[mask].copy()
    y = y[mask]
    X = X[mask]
    score_base = score_base[mask]
    df["month"] = pd.to_datetime(df["created"]).dt.to_period("M")
    months = sorted(df["month"].unique())

    best_result = None

    for alpha in ALPHAS:
        oos_preds = []
        oos_idxs = []
        fold_rows = []

        for i, m in enumerate(months):
            if i < MIN_TRAIN_MONTHS:
                continue
            train_mask = df["month"].isin(months[:i])
            test_mask = df["month"] == m
            if train_mask.sum() < 30 or test_mask.sum() < 5:
                continue

            X_train = X[train_mask.values]
            y_train = y[train_mask.values]
            X_test = X[test_mask.values]

            # Standardize on TRAIN only
            mean = X_train.mean(axis=0)
            std = X_train.std(axis=0)
            std[std == 0] = 1.0
            X_train_s = (X_train - mean) / std
            X_test_s = (X_test - mean) / std

            # Fit Ridge
            from sklearn.linear_model import Ridge
            model = Ridge(alpha=alpha, fit_intercept=True)
            model.fit(X_train_s, y_train)
            preds = model.predict(X_test_s)

            oos_preds.extend(preds.tolist())
            oos_idxs.extend(df.index[test_mask].tolist())

            fold_rows.append({
                "month": str(m), "n": int(test_mask.sum()),
                "alpha": alpha,
            })

        oos_preds = np.array(oos_preds)
        oos_y = y[[list(df.index).index(idx) for idx in oos_idxs]]
        oos_base = score_base[[list(df.index).index(idx) for idx in oos_idxs]]

        if len(oos_preds) < 30:
            continue

        ic_v2 = round(float(pd.Series(oos_preds).rank().corr(pd.Series(oos_y).rank())), 4)
        ic_base = round(float(pd.Series(oos_base).rank().corr(pd.Series(oos_y).rank())), 4)

        # Decile spread
        def _decile_spread(_preds, _y):
            df_tmp = pd.DataFrame({"p": _preds, "y": _y})
            df_tmp["decile"] = pd.qcut(df_tmp["p"], 10, labels=False, duplicates="drop")
            if df_tmp["decile"].nunique() < 3:
                return 0.0
            top = df_tmp[df_tmp["decile"] == df_tmp["decile"].max()]["y"].mean()
            bot = df_tmp[df_tmp["decile"] == df_tmp["decile"].min()]["y"].mean()
            return round(top - bot, 2)

        spread_v2 = _decile_spread(oos_preds, oos_y)
        spread_base = _decile_spread(oos_base, oos_y)

        # Top-decile OOS returns for DSR + permutation
        df_tmp = pd.DataFrame({"p": oos_preds, "y": oos_y})
        df_tmp["decile"] = pd.qcut(df_tmp["p"], 10, labels=False, duplicates="drop")
        top_returns = df_tmp[df_tmp["decile"] == df_tmp["decile"].max()]["y"].values
        pval = round(permutation_pvalue(top_returns.tolist(), n_perm=1000, seed=42), 4) if len(top_returns) >= 5 else 1.0
        dsr = round(deflated_sharpe(top_returns.tolist(), n_trials=N_TRIALS), 4) if len(top_returns) >= 5 else 0.0

        verdict = evaluate_adoption(ic_v2, ic_base, spread_v2, spread_base, pval, dsr)

        result = {
            "alpha": alpha,
            "ic_v2": ic_v2,
            "ic_base": ic_base,
            "spread_v2": spread_v2,
            "spread_base": spread_base,
            "pval": pval,
            "dsr": dsr,
            "n_obs": len(oos_preds),
            "verdict": verdict,
            "folds": fold_rows,
        }

        if best_result is None or ic_v2 > best_result["ic_v2"]:
            best_result = result

    if best_result is None:
        return {"verdict": "KEEP_V1", "error": "no valid OOS folds"}

    # Final-fit on ALL data if PASS
    if best_result["verdict"] == "PASS":
        alpha_win = best_result["alpha"]
        X_all = X
        y_all = y
        mean = X_all.mean(axis=0)
        std = X_all.std(axis=0)
        std[std == 0] = 1.0
        X_s = (X_all - mean) / std

        from sklearn.linear_model import Ridge
        model = Ridge(alpha=alpha_win, fit_intercept=True)
        model.fit(X_s, y_all)
        raw_scores = model.predict(X_s)
        quantiles = np.percentile(raw_scores, np.arange(1, 100)).tolist()

        best_result["means"] = mean.tolist()
        best_result["stds"] = std.tolist()
        best_result["coefs"] = model.coef_.tolist()
        best_result["intercept"] = float(model.intercept_)
        best_result["calibration_quantiles"] = quantiles
        best_result["features"] = FEATURE_ORDER
        best_result["version"] = f"tech-v2-{_utcnow().strftime('%Y-%m-%d')}"
        best_result["n_total"] = int(len(y_all))
        best_result["n_trials"] = N_TRIALS

    return best_result


# ── report formatting ───────────────────────────────────────────────────────

def format_fit_report(result: dict) -> str:
    lines = [
        "# Technical Score v2 — Walk-Forward Fit Report",
        "",
        f"**Generated:** {_utcnow().isoformat()}",
        f"**Verdict:** **{result.get('verdict', 'KEEP_V1')}**",
        "",
    ]

    if "error" in result:
        lines.append(f"**Error:** {result['error']}")
        return "\n".join(lines)

    lines.extend([
        f"**Alpha:** {result.get('alpha')}",
        f"**OOS observations:** {result.get('n_obs')}",
        f"**n_trials (honestly):** {result.get('n_trials', N_TRIALS)}",
        "",
        "## Pooled OOS Metrics (v2 vs baseline)",
        "",
        "| Metric | v2 | Baseline (recorded score) |",
        "|--------|-----|---------------------------|",
        f"| Rank-IC | {result.get('ic_v2', 0):+.4f} | {result.get('ic_base', 0):+.4f} |",
        f"| Decile Spread | {result.get('spread_v2', 0):.2f} | {result.get('spread_base', 0):.2f} |",
        f"| Permutation p | {result.get('pval', 1):.4f} | — |",
        f"| Deflated SR | {result.get('dsr', 0):.4f} | — |",
        "",
        "## Adoption Gate",
        "",
    ])

    verdict = result.get("verdict", "KEEP_V1")
    if verdict == "PASS":
        lines.append("✅ **PASS** — all conditions met:")
        lines.append(f"- ic_v2 ({result['ic_v2']:+.4f}) > ic_base ({result['ic_base']:+.4f})")
        lines.append("- spread_v2 > spread_base")
        lines.append(f"- pval ({result['pval']:.4f}) < 0.05")
        lines.append(f"- dsr ({result['dsr']:.4f}) >= 0.6")
    else:
        lines.append("❌ **KEEP v1 — no validated improvement.** (This is an honest, GOOD outcome.)")

    # Coefficient table
    if "coefs" in result and result.get("features"):
        lines.extend([
            "",
            "## Final-Fit Coefficients",
            "",
            "| Feature | Coef | Sign Commentary |",
            "|---------|------|-----------------|",
        ])
        for feat, coef in zip(result["features"], result["coefs"]):
            sign = "positive (bullish)" if coef > 0 else "negative (bearish)"
            lines.append(f"| {feat} | {coef:+.4f} | {sign} |")

    # Caveats
    lines.extend([
        "",
        "## Caveats",
        "",
        "- Single mostly-bull year — coefficients may not generalize to bear markets.",
        "- Conditioned on OUR Option-A exit policy (~20 day hold with catastrophe stop).",
        "- Baseline is the recorded score (confidence) proxy — NOT the exact v1 tech sub-score",
        "  (pre-instrumentation rows never stored the tech sub-score separately).",
        "- Sample = consensus-type signals; swing recording began with Phase 2 (C4).",
        "- Walk-forward is expanding-window, not purged — small lookahead risk from",
        "  overlapping feature windows within a month, but the OOS month boundary is strict.",
    ])

    return "\n".join(lines)


# ── artifact writer ─────────────────────────────────────────────────────────

def write_artifact(result: dict, path: str = "data/tech_v2_weights.json"):
    if result.get("verdict") != "PASS":
        artifact = {
            "version": "tech-v2-KEEP_V1",
            "verdict": "KEEP_V1",
        }
    else:
        artifact = {
            "version": result["version"],
            "features": result["features"],
            "means": result["means"],
            "stds": result["stds"],
            "coefs": result["coefs"],
            "intercept": result["intercept"],
            "calibration_quantiles": result["calibration_quantiles"],
            "alpha": result["alpha"],
            "n": result["n_total"],
            "oos": {
                "ic_v2": result["ic_v2"],
                "ic_base": result["ic_base"],
                "spread_v2": result["spread_v2"],
                "spread_base": result["spread_base"],
                "pval": result["pval"],
                "dsr": result["dsr"],
                "n_trials": result.get("n_trials", N_TRIALS),
            },
            "verdict": "PASS",
        }
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump(artifact, f, indent=2)
    print(f"Artifact written to {path}")


# ── CLI ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Walk-forward technical score fitter")
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--out", type=str, default="reports/tech_v2_fit.md")
    parser.add_argument("--artifact", type=str, default="data/tech_v2_weights.json")
    args = parser.parse_args()

    result = fit_composite_weights(days=args.days)

    # Write report
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    md = format_fit_report(result)
    with open(args.out, "w") as f:
        f.write(md)
    print(f"Report written to {args.out}")

    # Write artifact
    write_artifact(result, args.artifact)

    # Print verdict prominently
    print(f"\n{'='*60}")
    print(f"  VERDICT: {result.get('verdict', 'KEEP_V1')}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
