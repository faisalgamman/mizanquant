"""Chart generator for Telegram signal alerts.

Renders a clean candlestick chart with overlay indicators (EMA9, EMA21,
EMA50, VWAP, BB) plus markers for the current entry / SL / TP1 / TP2 / TP3
levels. Returns a PNG bytes payload suitable for `tg_send_photo()`.

Kept light — uses matplotlib + mplfinance which are already in
requirements.txt. No external services.
"""

from __future__ import annotations

import io
import logging
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger("screener")


def render_signal_chart(
    df: pd.DataFrame,
    symbol: str,
    strategy_label: str,
    confidence: float,
    entry: float,
    stop_loss: float,
    take_profit_1: float,
    take_profit_2: float,
    take_profit_3: float,
    bars: int = 60,
) -> Optional[bytes]:
    """Render a candlestick + indicators chart for a STRONG BUY signal.

    Args:
        df: OHLCV with columns ['date','open','high','low','close','volume']
            (or DatetimeIndex). Last `bars` rows used.
        symbol: ticker for title
        strategy_label: human-readable strategy name
        confidence: 0-100 score
        entry, stop_loss, tp_*: levels to draw as horizontal lines

    Returns PNG bytes, or None on failure.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
    except Exception as exc:
        logger.warning("chart: matplotlib unavailable: %s", exc)
        return None

    if df is None or len(df) < 20:
        return None

    # Normalize index
    sub = df.tail(bars).copy()
    if "date" in sub.columns:
        sub["date"] = pd.to_datetime(sub["date"])
        sub = sub.set_index("date")
    elif not isinstance(sub.index, pd.DatetimeIndex):
        try:
            sub.index = pd.to_datetime(sub.index)
        except Exception:
            sub.index = pd.RangeIndex(len(sub))

    closes = sub["close"].astype(float)
    highs = sub["high"].astype(float)
    lows = sub["low"].astype(float)
    opens = sub["open"].astype(float)
    vols = sub["volume"].astype(float)

    # Indicators
    ema9 = closes.ewm(span=9, adjust=False).mean()
    ema21 = closes.ewm(span=21, adjust=False).mean()
    ema50 = closes.ewm(span=50, adjust=False).mean()

    # Bollinger Bands (20, 2)
    sma20 = closes.rolling(20).mean()
    std20 = closes.rolling(20).std()
    bb_upper = sma20 + 2 * std20
    bb_lower = sma20 - 2 * std20

    # Build figure
    fig, (ax_price, ax_vol) = plt.subplots(
        2, 1, figsize=(11, 7),
        gridspec_kw={"height_ratios": [3, 1], "hspace": 0.05},
        facecolor="#0a0a0a",
    )

    for ax in (ax_price, ax_vol):
        ax.set_facecolor("#0a0a0a")
        for spine in ax.spines.values():
            spine.set_color("#333333")
        ax.tick_params(colors="#888888", labelsize=8)
        ax.grid(True, color="#222222", linewidth=0.4)

    # Candlesticks (manual draw to avoid mplfinance dependency on Railway)
    width = 0.6
    x_idx = np.arange(len(sub))
    for i, (o, h, l, c) in enumerate(zip(opens, highs, lows, closes)):
        col = "#26a69a" if c >= o else "#ef5350"
        ax_price.vlines(i, l, h, color=col, linewidth=0.8)
        ax_price.add_patch(plt.Rectangle(
            (i - width / 2, min(o, c)),
            width, max(abs(c - o), 1e-9),
            color=col, alpha=0.85,
        ))

    # Overlays
    ax_price.plot(x_idx, ema9.values, color="#42a5f5", linewidth=1.2, label="EMA 9", alpha=0.9)
    ax_price.plot(x_idx, ema21.values, color="#ffa726", linewidth=1.4, label="EMA 21", alpha=0.95)
    ax_price.plot(x_idx, ema50.values, color="#ab47bc", linewidth=1.0, label="EMA 50", alpha=0.85)
    ax_price.plot(x_idx, bb_upper.values, color="#666666", linewidth=0.7, linestyle="--", alpha=0.6)
    ax_price.plot(x_idx, bb_lower.values, color="#666666", linewidth=0.7, linestyle="--", alpha=0.6)

    # Levels
    ax_price.axhline(entry, color="#ffffff", linewidth=1.6, linestyle="-", alpha=0.95, label=f"Entry ${entry:.2f}")
    ax_price.axhline(stop_loss, color="#ef5350", linewidth=1.4, linestyle="--", alpha=0.9, label=f"SL ${stop_loss:.2f}")
    ax_price.axhline(take_profit_1, color="#66bb6a", linewidth=1.2, linestyle="--", alpha=0.85, label=f"TP1 ${take_profit_1:.2f}")
    ax_price.axhline(take_profit_2, color="#26c6da", linewidth=1.2, linestyle="--", alpha=0.85, label=f"TP2 ${take_profit_2:.2f}")
    ax_price.axhline(take_profit_3, color="#ba68c8", linewidth=1.2, linestyle="--", alpha=0.85, label=f"TP3 ${take_profit_3:.2f}")

    ax_price.set_title(
        f"{symbol}  •  {strategy_label}  •  STRONG BUY  •  conf {confidence:.0f}%",
        color="#ffd54f", fontsize=12, fontweight="bold", pad=12,
    )
    ax_price.legend(
        loc="upper left", fontsize=7, framealpha=0.6, facecolor="#1a1a2e",
        edgecolor="#333333", labelcolor="#dddddd", ncol=2,
    )
    ax_price.set_xticklabels([])

    # Volume
    vol_colors = ["#26a69a" if c >= o else "#ef5350" for c, o in zip(closes, opens)]
    ax_vol.bar(x_idx, vols, color=vol_colors, width=width, alpha=0.7)
    ax_vol.set_ylabel("Vol", color="#888888", fontsize=8)
    ax_vol.set_xlim(ax_price.get_xlim())

    # X-axis labels (sparse)
    if isinstance(sub.index, pd.DatetimeIndex):
        n = len(sub)
        ticks = list(range(0, n, max(1, n // 6)))
        labels = [sub.index[i].strftime("%m-%d") for i in ticks]
        ax_vol.set_xticks(ticks)
        ax_vol.set_xticklabels(labels)

    fig.tight_layout()

    buf = io.BytesIO()
    try:
        fig.savefig(buf, format="png", dpi=110, facecolor=fig.get_facecolor(), bbox_inches="tight")
        return buf.getvalue()
    except Exception as exc:
        logger.warning("chart: savefig failed: %s", exc)
        return None
    finally:
        plt.close(fig)


__all__ = ["render_signal_chart"]
