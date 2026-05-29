"""Selection validation harness — point-in-time backtest of the buy list.

Purpose: PROVE that the ENHANCED selection (market-context-conditioned ranking)
beats the CURRENT selection (raw technical ranking) on out-of-sample forward
returns BEFORE we flip any live flag. This is the safety net for real money.

═══════════════════════════════════════════════════════════════════════════════
LOOK-AHEAD DISCIPLINE (read before trusting any number this produces)
═══════════════════════════════════════════════════════════════════════════════
Only PRICE BARS can be replayed honestly — `market_data.fetch(start, end)` slices
to a date. So this harness scores using TECHNICAL + MARKET-CONTEXT legs ONLY:

  * Scoring at date `as_of` uses bars[:as_of] exclusively.
  * Forward return uses close[as_of + hold_days] — that is the OUTCOME, never an
    input to scoring, so it is not look-ahead.

DELIBERATELY EXCLUDED (they are fetched live and would leak the future):
  - Fundamentals / halal ratios (today's balance sheet ≠ as_of's)
  - Analyst targets, news sentiment (no historical archive)
  - The real-ML forecast leg (models trained through today)

Two further documented limitations (optimistic-bias caveats):
  - Universe is TODAY's surviving halal names → survivorship bias (upper bound).
  - Sector classification is today's (sectors rarely change) → mild leak only.

n_trials for the Deflated Sharpe is taken from conviction_engine.N_TRIALS_REGISTERED
(the honest count of design variants considered), NOT 1.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from app.services.backtest_qc import qc_report
from app.services.conviction_engine import context_multiplier, N_TRIALS_REGISTERED
from app.services.signal_archetypes import detect_archetype, _ARCHETYPE_NAMES as _ARCH_BACKTEST_NAMES

logger = logging.getLogger("screener")

# SPDR sector ETFs → sector name (mirror of market_context_bundle._SECTOR_ETFS).
_SECTOR_ETFS = {
    "XLK": "Technology", "XLF": "Financials", "XLV": "Health Care", "XLE": "Energy",
    "XLI": "Industrials", "XLP": "Consumer Staples", "XLY": "Consumer Discretionary",
    "XLU": "Utilities", "XLB": "Materials", "XLRE": "Real Estate",
    "XLC": "Communication Services",
}


# ── Point-in-time indicators (bars only) ────────────────────────────────────────

def _ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()


def _rsi(close: pd.Series, period: int = 14) -> float:
    delta = close.diff()
    up = delta.clip(lower=0).rolling(period).mean()
    down = (-delta.clip(upper=0)).rolling(period).mean()
    rs = up / down.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    val = rsi.iloc[-1]
    return float(val) if pd.notna(val) else 50.0


def _macd_bull(close: pd.Series) -> bool:
    ema12 = _ema(close, 12)
    ema26 = _ema(close, 26)
    macd = ema12 - ema26
    signal = _ema(macd, 9)
    if len(macd) < 2:
        return False
    return bool(macd.iloc[-1] > signal.iloc[-1] and (macd.iloc[-1] - signal.iloc[-1]) > 0)


def technical_score(df: pd.DataFrame, spy_df: pd.DataFrame | None) -> float:
    """Point-in-time technical score 0-100 from bars up to (and incl.) the last row.

    Mirrors the spirit of the live technical leg (trend + RSI + MACD + RS + volume)
    using ONLY data available at `as_of`. Hard-trend failure caps the score low so
    a name below its 200-EMA can't rank highly — the backtest's gate floor.
    """
    if df is None or len(df) < 60:
        return 0.0
    close = df["close"].astype(float)
    last = float(close.iloc[-1])
    ema20 = float(_ema(close, 20).iloc[-1])
    ema200 = float(_ema(close, 200).iloc[-1]) if len(close) >= 200 else float(_ema(close, min(len(close)-1, 150)).iloc[-1])

    score = 0.0
    # Trend (hard floor: below EMA200 → cannot accumulate trend points)
    above_200 = last > ema200
    if above_200:
        score += 15
    if last > ema20:
        score += 15

    # RSI sweet spot
    rsi = _rsi(close)
    if 40 <= rsi <= 60:
        score += 20
    elif 30 <= rsi < 40 or 60 < rsi <= 70:
        score += 12
    else:
        score += 5

    # MACD
    if _macd_bull(close):
        score += 20

    # Relative strength vs SPY over 20 sessions
    if spy_df is not None and len(spy_df) >= 21 and len(close) >= 21:
        try:
            stock_ret = last / float(close.iloc[-21]) - 1.0
            spy_close = spy_df["close"].astype(float)
            spy_ret = float(spy_close.iloc[-1]) / float(spy_close.iloc[-21]) - 1.0
            rs = (stock_ret - spy_ret) * 100.0
            if rs > 5:
                score += 20
            elif rs > 0:
                score += 15
            elif rs > -1:
                score += 5
        except Exception:
            pass

    # Volume confirmation
    if "volume" in df.columns and len(df) >= 20:
        vol = df["volume"].astype(float)
        avg = float(vol.iloc[-20:].mean())
        if avg > 0 and float(vol.iloc[-1]) / avg >= 0.7:
            score += 10

    # Hard-trend floor: scale down hard if below EMA200 (gate analogue)
    if not above_200:
        score *= 0.4

    return float(min(100.0, score))


# ── Point-in-time market-context bundle (bars only) ─────────────────────────────

def _rs_quadrant(etf_df: pd.DataFrame, spy_df: pd.DataFrame, win: int = 21) -> str:
    """RRG quadrant from trailing RS-Ratio + RS-Momentum vs SPY.

    Aligns on the 'date' column (inner join) so ETF bars fetched from APIs with
    slightly different start dates — or with a handful of missing sessions — never
    cause an integer-index position mismatch that corrupts the RS calculation.

    Also applies a 3-period trailing mean to rs_ratio / rs_mom before reading the
    terminal value; this smooths single-day flip-flops that make the quadrant noisy.
    """
    try:
        # --- date-aligned inner join (fixes integer-index misalignment bug) ---
        e = etf_df[["date", "close"]].rename(columns={"close": "e"}).set_index("date")
        s = spy_df[["date", "close"]].rename(columns={"close": "s"}).set_index("date")
        df = e.join(s, how="inner").dropna()
        if len(df) < win * 3:
            return "unknown"
        rs = 100.0 * (df["e"] / df["s"])
        mean = rs.rolling(win).mean()
        std  = rs.rolling(win).std()
        rs_ratio = 100.0 + ((rs - mean) / std).fillna(0)
        roc   = rs_ratio.diff(win)
        rmean = roc.rolling(win).mean()
        rstd  = roc.rolling(win).std()
        rs_mom = 100.0 + ((roc - rmean) / rstd).fillna(0)
        # Smooth the terminal value over 3 bars to reduce flip-flop noise.
        ratio_val = float(rs_ratio.rolling(3).mean().iloc[-1])
        mom_val   = float(rs_mom.rolling(3).mean().iloc[-1])
        if ratio_val > 100 and mom_val > 100:
            return "leading"
        if ratio_val > 100 and mom_val <= 100:
            return "weakening"
        if ratio_val <= 100 and mom_val > 100:
            return "improving"
        return "lagging"
    except Exception:
        return "unknown"


def pit_bundle(as_of_spy: pd.DataFrame, etf_slices: dict) -> dict:
    """Build a point-in-time MarketContextBundle-shaped dict from bars only.

    risk_posture: from SPY trend (vs EMA200) + realized-vol proxy.
    sector_ranks: RRG quadrant per ETF from trailing RS vs SPY.
    """
    posture = "neutral"
    try:
        sc = as_of_spy["close"].astype(float)
        if len(sc) >= 200:
            ema200 = float(_ema(sc, 200).iloc[-1])
            last = float(sc.iloc[-1])
            # Realized 20d vol annualized as a VIX proxy
            rv = float(sc.pct_change().iloc[-20:].std() * np.sqrt(252) * 100) if len(sc) >= 21 else 0.0
            if last < ema200 or rv > 30:
                posture = "risk_off"
            elif last > ema200 and rv < 18:
                posture = "risk_on"
    except Exception:
        pass

    sector_ranks: dict = {}
    for etf, name in _SECTOR_ETFS.items():
        esl = etf_slices.get(etf)
        if esl is None or len(esl) < 63:
            continue
        quad = _rs_quadrant(esl, as_of_spy)
        sector_ranks[etf] = {"sector": name, "quadrant": quad, "rank": None}
    return {"risk_posture": posture, "sector_ranks": sector_ranks}


# ── Data access ─────────────────────────────────────────────────────────────────

def _fetch_series(symbol: str) -> pd.DataFrame | None:
    """Fetch ~1y daily bars indexed by tz-naive date. None on failure."""
    try:
        from app.services.market_data import fetch as md_fetch
        df = md_fetch(symbol, period="1y")
        if df is None or len(df) < 60:
            return None
        df = df.copy()
        df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
        return df.sort_values("date").reset_index(drop=True)
    except Exception as exc:
        logger.debug("harness fetch %s failed: %s", symbol, exc)
        return None


def _slice_to(df: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame | None:
    """Rows with date <= as_of. None if too few."""
    s = df[df["date"] <= as_of]
    return s if len(s) >= 60 else None


def _forward_return(df: pd.DataFrame, as_of: pd.Timestamp, hold_days: int) -> float | None:
    """Return from the close at/just before as_of to close hold_days later."""
    at = df[df["date"] <= as_of]
    fut = df[df["date"] > as_of]
    if len(at) == 0 or len(fut) < hold_days:
        return None
    entry = float(at["close"].iloc[-1])
    exit_px = float(fut["close"].iloc[hold_days - 1])
    if entry <= 0:
        return None
    return (exit_px - entry) / entry


def _simulate_trailing_stop(
    df: pd.DataFrame, as_of: pd.Timestamp, stop_pct: float, max_hold_days: int,
) -> tuple[float, int, str] | None:
    """Replay a trailing-stop exit on daily OHLC bars from entry at `as_of`.

    Entry = close at/just before as_of. For each forward bar:
      1. ratchet the high-water peak up using that bar's HIGH,
      2. if that bar's LOW pierces peak·(1 − stop_pct), exit at the stop level.
    If the stop is never hit, exit at the close of bar `max_hold_days` (time stop).

    Returns (return_fraction, holding_days, exit_reason) or None if no forward data.
    exit_reason ∈ {"trail_stop", "time_stop"}.

    NOTE: intrabar high-before-low ordering is assumed (standard trailing-stop
    convention). On a gap-down bar the LOW can be below the stop level itself; we
    then fill at the stop level, which is mildly optimistic on gap days — a known,
    documented simplification shared by most daily-bar stop backtests.
    """
    at = df[df["date"] <= as_of]
    fut = df[df["date"] > as_of]
    if len(at) == 0 or len(fut) < 1:
        return None
    entry = float(at["close"].iloc[-1])
    if entry <= 0:
        return None
    fut = fut.iloc[:max_hold_days]
    stop_frac = stop_pct / 100.0
    peak = entry
    for i in range(len(fut)):
        bar = fut.iloc[i]
        hi = float(bar["high"]) if "high" in fut.columns and pd.notna(bar["high"]) else float(bar["close"])
        lo = float(bar["low"]) if "low" in fut.columns and pd.notna(bar["low"]) else float(bar["close"])
        peak = max(peak, hi)
        stop_level = peak * (1.0 - stop_frac)
        if lo <= stop_level:
            return (stop_level - entry) / entry, i + 1, "trail_stop"
    exit_px = float(fut["close"].iloc[-1])
    return (exit_px - entry) / entry, len(fut), "time_stop"


def _simulate_live_exit_stack(
    df: pd.DataFrame, as_of: pd.Timestamp,
    trail_pct: float = 2.5, atr_mult: float = 1.5, max_hold_days: int = 20,
) -> tuple[float, int, str] | None:
    """Replay the EXACT live exit structure for one pick (trading_engine + trade_plan).

    Mirrors production:
      • initial stop  = min(recent_low − 0.005·entry, entry − ATR(14)·atr_mult)
                        (trade_plan.calculate_stop_loss)
      • R             = entry − initial_stop
      • TP ladder     = entry+1R / +2R / +3R closing 50% / 30% / 20%
                        (trade_plan.calculate_tp_levels)
      • trailing stop = trail_pct from the high-water peak (trading_engine trail_percent)
      • binding stop  = max(initial_stop, peak·(1−trail_pct))
      • time cap       = close remainder at close[max_hold_days]

    Per-bar order (conservative, no intrabar optimism): check the binding stop
    against the bar LOW first; if untouched, ratchet the peak with the bar HIGH
    and fill any TP levels the HIGH reached. The trade return is the share-weighted
    blend of every partial fill.

    Returns (blended_return_fraction, holding_days, last_exit_reason) or None.
    """
    at = df[df["date"] <= as_of]
    fut = df[df["date"] > as_of]
    if len(at) < 15 or len(fut) < 1:
        return None
    entry = float(at["close"].iloc[-1])
    if entry <= 0:
        return None
    try:
        from app.services.technical import atr as _ta_atr
        atr_val = float(_ta_atr(at, 14).iloc[-1])
    except Exception:
        atr_val = float((at["high"] - at["low"]).iloc[-14:].mean())
    if not (atr_val > 0):
        atr_val = entry * 0.02
    recent_low = float(at["low"].iloc[-10:].min())
    init_stop = min(recent_low - 0.005 * entry, entry - atr_val * atr_mult)
    if init_stop <= 0 or init_stop >= entry:
        init_stop = entry * 0.95
    R = entry - init_stop
    tp_levels = [(entry + R, 0.5), (entry + 2 * R, 0.3), (entry + 3 * R, 0.2)]
    tp_hit = [False, False, False]

    remaining = 1.0
    realized = 0.0
    peak = entry
    trail_frac = trail_pct / 100.0
    fut = fut.iloc[:max_hold_days]
    last_reason = "time_stop"
    for i in range(len(fut)):
        bar = fut.iloc[i]
        hi = float(bar["high"]) if "high" in fut.columns and pd.notna(bar["high"]) else float(bar["close"])
        lo = float(bar["low"]) if "low" in fut.columns and pd.notna(bar["low"]) else float(bar["close"])
        binding_stop = max(init_stop, peak * (1.0 - trail_frac))
        if lo <= binding_stop:
            realized += remaining * (binding_stop - entry) / entry
            return realized, i + 1, "stop"
        peak = max(peak, hi)
        for j, (lvl, frac) in enumerate(tp_levels):
            if not tp_hit[j] and hi >= lvl:
                realized += frac * (lvl - entry) / entry
                remaining -= frac
                tp_hit[j] = True
                last_reason = "take_profit"
        if remaining <= 1e-9:
            return realized, i + 1, "take_profit"
    exit_px = float(fut["close"].iloc[-1])
    realized += remaining * (exit_px - entry) / entry
    return realized, len(fut), last_reason


# ── The backtest ────────────────────────────────────────────────────────────────

def run_selection_backtest(
    symbols: list[str],
    lookback_days: int = 120,
    hold_days: int = 5,
    step_days: int = 5,
    top_n: int = 15,
    max_symbols: int = 80,
    sector_map: dict | None = None,
    n_trials: int = N_TRIALS_REGISTERED,
    archetype: str = "usx",
) -> dict:
    """Paired point-in-time backtest: CURRENT vs ENHANCED selection.

    For each as_of date (stepped through the last `lookback_days`):
      1. Score the universe with `technical_score` (bars[:as_of] only).
      2. Build a point-in-time context bundle (bars only).
      3. CURRENT picks top_n by technical_score.
         ENHANCED picks top_n by technical_score × context_multiplier(sector).
      4. Record each pick's forward return over `hold_days`.
    Then aggregate per-trade returns for each variant and run qc_report.

    Returns a dict with paired metrics + QC for both variants + the verdict.
    """
    syms = [s.upper() for s in symbols][:max_symbols]
    sector_map = sector_map or {}

    # Prefetch all series once (bars only; sliced per as_of for scoring).
    series: dict[str, pd.DataFrame] = {}
    for s in syms:
        df = _fetch_series(s)
        if df is not None:
            series[s] = df
    spy = _fetch_series("SPY")
    if spy is None or len(series) < 5:
        return {"status": "insufficient_data",
                "message": f"Need SPY + ≥5 symbols with bars; got {len(series)} symbols, SPY={'ok' if spy is not None else 'missing'}."}

    etf_series: dict[str, pd.DataFrame] = {}
    for etf in _SECTOR_ETFS:
        df = _fetch_series(etf)
        if df is not None:
            etf_series[etf] = df

    # Build the as_of grid from SPY's trading days.
    spy_dates = spy["date"]
    end_date = spy_dates.iloc[-1]
    start_date = end_date - timedelta(days=lookback_days)
    grid_all = spy_dates[(spy_dates >= start_date)].tolist()
    # Leave room for hold_days of forward bars after each as_of.
    grid = [d for d in grid_all[::step_days] if len(spy[spy["date"] > d]) >= hold_days]

    cur_returns: list[float] = []
    enh_returns: list[float] = []
    n_dates = 0
    # Diagnostic counters — help verify sector info is actually differentiating picks.
    _quad_counts: dict[str, int] = {}
    _mult_sum = 0.0
    _mult_n = 0

    for as_of in grid:
        spy_slice = _slice_to(spy, as_of)
        if spy_slice is None:
            continue
        etf_slices = {e: _slice_to(df, as_of) for e, df in etf_series.items()}
        etf_slices = {e: d for e, d in etf_slices.items() if d is not None}
        bundle = pit_bundle(spy_slice, etf_slices)

        use_archetype = archetype != "usx"
        scored = []
        for s, df in series.items():
            sl = _slice_to(df, as_of)
            if sl is None:
                continue
            tscore = technical_score(sl, spy_slice)
            if tscore <= 0:
                continue
            fwd = _forward_return(df, as_of, hold_days)
            if fwd is None:
                continue
            mult, quad, _rank = context_multiplier(sector_map.get(s), bundle)
            _quad_counts[quad] = _quad_counts.get(quad, 0) + 1
            _mult_sum += mult
            _mult_n += 1
            if use_archetype:
                # Archetype mode: ENHANCED = archetype detector score
                arch = detect_archetype(archetype, sl, spy_slice)
                arch_score = arch["score"] if arch else 0.0
                enh = arch_score
            else:
                enh = tscore * mult
            scored.append({"sym": s, "tscore": tscore, "enh": enh, "fwd": fwd})

        if len(scored) < top_n:
            continue
        n_dates += 1

        cur_top = sorted(scored, key=lambda x: x["tscore"], reverse=True)[:top_n]
        enh_top = sorted(scored, key=lambda x: x["enh"], reverse=True)[:top_n]
        cur_returns.extend(x["fwd"] for x in cur_top)
        enh_returns.extend(x["fwd"] for x in enh_top)

    if n_dates == 0 or not cur_returns:
        return {"status": "insufficient_data",
                "message": "No as_of dates produced ≥top_n scored names with forward bars."}

    def _summary(rets: list[float]) -> dict:
        arr = np.asarray(rets, dtype=float)
        wins = arr[arr > 0]
        return {
            "n_trades": int(arr.size),
            "mean_return_pct": round(float(arr.mean()) * 100, 3),
            "median_return_pct": round(float(np.median(arr)) * 100, 3),
            "win_rate_pct": round(float(wins.size) / arr.size * 100, 1) if arr.size else 0.0,
            "qc": qc_report(arr.tolist(), n_trials=n_trials),
        }

    cur = _summary(cur_returns)
    enh = _summary(enh_returns)

    # Paired bootstrap on the per-date mean-return delta would be ideal; as a
    # robust v1 we report the difference of means + whether ENHANCED passes the
    # acceptance gate. (Paired-by-date deltas can be added when needed.)
    enh_qc = enh["qc"]
    passes = bool(
        enh_qc["deflated_sharpe"] >= 0.95
        and enh_qc["permutation_pvalue"] < 0.05
        and enh_qc["bootstrap_lower_5pct"] > 0
        and enh["mean_return_pct"] > cur["mean_return_pct"]
    )

    # Diagnostics: were sector quadrants actually differentiating picks?
    # If avg_multiplier ≈ 1.0 and unknown_pct is high, sectors weren't used.
    total_obs = _mult_n or 1
    context_diagnostics = {
        "avg_multiplier": round(_mult_sum / total_obs, 4),
        "quadrant_distribution": {k: round(v / total_obs * 100, 1) for k, v in sorted(_quad_counts.items())},
        "unknown_pct": round(_quad_counts.get("unknown", 0) / total_obs * 100, 1),
        "differentiation_active": _quad_counts.get("unknown", 0) / total_obs < 0.70,
    }

    return {
        "archetype": archetype,
        "status": "ready",
        "as_of_dates": n_dates,
        "hold_days": hold_days,
        "step_days": step_days,
        "top_n": top_n,
        "universe_size": len(series),
        "n_trials": n_trials,
        "current": cur,
        "enhanced": enh,
        "delta_mean_return_pct": round(enh["mean_return_pct"] - cur["mean_return_pct"], 3),
        "context_diagnostics": context_diagnostics,
        "acceptance": {
            "passes": passes,
            "criteria": "DSR≥0.95 AND permutation_p<0.05 AND bootstrap_lower>0 AND enhanced_mean>current_mean",
            "note": "Technical+context legs only — fundamentals/ML/sentiment excluded to avoid look-ahead. "
                    "Universe is survivorship-biased (today's halal names): treat absolute returns as an optimistic upper bound; "
                    "the CURRENT-vs-ENHANCED DELTA is the trustworthy signal.",
        },
    }


def run_trailing_stop_backtest(
    symbols: list[str],
    lookback_days: int = 365,
    max_hold_days: int = 20,
    step_days: int = 5,
    top_n: int = 15,
    max_symbols: int = 90,
    stop_variants: tuple[float, ...] = (2.5, 4.0, 6.0),
    n_trials: int = N_TRIALS_REGISTERED,
) -> dict:
    """Compare trailing-stop widths on the VALIDATED CURRENT (technical) selection.

    Motivation: the selection backtest showed the technical edge is far stronger
    at a 20-day horizon (mean 2.63%, DSR 0.97) than at 5 days (1.22%, DSR 0.67).
    That raises the question — is the live 2.5% trailing stop exiting winners too
    early?  This harness answers it directly.

    For each as_of date it picks the SAME top_n by technical_score (the system we
    already validated), then simulates EVERY stop width on those identical picks,
    plus a `time_stop_only` baseline (hold to max_hold_days, no stop). Because all
    variants act on the same picks/dates, the comparison is paired and robust to
    the survivorship/overlap caveats that affect absolute numbers.

    Returns per-variant mean/median/win/QC + average holding days + exit mix.
    """
    syms = [s.upper() for s in symbols][:max_symbols]

    series: dict[str, pd.DataFrame] = {}
    for s in syms:
        df = _fetch_series(s)
        if df is not None:
            series[s] = df
    spy = _fetch_series("SPY")
    if spy is None or len(series) < 5:
        return {"status": "insufficient_data",
                "message": f"Need SPY + ≥5 symbols with bars; got {len(series)}."}

    spy_dates = spy["date"]
    end_date = spy_dates.iloc[-1]
    start_date = end_date - timedelta(days=lookback_days)
    grid_all = spy_dates[(spy_dates >= start_date)].tolist()
    # Need room for at least a few forward bars; require >= max_hold_days for the
    # time-stop baseline to be comparable across variants.
    grid = [d for d in grid_all[::step_days] if len(spy[spy["date"] > d]) >= max_hold_days]

    # Collect, per variant, the list of per-trade returns + holding days + exits.
    variants = list(stop_variants)
    returns: dict[str, list[float]] = {f"trail_{v}pct": [] for v in variants}
    hold_days_acc: dict[str, list[int]] = {f"trail_{v}pct": [] for v in variants}
    exit_mix: dict[str, dict[str, int]] = {f"trail_{v}pct": {"trail_stop": 0, "time_stop": 0} for v in variants}
    returns["time_stop_only"] = []
    hold_days_acc["time_stop_only"] = []

    n_dates = 0
    for as_of in grid:
        spy_slice = _slice_to(spy, as_of)
        if spy_slice is None:
            continue
        scored = []
        for s, df in series.items():
            sl = _slice_to(df, as_of)
            if sl is None:
                continue
            tscore = technical_score(sl, spy_slice)
            if tscore <= 0:
                continue
            scored.append({"sym": s, "tscore": tscore, "df": df})
        if len(scored) < top_n:
            continue
        n_dates += 1
        top = sorted(scored, key=lambda x: x["tscore"], reverse=True)[:top_n]

        for pick in top:
            df = pick["df"]
            # Time-stop baseline (hold to max_hold_days).
            base = _forward_return(df, as_of, max_hold_days)
            if base is not None:
                returns["time_stop_only"].append(base)
                hold_days_acc["time_stop_only"].append(max_hold_days)
            # Each trailing-stop width on the same pick.
            for v in variants:
                sim = _simulate_trailing_stop(df, as_of, v, max_hold_days)
                if sim is None:
                    continue
                ret, hd, reason = sim
                key = f"trail_{v}pct"
                returns[key].append(ret)
                hold_days_acc[key].append(hd)
                exit_mix[key][reason] += 1

    if n_dates == 0 or not returns["time_stop_only"]:
        return {"status": "insufficient_data",
                "message": "No as_of dates produced ≥top_n scored names with forward bars."}

    def _summary(rets: list[float], holds: list[int], exits: dict | None) -> dict:
        arr = np.asarray(rets, dtype=float)
        if arr.size == 0:
            return {"n_trades": 0}
        wins = arr[arr > 0]
        out = {
            "n_trades": int(arr.size),
            "mean_return_pct": round(float(arr.mean()) * 100, 3),
            "median_return_pct": round(float(np.median(arr)) * 100, 3),
            "win_rate_pct": round(float(wins.size) / arr.size * 100, 1),
            "avg_hold_days": round(float(np.mean(holds)), 1) if holds else None,
            "qc": qc_report(arr.tolist(), n_trials=n_trials),
        }
        if exits:
            tot = sum(exits.values()) or 1
            out["exit_mix_pct"] = {k: round(v / tot * 100, 1) for k, v in exits.items()}
        return out

    results = {"time_stop_only": _summary(returns["time_stop_only"], hold_days_acc["time_stop_only"], None)}
    for v in variants:
        key = f"trail_{v}pct"
        results[key] = _summary(returns[key], hold_days_acc[key], exit_mix[key])

    # Identify the best variant by mean return (with a positive bootstrap floor).
    ranked = sorted(
        ((k, r) for k, r in results.items() if r.get("n_trades", 0) > 0),
        key=lambda kv: kv[1].get("mean_return_pct", -999), reverse=True,
    )
    best = ranked[0][0] if ranked else None
    live_stop = "trail_2.5pct"
    live_mean = results.get(live_stop, {}).get("mean_return_pct")
    best_mean = results.get(best, {}).get("mean_return_pct") if best else None

    return {
        "status": "ready",
        "as_of_dates": n_dates,
        "max_hold_days": max_hold_days,
        "step_days": step_days,
        "top_n": top_n,
        "universe_size": len(series),
        "n_trials": n_trials,
        "variants": results,
        "best_variant": best,
        "verdict": {
            "current_live_stop": "2.5%",
            "current_live_mean_pct": live_mean,
            "best_variant": best,
            "best_mean_pct": best_mean,
            "improvement_pct": (round(best_mean - live_mean, 3)
                                if (best_mean is not None and live_mean is not None) else None),
            "note": "Picks come from the validated technical selection (CURRENT). All "
                    "variants share identical picks/dates → paired comparison. Daily-bar "
                    "trailing stop (high ratchets peak, low triggers exit). Survivorship- "
                    "biased universe → trust the CROSS-VARIANT delta, not absolute returns.",
        },
    }


def run_exit_structure_comparison(
    symbols: list[str],
    lookback_days: int = 365,
    max_hold_days: int = 20,
    step_days: int = 5,
    top_n: int = 15,
    max_symbols: int = 90,
    live_trail_pct: float = 2.5,
    live_atr_mult: float = 1.5,
    proposed_trail_pct: float = 12.0,
    n_trials: int = N_TRIALS_REGISTERED,
) -> dict:
    """Head-to-head: the ACTUAL live exit stack vs the proposed simplified exit.

    On the SAME validated technical picks/dates (paired), compares:
      • "live_stack"  — initial ATR stop + live trailing stop + TP1/2/3 partial
                        exits (exactly what production does today).
      • "proposed"    — single wide trailing stop (proposed_trail_pct) + time cap,
                        no early take-profits (the configuration the trailing-stop
                        backtest found optimal).

    This is the "verify before touching real-money risk params" step: it proves
    how much the current multi-exit structure leaves on the table before any
    trading-engine change is made.

    Survivorship-biased universe + technical-only selection → trust the paired
    DELTA between the two structures, not the absolute returns.
    """
    syms = [s.upper() for s in symbols][:max_symbols]
    series: dict[str, pd.DataFrame] = {}
    for s in syms:
        d = _fetch_series(s)
        if d is not None:
            series[s] = d
    spy = _fetch_series("SPY")
    if spy is None or len(series) < 5:
        return {"status": "insufficient_data",
                "message": f"Need SPY + ≥5 symbols with bars; got {len(series)}."}

    spy_dates = spy["date"]
    end_date = spy_dates.iloc[-1]
    start_date = end_date - timedelta(days=lookback_days)
    grid_all = spy_dates[(spy_dates >= start_date)].tolist()
    grid = [d for d in grid_all[::step_days] if len(spy[spy["date"] > d]) >= max_hold_days]

    live_rets: list[float] = []
    prop_rets: list[float] = []
    live_holds: list[int] = []
    prop_holds: list[int] = []
    live_exits: dict[str, int] = {"stop": 0, "take_profit": 0, "time_stop": 0}
    prop_exits: dict[str, int] = {"trail_stop": 0, "time_stop": 0}
    n_dates = 0

    for as_of in grid:
        spy_slice = _slice_to(spy, as_of)
        if spy_slice is None:
            continue
        scored = []
        for s, d in series.items():
            sl = _slice_to(d, as_of)
            if sl is None:
                continue
            tscore = technical_score(sl, spy_slice)
            if tscore <= 0:
                continue
            scored.append({"tscore": tscore, "df": d})
        if len(scored) < top_n:
            continue
        n_dates += 1
        top = sorted(scored, key=lambda x: x["tscore"], reverse=True)[:top_n]
        for pick in top:
            d = pick["df"]
            ls = _simulate_live_exit_stack(d, as_of, live_trail_pct, live_atr_mult, max_hold_days)
            ps = _simulate_trailing_stop(d, as_of, proposed_trail_pct, max_hold_days)
            if ls is not None:
                live_rets.append(ls[0]); live_holds.append(ls[1]); live_exits[ls[2]] = live_exits.get(ls[2], 0) + 1
            if ps is not None:
                prop_rets.append(ps[0]); prop_holds.append(ps[1]); prop_exits[ps[2]] = prop_exits.get(ps[2], 0) + 1

    if n_dates == 0 or not live_rets:
        return {"status": "insufficient_data",
                "message": "No as_of dates produced ≥top_n scored names with forward bars."}

    def _summary(rets, holds, exits) -> dict:
        arr = np.asarray(rets, dtype=float)
        wins = arr[arr > 0]
        tot = sum(exits.values()) or 1
        return {
            "n_trades": int(arr.size),
            "mean_return_pct": round(float(arr.mean()) * 100, 3),
            "median_return_pct": round(float(np.median(arr)) * 100, 3),
            "win_rate_pct": round(float(wins.size) / arr.size * 100, 1),
            "avg_hold_days": round(float(np.mean(holds)), 1) if holds else None,
            "exit_mix_pct": {k: round(v / tot * 100, 1) for k, v in exits.items()},
            "qc": qc_report(arr.tolist(), n_trials=n_trials),
        }

    live = _summary(live_rets, live_holds, live_exits)
    prop = _summary(prop_rets, prop_holds, prop_exits)
    delta = round(prop["mean_return_pct"] - live["mean_return_pct"], 3)

    return {
        "status": "ready",
        "as_of_dates": n_dates,
        "max_hold_days": max_hold_days,
        "step_days": step_days,
        "top_n": top_n,
        "universe_size": len(series),
        "n_trials": n_trials,
        "live_stack": live,
        "proposed": prop,
        "delta_mean_return_pct": delta,
        "verdict": {
            "live_config": f"ATR×{live_atr_mult} stop + {live_trail_pct}% trail + TP 1R/2R/3R (50/30/20%)",
            "proposed_config": f"{proposed_trail_pct}% trail + {max_hold_days}d time exit, no early TP",
            "proposed_beats_live": delta > 0,
            "improvement_pct": delta,
            "note": "Paired on identical validated technical picks/dates. Live stack mirrors "
                    "trade_plan.calculate_stop_loss + trading_engine trail + TP ladder exactly. "
                    "Conservative intrabar order (stop checked on LOW before TP on HIGH). "
                    "Survivorship-biased universe → trust the DELTA, not absolute returns.",
        },
    }


__all__ = [
    "run_selection_backtest", "run_trailing_stop_backtest",
    "run_exit_structure_comparison", "technical_score", "pit_bundle",
]
