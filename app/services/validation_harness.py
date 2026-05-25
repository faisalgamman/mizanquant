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
    """Approximate RRG quadrant from trailing RS ratio + momentum vs SPY."""
    try:
        e = etf_df["close"].astype(float)
        s = spy_df["close"].astype(float)
        df = pd.concat([e.rename("e"), s.rename("s")], axis=1).dropna()
        if len(df) < win * 3:
            return "unknown"
        rs = 100.0 * (df["e"] / df["s"])
        mean = rs.rolling(win).mean()
        std = rs.rolling(win).std()
        rs_ratio = 100.0 + ((rs - mean) / std).fillna(0)
        roc = rs_ratio.diff(win)
        rmean = roc.rolling(win).mean()
        rstd = roc.rolling(win).std()
        rs_mom = 100.0 + ((roc - rmean) / rstd).fillna(0)
        ratio_val = float(rs_ratio.iloc[-1])
        mom_val = float(rs_mom.iloc[-1])
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

    for as_of in grid:
        spy_slice = _slice_to(spy, as_of)
        if spy_slice is None:
            continue
        etf_slices = {e: _slice_to(df, as_of) for e, df in etf_series.items()}
        etf_slices = {e: d for e, d in etf_slices.items() if d is not None}
        bundle = pit_bundle(spy_slice, etf_slices)

        scored = []
        for s, df in series.items():
            sl = _slice_to(df, as_of)
            if sl is None:
                continue
            tscore = technical_score(sl, spy_slice)
            if tscore <= 0:
                continue
            mult, _quad, _rank = context_multiplier(sector_map.get(s), bundle)
            fwd = _forward_return(df, as_of, hold_days)
            if fwd is None:
                continue
            scored.append({"sym": s, "tscore": tscore, "enh": tscore * mult, "fwd": fwd})

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

    return {
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
        "acceptance": {
            "passes": passes,
            "criteria": "DSR≥0.95 AND permutation_p<0.05 AND bootstrap_lower>0 AND enhanced_mean>current_mean",
            "note": "Technical+context legs only — fundamentals/ML/sentiment excluded to avoid look-ahead. "
                    "Universe is survivorship-biased (today's halal names): treat absolute returns as an optimistic upper bound; "
                    "the CURRENT-vs-ENHANCED DELTA is the trustworthy signal.",
        },
    }


__all__ = ["run_selection_backtest", "technical_score", "pit_bundle"]
