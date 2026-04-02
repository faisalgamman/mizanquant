"""Trading chart generator for Telegram alerts.

Generates professional candlestick charts with:
- Buy/Sell arrow markers
- Entry price line (blue dashed)
- Stop-loss line (red dashed)
- Take-profit lines (green dashed)
- EMA 21 overlay
- Volume bars
- RSI subplot

Output: PNG bytes (in-memory, no file disk IO).
"""

import io
import logging
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger("screener")

# Use non-interactive backend (no GUI needed on server)
import matplotlib
matplotlib.use("Agg")
# Suppress font warnings on Railway (no display, limited fonts)
import warnings
warnings.filterwarnings("ignore", category=UserWarning, module="matplotlib")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import FancyArrowPatch

logger.info("matplotlib loaded successfully (Agg backend)")


def generate_signal_chart(
    df: pd.DataFrame,
    symbol: str,
    verdict: str,
    entry_price: float,
    stop_loss: float,
    tp1: float,
    tp2: float = None,
    tp3: float = None,
    confidence: float = 0,
    votes_buy: int = 0,
    votes_sell: int = 0,
    votes_hold: int = 0,
    days_back: int = 60,
) -> Optional[bytes]:
    """Generate a candlestick signal chart and return PNG bytes.

    Args:
        df: OHLCV DataFrame with columns [date, open, high, low, close, volume]
        symbol: Stock symbol
        verdict: "STRONG BUY", "STRONG SELL", etc.
        entry_price: Current/entry price
        stop_loss: Stop-loss level
        tp1: Take-profit level 1
        tp2: Take-profit level 2 (optional)
        tp3: Take-profit level 3 (optional)
        confidence: Confidence percentage
        days_back: How many days of data to show

    Returns:
        PNG image as bytes, or None on error.
    """
    try:
        # Prepare data — last N days
        chart_df = df.tail(days_back).copy()
        if len(chart_df) < 10:
            return None

        chart_df = chart_df.reset_index(drop=True)
        dates = pd.to_datetime(chart_df["date"])
        opens = chart_df["open"].values
        highs = chart_df["high"].values
        lows = chart_df["low"].values
        closes = chart_df["close"].values
        volumes = chart_df["volume"].values

        # Calculate EMA 21
        ema21 = pd.Series(closes).ewm(span=21, adjust=False).mean().values

        # Calculate RSI 14
        delta = pd.Series(closes).diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rs = gain / loss
        rsi_vals = (100 - (100 / (1 + rs))).values

        # --- Create figure ---
        is_buy = "BUY" in verdict
        main_color = "#00C853" if is_buy else "#FF1744"  # green or red
        bg_color = "#1a1a2e"
        grid_color = "#2a2a4a"
        text_color = "#e0e0e0"

        fig = plt.figure(figsize=(10, 7), facecolor=bg_color)

        # Layout: candlestick (top 60%), volume (20%), RSI (20%)
        ax_candle = fig.add_axes([0.08, 0.38, 0.88, 0.55], facecolor=bg_color)
        ax_volume = fig.add_axes([0.08, 0.22, 0.88, 0.15], facecolor=bg_color, sharex=ax_candle)
        ax_rsi = fig.add_axes([0.08, 0.05, 0.88, 0.16], facecolor=bg_color, sharex=ax_candle)

        x = np.arange(len(chart_df))

        # --- Candlesticks ---
        for i in range(len(chart_df)):
            color = "#00C853" if closes[i] >= opens[i] else "#FF1744"
            # Body
            body_bottom = min(opens[i], closes[i])
            body_height = abs(closes[i] - opens[i])
            ax_candle.bar(x[i], body_height, bottom=body_bottom, width=0.6,
                         color=color, edgecolor=color, linewidth=0.5)
            # Wicks
            ax_candle.vlines(x[i], lows[i], highs[i], color=color, linewidth=0.5)

        # EMA 21 line
        ax_candle.plot(x, ema21, color="#FFD700", linewidth=1.2, alpha=0.8, label="EMA 21")

        # --- Signal arrow ---
        arrow_x = x[-1]
        arrow_y = closes[-1]
        if is_buy:
            ax_candle.annotate(
                "", xy=(arrow_x, arrow_y * 0.99),
                xytext=(arrow_x, arrow_y * 0.96),
                arrowprops=dict(arrowstyle="-|>", color="#00E676", lw=3),
            )
            ax_candle.scatter([arrow_x], [arrow_y * 0.955], marker="^",
                            s=200, color="#00E676", zorder=10)
        else:
            ax_candle.annotate(
                "", xy=(arrow_x, arrow_y * 1.01),
                xytext=(arrow_x, arrow_y * 1.04),
                arrowprops=dict(arrowstyle="-|>", color="#FF1744", lw=3),
            )
            ax_candle.scatter([arrow_x], [arrow_y * 1.045], marker="v",
                            s=200, color="#FF1744", zorder=10)

        # --- Entry, SL, TP lines ---
        line_start = max(0, len(x) - 20)

        # Entry price
        ax_candle.hlines(entry_price, line_start, x[-1] + 3,
                        colors="#42A5F5", linestyles="dashed", linewidth=1.5, alpha=0.9)
        ax_candle.text(x[-1] + 3.5, entry_price, f"Entry ${entry_price:.2f}",
                      color="#42A5F5", fontsize=8, va="center", fontweight="bold")

        # Stop Loss
        ax_candle.hlines(stop_loss, line_start, x[-1] + 3,
                        colors="#FF1744", linestyles="dashed", linewidth=1.5, alpha=0.9)
        ax_candle.text(x[-1] + 3.5, stop_loss, f"SL ${stop_loss:.2f}",
                      color="#FF1744", fontsize=8, va="center", fontweight="bold")

        # Take Profit 1
        ax_candle.hlines(tp1, line_start, x[-1] + 3,
                        colors="#00E676", linestyles="dashed", linewidth=1.5, alpha=0.9)
        ax_candle.text(x[-1] + 3.5, tp1, f"TP1 ${tp1:.2f}",
                      color="#00E676", fontsize=8, va="center", fontweight="bold")

        # TP2 and TP3
        if tp2:
            ax_candle.hlines(tp2, line_start, x[-1] + 3,
                            colors="#69F0AE", linestyles="dotted", linewidth=1.0, alpha=0.7)
            ax_candle.text(x[-1] + 3.5, tp2, f"TP2 ${tp2:.2f}",
                          color="#69F0AE", fontsize=7, va="center")
        if tp3:
            ax_candle.hlines(tp3, line_start, x[-1] + 3,
                            colors="#B9F6CA", linestyles="dotted", linewidth=1.0, alpha=0.6)
            ax_candle.text(x[-1] + 3.5, tp3, f"TP3 ${tp3:.2f}",
                          color="#B9F6CA", fontsize=7, va="center")

        # --- Risk/Reward zone shading ---
        if is_buy:
            # Green zone: entry → TP1
            ax_candle.axhspan(entry_price, tp1, alpha=0.06, color="#00E676")
            # Red zone: SL → entry
            ax_candle.axhspan(stop_loss, entry_price, alpha=0.06, color="#FF1744")

        # --- Title ---
        arrow_icon = "BUY" if is_buy else "SELL"
        risk_reward = abs(tp1 - entry_price) / abs(entry_price - stop_loss) if abs(entry_price - stop_loss) > 0 else 0
        title = (
            f"{symbol}  |  {verdict}  |  Confidence: {confidence:.0f}%  |  "
            f"R:R = 1:{risk_reward:.1f}"
        )
        ax_candle.set_title(title, color=main_color, fontsize=13, fontweight="bold", pad=12)

        # Candle axes styling
        ax_candle.set_ylabel("Price ($)", color=text_color, fontsize=9)
        ax_candle.tick_params(colors=text_color, labelsize=8)
        ax_candle.grid(True, alpha=0.15, color=grid_color)
        ax_candle.spines["top"].set_visible(False)
        ax_candle.spines["right"].set_visible(False)
        ax_candle.spines["bottom"].set_color(grid_color)
        ax_candle.spines["left"].set_color(grid_color)
        ax_candle.legend(loc="upper left", fontsize=8, facecolor=bg_color,
                        edgecolor=grid_color, labelcolor=text_color)

        # X-axis tick labels (dates) — show every 5th date
        tick_positions = x[::5]
        tick_labels = [dates.iloc[i].strftime("%m/%d") if i < len(dates) else "" for i in tick_positions]
        ax_candle.set_xticks(tick_positions)
        ax_candle.set_xticklabels([])  # hide on top chart

        # Add padding on right for labels
        ax_candle.set_xlim(-1, x[-1] + 12)

        # --- Volume bars ---
        vol_colors = ["#00C853" if closes[i] >= opens[i] else "#FF1744" for i in range(len(chart_df))]
        ax_volume.bar(x, volumes, width=0.6, color=vol_colors, alpha=0.6)
        ax_volume.set_ylabel("Vol", color=text_color, fontsize=8)
        ax_volume.tick_params(colors=text_color, labelsize=7)
        ax_volume.grid(True, alpha=0.1, color=grid_color)
        ax_volume.spines["top"].set_visible(False)
        ax_volume.spines["right"].set_visible(False)
        ax_volume.spines["bottom"].set_color(grid_color)
        ax_volume.spines["left"].set_color(grid_color)
        ax_volume.set_xticklabels([])
        ax_volume.set_xlim(-1, x[-1] + 12)

        # --- RSI ---
        ax_rsi.plot(x, rsi_vals, color="#AB47BC", linewidth=1.2)
        ax_rsi.axhline(70, color="#FF1744", linewidth=0.8, linestyle="--", alpha=0.5)
        ax_rsi.axhline(30, color="#00E676", linewidth=0.8, linestyle="--", alpha=0.5)
        ax_rsi.axhspan(30, 70, alpha=0.04, color="#AB47BC")
        ax_rsi.fill_between(x, rsi_vals, 70, where=(rsi_vals > 70),
                           alpha=0.2, color="#FF1744")
        ax_rsi.fill_between(x, rsi_vals, 30, where=(rsi_vals < 30),
                           alpha=0.2, color="#00E676")
        ax_rsi.set_ylabel("RSI", color=text_color, fontsize=8)
        ax_rsi.set_ylim(0, 100)
        ax_rsi.tick_params(colors=text_color, labelsize=7)
        ax_rsi.grid(True, alpha=0.1, color=grid_color)
        ax_rsi.spines["top"].set_visible(False)
        ax_rsi.spines["right"].set_visible(False)
        ax_rsi.spines["bottom"].set_color(grid_color)
        ax_rsi.spines["left"].set_color(grid_color)
        ax_rsi.set_xlim(-1, x[-1] + 12)

        # X-axis dates on RSI (bottom)
        ax_rsi.set_xticks(tick_positions)
        ax_rsi.set_xticklabels(tick_labels, color=text_color, fontsize=7, rotation=45)

        # --- Votes info box ---
        info_text = (
            f"Votes: BUY {votes_buy} | SELL {votes_sell} | HOLD {votes_hold}\n"
            f"Entry: ${entry_price:.2f}  |  SL: ${stop_loss:.2f}  |  TP1: ${tp1:.2f}"
        )
        ax_candle.text(
            0.02, 0.02, info_text, transform=ax_candle.transAxes,
            fontsize=8, color=text_color, alpha=0.8, va="bottom",
            bbox=dict(boxstyle="round,pad=0.4", facecolor=bg_color,
                     edgecolor=grid_color, alpha=0.9),
        )

        # --- Watermark ---
        fig.text(0.98, 0.01, "Halal Trading Bot",
                color=text_color, alpha=0.3, fontsize=7, ha="right")

        # --- Export to PNG bytes ---
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=100, bbox_inches="tight",
                   facecolor=bg_color, edgecolor="none")
        plt.close(fig)
        buf.seek(0)

        logger.info(f"Chart generated for {symbol} ({verdict})")
        return buf.getvalue()

    except Exception as e:
        logger.error(f"Chart generation failed for {symbol}: {e}")
        plt.close("all")
        return None
