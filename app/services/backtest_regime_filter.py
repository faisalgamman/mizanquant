"""Backtest Regime Filter — classify and filter trades by market regime.

Each trade is tagged with the macro regime (BULL/BEAR/NEUTRAL) at entry
based on SPY price relative to its 200-day EMA. This allows:
1. Filtering out trades from unwanted regimes
2. Per-regime performance reporting
3. Avoiding overfitting to a single regime
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger("screener")

_EMA_PERIOD = 200
_NEUTRAL_BAND_PCT = 2.0  # ±2% around EMA200 is considered NEUTRAL


def _fetch_spy_since(days: int = 400) -> pd.DataFrame | None:
    """Fetch SPY daily close data going back `days`."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=max(days, 400))
    try:
        import yfinance as yf
        ticker = yf.Ticker("SPY")
        df = ticker.history(start=start.strftime("%Y-%m-%d"),
                            end=end.strftime("%Y-%m-%d"))
        if df is not None and len(df) > _EMA_PERIOD:
            df.columns = [c.lower() for c in df.columns]
            return df
    except Exception as exc:
        logger.debug("yfinance SPY fetch failed: %s", exc)
    try:
        from app.services.market_data import fetch as fetch_market_data
        df = fetch_market_data("SPY", period=f"{max(days, 400)}d")
        if df is not None and len(df) > _EMA_PERIOD:
            return df
    except Exception as exc:
        logger.debug("Failed to fetch SPY for regime filter: %s", exc)
    return None


def _ema(series: pd.Series, period: int = _EMA_PERIOD) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def classify_regime(
    entry_date: datetime | str,
    spy_df: pd.DataFrame | None = None,
) -> str:
    """Classify the market regime at a given entry date.

    Uses SPY's position relative to its 200-day EMA:
      - SPY > EMA200 + 2% → BULL
      - SPY < EMA200 - 2% → BEAR
      - Otherwise → NEUTRAL

    Args:
        entry_date: The date of the trade entry (ISO string or datetime).
        spy_df: Optional pre-fetched SPY DataFrame (must have a "close"
            column and a DatetimeIndex covering ``entry_date``).  When
            supplied the function uses *point-in-time* data from the
            backtest window itself — faster and correct for historical
            trades older than 400 days.  When ``None`` the function
            fetches the last 400 days from today (legacy behaviour, only
            suitable for recent/live trades).

    Returns "UNKNOWN" if SPY data is unavailable.
    """
    if isinstance(entry_date, str):
        # Handle both "2024-03-15" and full ISO timestamps
        try:
            entry_date = datetime.fromisoformat(entry_date.replace("Z", "+00:00"))
        except ValueError:
            entry_date = datetime.fromisoformat(entry_date[:10])
    if entry_date.tzinfo is None:
        entry_date = entry_date.replace(tzinfo=timezone.utc)

    df = spy_df if spy_df is not None else _fetch_spy_since(days=400)
    if df is None:
        return "UNKNOWN"

    closes = df["close"]
    ema200 = _ema(closes)

    # Find the closest trading day on or before entry_date
    entry_dt = entry_date.replace(tzinfo=None) if entry_date.tzinfo else entry_date
    entry_ts = pd.Timestamp(entry_dt)
    idx = closes.index
    if isinstance(idx, pd.DatetimeIndex) and idx.tz is not None:
        mask = idx.tz_localize(None) <= entry_ts
    else:
        mask = idx <= entry_ts
    if not mask.any():
        return "UNKNOWN"

    last_pos = int(np.where(mask)[0][-1])
    spy_price = float(closes.iloc[last_pos])
    spy_ema = float(ema200.iloc[last_pos])
    if spy_ema <= 0:
        return "UNKNOWN"

    pct_from_ema = (spy_price - spy_ema) / spy_ema * 100.0

    if pct_from_ema > _NEUTRAL_BAND_PCT:
        return "BULL"
    elif pct_from_ema < -_NEUTRAL_BAND_PCT:
        return "BEAR"
    return "NEUTRAL"


def tag_trades(
    trades: list[dict[str, Any]],
    spy_df: pd.DataFrame | None = None,
) -> list[dict[str, Any]]:
    """Tag each trade dict with 'regime_at_entry'.

    Accepts dicts that use either ``'entry_date'`` (lower-case, from
    strategies/backtest.py) or ``'Entry Date'`` (Title Case, from
    backtest_service.py / halal_screener.py).  Modifies and returns the
    same list.

    Args:
        trades:  List of trade dicts.
        spy_df:  Optional pre-fetched SPY DataFrame forwarded to
            ``classify_regime`` for point-in-time accuracy.
    """
    for trade in trades:
        entry = trade.get("entry_date") or trade.get("Entry Date")
        if entry:
            trade["regime_at_entry"] = classify_regime(entry, spy_df=spy_df)
        else:
            trade["regime_at_entry"] = "UNKNOWN"
    return trades


def filter_trades(
    trades: list[dict[str, Any]],
    allowed_regimes: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Filter trades to only those in allowed regimes.

    Args:
        trades: List of trade dicts with 'regime_at_entry'.
        allowed_regimes: List like ['BULL', 'NEUTRAL']. Defaults to no
            filtering (all regimes allowed except UNKNOWN).

    Returns:
        Filtered list of trades.
    """
    if allowed_regimes is None:
        allowed_regimes = ["BULL", "NEUTRAL", "BEAR"]
    return [t for t in trades if t.get("regime_at_entry") in allowed_regimes]


def compute_per_regime_breakdown(trades: list[dict[str, Any]]) -> dict[str, dict]:
    """Group trades by entry regime and compute basic metrics per regime.

    Each trade dict should have:
      - 'regime_at_entry': str (BULL/BEAR/NEUTRAL/UNKNOWN)
      - 'return_pct': float (trade return percentage)
      - Other fields are optional

    Returns:
        {
            "BULL": {"trades": N, "win_rate": %, "avg_return": %,
                     "total_return": %, "profit_factor": X},
            "NEUTRAL": {...},
            "BEAR": {...},
            "all": {...},
        }
    """
    regimes = {"BULL": [], "NEUTRAL": [], "BEAR": [], "UNKNOWN": []}
    for trade in trades:
        reg = trade.get("regime_at_entry", "UNKNOWN")
        regimes.setdefault(reg, []).append(trade)

    result = {}
    all_returns = []
    for reg, reg_trades in regimes.items():
        if not reg_trades:
            result[reg] = {"trades": 0, "win_rate": 0.0, "avg_return": 0.0,
                           "total_return": 0.0, "profit_factor": 0.0}
            continue
        returns = [float(t.get("return_pct", 0)) for t in reg_trades]
        all_returns.extend(returns)
        wins = [r for r in returns if r > 0]
        losses = [r for r in returns if r < 0]
        gross_win = sum(wins) if wins else 0
        gross_loss = abs(sum(losses)) if losses else 1
        result[reg] = {
            "trades": len(reg_trades),
            "win_rate": round(len(wins) / len(returns) * 100, 1) if returns else 0.0,
            "avg_return": round(np.mean(returns), 2) if returns else 0.0,
            "total_return": round(sum(returns), 2),
            "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else 0.0,
        }

    if all_returns:
        wins_all = [r for r in all_returns if r > 0]
        losses_all = [r for r in all_returns if r < 0]
        gwin = sum(wins_all) if wins_all else 0
        gloss = abs(sum(losses_all)) if losses_all else 1
        result["all"] = {
            "trades": len(all_returns),
            "win_rate": round(len(wins_all) / len(all_returns) * 100, 1),
            "avg_return": round(np.mean(all_returns), 2),
            "total_return": round(sum(all_returns), 2),
            "profit_factor": round(gwin / gloss, 2) if gloss > 0 else 0.0,
        }
    else:
        result["all"] = {"trades": 0, "win_rate": 0.0, "avg_return": 0.0,
                         "total_return": 0.0, "profit_factor": 0.0}

    return result


__all__ = [
    "classify_regime",
    "tag_trades",
    "filter_trades",
    "compute_per_regime_breakdown",
]
