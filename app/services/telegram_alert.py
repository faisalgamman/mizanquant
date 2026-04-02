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
from datetime import datetime, timedelta
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger("screener")

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"

# Rate limit: max 30 messages/second per bot (Telegram limit)
_last_send = 0.0
_send_lock = threading.Lock()


def _is_configured() -> bool:
    """Check if Telegram credentials are set."""
    return bool(settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_ID)


def send_message(text: str, parse_mode: str = "HTML") -> bool:
    """Send a message to the configured Telegram chat.

    Returns True if sent successfully, False otherwise.
    """
    if not _is_configured():
        return False

    global _last_send
    with _send_lock:
        # Simple rate limit
        elapsed = time.time() - _last_send
        if elapsed < 0.1:
            time.sleep(0.1 - elapsed)
        _last_send = time.time()

    url = _TELEGRAM_API.format(token=settings.TELEGRAM_BOT_TOKEN)
    payload = {
        "chat_id": settings.TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }

    try:
        with httpx.Client(timeout=10) as client:
            resp = client.post(url, json=payload)
            resp.raise_for_status()
            return True
    except Exception as e:
        logger.error(f"Telegram send failed: {e}")
        return False


# ---------------------------------------------------------------------------
# Alert templates
# ---------------------------------------------------------------------------

def alert_strong_signal(symbol: str, signal_type: str, score: float,
                        price: float, stop_loss: float, take_profit: float,
                        details: str = ""):
    """Send alert for a strong trading signal."""
    emoji = "\U0001F7E2" if "BUY" in signal_type else "\U0001F534"  # green/red circle
    text = (
        f"{emoji} <b>{signal_type}</b> — {symbol}\n"
        f"\n"
        f"Price: <b>${price:.2f}</b>\n"
        f"Score: <b>{score}</b>\n"
        f"Stop Loss: ${stop_loss:.2f}\n"
        f"Take Profit: ${take_profit:.2f}\n"
    )
    if details:
        text += f"\n{details}"
    text += f"\n\n<i>{datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC</i>"
    return send_message(text)


def alert_consensus(symbol: str, verdict: str, confidence: float,
                    votes_buy: int, votes_sell: int, votes_hold: int,
                    price: float, stop_loss: float, tp1: float):
    """Send alert for consensus verdict."""
    if "STRONG BUY" in verdict:
        emoji = "\U0001F680"  # rocket
    elif "BUY" in verdict:
        emoji = "\U0001F7E2"
    elif "STRONG SELL" in verdict:
        emoji = "\U0001F6A8"  # siren
    elif "SELL" in verdict:
        emoji = "\U0001F534"
    else:
        return False  # Don't alert for HOLD/NEUTRAL

    text = (
        f"{emoji} <b>AI CONSENSUS: {verdict}</b>\n"
        f"Symbol: <b>{symbol}</b> @ ${price:.2f}\n"
        f"\n"
        f"Confidence: <b>{confidence:.1f}%</b>\n"
        f"Votes: BUY {votes_buy} | SELL {votes_sell} | HOLD {votes_hold}\n"
        f"SL: ${stop_loss:.2f} | TP: ${tp1:.2f}\n"
        f"\n"
        f"<i>{datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC</i>"
    )
    return send_message(text)


def alert_daily_summary(screener_data: list, portfolio_data: dict = None):
    """Send daily pre-market summary."""
    strong_buys = [s for s in screener_data if s.get("swing_signal") == "STRONG BUY"]
    buys = [s for s in screener_data if s.get("swing_signal") == "BUY"]

    text = (
        f"\U0001F4CA <b>Daily Pre-Market Summary</b>\n"
        f"{datetime.utcnow().strftime('%Y-%m-%d')}\n"
        f"\n"
        f"Stocks Screened: {len(screener_data)}\n"
        f"STRONG BUY: <b>{len(strong_buys)}</b>\n"
        f"BUY: <b>{len(buys)}</b>\n"
    )

    if strong_buys:
        text += f"\n<b>Top Signals:</b>\n"
        for s in strong_buys[:5]:
            text += f"  {s['symbol']} — Score {s['swing_score']}, ${s['price']}\n"

    if portfolio_data:
        text += (
            f"\n<b>Portfolio:</b>\n"
            f"  Equity: ${portfolio_data.get('equity', 0):,.2f}\n"
            f"  Daily P&L: ${portfolio_data.get('daily_pl', 0):+,.2f}\n"
        )

    return send_message(text)


def alert_system_health(issue: str, severity: str = "WARNING"):
    """Send system health alert."""
    emoji = "\U000026A0" if severity == "WARNING" else "\U0001F6A8"  # warning / siren
    text = (
        f"{emoji} <b>SYSTEM {severity}</b>\n"
        f"\n"
        f"{issue}\n"
        f"\n"
        f"<i>{datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC</i>"
    )
    return send_message(text)
