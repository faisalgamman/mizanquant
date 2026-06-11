
"""
Signal Attribution - Phase 0: MEASUREMENT ONLY.
Loads matured signals, reconstructs technical features at signal date,
computes rank-IC and per-band/decile/regime statistics.
Pandas-only math, no scipy. Honest caveats in report.
"""
import os
import sys
import json
import argparse
from datetime import datetime, timedelta, timezone
import numpy as np
import pandas as pd

def _utcnow():
    return datetime.now(timezone.utc)

def load_matured_signals(days=365, db_override=None):
    """Load SignalHistory rows with outcome_return_pct IS NOT NULL."""
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from app.db.database import SessionLocal
    from app.db.models import SignalHistory
    cutoff = _utcnow() - timedelta(days=days)
    db = SessionLocal()
    try:
        rows = (db.query(SignalHistory)
                .filter(SignalHistory.outcome_return_pct.isnot(None),
                        SignalHistory.created_at >= cutoff)
                .all())
        return [
            {
                "id": r.id, "symbol": r.symbol, "signal_type": r.signal_type,
                "signal": r.signal, "score": r.score or 0, "price": r.price or 0,
                "created_at": r.created_at.replace(tzinfo=timezone.utc) if r.created_at else None,
                "outcome_date": r.outcome_date.replace(tzinfo=timezone.utc) if r.outcome_date else None,
                "outcome_return_pct": r.outcome_return_pct or 0,
                "details": r.details or {},
            }
            for r in rows
        ]
    finally:
        db.close()

def apply_signal_filter(signals, direction):
    """Filter signals by BUY/SELL direction. Returns filtered list and count."""
    if direction == "all":
        return signals, len(signals)
    if direction == "buy":
        filtered = [s for s in signals if "BUY" in (s.get("signal") or "").upper()]
        return filtered, len(filtered)
    if direction == "sell":
        filtered = [s for s in signals
                    if "SELL" in (s.get("signal") or "").upper()
                    and "BUY" not in (s.get("signal") or "").upper()]
        return filtered, len(filtered)
    return signals, len(signals)


def fetch_prices_for_signals(signals):
    """Fetch 2y price data once per unique symbol + SPY."""
    from app.services.market_data import fetch as md_fetch
    symbols = list(set(s["symbol"] for s in signals)) + ["SPY"]
    data = {}
    for sym in symbols:
        try:
            df = md_fetch(sym, period="2y")
            if df is not None and len(df) >= 60:
                if "date" in df.columns:
                    df = df.set_index("date")
                data[sym] = df
        except Exception:
            pass
    return data

def _compute_features_for_signal(signal, df, spy_df):
    """Reconstruct technical features at signal date. Returns dict or None.

    D1: delegates to app.services.feature_lib.compute_features (shared with
    fitter and scorer) — wraps with signal metadata + excess_vs_spy.
    """
    try:
        from app.services.feature_lib import _cut, compute_features as _lib_feats

        created = signal["created_at"]
        feats = _lib_feats(df, spy_df, created)
        if feats is None:
            return None

        # Excess vs SPY (Phase 1)
        excess_vs_spy = None
        outcome_date = signal.get("outcome_date")
        if outcome_date is not None and spy_df is not None and len(spy_df) >= 2:
            try:
                spy_created = _cut(spy_df, created)
                spy_outcome = _cut(spy_df, outcome_date)
                if len(spy_created) >= 1 and len(spy_outcome) >= 1:
                    spyc_entry = float(spy_created["close"].iloc[-1])
                    spyc_exit = float(spy_outcome["close"].iloc[-1])
                    if spyc_entry > 0:
                        spy_ret_pct = (spyc_exit / spyc_entry - 1) * 100
                        excess_vs_spy = round(signal["outcome_return_pct"] - spy_ret_pct, 2)
            except Exception:
                pass

        return {
            "signal_id": signal["id"],
            "symbol": signal["symbol"],
            "signal_type": signal["signal_type"],
            "signal": signal["signal"],
            "score": signal["score"],
            "outcome_return_pct": signal["outcome_return_pct"],
            "excess_vs_spy": excess_vs_spy,
            "regime": signal["details"].get("regime", "Unknown") if signal["details"] else "Unknown",
            "forecast_agrees": signal["details"].get("forecast_agrees"),
            **feats,
        }
    except Exception:
        return None

def reconstruct_features(signals, price_data):
    """Reconstruct features for all signals. Returns (DataFrame, skipped_count)."""
    spy_df = price_data.get("SPY")
    rows = []
    skipped = 0
    for sig in signals:
        sym = sig["symbol"]
        df = price_data.get(sym)
        if df is None:
            skipped += 1
            continue
        feats = _compute_features_for_signal(sig, df, spy_df)
        if feats is None:
            skipped += 1
            continue
        rows.append(feats)
    return pd.DataFrame(rows), skipped

def compute_attribution(features_df, signals, days=365, skipped=0):
    """Compute rank-IC, per-band, per-decile, per-regime statistics."""
    feat_cols = [c for c in features_df.columns
                 if c not in ("signal_id","symbol","signal_type","signal","score",
                              "outcome_return_pct","excess_vs_spy","regime","forecast_agrees")]
    num_cols = [c for c in feat_cols if features_df[c].dtype in (np.float64, np.int64, float, int, np.bool_)]
    bool_cols = [c for c in feat_cols if features_df[c].dtype == np.bool_]
    # bool dtype matches BOTH lists above — dedupe so each feature gets ONE IC row.
    all_num = list(dict.fromkeys(num_cols + bool_cols))
    ret = features_df["outcome_return_pct"].values
    # Overall stats
    profits = ret[ret > 0]
    losses = ret[ret <= 0]
    overall_wr = round(len(profits) / len(ret) * 100, 1) if len(ret) > 0 else 0
    overall_pf = round(sum(profits) / abs(sum(losses)), 2) if len(losses) > 0 and sum(losses) != 0 else (999 if len(losses) == 0 else 0)
    # Overall avg excess
    excess_col = features_df.get("excess_vs_spy")
    excess_vals = excess_col.dropna().values if excess_col is not None else np.array([])
    overall_avg_excess = round(float(np.mean(excess_vals)), 2) if len(excess_vals) > 0 else None
    header = {
        "as_of": _utcnow().isoformat(), "days": days, "n": len(features_df),
        "overall_wr": overall_wr, "overall_pf": overall_pf,
        "overall_avg_excess": overall_avg_excess,
        "skipped_symbols": skipped,
    }
    # Rank-IC
    ic_rows = []
    for col in all_num:
        vals = features_df[col].values.astype(float)
        mask = ~np.isnan(vals) & ~np.isinf(vals)
        if mask.sum() < 30:
            continue
        rank_feat = pd.Series(vals[mask]).rank()
        rank_ret = pd.Series(ret[mask]).rank()
        ic = round(float(rank_feat.corr(rank_ret)), 4)
        ic_rows.append({"feature": col, "ic": ic, "n": int(mask.sum())})
    ic_rows.sort(key=lambda x: abs(x["ic"]), reverse=True)
    # Rank-IC vs EXCESS return (skill, beta removed)
    ic_excess_rows = []
    if excess_col is not None and len(excess_vals) >= 30:
        for col in all_num:
            vals = features_df[col].values.astype(float)
            mask = ~np.isnan(vals) & ~np.isinf(vals)
            excess_mask = ~np.isnan(excess_col.values) & ~np.isinf(excess_col.values)
            joint = mask & excess_mask
            if joint.sum() < 30:
                continue
            rank_feat = pd.Series(vals[joint]).rank()
            rank_exc = pd.Series(excess_col.values[joint]).rank()
            ic = round(float(rank_feat.corr(rank_exc)), 4)
            ic_excess_rows.append({"feature": col, "ic": ic, "n": int(joint.sum())})
        ic_excess_rows.sort(key=lambda x: abs(x["ic"]), reverse=True)
    # Per-band
    per_band = []
    for (stype, sig), grp in features_df.groupby(["signal_type", "signal"]):
        gret = grp["outcome_return_pct"].values
        wp = gret[gret > 0]
        lp = gret[gret <= 0]
        n = len(grp)
        wr = round(len(wp)/n*100, 1) if n > 0 else 0
        pf = round(sum(wp)/abs(sum(lp)), 2) if len(lp)>0 and sum(lp)!=0 else (999 if len(lp)==0 else 0)
        per_band.append({
            "signal_type": stype, "signal": sig, "n": n,
            "win_rate": wr,
            "avg_ret": round(float(np.mean(gret)), 2) if n>0 else 0,
            "median_ret": round(float(np.median(gret)), 2) if n>0 else 0,
            "profit_factor": pf,
            "avg_excess": round(float(grp["excess_vs_spy"].dropna().mean()), 2)
            if "excess_vs_spy" in grp.columns and grp["excess_vs_spy"].dropna().any() else None,
        })
    per_band.sort(key=lambda x: (-x["n"], -x["win_rate"]))
    # Score decile
    per_decile = []
    scores = features_df["score"].values.astype(float)
    if len(scores) >= 10:
        try:
            deciles = pd.qcut(scores, 10, labels=False, duplicates="drop")
            for d in sorted(set(deciles)):
                mask = deciles == d
                dret = ret[mask]
                per_decile.append({
                    "decile": int(d), "n": int(mask.sum()),
                    "score_min": round(float(scores[mask].min()), 1),
                    "score_max": round(float(scores[mask].max()), 1),
                    "avg_outcome": round(float(np.mean(dret)), 2) if len(dret)>0 else 0,
                })
        except Exception:
            pass
    # Per-regime
    per_regime = []
    for regime, grp in features_df.groupby("regime"):
        gret = grp["outcome_return_pct"].values
        wp_ = gret[gret > 0]
        lp_ = gret[gret <= 0]
        n = len(grp)
        wr = round(len(wp_)/n*100, 1) if n>0 else 0
        pf = round(sum(wp_)/abs(sum(lp_)), 2) if len(lp_)>0 and sum(lp_)!=0 else (999 if len(lp_)==0 else 0)
        per_regime.append({
            "regime": str(regime), "n": n, "win_rate": wr,
            "avg_ret": round(float(np.mean(gret)), 2) if n>0 else 0,
            "profit_factor": pf,
        })
    per_regime.sort(key=lambda x: -x["n"])
    # Per forecast_agrees
    per_forecast = []
    if "forecast_agrees" in features_df.columns:
        for agrees, grp in features_df.groupby("forecast_agrees"):
            label = "True" if agrees is True else ("False" if agrees is False else "None")
            gret = grp["outcome_return_pct"].values
            wp2 = gret[gret > 0]
            lp2 = gret[gret <= 0]
            n = len(grp)
            wr = round(len(wp2)/n*100, 1) if n>0 else 0
            pf = round(sum(wp2)/abs(sum(lp2)), 2) if len(lp2)>0 and sum(lp2)!=0 else (999 if len(lp2)==0 else 0)
            per_forecast.append({
                "forecast_agrees": label, "n": n, "win_rate": wr,
                "avg_ret": round(float(np.mean(gret)), 2) if n>0 else 0,
                "profit_factor": pf,
            })
        per_forecast.sort(key=lambda x: -x["n"])
    # Verdict
    candidates = [r for r in ic_rows if abs(r["ic"]) >= 0.03 and r["n"] >= 1000]
    noise = [r for r in ic_rows if abs(r["ic"]) < 0.02]
    verdict = {
        "candidate_features": [r["feature"] for r in candidates],
        "noise_features": [r["feature"] for r in noise],
        "caveats": [
            "Fundamentals are NOT point-in-time reconstructable - excluded here, captured going forward by Task A2.",
            "IC measured on OUR exit policy (Option-A: fixed catastrophe stop + time exit). Exit changes (Phase 4) can change these numbers.",
            "This is in-sample description, not proof of edge. Out-of-sample validation required before any weight change.",
        ],
    }
    return {
        "header": header, "rank_ic": ic_rows,
        "rank_ic_excess": ic_excess_rows,
        "per_band": per_band, "per_decile": per_decile,
        "per_regime": per_regime, "per_forecast": per_forecast,
        "verdict": verdict,
    }

def format_markdown_report(summary):
    """Generate Markdown report."""
    h = summary["header"]
    lines = [
        "# Signal Attribution Report - Phase 0",
        "",
        f"**Generated:** {h['as_of']}  ",
        f"**Period:** {h['days']} days  ",
        f"**Matured signals evaluated:** {h['n']}  ",
        f"**Overall Win Rate:** {h['overall_wr']}%  \n",
        f"**Overall Profit Factor:** {h['overall_pf']}  \n",
        f"**Overall Avg Excess vs SPY:** {h.get('overall_avg_excess', '—')}%  \n" if h.get('overall_avg_excess') is not None else "",
        f"**Symbols skipped (no price data):** {h['skipped_symbols']}  \n",
        "",
        "## Rank-IC (Spearman rank correlation with outcome_return_pct)",
        "",
        "| Feature | IC | N | Assessment |",
        "|---------|-----|---|------------|",
    ]
    for row in summary["rank_ic"]:
        ic = row["ic"]
        n = row["n"]
        if abs(ic) >= 0.03 and n >= 1000:
            assess = "**CANDIDATE** (real signal)"
        elif abs(ic) < 0.02:
            assess = "noise - remove candidate"
        else:
            assess = "marginal"
        lines.append(f"| {row['feature']} | {ic:+.4f} | {n} | {assess} |")
    lines.append("")
    # Rank-IC vs EXCESS
    if summary.get("rank_ic_excess"):
        lines.append("## Rank-IC vs EXCESS return (skill, beta removed)")
        lines.append("")
        lines.append("| Feature | IC | N |")
        lines.append("|---------|-----|---|")
        for row in summary["rank_ic_excess"]:
            lines.append(f"| {row['feature']} | {row['ic']:+.4f} | {row['n']} |")
        lines.append("")
    # Per-band
    lines.append("## Per-Band Performance")
    lines.append("")
    lines.append("| Signal Type | Signal | N | Win Rate | Avg Ret | Median Ret | Avg Excess | PF |")
    lines.append("|------------|--------|---|----------|---------|-----------|------------|-----|")
    for row in summary["per_band"]:
        exc = f"{row['avg_excess']}%" if row.get("avg_excess") is not None else "—"
        lines.append(f"| {row['signal_type']} | {row['signal']} | {row['n']} | "
                     f"{row['win_rate']}% | {row['avg_ret']}% | {row['median_ret']}% | "
                     f"{exc} | {row['profit_factor']} |")
    lines.append("")
    # Per-decile
    if summary["per_decile"]:
        lines.append("## Score Decile Monotonicity")
        lines.append("")
        lines.append("| Decile | N | Score Range | Avg Outcome % |")
        lines.append("|--------|---|-------------|---------------|")
        for row in summary["per_decile"]:
            lines.append(f"| {row['decile']} | {row['n']} | "
                         f"{row['score_min']}-{row['score_max']} | {row['avg_outcome']} |")
        top_d = summary["per_decile"][-1]
        bot_d = summary["per_decile"][0]
        lines.append("")
        if top_d["avg_outcome"] > bot_d["avg_outcome"]:
            lines.append(f"Top decile avg ({top_d['avg_outcome']}%) > bottom ({bot_d['avg_outcome']}%) - composite score carries positive rank information.")
        else:
            lines.append(f"**WARNING: Top decile avg ({top_d['avg_outcome']}%) <= bottom ({bot_d['avg_outcome']}%) - composite ranking carries NO information.**")
        lines.append("")
    # Per-regime
    lines.append("## Per-Regime Performance")
    lines.append("")
    lines.append("| Regime | N | Win Rate | Avg Ret | PF |")
    lines.append("|--------|---|----------|---------|-----|")
    for row in summary["per_regime"]:
        lines.append(f"| {row['regime']} | {row['n']} | {row['win_rate']}% | "
                     f"{row['avg_ret']}% | {row['profit_factor']} |")
    lines.append("")
    # Per-forecast
    if summary["per_forecast"]:
        lines.append("## Per-Forecast-Agrees")
        lines.append("")
        lines.append("| Forecast Agrees | N | Win Rate | Avg Ret | PF |")
        lines.append("|-----------------|---|----------|---------|-----|")
        for row in summary["per_forecast"]:
            lines.append(f"| {row['forecast_agrees']} | {row['n']} | "
                         f"{row['win_rate']}% | {row['avg_ret']}% | {row['profit_factor']} |")
        lines.append("")
    # Verdict
    v = summary["verdict"]
    lines.append("## Verdict")
    lines.append("")
    if v["candidate_features"]:
        lines.append(f"**Candidate real signal ({len(v['candidate_features'])} features):** "
                     f"{', '.join(v['candidate_features'])}")
    if v["noise_features"]:
        lines.append(f"**Noise - candidate for removal in Phase 1 ({len(v['noise_features'])} features):** "
                     f"{', '.join(v['noise_features'])}")
    lines.append("")
    lines.append("### Caveats")
    for c in v["caveats"]:
        lines.append(f"- {c}")
    return "\n".join(lines)

def format_stdout_report(summary):
    """Human-readable console summary (exported for tests)."""
    h = summary["header"]
    lines = [
        f"Signal Attribution Report - {h['as_of']}",
        f"Period: {h.get('days','?')}d  |  Signals: {h['n']}  |  "
        f"WR: {h.get('overall_wr','?')}%  |  PF: {h.get('overall_pf','?')}  |  "
        f"Skipped: {h.get('skipped_symbols','?')}",
    ]
    ic_table = summary.get("rank_ic", [])
    if ic_table:
        for row in ic_table:
            ic_val = row["ic"]
            n_val = row["n"]
            flag = ("CANDIDATE (|IC|>=0.03)" if abs(ic_val) >= 0.03 and n_val >= 1000
                    else "noise" if abs(ic_val) < 0.02
                    else "marginal")
            lines.append(f"  {row['feature']:<25} {ic_val:>+7.4f}  {n_val:>6d}  {flag}")
    return "\n".join(lines)

def main():
    parser = argparse.ArgumentParser(description="Phase 0 Signal Attribution")
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--signal-filter", type=str, default="all",
                        choices=["all", "buy", "sell"],
                        help="Filter signals by direction (all/buy/sell)")
    parser.add_argument("--out", type=str, default="reports/signal_attribution.md")
    parser.add_argument("--json-out", type=str, default="reports/signal_attribution.json")
    parser.add_argument("--db-url", type=str, default=None)
    args = parser.parse_args()

    try:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        if args.db_url:
            import app.db.database as _adb
            from sqlalchemy import create_engine as _ce
            _adb.engine = _ce(args.db_url)

        print(f"Loading matured signals (last {args.days} days)...")
        signals = load_matured_signals(days=args.days)
        print(f"  Loaded {len(signals)} matured signals.")

        # Apply signal-direction filter (B1)
        if args.signal_filter != "all":
            signals, filtered_n = apply_signal_filter(signals, args.signal_filter)
            print(f"  After --signal-filter {args.signal_filter}: {filtered_n} signals.")

        if len(signals) < 50:
            print("  WARNING: Too few signals for meaningful attribution. Exiting.")
            return

        print("Fetching price data...")
        price_data = fetch_prices_for_signals(signals)
        print(f"  Got price data for {len(price_data)} symbols.")

        print("Reconstructing features...")
        features_df, skipped = reconstruct_features(signals, price_data)
        print(f"  {len(features_df)} rows, {skipped} signals skipped (insufficient bars).")

        print("Computing attribution statistics...")
        summary = compute_attribution(features_df, signals, days=args.days, skipped=skipped)

        print(format_stdout_report(summary))

        # Write JSON
        os.makedirs(os.path.dirname(args.json_out) or ".", exist_ok=True)
        with open(args.json_out, "w") as f:
            json.dump(summary, f, indent=2, default=str)
        print(f"\nJSON report written to {args.json_out}")

        # Write Markdown
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        md = format_markdown_report(summary)
        with open(args.out, "w") as f:
            f.write(md)
        print(f"Markdown report written to {args.out}")

    except ModuleNotFoundError as e:
        print(f"ERROR: Cannot import app modules - run from project root.\n  {e}")
        sys.exit(1)
    except RuntimeError as e:
        print(f"ERROR: {e}")
        sys.exit(1)
    except OSError as e:
        print(f"FILE ERROR: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
