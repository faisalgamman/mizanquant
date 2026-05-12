"""Telegram notification service for trading alerts.

Sends alerts for:
- STRONG BUY signals (score >= 75 swing or >= 8/10 USX)
- Consensus verdicts (STRONG BUY / STRONG SELL)
- Daily pre-market summary
- System health alerts (OOM, data failures, etc.)

Setup:
1. Message @BotFather on Telegram, /newbot, get your bot token
2. Message your bot, then visit:
   https://api.telegram.org/bot<TOKEN>/getUpdates
   to find your chat_id
3. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env
"""

import logging
import time
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

from app.config import settings
from app.services.notify import send_message as queued_send_message, send_photo as queued_send_photo

logger = logging.getLogger("screener")


def _utc_label() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")


def _utc_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
_TELEGRAM_PHOTO_API = "https://api.telegram.org/bot{token}/sendPhoto"

# Rate limit: max 30 messages/second per bot (Telegram limit)
_last_send = 0.0
_send_lock = threading.Lock()

# Deduplication: prevent sending the same signal within 30 minutes
_sent_signals = {}  # {symbol_verdict: timestamp}
_DEDUP_WINDOW = 1800  # 30 minutes


def _is_duplicate_signal(symbol: str, verdict: str) -> bool:
    """Check if we already sent this signal recently."""
    key = f"{symbol}_{verdict}"
    now = time.time()
    # Clean old entries
    expired = [k for k, ts in _sent_signals.items() if now - ts > _DEDUP_WINDOW]
    for k in expired:
        del _sent_signals[k]
    # Check
    if key in _sent_signals:
        return True
    _sent_signals[key] = now
    return False


def _is_configured() -> bool:
    """Check if Telegram credentials are set."""
    return bool(settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_ID)


# Marker tokens that identify a BUY signal message. When
# TELEGRAM_BUY_ONLY is enabled, send_message will silently drop any
# text that does not contain at least one of these markers. The
# signals_advisor formats messages with the exact "STRONG BUY SIGNAL"
# header, and the pre-market summary uses "PRE-MARKET SIGNALS"; both
# pass through. Trade-execution confirmations, daily summaries, regime
# changes, reconciliation alerts, etc. do not contain these markers
# and are suppressed.
_BUY_SIGNAL_MARKERS = (
    "STRONG BUY SIGNAL",     # signals_advisor per-symbol alert
    "PRE-MARKET SIGNALS",    # signals_advisor daily header
    "READY TO TRADE",        # legacy ready-to-trade alert (also buy-side)
    "QUALIFIED",             # smart_screener qualified signal (Roadmap 1.6)
    "MARKET BLOCK",          # market block alert (always critical)
    "END OF DAY REPORT",     # daily end-of-day summary
)


def _is_buy_signal_message(text: str) -> bool:
    """True if the message content qualifies as a buy-signal alert."""
    upper = (text or "").upper()
    return any(marker in upper for marker in _BUY_SIGNAL_MARKERS)


def send_message(text: str) -> bool:
    """Send a plain text message to the configured Telegram chat.

    Honours `settings.TELEGRAM_BUY_ONLY`: when True, anything that is
    not a buy-signal alert is silently dropped (returns True so the
    caller doesn't think the alert pipeline is broken). Set
    `TELEGRAM_BUY_ONLY=false` on Railway to restore the full feed.
    """
    if getattr(settings, "TELEGRAM_BUY_ONLY", False) and not _is_buy_signal_message(text):
        # Drop everything that isn't a buy signal. We keep this
        # quiet — no logger.warning — because by design dozens of
        # callsites will hit this path on a normal day.
        return True
    return queued_send_message(text)


def send_photo(image_bytes: bytes, caption: str = "") -> bool:
    """Send a photo (PNG bytes) to the configured Telegram chat.

    Photos (chart screenshots) are also gated by TELEGRAM_BUY_ONLY: when
    enabled, charts are dropped because the operator only wants action
    signals, not visual noise.
    """
    if getattr(settings, "TELEGRAM_BUY_ONLY", False) and not _is_buy_signal_message(caption):
        return True
    return queued_send_photo(image_bytes, caption=caption)


# ---------------------------------------------------------------------------
# Alert templates
# ---------------------------------------------------------------------------

def alert_strong_signal(symbol: str, signal_type: str, score: float,
                        price: float, stop_loss: float, take_profit: float,
                        details: str = ""):
    """Send alert for a strong trading signal."""
    icon = "[BUY]" if "BUY" in signal_type else "[SELL]"
    text = (
        f"{icon} {signal_type} -- {symbol}\n"
        f"\n"
        f"Price: ${price:.2f}\n"
        f"Score: {score}\n"
        f"Stop Loss: ${stop_loss:.2f}\n"
        f"Take Profit: ${take_profit:.2f}\n"
    )
    if details:
        text += f"\n{details}"
    text += f"\n\n{_utc_label()} UTC"
    return send_message(text)


def alert_consensus(symbol: str, verdict: str, confidence: float,
                    votes_buy: int, votes_sell: int, votes_hold: int,
                    price: float, stop_loss: float, tp1: float):
    """Send alert for consensus verdict."""
    if _is_duplicate_signal(symbol, verdict):
        logger.info(f"Skipping duplicate consensus: {symbol} {verdict}")
        return False
    if "STRONG BUY" in verdict:
        icon = "[STRONG BUY]"
    elif "BUY" in verdict:
        icon = "[BUY]"
    elif "STRONG SELL" in verdict:
        icon = "[STRONG SELL]"
    elif "SELL" in verdict:
        icon = "[SELL]"
    else:
        return False  # Don't alert for HOLD/NEUTRAL

    text = (
        f"{icon} AI CONSENSUS: {verdict}\n"
        f"Symbol: {symbol} @ ${price:.2f}\n"
        f"\n"
        f"Confidence: {confidence:.1f}%\n"
        f"Votes: BUY {votes_buy} | SELL {votes_sell} | HOLD {votes_hold}\n"
        f"SL: ${stop_loss:.2f} | TP: ${tp1:.2f}\n"
        f"\n"
        f"{_utc_label()} UTC"
    )
    return send_message(text)


def alert_daily_summary(screener_data: list, portfolio_data: dict = None):
    """Send daily pre-market summary."""
    strong_buys = [s for s in screener_data if s.get("swing_signal") == "STRONG BUY"]
    buys = [s for s in screener_data if s.get("swing_signal") == "BUY"]

    text = (
        f"DAILY PRE-MARKET SUMMARY\n"
        f"{_utc_day()}\n"
        f"\n"
        f"Stocks Screened: {len(screener_data)}\n"
        f"STRONG BUY: {len(strong_buys)}\n"
        f"BUY: {len(buys)}\n"
    )

    if strong_buys:
        text += f"\nTop Signals:\n"
        for s in strong_buys[:5]:
            text += f"  {s['symbol']} -- Score {s['swing_score']}, ${s['price']}\n"

    if portfolio_data:
        text += (
            f"\nPortfolio:\n"
            f"  Equity: ${portfolio_data.get('equity', 0):,.2f}\n"
            f"  Daily P&L: ${portfolio_data.get('daily_pl', 0):+,.2f}\n"
        )

    return send_message(text)


def alert_signal_with_chart(
    symbol: str,
    verdict: str,
    confidence: float,
    price: float,
    stop_loss: float,
    tp1: float,
    tp2: float = None,
    tp3: float = None,
    votes_buy: int = 0,
    votes_sell: int = 0,
    votes_hold: int = 0,
    df=None,
) -> bool:
    """Send a signal alert WITH a professional chart image.

    This is the primary post-market alert function. Generates a
    candlestick chart with entry/SL/TP lines and buy/sell arrows,
    then sends it as a photo to Telegram.
    """
    # Deduplication — don't send the same signal twice in 30 min
    if _is_duplicate_signal(symbol, verdict):
        logger.info(f"Skipping duplicate signal: {symbol} {verdict}")
        return False

    try:
        from app.services.chart_generator import generate_signal_chart

        # Risk:Reward ratio
        risk = abs(price - stop_loss)
        reward = abs(tp1 - price)
        rr = round(reward / risk, 1) if risk > 0 else 0

        # Caption text
        icon = "[STRONG BUY]" if "BUY" in verdict else "[STRONG SELL]"
        caption = (
            f"{icon} {symbol} -- {verdict}\n"
            f"\n"
            f"Entry: ${price:.2f}\n"
            f"Stop Loss: ${stop_loss:.2f}\n"
            f"TP1: ${tp1:.2f}"
        )
        if tp2:
            caption += f"  |  TP2: ${tp2:.2f}"
        if tp3:
            caption += f"  |  TP3: ${tp3:.2f}"
        caption += (
            f"\n"
            f"Risk:Reward = 1:{rr}\n"
            f"Confidence: {confidence:.0f}%\n"
            f"Votes: BUY {votes_buy} | SELL {votes_sell} | HOLD {votes_hold}\n"
            f"\n"
            f"{_utc_label()} UTC"
        )

        # Generate chart if we have data
        if df is not None and len(df) >= 30:
            chart_bytes = generate_signal_chart(
                df=df,
                symbol=symbol,
                verdict=verdict,
                entry_price=price,
                stop_loss=stop_loss,
                tp1=tp1,
                tp2=tp2,
                tp3=tp3,
                confidence=confidence,
                votes_buy=votes_buy,
                votes_sell=votes_sell,
                votes_hold=votes_hold,
            )
            if chart_bytes:
                logger.info(f"Chart ready for {symbol}: {len(chart_bytes)} bytes, sending photo...")
                ok = send_photo(chart_bytes, caption=caption)
                if ok:
                    return True
                logger.error(f"send_photo failed for {symbol}, falling back to text")
            else:
                logger.error(f"Chart bytes is None for {symbol}")

        # Fallback: text-only alert if chart generation fails
        logger.info(f"Sending text-only fallback for {symbol}")
        return send_message(caption)

    except Exception as e:
        logger.error(f"Signal chart alert failed for {symbol}: {e}")
        # Fallback to text
        return send_message(f"[{verdict}] {symbol} @ ${price:.2f} | SL ${stop_loss:.2f} | TP ${tp1:.2f}")


def alert_strategy_comparison():
    """Send daily comparison of all 3 strategies to Telegram."""
    from app.config import STRATEGY_CONFIGS
    from app.services.alpaca_client import get_account, get_positions

    if not STRATEGY_CONFIGS:
        return False

    lines = ["DAILY STRATEGY COMPARISON", ""]

    total_equity = 0
    total_pnl = 0

    for sid in ("A", "B", "C"):
        cfg = STRATEGY_CONFIGS.get(sid)
        if not cfg:
            continue

        account = get_account(strategy_id=sid)
        positions = get_positions(strategy_id=sid)

        if account:
            equity = account.get("equity", 0)
            last_eq = account.get("last_equity", 0)
            pnl = equity - last_eq if last_eq > 0 else 0
            pnl_pct = (pnl / last_eq * 100) if last_eq > 0 else 0
            total_equity += equity
            total_pnl += pnl

            pos_count = len(positions)
            max_pos = cfg.max_positions

            pnl_sign = "+" if pnl >= 0 else ""
            lines.append(f"[{sid}] {cfg.name}")
            lines.append(f"  Equity: ${equity:,.2f} | P&L: {pnl_sign}${pnl:,.2f} ({pnl_sign}{pnl_pct:.1f}%)")
            lines.append(f"  Positions: {pos_count}/{max_pos}")

            # Show individual positions
            for p in positions:
                pl = p.get("unrealized_pl", 0)
                pl_sign = "+" if pl >= 0 else ""
                lines.append(f"    {p['symbol']}: {pl_sign}${pl:.2f} ({p.get('unrealized_plpc', 0):+.1f}%)")

            lines.append("")
        else:
            lines.append(f"[{sid}] {cfg.name}")
            lines.append(f"  (account unavailable)")
            lines.append("")

    total_sign = "+" if total_pnl >= 0 else ""
    lines.append(f"TOTAL: ${total_equity:,.2f} | Net P&L: {total_sign}${total_pnl:,.2f}")
    lines.append(f"\n{_utc_label()} UTC")

    return send_message("\n".join(lines))


def alert_system_health(issue: str, severity: str = "WARNING"):
    """Send system health alert."""
    text = (
        f"[{severity}] SYSTEM ALERT\n"
        f"\n"
        f"{issue}\n"
        f"\n"
        f"{_utc_label()} UTC"
    )
    return send_message(text)


# ---------------------------------------------------------------------------
# Roadmap 1.6 — Telegram Integration (3 new alert types)
# ---------------------------------------------------------------------------


def alert_qualified_signal(
    symbol: str,
    company: str,
    score: int,
    price: float,
    strategy: str,
    stop_loss: float = 0,
    take_profits: list = None,
    rr_ratio: float = 0,
    top_indicators: list = None,
    halal_status: str = "",
) -> bool:
    """Type 1 — QUALIFIED signal: stock passed Hard Gates + Strong Gate."""
    tp_lines = []
    if take_profits:
        for i, tp in enumerate(take_profits[:3]):
            pct = (tp / price - 1) * 100 if price > 0 else 0
            qty_pct = [40, 35, 25][i] if i < 3 else 0
            tp_lines.append(f"TP{i+1}: ${tp:.2f} ({pct:+.1f}%) — sell {qty_pct}%")

    sl_pct = (stop_loss / price - 1) * 100 if price > 0 and stop_loss > 0 else 0
    indicators_str = " | ".join((top_indicators or [])[:3])
    halal_badge = "✅ HALAL" if "HALAL" in halal_status.upper() else "⚠️ " + halal_status if halal_status else ""

    text = (
        f"🚀 QUALIFIED SIGNAL\n"
        f"{symbol} — {company}\n"
    )
    if halal_badge:
        text += f"{halal_badge}\n"
    text += (
        f"\n"
        f"Price: ${price:.2f}\n"
        f"Score: {score}/100\n"
        f"Strategy: {strategy}\n"
    )
    if stop_loss > 0:
        text += f"\nEntry: ${price:.2f}\n"
        text += f"Stop Loss: ${stop_loss:.2f} ({sl_pct:+.1f}%)\n"
    for line in tp_lines:
        text += f"{line}\n"
    if rr_ratio > 0:
        text += f"R:R Ratio: 1:{rr_ratio:.1f}\n"
    if indicators_str:
        text += f"\nTop Indicators:\n{indicators_str}\n"
    text += (
        f"\n"
        f"🕐 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M ET')}"
    )
    return send_message(text)


def alert_market_block(
    status: str,
    reason: str,
    vix: float,
    vix_threshold: float,
    hyg_lqd_ratio: float = None,
    spy_regime: str = None,
) -> bool:
    """Type 2 — MARKET BLOCK: market status changed to CREDIT STRESS or BEAR."""
    block_icon = "🛑" if "BEAR" in status or "EXTREME" in status else "⚠️"
    text = (
        f"{block_icon} MARKET BLOCK ACTIVATED\n"
        f"Status: {status}\n"
        f"\n"
        f"Reason: {reason}\n"
        f"\n"
        f"VIX: {vix:.1f} (threshold: {vix_threshold})\n"
    )
    if hyg_lqd_ratio is not None:
        text += f"HYG/LQD Ratio: {hyg_lqd_ratio:.4f}\n"
    if spy_regime:
        text += f"SPY Regime: {spy_regime}\n"

    text += (
        f"\nAction: No new signals will be generated\n"
        f"Open positions: remain with existing stops\n"
        f"\n"
        f"🕐 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M ET')}"
    )
    return send_message(text)


def alert_end_of_day_report(
    market_status: dict,
    total_scanned: int,
    passed_gates: int,
    qualified_signals: list,
    watch_signals: list,
    open_positions: list = None,
    pipeline_runtime: float = None,
    system_status: str = "ok",
) -> bool:
    """Type 3 — End of Day Report, sent daily at 4:00 PM ET."""
    spy_regime = market_status.get("spy_regime", market_status.get("status", "N/A"))
    vix_val = market_status.get("vix", "N/A")
    vix_cls = market_status.get("classification", "N/A")

    text = (
        f"📊 END OF DAY REPORT\n"
        f"{_utc_day()}\n"
        f"\n"
        f"Market:\n"
        f"  SPY Regime: {spy_regime}\n"
        f"  VIX: {vix_val} ({vix_cls})\n"
        f"  Status: {market_status.get('status', 'N/A')}\n"
        f"\n"
        f"Today's Scan:\n"
        f"  Scanned: {total_scanned}\n"
        f"  Passed Gates: {passed_gates}\n"
    )

    if qualified_signals:
        text += f"\n  Qualified Signals ({len(qualified_signals)}):\n"
        for s in qualified_signals[:5]:
            sym = s.get("symbol", s) if isinstance(s, dict) else s
            sc = s.get("smart_score", "") if isinstance(s, dict) else ""
            text += f"    • {sym}" + (f" ({sc}/100)" if sc else "") + "\n"

    if watch_signals:
        text += f"\n  Watch List (top):\n"
        for s in watch_signals[:3]:
            sym = s.get("symbol", s) if isinstance(s, dict) else s
            text += f"    • {sym}\n"

    if open_positions:
        text += f"\n  Open Positions:\n"
        for p in open_positions:
            sym = p.get("symbol", "")
            pl = p.get("unrealized_pl", 0)
            pl_sign = "+" if pl >= 0 else ""
            text += f"    {sym}: {pl_sign}${pl:.2f}\n"

    if pipeline_runtime is not None:
        text += f"\n  Pipeline Runtime: {pipeline_runtime:.1f}s\n"
    text += f"  System: {system_status}\n"
    text += f"\n🕐 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M ET')}"

    return send_message(text)
