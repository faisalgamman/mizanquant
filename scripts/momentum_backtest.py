"""CLI: research backtest of cross-sectional 12-1 momentum vs SPY on the halal universe.

Fetches ~N years of daily closes for the halal universe (+ known delisted names to
soften survivorship bias), runs the pure momentum backtest, and prints the verdict.

    python scripts/momentum_backtest.py --top 15 --years 5
    python scripts/momentum_backtest.py --top 20 --years 7 --max-symbols 200 --refresh

RESEARCH ONLY — not wired to trading. The result is a survivorship-biased UPPER
BOUND (current universe, not point-in-time); the real, live-tradeable edge is
almost always smaller. Caches the assembled price panel under reports/ so reruns
are fast (use --refresh to refetch).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import json  # noqa: E402
import pandas as pd  # noqa: E402


def _load_universe() -> list[str]:
    syms: list[str] = []
    jp = _ROOT / "data" / "halal_universe_v2.json"
    if jp.exists():
        try:
            syms = list(json.loads(jp.read_text()).get("symbols", []))
        except Exception:
            syms = []
    if not syms:
        from app.services.universe import HALAL_STOCKS_FALLBACK as fb
        syms = list(fb)
    try:
        from app.data.halal_exclusions import SP500_DELISTED_HALAL
        syms = syms + [s for s in SP500_DELISTED_HALAL if s not in syms]
    except Exception:
        pass
    return syms


def _close_series(d):
    """Date-indexed close Series from a fetch() DataFrame (RangeIndex + 'date' col)."""
    if d is None or "close" not in getattr(d, "columns", []) or len(d) == 0:
        return None
    if isinstance(d.index, pd.DatetimeIndex):
        idx = d.index
    elif "date" in d.columns:
        idx = pd.to_datetime(d["date"], errors="coerce")
    elif "datetime" in d.columns:
        idx = pd.to_datetime(d["datetime"], errors="coerce")
    else:
        idx = pd.to_datetime(d.index, errors="coerce")
    return pd.Series(pd.to_numeric(d["close"].values, errors="coerce"), index=idx).dropna()


def _build_panel(symbols, years, refresh):
    cache = _ROOT / "reports" / f"momentum_panel_{years}y.csv"
    if cache.exists() and not refresh:
        print(f"[cache] loading {cache.name}")
        df = pd.read_csv(cache, index_col=0, parse_dates=True)
        return df
    from app.services.market_data import fetch
    period = f"{years}y"
    series = {}
    ok = fail = 0
    for i, sym in enumerate(symbols, 1):
        try:
            s = _close_series(fetch(sym, period=period))
            if s is not None and len(s) > 0:
                series[sym] = s
                ok += 1
            else:
                fail += 1
        except Exception:
            fail += 1
        if i % 50 == 0:
            print(f"  fetched {i}/{len(symbols)} (ok={ok}, fail={fail})")
    panel = pd.DataFrame(series).sort_index()
    cache.parent.mkdir(parents=True, exist_ok=True)
    panel.to_csv(cache)
    print(f"[fetch] {ok} symbols ok, {fail} failed → cached {cache.name}")
    return panel


def _fmt(m):
    return (f"CAGR {m['cagr_pct']:>7.2f}%  Sharpe {m['sharpe']:>5.2f}  "
            f"MaxDD {m['max_dd_pct']:>7.2f}%  Total {m['total_return_pct']:>8.2f}%  (n={m['months']}mo)")


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(description="Cross-sectional momentum backtest (research).")
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--lookback", type=int, default=252)
    ap.add_argument("--skip", type=int, default=21)
    ap.add_argument("--cost-bps", type=float, default=20.0)
    ap.add_argument("--years", type=int, default=5)
    ap.add_argument("--max-symbols", type=int, default=0, help="0 = all")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--out", type=str, default=None)
    args = ap.parse_args(argv)

    from app.services.momentum_backtest import cross_sectional_momentum_backtest

    symbols = _load_universe()
    if args.max_symbols and args.max_symbols < len(symbols):
        symbols = symbols[: args.max_symbols]
    print(f"Universe: {len(symbols)} symbols · {args.years}y · top-{args.top} · cost {args.cost_bps}bps/side")

    panel = _build_panel(symbols, args.years, args.refresh)
    if "SPY" in panel.columns:
        spy = panel["SPY"]
        panel = panel.drop(columns=["SPY"])
    else:
        from app.services.market_data import fetch
        spy = _close_series(fetch("SPY", period=f"{args.years}y"))
    if spy is None or panel.shape[1] < args.top:
        print("ERROR: insufficient data (SPY or breadth). Try --refresh or fewer --top.")
        return 1

    res = cross_sectional_momentum_backtest(
        panel, spy, top_n=args.top, lookback=args.lookback, skip=args.skip, cost_bps=args.cost_bps,
    )
    if "error" in res:
        print("ERROR:", res["error"])
        return 1

    print("\n" + "=" * 78)
    print(f"  {res['strategy']}")
    print(f"  Period: {res['period']}  ·  avg monthly turnover {res['avg_monthly_turnover_pct']}%")
    print("-" * 78)
    print(f"  Momentum : {_fmt(res['momentum_portfolio'])}")
    print(f"  SPY hold : {_fmt(res['spy_buy_hold'])}")
    print(f"  DSR {res['dsr']}  ·  permutation p {res['permutation_p']}")
    print("-" * 78)
    print(f"  VERDICT: {res['verdict']}")
    print("  NOTE: survivorship-biased UPPER BOUND (universe is current, NOT point-in-time);")
    print("        real live edge is almost always smaller. Research only — not wired to trading.")
    print("=" * 78)

    if args.out:
        Path(args.out).write_text(json.dumps(res, indent=2), encoding="utf-8")
        print(f"[written] {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
