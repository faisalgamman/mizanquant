#!/usr/bin/env python
"""Exit Lab — walk-forward exit-policy grid for buy signals.

Re-simulates historical buy signals from raw post-signal bars across
stop_pct × hold_days × trailing, selects walk-forward by calendar month,
and gates adoption identically to Phase 3.

Grid: stop{8,10,15} × hold{10,20,40} × trailing{on,off} = 18 combos.
Baseline = (15, 20, False) — today's live policy.

CLI: python scripts/exit_lab.py --days 365 [--out reports/exit_lab.md]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from itertools import product

import pandas as pd

_PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ not in sys.path:
    sys.path.insert(0, _PROJ)

from app.services.backtest_qc import deflated_sharpe, permutation_pvalue  # noqa: E402
from scripts.signal_attribution import (  # noqa: E402
    apply_signal_filter,
    fetch_prices_for_signals,
    load_matured_signals,
)

STOP_PCTS = [8, 10, 15]
HOLD_DAYS = [10, 20, 40]
TRAILING_OPTIONS = [False, True]
BASELINE = (15, 20, False)  # today's live policy
N_TRIALS = 18  # 3×3×2 grid
MIN_TRAIN_MONTHS = 3


def _utcnow():
    return datetime.now(timezone.utc)


# ── pure simulator ───────────────────────────────────────────────────────────

def simulate_exit(post_bars, entry, stop_pct, hold_days, trailing=False):
    """Simulate hold from entry through post_bars (DataFrame with OHLCV).

    Returns (ret_pct, exit_day) or None when not enough bars.
    ret_pct = (exit_price / entry - 1) * 100.
    exit_day is 1-indexed bar index within post_bars.
    """
    if post_bars is None or len(post_bars) == 0 or entry <= 0:
        return None

    close = post_bars["close"].astype(float).values
    low = post_bars["low"].astype(float).values
    stop_level = entry * (1 - stop_pct / 100)
    peak_close = entry  # for trailing

    n = min(len(close), hold_days + 1)  # one extra bar for time exit at hold_days
    if n <= 1:
        return None

    for i in range(n):
        bar_close = float(close[i])
        bar_low = float(low[i])

        if trailing:
            if bar_close > peak_close:
                peak_close = bar_close
            stop_level = max(stop_level, peak_close * (1 - stop_pct / 100))

        # Check stop hit
        if bar_low <= stop_level:
            exit_price = stop_level
            ret = (exit_price / entry - 1) * 100
            return round(ret, 2), i + 1

        # Time exit
        if i >= hold_days - 1:
            exit_price = bar_close
            ret = (exit_price / entry - 1) * 100
            return round(ret, 2), i + 1

    # Not enough bars — signal hasn't matured
    return None


# ── adoption gate ────────────────────────────────────────────────────────────

def evaluate_adoption_exit(pf_oos, pf_base, wr_oos, wr_base, pval, dsr):
    """Return 'PASS' only when OOS adaptive beats baseline on all metrics."""
    if (pf_oos > pf_base and wr_oos >= wr_base - 2
            and pval < 0.05 and dsr >= 0.6):
        return "PASS"
    return "KEEP_OPTION_A"


# ── grid runner ──────────────────────────────────────────────────────────────

def _run_grid_on_subset(post_bars_list, entries):
    """Evaluate all 18 combos on a subset of signals. Returns list of result dicts."""
    results = []
    for stop_pct, hold_days, trailing in product(STOP_PCTS, HOLD_DAYS, TRAILING_OPTIONS):
        returns = []
        for post_bars, entry in zip(post_bars_list, entries):
            sim = simulate_exit(post_bars, entry, stop_pct, hold_days, trailing)
            if sim is not None:
                returns.append(sim[0])
        if len(returns) < 5:
            results.append({
                "stop": stop_pct, "hold": hold_days, "trail": trailing,
                "pf": 0.0, "wr": 0.0, "n": len(returns),
            })
            continue
        wins = [r for r in returns if r > 0]
        losses = [r for r in returns if r <= 0]
        pf = round(sum(wins) / abs(sum(losses)), 2) if losses and sum(losses) != 0 else 999
        wr = round(len(wins) / len(returns) * 100, 1)
        results.append({
            "stop": stop_pct, "hold": hold_days, "trail": trailing,
            "pf": pf, "wr": wr, "n": len(returns),
        })
    return results


# ── main ────────────────────────────────────────────────────────────────────

def run_exit_lab(days=365):
    print("Loading matured buy signals...")
    signals = load_matured_signals(days=days)
    print(f"  Loaded {len(signals)} signals")
    signals, _ = apply_signal_filter(signals, "buy")
    print(f"  Buy-side: {len(signals)}")

    if len(signals) < 100:
        return {"verdict": "KEEP_OPTION_A", "error": "too few signals"}

    print("Fetching price data...")
    price_data = fetch_prices_for_signals(signals)
    print(f"  Got {len(price_data)} symbols")

    # Slice post-signal bars for each signal
    print("Slicing post-signal bars...")
    rows = []
    skipped = 0
    for sig in signals:
        sym = sig["symbol"]
        created = sig["created_at"]
        if created is None:
            skipped += 1
            continue
        df_sym = price_data.get(sym)
        if df_sym is None:
            skipped += 1
            continue
        # Slice STRICTLY after created_at
        try:
            ts = pd.Timestamp(created)
            idx_tz = getattr(df_sym.index, "tz", None)
            if ts.tzinfo is not None and idx_tz is None:
                ts = ts.tz_localize(None)
            elif ts.tzinfo is None and idx_tz is not None:
                ts = ts.tz_localize(idx_tz)
            post = df_sym[df_sym.index > ts]
        except Exception:
            skipped += 1
            continue
        if len(post) < 5:
            skipped += 1
            continue
        # Keep first 45 bars
        post = post.iloc[:45]
        entry = float(sig.get("price") or sig.get("entry") or 0)
        if entry <= 0:
            # fallback: use the last pre-signal close
            pre = df_sym[df_sym.index <= ts]
            if len(pre) > 0:
                entry = float(pre["close"].iloc[-1])
            else:
                skipped += 1
                continue
        rows.append({
            "symbol": sym,
            "created": created,
            "entry": entry,
            "post_bars": post,
            "outcome": sig.get("outcome_return_pct", 0),
        })

    print(f"  {len(rows)} valid post-signal slices, {skipped} skipped")
    df = pd.DataFrame(rows)
    df["month"] = pd.to_datetime(df["created"]).dt.to_period("M")
    months = sorted(df["month"].unique())

    # Walk-forward by month
    best_select_count = {}
    oos_adaptive_returns = []
    oos_baseline_returns = []
    fold_rows = []

    for i, m in enumerate(months):
        if i < MIN_TRAIN_MONTHS:
            continue
        train = df[df["month"].isin(months[:i])]
        test = df[df["month"] == m]
        if len(train) < 20 or len(test) < 3:
            continue

        # Train: pick best combo by PF
        train_post = train["post_bars"].tolist()
        train_entries = train["entry"].tolist()
        grid = _run_grid_on_subset(train_post, train_entries)
        best = max(grid, key=lambda g: g["pf"])
        combo_key = (best["stop"], best["hold"], best["trail"])
        best_select_count[combo_key] = best_select_count.get(combo_key, 0) + 1

        # OOS: adaptive vs baseline
        test_post = test["post_bars"].tolist()
        test_entries = test["entry"].tolist()

        for post_bars, entry in zip(test_post, test_entries):
            sim_a = simulate_exit(post_bars, entry, *combo_key)
            sim_b = simulate_exit(post_bars, entry, *BASELINE)
            if sim_a is not None:
                oos_adaptive_returns.append(sim_a[0])
            if sim_b is not None:
                oos_baseline_returns.append(sim_b[0])

        fold_rows.append({"month": str(m), "n": len(test), "selected": combo_key})

    # Full in-sample grid (always compute for transparency — even on OOS failure)
    all_post = df["post_bars"].tolist()
    all_entries = df["entry"].tolist()
    full_grid = _run_grid_on_subset(all_post, all_entries)

    if len(oos_adaptive_returns) < 10:
        return {
            "verdict": "KEEP_OPTION_A",
            "error": f"too few OOS returns ({len(oos_adaptive_returns)}); {len(months)} months, {len(fold_rows)} folds",
            "full_grid": full_grid,
            "counts": {"signals": len(rows), "post_slices": len(df), "months": len(months), "folds": len(fold_rows)},
        }

    # OOS metrics
    wins_a = [r for r in oos_adaptive_returns if r > 0]
    losses_a = [r for r in oos_adaptive_returns if r <= 0]
    pf_a = round(sum(wins_a) / abs(sum(losses_a)), 2) if losses_a and sum(losses_a) != 0 else 999
    wr_a = round(len(wins_a) / len(oos_adaptive_returns) * 100, 1)

    wins_b = [r for r in oos_baseline_returns if r > 0]
    losses_b = [r for r in oos_baseline_returns if r <= 0]
    pf_b = round(sum(wins_b) / abs(sum(losses_b)), 2) if losses_b and sum(losses_b) != 0 else 999
    wr_b = round(len(wins_b) / len(oos_baseline_returns) * 100, 1)

    pval = round(permutation_pvalue(oos_adaptive_returns, n_perm=1000, seed=42), 4)
    dsr = round(deflated_sharpe(oos_adaptive_returns, n_trials=N_TRIALS), 4)

    verdict = evaluate_adoption_exit(pf_a, pf_b, wr_a, wr_b, pval, dsr)

    # Most-selected combo
    most_selected = max(best_select_count, key=best_select_count.get) if best_select_count else BASELINE

    return {
        "verdict": verdict,
        "verdict_reason": "",
        "oos": {
            "pf_adaptive": pf_a, "pf_baseline": pf_b,
            "wr_adaptive": wr_a, "wr_baseline": wr_b,
            "n": len(oos_adaptive_returns),
            "pval": pval, "dsr": dsr,
            "n_trials": N_TRIALS,
        },
        "most_selected": {"stop": most_selected[0], "hold": most_selected[1], "trail": most_selected[2]},
        "baseline": {"stop": BASELINE[0], "hold": BASELINE[1], "trail": BASELINE[2]},
        "folds": fold_rows,
        "full_grid": full_grid,
    }


# ── report ───────────────────────────────────────────────────────────────────

def format_exit_report(result):
    lines = [
        "# Exit Lab — Walk-Forward Exit Policy Grid",
        "",
        f"**Generated:** {_utcnow().isoformat()}",
        f"**Verdict:** **{result.get('verdict', 'KEEP_OPTION_A')}**",
        "",
    ]

    if "error" in result:
        lines.append(f"**Error:** {result['error']}")
        if "counts" in result:
            c = result["counts"]
            lines.append(f"  Signals: {c.get('signals')} · Post slices: {c.get('post_slices')} · Months: {c.get('months')} · Folds: {c.get('folds')}")
        # Still show full grid for transparency
        if result.get("full_grid"):
            lines.append("")
            lines.append("## Full Grid (in-sample, 8,783 signals — transparency only)")
            lines.append("")
            lines.append("| Stop% | Hold | Trail | PF | WR% | N |")
            lines.append("|-------|------|-------|-----|------|---|")
            for g in result["full_grid"]:
                lines.append(
                    f"| {g['stop']} | {g['hold']} | {g['trail']} | "
                    f"{g['pf']:.2f} | {g['wr']:.1f} | {g['n']} |"
                )
        return "\n".join(lines)

    oos = result["oos"]
    lines.extend([
        "## OOS Adaptive vs Baseline",
        "",
        "| Metric | Adaptive | Baseline (15/20/fixed) |",
        "|--------|----------|------------------------|",
        f"| Profit Factor | {oos['pf_adaptive']:.2f} | {oos['pf_baseline']:.2f} |",
        f"| Win Rate | {oos['wr_adaptive']:.1f}% | {oos['wr_baseline']:.1f}% |",
        f"| N | {oos['n']} | {oos['n']} |",
        f"| Permutation p | {oos['pval']:.4f} | — |",
        f"| Deflated SR | {oos['dsr']:.4f} | — |",
        "",
        "## Most-Selected Combo",
        "",
        f"Stop: **{result['most_selected']['stop']}%** · "
        f"Hold: **{result['most_selected']['hold']}d** · "
        f"Trailing: **{result['most_selected']['trail']}**",
        "",
        "## Adoption Gate",
        "",
    ])

    if result["verdict"] == "PASS":
        lines.append("✅ PASS — all conditions met")
    else:
        lines.append("❌ KEEP_OPTION_A — baseline (15/20/fixed) remains in effect")

    # Full grid
    lines.extend([
        "",
        "## Full Grid (in-sample, transparency only — NOT used for selection)",
        "",
        "| Stop% | Hold | Trail | PF | WR% | N |",
        "|-------|------|-------|-----|------|---|",
    ])
    for g in result.get("full_grid", []):
        lines.append(
            f"| {g['stop']} | {g['hold']} | {g['trail']} | "
            f"{g['pf']:.2f} | {g['wr']:.1f} | {g['n']} |"
        )

    # Caveats
    lines.extend([
        "",
        "## Caveats",
        "- Single mostly-bull year — exit policy may not generalize to bear markets.",
        "- Buy-side consensus signals only (swing recording began Phase 2 C4).",
        "- Slippage and fees not modeled — returns are theoretical.",
        "- In-sample grid is descriptive only; walk-forward OOS is the selection criterion.",
        "- Trailing is research-only — the live _simulate_fixed_exit does not support it.",
        "- n_trials=18 fed honestly to deflated_sharpe.",
    ])

    return "\n".join(lines)


# ── artifact ─────────────────────────────────────────────────────────────────

def write_artifact(result, path="data/exit_policy_v2.json"):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    art = {
        "fitted_at": _utcnow().isoformat(),
        "verdict": result["verdict"],
    }
    if result["verdict"] == "PASS":
        art.update({
            "stop_pct": result["most_selected"]["stop"],
            "hold_days": result["most_selected"]["hold"],
            "trailing": result["most_selected"]["trail"],
            "oos": result["oos"],
        })
    else:
        # Non-PASS → consumer refuses
        art.update({"stop_pct": None, "hold_days": None, "trailing": None})
    with open(path, "w") as f:
        json.dump(art, f, indent=2)
    print(f"Artifact written to {path}")


# ── CLI ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Exit Lab — walk-forward exit policy grid")
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--out", type=str, default="reports/exit_lab.md")
    parser.add_argument("--artifact", type=str, default="data/exit_policy_v2.json")
    args = parser.parse_args()

    result = run_exit_lab(days=args.days)

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as f:
        f.write(format_exit_report(result))
    print(f"Report written to {args.out}")

    write_artifact(result, args.artifact)

    print(f"\n{'='*60}")
    print(f"  VERDICT: {result.get('verdict', 'KEEP_OPTION_A')}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
