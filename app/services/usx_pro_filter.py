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
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np
import pandas as pd

from app.services.technical import adx, atr, bollinger_bands, ema, macd, rsi, sma, vwap

logger = logging.getLogger("screener")

# ── Near-miss cache (thread-safe) ──────────────────────────────────────────
# Collected during filter_universe() and exposed via GET /api/screener/near-miss.
# Symbols that scored 60-64 or passed every gate except the daily trend gate.
_nm_cache: list = []
_nm_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Defaults (mirror the Pine input defaults)
# ---------------------------------------------------------------------------

# NOTE: defaults relaxed from the Pine settings because Pine ran on a
# single 1H chart; here we are universe-scanning for "close enough"
# rather than gating one specific position. Strict Pine defaults
# eliminated 100% of S&P 500 candidates on rising-market days.
DEFAULT_MIN_SCORE = 50.0           # was 65.0 — Pine score on daily TF
                                   # rarely clears 65 on broad scans
DEFAULT_MIN_ADV_M = 25.0           # was 50; many real winners ran below
DEFAULT_MIN_PRICE = 5.0            # was 10; lots of $5-10 names trade fine
DEFAULT_MAX_EXTENSION_PCT = 25.0   # was 15; momentum names overshoot
DEFAULT_VIX_RANK_BLOCK = 90.0      # was 85; too many false closes
DEFAULT_EARNINGS_BLACKOUT_DAYS = 3  # was 5; too aggressive
DEFAULT_BLOCK_ON_NO_EARNINGS = False  # was True; yfinance earnings often
                                       # missing → fail-safe blocked
                                       # ~70% of universe

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

    # SPY daily — primary trend (this is the only HARD gate now)
    spy_df = _fetch_daily("SPY", period="2y")
    if spy_df is None:
        # Fail-OPEN, not closed: missing SPY data should not silence
        # the whole pipeline. Down-stream filters still run.
        rep = RegimeReport(True, float("nan"), True, True, True, True,
                           "SPY data unavailable — regime check skipped")
        if use_cache:
            _market_cache["regime"] = rep
        return rep
    spy_close = spy_df["close"]
    spy_ema21 = ema(spy_close, 21)
    spy_bull = bool(spy_close.iloc[-1] > spy_ema21.iloc[-1])

    # VIX percentile (relaxed: only block if extreme)
    vix_rank = float("nan")
    vix_ok = True
    try:
        vix_df = _fetch_daily("^VIX", period="2y")
        if vix_df is not None and len(vix_df) >= 60:
            vix_rank = _percent_rank(vix_df["close"], 252)
            vix_ok = vix_rank < DEFAULT_VIX_RANK_BLOCK
    except Exception:
        pass

    # HY credit — INFORMATIONAL ONLY now (was a hard gate). The
    # HYG/LQD and HYG/TLT ratios cross their EMA20 multiple times per
    # week and were causing the regime gate to oscillate closed for
    # the wrong reason.
    credit_ok = True
    try:
        hyg = _fetch_daily("HYG", period="6mo")
        lqd = _fetch_daily("LQD", period="6mo")
        if hyg is not None and lqd is not None:
            cr = hyg["close"].iloc[-1] / max(lqd["close"].iloc[-1], 1e-6)
            cr_ema = ema(hyg["close"] / lqd["close"].clip(lower=1e-6), 20).iloc[-1]
            # 1% buffer so noise doesn't flip the gate
            credit_ok = bool(cr >= cr_ema * 0.99)
    except Exception:
        pass

    # Breadth — REMOVED as a hard check. SPY trend is sufficient.
    breadth_ok = True

    # Hard gate: SPY trend ONLY. VIX & credit are reported but only
    # block at extreme readings (relaxed thresholds).
    overall = spy_bull
    reasons = []
    if not spy_bull:
        reasons.append("SPY < EMA21 (bearish daily trend)")
    if not vix_ok:
        reasons.append(f"VIX rank {vix_rank:.0f}>={DEFAULT_VIX_RANK_BLOCK:.0f} — fear extreme")
        overall = False  # extreme VIX is the only other hard close
    if not credit_ok:
        reasons.append("HY credit info: under EMA20 (informational)")
        # NOT counted in overall — informational only
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
    block_on_no_earnings: bool | None = None,
    df: Optional[pd.DataFrame] = None,
) -> SymbolScore:
    """Run the per-symbol USX V4 qualifier. Returns SymbolScore with
    `passes=True` only when ALL safety filters AND score >= min_score
    are satisfied."""
    # Resolve block_on_no_earnings: explicit arg > config > module default
    if block_on_no_earnings is None:
        try:
            from app.config import settings as _usx_cfg
            block_on_no_earnings = getattr(_usx_cfg, "BLOCK_ON_NO_EARNINGS", DEFAULT_BLOCK_ON_NO_EARNINGS)
        except Exception:
            block_on_no_earnings = DEFAULT_BLOCK_ON_NO_EARNINGS
    if df is None:
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
    if days_to_earn is None:
        if block_on_no_earnings:
            return SymbolScore(symbol, 0.0, False, "earnings data missing", {})
        # Data missing but config allows — warn, don't block
        logger.debug("usx_filter: %s earnings data missing (BLOCK_ON_NO_EARNINGS=False)", symbol)
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
        # Near-miss: trend gate only failure — collect if score is reasonable
        if total >= 60:
            with _nm_lock:
                _nm_cache.append(SymbolScore(symbol, total, False,
                    "daily not in uptrend (price>EMA21>EMA50)", dict(breakdown)))
        return SymbolScore(symbol, total, False,
                           "daily not in uptrend (price>EMA21>EMA50)", breakdown)
    if total < min_score:
        # Near-miss: score in [60, min_score) — borderline quality
        if 60 <= total < min_score:
            with _nm_lock:
                _nm_cache.append(SymbolScore(symbol, total, False,
                    f"score {total:.0f}<{min_score:.0f}", dict(breakdown)))
        return SymbolScore(symbol, total, False,
                           f"score {total:.0f}<{min_score:.0f}", breakdown)

    return SymbolScore(symbol, total, True, "passed", breakdown)


# ---------------------------------------------------------------------------
# Universe filter — orchestration
# ---------------------------------------------------------------------------

def filter_universe(
    symbols: list[str],
    min_score: float = DEFAULT_MIN_SCORE,
    max_workers: int = 3,
    skip_regime_check: bool = False,
) -> tuple[list[SymbolScore], RegimeReport]:
    """Two-stage funnel.

    Returns (passing_scores_sorted_high_to_low, regime_report).
    When the regime gate is closed, returns ([], regime_report).
    """
    _market_cache.clear()
    with _nm_lock:
        _nm_cache.clear()

    # ── Batch pre-fetch via Alpaca (10-50× faster than per-symbol yfinance) ──
    _batched: dict = {}
    try:
        from app.services.market_data import fetch_alpaca_batch as _batch
        _batched = _batch(symbols, period="2y")
        logger.info("usx_filter: batch-prefetched %d/%d symbols", len(_batched), len(symbols))
    except Exception as _be:
        logger.debug("usx_filter: batch pre-fetch unavailable (%s) — per-symbol fallback", _be)

    regime = check_market_regime(use_cache=True)
    if not skip_regime_check and not regime.overall_ok:
        logger.info(
            "USX V4: regime gate closed (%s) — skipping universe scan", regime.reason
        )
        return [], regime

    out: list[SymbolScore] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        _score_min = min_score
        futures = {
            pool.submit(score_symbol, s, _score_min, df=_batched.get(s)): s
            for s in symbols
        }
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


def get_near_misses() -> list:
    """Return the near-miss cache from the most recent filter_universe() call.

    Entries are SymbolScore instances (or compatible dicts) for symbols that:
      - scored 60-64 (below the min_score gate but borderline quality)
      - passed every gate EXCEPT the daily trend gate (price>EMA21>EMA50)

    The cache is cleared at the start of each filter_universe() call.
    """
    with _nm_lock:
        return list(_nm_cache)


__all__ = [
    "RegimeReport",
    "SymbolScore",
    "check_market_regime",
    "score_symbol",
    "filter_universe",
    "get_near_misses",
]
