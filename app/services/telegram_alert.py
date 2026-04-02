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


def send_message(text: str) -> bool:
    """Send a plain text message to the configured Telegram chat."""
    if not _is_configured():
        return False

    global _last_send
    with _send_lock:
        elapsed = time.time() - _last_send
        if elapsed < 0.1:
            time.sleep(0.1 - elapsed)
        _last_send = time.time()

    url = _TELEGRAM_API.format(token=settings.TELEGRAM_BOT_TOKEN)
    payload = {
        "chat_id": settings.TELEGRAM_CHAT_ID,
        "text": text,
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


def send_photo(image_bytes: bytes, caption: str = "") -> bool:
    """Send a photo (PNG bytes) to the configured Telegram chat."""
    if not _is_configured():
        return False

    global _last_send
    with _send_lock:
        elapsed = time.time() - _last_send
        if elapsed < 0.1:
            time.sleep(0.1 - elapsed)
        _last_send = time.time()

    url = _TELEGRAM_PHOTO_API.format(token=settings.TELEGRAM_BOT_TOKEN)

    try:
        with httpx.Client(timeout=30) as client:
            files = {"photo": ("chart.png", image_bytes, "image/png")}
            data = {
                "chat_id": settings.TELEGRAM_CHAT_ID,
            }
            if caption:
                data["caption"] = caption[:1024]  # Telegram caption limit
            resp = client.post(url, data=data, files=files)
            resp.raise_for_status()
            return True
    except Exception as e:
        logger.error(f"Telegram send_photo failed: {e}")
        return False


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
    text += f"\n\n{datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC"
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
        f"{datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC"
    )
    return send_message(text)


def alert_daily_summary(screener_data: list, portfolio_data: dict = None):
    """Send daily pre-market summary."""
    strong_buys = [s for s in screener_data if s.get("swing_signal") == "STRONG BUY"]
    buys = [s for s in screener_data if s.get("swing_signal") == "BUY"]

    text = (
        f"DAILY PRE-MARKET SUMMARY\n"
        f"{datetime.utcnow().strftime('%Y-%m-%d')}\n"
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
            f"{datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC"
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


def alert_system_health(issue: str, severity: str = "WARNING"):
    """Send system health alert."""
    text = (
        f"[{severity}] SYSTEM ALERT\n"
        f"\n"
        f"{issue}\n"
        f"\n"
        f"{datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC"
    )
    return send_message(text)
