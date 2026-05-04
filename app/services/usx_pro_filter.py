"""USX Pro V4 pre-filter — port of the TradingView Pine indicator into a
single Python pass that runs ahead of the AI consensus.

Pipeline integration:

    HALAL_STOCKS  ─►  USX V4 (this module)  ─►  AI consensus  ─►  Telegram
       (~357)         (regime + per-stock)       (14 tools/sym)    (BUY only)

Stage 1 — market regime gate (one shot, not per-symbol):
    - SPY daily bull (price > EMA21)
    - VIX percentile rank < 85th (252-day window)
    - HY credit spreads OK (HYG/LQD and HYG/TLT vs their EMA20)
    - Breadth proxy (VIX-derived heuristic; the original Pine uses
      INDEX:MMTH which yfinance doesn't expose, so we approximate)
    If any check fails → no signals today, full stop.

Stage 2 — per-symbol qualifier (cheap):
    - Liquidity: ADV20 (20-day avg dollar volume) >= $50M, price >= $10
    - Not extended: |% from daily EMA200| <= 15
    - Daily uptrend: price > EMA21 AND EMA21 > EMA50
    - Weighted USX V4 score >= 65 (out of 100)
    - Earnings >= 5 days away (or earnings data missing → blocked,
      fail-safe)

The weighted score is the same 100-point formula from
USX_Pro_Dashboard_V4.pine (long side):

    Daily Trend (price>EMA21>EMA50)        20
    Regime (SPY bull + VIX OK)             15
    MACD histogram positive & rising       10
    RSI in 45..65 zone                      8
    ADX >= 20 with DI+ > DI-                7
    RS vs SPY (5-bar excess ROC, tiered)   20
    Volume ratio (>=1.2: 6, >=1.0: 3)       6
    Gap up 0..1.5%                          4
    Bollinger squeeze                       5
    Above VWAP                              5
                                          ----
                                          100

Every callsite that needs to scan many tickers should call
`filter_universe(symbols)` once — it caches the regime check and the
SPY/VIX/HY series for the duration of the call so per-symbol cost
stays small.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np
import pandas as pd

from app.services.technical import adx, atr, bollinger_bands, ema, macd, rsi, sma, vwap

logger = logging.getLogger("screener")


# ---------------------------------------------------------------------------
# Defaults (mirror the Pine input defaults)
# ---------------------------------------------------------------------------

DEFAULT_MIN_SCORE = 65.0
DEFAULT_MIN_ADV_M = 50.0           # $M, 20-day average dollar volume
DEFAULT_MIN_PRICE = 10.0
DEFAULT_MAX_EXTENSION_PCT = 15.0   # |% from daily EMA200|
DEFAULT_VIX_RANK_BLOCK = 85.0      # block above this percentile
DEFAULT_EARNINGS_BLACKOUT_DAYS = 5

# Cache for market-wide series so we don't refetch SPY/VIX/HYG/LQD/TLT
# per symbol. Cleared at the start of every filter_universe() call.
_market_cache: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Data fetching wrappers
# ---------------------------------------------------------------------------

def _fetch_daily(symbol: str, period: str = "1y") -> Optional[pd.DataFrame]:
    """Wrapper around halal_screener.fetch_yf that returns daily bars
    with the columns the Pine port expects."""
    try:
        import halal_screener as hs

        df = hs.fetch_yf(symbol, period=period)
        if df is None or len(df) < 60:
            return None
        return df
    except Exception as exc:  # noqa: BLE001
        logger.debug("usx_filter: fetch_yf(%s) failed: %s", symbol, exc)
        return None


def _next_earnings_days(symbol: str) -> Optional[int]:
    """Return integer days until next earnings, or None when unavailable."""
    try:
        import yfinance as yf

        ticker = yf.Ticker(symbol)
        cal = getattr(ticker, "calendar", None)
        if cal is None or cal.empty:
            return None
        # yfinance returns either a DataFrame or dict depending on version.
        if isinstance(cal, dict):
            ed = cal.get("Earnings Date")
        else:
            ed = cal.iloc[0].get("Earnings Date") if "Earnings Date" in cal.index else None
        if ed is None:
            return None
        if isinstance(ed, (list, tuple, np.ndarray, pd.Series)):
            ed = list(ed)[0] if len(ed) else None
        if ed is None:
            return None
        ed_ts = pd.Timestamp(ed)
        if ed_ts.tzinfo is None:
            ed_ts = ed_ts.tz_localize("US/Eastern")
        now_et = pd.Timestamp.now(tz="US/Eastern")
        return int((ed_ts - now_et).total_seconds() // 86400)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Stage 1 — market regime gate
# ---------------------------------------------------------------------------

@dataclass
class RegimeReport:
    spy_bull: bool
    vix_rank: float
    vix_ok: bool
    credit_ok: bool
    breadth_ok: bool
    overall_ok: bool
    reason: str

    def as_dict(self) -> dict:
        return {
            "spy_bull": self.spy_bull,
            "vix_rank": round(self.vix_rank, 1) if not np.isnan(self.vix_rank) else None,
            "vix_ok": self.vix_ok,
            "credit_ok": self.credit_ok,
            "breadth_ok": self.breadth_ok,
            "overall_ok": self.overall_ok,
            "reason": self.reason,
        }


def _percent_rank(series: pd.Series, lookback: int = 252) -> float:
    """Latest value's percentile rank within the last `lookback` bars."""
    s = series.dropna().tail(lookback)
    if len(s) < 30:
        return float("nan")
    last = float(s.iloc[-1])
    return float((s <= last).sum() / len(s) * 100.0)


def check_market_regime(use_cache: bool = True) -> RegimeReport:
    """Single-shot gate. Returns overall_ok=False with a reason string
    when any market-wide check fails."""
    if use_cache and "regime" in _market_cache:
        return _market_cache["regime"]

    # SPY daily — check trend
    spy_df = _fetch_daily("SPY", period="2y")
    if spy_df is None:
        rep = RegimeReport(False, float("nan"), False, False, False, False,
                           "SPY data unavailable")
        return rep
    spy_close = spy_df["close"]
    spy_ema21 = ema(spy_close, 21)
    spy_bull = bool(spy_close.iloc[-1] > spy_ema21.iloc[-1])

    # VIX percentile (lower better)
    vix_rank = float("nan")
    vix_ok = True
    try:
        vix_df = _fetch_daily("^VIX", period="2y")
        if vix_df is not None and len(vix_df) >= 60:
            vix_rank = _percent_rank(vix_df["close"], 252)
            vix_ok = vix_rank < DEFAULT_VIX_RANK_BLOCK
    except Exception:
        pass

    # HY credit spread proxies — HYG vs LQD and HYG vs TLT
    credit_ok = True
    try:
        hyg = _fetch_daily("HYG", period="6mo")
        lqd = _fetch_daily("LQD", period="6mo")
        tlt = _fetch_daily("TLT", period="6mo")
        if hyg is not None and lqd is not None and tlt is not None:
            cr = hyg["close"].iloc[-1] / max(lqd["close"].iloc[-1], 1e-6)
            cr_ema = ema(hyg["close"] / lqd["close"].clip(lower=1e-6), 20).iloc[-1]
            ht = hyg["close"].iloc[-1] / max(tlt["close"].iloc[-1], 1e-6)
            ht_ema = ema(hyg["close"] / tlt["close"].clip(lower=1e-6), 20).iloc[-1]
            credit_ok = bool(cr >= cr_ema and ht >= ht_ema)
    except Exception:
        pass

    # Breadth proxy: SPY vs its 200-day MA. If SPY < 200d MA, treat as
    # weak breadth. Real MMTH index isn't on yfinance.
    breadth_ok = True
    try:
        spy_sma200 = sma(spy_close, 200)
        if not np.isnan(spy_sma200.iloc[-1]):
            breadth_ok = bool(spy_close.iloc[-1] >= spy_sma200.iloc[-1] * 0.97)
    except Exception:
        pass

    overall = spy_bull and vix_ok and credit_ok and breadth_ok
    reasons = []
    if not spy_bull:
        reasons.append("SPY bear")
    if not vix_ok:
        reasons.append(f"VIX rank {vix_rank:.0f}>=85")
    if not credit_ok:
        reasons.append("HY credit stress")
    if not breadth_ok:
        reasons.append("breadth weak")
    reason = "all checks passed" if overall else "; ".join(reasons)

    rep = RegimeReport(spy_bull, vix_rank, vix_ok, credit_ok, breadth_ok, overall, reason)
    if use_cache:
        _market_cache["regime"] = rep
        _market_cache["spy_close"] = spy_close
    return rep


# ---------------------------------------------------------------------------
# Stage 2 — per-symbol weighted score + safety filters
# ---------------------------------------------------------------------------

@dataclass
class SymbolScore:
    symbol: str
    score: float
    passes: bool
    reason: str
    breakdown: dict

    def as_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "score": round(self.score, 1),
            "passes": self.passes,
            "reason": self.reason,
            "breakdown": self.breakdown,
        }


def _rs_tier(rs_excess_pct: float) -> int:
    """Tiered RS scoring matching the Pine f_rsTier function."""
    v = rs_excess_pct
    return 20 if v >= 4.0 else 15 if v >= 2.5 else 10 if v >= 1.5 else 5 if v >= 0.5 else 0


def score_symbol(
    symbol: str,
    min_score: float = DEFAULT_MIN_SCORE,
    min_adv_m: float = DEFAULT_MIN_ADV_M,
    min_price: float = DEFAULT_MIN_PRICE,
    max_extension_pct: float = DEFAULT_MAX_EXTENSION_PCT,
    earnings_blackout_days: int = DEFAULT_EARNINGS_BLACKOUT_DAYS,
    block_on_no_earnings: bool = True,
) -> SymbolScore:
    """Run the per-symbol USX V4 qualifier. Returns SymbolScore with
    `passes=True` only when ALL safety filters AND score >= min_score
    are satisfied."""
    df = _fetch_daily(symbol, period="2y")
    if df is None:
        return SymbolScore(symbol, 0.0, False, "no data", {})

    close = df["close"]
    high = df["high"]
    low = df["low"]
    open_ = df["open"]
    volume = df["volume"]
    last_close = float(close.iloc[-1])

    # --- Liquidity gate ---
    adv20 = float((close * volume).rolling(20).mean().iloc[-1]) / 1e6
    if adv20 < min_adv_m:
        return SymbolScore(symbol, 0.0, False, f"ADV ${adv20:.1f}M<${min_adv_m:.0f}M", {})
    if last_close < min_price:
        return SymbolScore(symbol, 0.0, False, f"price ${last_close:.2f}<${min_price:.2f}", {})

    # --- Extension filter ---
    ema200 = ema(close, 200).iloc[-1]
    ext_pct = float((last_close - ema200) / ema200 * 100) if ema200 > 0 else 0.0
    if abs(ext_pct) > max_extension_pct:
        return SymbolScore(symbol, 0.0, False, f"extended {ext_pct:+.1f}% from EMA200", {})

    # --- Earnings blackout ---
    days_to_earn = _next_earnings_days(symbol)
    if days_to_earn is None and block_on_no_earnings:
        # Fail-safe: missing data → block
        return SymbolScore(symbol, 0.0, False, "earnings data missing", {})
    if days_to_earn is not None and days_to_earn <= earnings_blackout_days:
        return SymbolScore(symbol, 0.0, False, f"earnings in {days_to_earn}d", {})

    # --- Daily trend gate (also a 20-pt scoring component) ---
    ema21_d = ema(close, 21).iloc[-1]
    ema50_d = ema(close, 50).iloc[-1]
    d_bull = bool(last_close > ema21_d > ema50_d)

    # --- Regime (must be re-checked here in case caller bypassed Stage 1) ---
    spy_close = _market_cache.get("spy_close")
    if spy_close is None:
        check_market_regime(use_cache=True)
        spy_close = _market_cache.get("spy_close")
    spy_ok = bool(spy_close is not None and spy_close.iloc[-1] > ema(spy_close, 21).iloc[-1])
    vix_rank = _market_cache.get("regime").vix_rank if "regime" in _market_cache else float("nan")
    vix_ok = bool(np.isnan(vix_rank) or vix_rank < DEFAULT_VIX_RANK_BLOCK)

    # --- Indicator series for weighting ---
    macd_line, _macd_sig, macd_hist = macd(close)
    macd_hist_prev = float(macd_hist.iloc[-2]) if len(macd_hist) >= 2 else 0.0
    macd_hist_p2 = float(macd_hist.iloc[-3]) if len(macd_hist) >= 3 else 0.0

    rsi_series = rsi(close, 14)
    rsi_prev = float(rsi_series.iloc[-2]) if len(rsi_series) >= 2 else 50.0

    adx_series, dip, dim = adx(df, 14)
    adx_prev = float(adx_series.iloc[-2]) if len(adx_series) >= 2 else 0.0
    dip_prev = float(dip.iloc[-2]) if len(dip) >= 2 else 0.0
    dim_prev = float(dim.iloc[-2]) if len(dim) >= 2 else 0.0

    # RS vs SPY: 5-bar return excess
    rs_pct = 0.0
    rs_pos = False
    if spy_close is not None and len(close) >= 6 and len(spy_close) >= 6:
        try:
            stock_roc = float(close.iloc[-1] / close.iloc[-6] - 1)
            spy_roc = float(spy_close.iloc[-1] / spy_close.iloc[-6] - 1)
            rs_pct = (stock_roc - spy_roc) * 100.0
            rs_pos = stock_roc > spy_roc
        except Exception:
            pass

    # Volume ratio (prior bar)
    vol_ma20 = volume.rolling(20).mean()
    v_prev = float(volume.iloc[-2]) if len(volume) >= 2 else 0.0
    vma_prev = float(vol_ma20.iloc[-2]) if len(vol_ma20) >= 2 else 1.0
    vr_prev = (v_prev / vma_prev) if vma_prev > 0 else 1.0

    # Gap (today's open vs prev close)
    gap_pct = 0.0
    if len(open_) >= 1 and len(close) >= 2:
        prev_c = float(close.iloc[-2])
        if prev_c > 0:
            gap_pct = float((float(open_.iloc[-1]) - prev_c) / prev_c * 100.0)

    # BB squeeze (current vs 20-bar mean of bandwidth)
    upper, mid, lower = bollinger_bands(close, 20, 2)
    bb_width = ((upper - lower) / mid * 100).fillna(0)
    bb_sqz = bool(bb_width.iloc[-2] < bb_width.rolling(20).mean().iloc[-2]) if len(bb_width) >= 22 else False

    # VWAP
    vwap_series = vwap(df)
    above_vwap = bool(close.iloc[-2] > vwap_series.iloc[-2]) if len(vwap_series) >= 2 else False

    # ---- Score components (long-only, mirrors Pine) ----
    w_d_trend = 20 if d_bull else 0
    w_reg = (10 if spy_ok else 0) + (5 if vix_ok else 0)
    w_macd = 10 if (macd_hist_prev > 0 and macd_hist_prev > macd_hist_p2) else 0
    w_rsi = 8 if 45 <= rsi_prev <= 65 else 0
    w_adx = 7 if (adx_prev >= 20 and dip_prev > dim_prev) else 0
    w_rs = _rs_tier(rs_pct) if rs_pos else 0
    w_vol = 6 if vr_prev >= 1.2 else 3 if vr_prev >= 1.0 else 0
    w_gap = 4 if (0 <= gap_pct <= 1.5) else 0
    w_sqz = 5 if bb_sqz else 0
    w_vwap = 5 if above_vwap else 0

    total = w_d_trend + w_reg + w_macd + w_rsi + w_adx + w_rs + w_vol + w_gap + w_sqz + w_vwap

    breakdown = {
        "daily_trend": w_d_trend,
        "regime": w_reg,
        "macd": w_macd,
        "rsi": w_rsi,
        "adx": w_adx,
        "rs_vs_spy": w_rs,
        "volume": w_vol,
        "gap": w_gap,
        "bb_squeeze": w_sqz,
        "vwap": w_vwap,
        "rs_pct": round(rs_pct, 2),
        "ext_pct": round(ext_pct, 2),
        "adv_m": round(adv20, 1),
        "days_to_earn": days_to_earn,
        "price": last_close,
    }

    if not d_bull:
        return SymbolScore(symbol, total, False,
                           "daily not in uptrend (price>EMA21>EMA50)", breakdown)
    if total < min_score:
        return SymbolScore(symbol, total, False,
                           f"score {total:.0f}<{min_score:.0f}", breakdown)

    return SymbolScore(symbol, total, True, "passed", breakdown)


# ---------------------------------------------------------------------------
# Universe filter — orchestration
# ---------------------------------------------------------------------------

def filter_universe(
    symbols: list[str],
    min_score: float = DEFAULT_MIN_SCORE,
    max_workers: int = 8,
    skip_regime_check: bool = False,
) -> tuple[list[SymbolScore], RegimeReport]:
    """Two-stage funnel.

    Returns (passing_scores_sorted_high_to_low, regime_report).
    When the regime gate is closed, returns ([], regime_report).
    """
    _market_cache.clear()

    regime = check_market_regime(use_cache=True)
    if not skip_regime_check and not regime.overall_ok:
        logger.info(
            "USX V4: regime gate closed (%s) — skipping universe scan", regime.reason
        )
        return [], regime

    out: list[SymbolScore] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(score_symbol, s, min_score): s for s in symbols}
        for fut in as_completed(futures):
            try:
                sc = fut.result()
            except Exception as exc:  # noqa: BLE001
                logger.debug("usx_filter: %s failed: %s", futures[fut], exc)
                continue
            if sc.passes:
                out.append(sc)
    out.sort(key=lambda s: s.score, reverse=True)
    logger.info(
        "USX V4: regime=ok scanned=%d passed=%d top=%s",
        len(symbols), len(out),
        ",".join(f"{s.symbol}({s.score:.0f})" for s in out[:5]),
    )
    return out, regime


__all__ = [
    "RegimeReport",
    "SymbolScore",
    "check_market_regime",
    "score_symbol",
    "filter_universe",
]
