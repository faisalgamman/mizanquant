"""EGX Telegram alerts — separate from US stock alerts.

Sends EGX-specific alerts with:
- EGP currency formatting
- V9 score badge
- 7-tool consensus breakdown
- Candlestick charts
- Backtest stats
"""

import logging
import time
import threading
from datetime import datetime
from typing import Optional, Dict

import httpx

from app.config import settings

logger = logging.getLogger("egx")

# Rate limit (shared with US alerts via Telegram API limits)
_last_send = 0.0
_send_lock = threading.Lock()

# Deduplication: prevent same EGX signal within 30 minutes
_sent_signals = {}
_DEDUP_WINDOW = 1800


def _is_configured() -> bool:
    return bool(settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_ID)


def _rate_limit():
    global _last_send
    with _send_lock:
        elapsed = time.time() - _last_send
        if elapsed < 0.1:
            time.sleep(0.1 - elapsed)
        _last_send = time.time()


def _is_duplicate(symbol: str, verdict: str) -> bool:
    key = f"egx_{symbol}_{verdict}"
    now = time.time()
    expired = [k for k, ts in _sent_signals.items() if now - ts > _DEDUP_WINDOW]
    for k in expired:
        del _sent_signals[k]
    if key in _sent_signals:
        return True
    _sent_signals[key] = now
    return False


def send_message(text: str) -> bool:
    """Send a plain text message to Telegram."""
    if not _is_configured():
        return False
    _rate_limit()

    url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"
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
        logger.error(f"EGX Telegram send failed: {e}")
        return False


def send_photo(image_bytes: bytes, caption: str = "") -> bool:
    """Send a photo to Telegram."""
    if not _is_configured():
        return False
    _rate_limit()

    url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendPhoto"
    try:
        with httpx.Client(timeout=30) as client:
            files = {"photo": ("egx_chart.png", image_bytes, "image/png")}
            data = {"chat_id": settings.TELEGRAM_CHAT_ID}
            if caption:
                data["caption"] = caption[:1024]
            resp = client.post(url, data=data, files=files)
            resp.raise_for_status()
            return True
    except Exception as e:
        logger.error(f"EGX Telegram send_photo failed: {e}")
        return False


def alert_egx_signal(
    symbol: str,
    consensus: Dict,
    chart_bytes: Optional[bytes] = None,
) -> bool:
    """Send EGX signal alert with chart and full tool breakdown.

    Only sends for STRONG BUY / STRONG SELL signals.
    """
    verdict = consensus.get("verdict", "")
    if "STRONG" not in verdict:
        return False

    if _is_duplicate(symbol, verdict):
        logger.info(f"Skipping duplicate EGX signal: {symbol} {verdict}")
        return False

    price = consensus.get("price", 0)
    sl = consensus.get("stop_loss", 0)
    tp1 = consensus.get("tp1", 0)
    tp2 = consensus.get("tp2")
    tp3 = consensus.get("tp3")
    confidence = consensus.get("confidence", 0)
    v9_score = consensus.get("v9_score", 0)
    rr = consensus.get("risk_reward", 0)

    votes_buy = consensus.get("votes_buy", 0)
    votes_sell = consensus.get("votes_sell", 0)
    votes_hold = consensus.get("votes_hold", 0)

    icon = "[STRONG BUY]" if "BUY" in verdict else "[STRONG SELL]"

    # Build caption
    caption = (
        f"{icon} EGX: {symbol}\n"
        f"\n"
        f"V9 Score: {v9_score}/6\n"
        f"Entry: {price:.2f} EGP\n"
        f"Stop Loss: {sl:.2f} EGP\n"
        f"TP1: {tp1:.2f} EGP"
    )
    if tp2:
        caption += f"  |  TP2: {tp2:.2f}"
    if tp3:
        caption += f"  |  TP3: {tp3:.2f}"
    caption += (
        f"\n"
        f"Risk:Reward = 1:{rr}\n"
        f"Confidence: {confidence:.0f}%\n"
        f"Votes: BUY {votes_buy} | SELL {votes_sell} | HOLD {votes_hold}"
    )

    # Tool breakdown
    tools = consensus.get("tools", {})
    if tools:
        caption += "\n\nTool Breakdown:"
        for tool_name, vote in tools.items():
            emoji = "+" if vote == "BUY" else ("-" if vote == "SELL" else "=")
            caption += f"\n  [{emoji}] {tool_name}: {vote}"

    # Backtest stats
    bt = consensus.get("backtest_stats")
    if bt and bt.get("total_trades", 0) > 0:
        caption += (
            f"\n\nBacktest: {bt['win_rate']:.0f}% WR | "
            f"PF {bt['profit_factor']:.1f} | "
            f"{bt['total_trades']} trades"
        )

    # Monte Carlo
    mc = consensus.get("monte_carlo")
    if mc:
        caption += f"\nMC: {mc['prob_profit']*100:.0f}% profit prob ({mc['horizon_days']}d)"

    caption += f"\n\n{datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC"

    # Send with chart if available
    if chart_bytes:
        ok = send_photo(chart_bytes, caption=caption)
        if ok:
            return True
        logger.error(f"EGX photo send failed for {symbol}, falling back to text")

    return send_message(caption)


def alert_egx_scan_summary(results: list) -> bool:
    """Send a summary of EGX scan results."""
    if not results:
        return False

    strong = [r for r in results if "STRONG" in r.get("verdict", "")]
    buys = [r for r in results if r.get("verdict") == "BUY"]

    text = (
        f"EGX SCAN COMPLETE\n"
        f"{datetime.utcnow().strftime('%Y-%m-%d')}\n"
        f"\n"
        f"Stocks Analyzed: {len(results)}\n"
        f"STRONG Signals: {len(strong)}\n"
        f"BUY Signals: {len(buys)}\n"
    )

    if strong:
        text += "\nTop Signals:"
        for r in strong[:5]:
            text += (
                f"\n  {r['symbol']}: {r['verdict']} "
                f"(V9: {r.get('v9_score', '?')}/6, {r.get('confidence', 0):.0f}%)"
            )

    return send_message(text)
