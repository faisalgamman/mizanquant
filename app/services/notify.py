"""Non-blocking notification dispatcher with queueing and deduplication."""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

import httpx

from app.config import settings
from app.core.config import app_cfg

logger = logging.getLogger("screener")

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
_TELEGRAM_PHOTO_API = "https://api.telegram.org/bot{token}/sendPhoto"


@dataclass(slots=True)
class NotificationTask:
    kind: str
    text: str = ""
    image_bytes: bytes | None = None
    caption: str = ""
    dedup_key: str = ""
    critical: bool = False


_queue: queue.Queue[NotificationTask] = queue.Queue(maxsize=1000)
_worker_started = False
_worker_lock = threading.Lock()
_dedup: dict[str, float] = {}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _is_configured() -> bool:
    return bool(
        app_cfg.notify.telegram_enabled
        and settings.TELEGRAM_BOT_TOKEN
        and settings.TELEGRAM_CHAT_ID
    )


def _render_default(template_name: str, **ctx) -> str:
    if template_name == "startup_ok":
        return f"STARTUP OK\n\n{ctx.get('message', 'System boot completed.')}"
    if template_name == "startup_blocked":
        return f"STARTUP BLOCKED\n\n{ctx.get('reason', 'Unknown reason')}"
    if template_name == "regime_change":
        return (
            f"REGIME CHANGE\n\n"
            f"State: {ctx.get('state')}\n"
            f"VIX percentile: {ctx.get('vix_pctile')}\n"
            f"SPY slope: {ctx.get('spy_ema_slope_pct')}\n"
            f"Yield spread: {ctx.get('yield_spread_bps')} bps"
        )
    if template_name == "guard_halt":
        return (
            f"GUARD HALT\n\n"
            f"{ctx.get('symbol')} {ctx.get('side', '').upper()}\n"
            f"Guard: {ctx.get('guard_name')}\n"
            f"Reason: {ctx.get('reason')}"
        )
    if template_name == "trade_submitted":
        return (
            f"TRADE SUBMITTED\n\n"
            f"{ctx.get('strategy_id') or '-'} {ctx.get('symbol')}\n"
            f"Qty: {ctx.get('qty')}\n"
            f"Price: {ctx.get('price')}"
        )
    if template_name == "trade_filled":
        return (
            f"TRADE FILLED\n\n"
            f"{ctx.get('symbol')} x{ctx.get('qty')}\n"
            f"Avg fill: {ctx.get('filled_avg_price')}"
        )
    if template_name == "trade_rejected":
        return (
            f"TRADE REJECTED\n\n"
            f"{ctx.get('symbol')}\n"
            f"Reason: {ctx.get('reason')}"
        )
    if template_name == "bracket_leg_missing":
        return (
            f"BRACKET LEG MISSING\n\n"
            f"Parent order: {ctx.get('parent_id')}\n"
            f"Strategy: {ctx.get('strategy_id')}\n"
            f"Parent was canceled before the order was armed."
        )
    if template_name == "reconcile_orphan":
        return (
            f"RECONCILIATION ALERT\n\n"
            f"orphan_positions={ctx.get('orphan_positions')}\n"
            f"orphan_brackets={ctx.get('orphan_brackets')}"
        )
    if template_name == "critical_error":
        return f"CRITICAL ERROR\n\n{ctx.get('message', 'Unhandled critical error')}"
    if template_name == "eod_summary":
        return f"EOD SUMMARY\n\n{ctx.get('message', '')}"
    return str(ctx.get("message", ""))


_renderers: dict[str, Callable[..., str]] = {}


def render(template_name: str, **ctx) -> str:
    renderer = _renderers.get(template_name)
    if renderer:
        return renderer(**ctx)
    return _render_default(template_name, **ctx)


def register_renderer(template_name: str, renderer: Callable[..., str]) -> None:
    _renderers[template_name] = renderer


def _bucketed(dedup_key: str) -> str:
    bucket = int(time.time() // max(app_cfg.notify.dedup_window_seconds, 1))
    return f"{dedup_key}:{bucket}"


def _seen_recently(dedup_key: str) -> bool:
    if not dedup_key:
        return False
    bucketed = _bucketed(dedup_key)
    now = time.time()
    expired = [
        key for key, ts in _dedup.items()
        if now - ts > max(app_cfg.notify.dedup_window_seconds, 1)
    ]
    for key in expired:
        _dedup.pop(key, None)
    if bucketed in _dedup:
        return True
    _dedup[bucketed] = now
    return False


def _send_text_now(text: str, chat_id: str | None = None) -> bool:
    if not _is_configured():
        return False
    url = _TELEGRAM_API.format(token=settings.TELEGRAM_BOT_TOKEN)
    payload = {
        "chat_id": chat_id or settings.TELEGRAM_CHAT_ID,
        "text": text,
        "disable_web_page_preview": True,
    }
    with httpx.Client(timeout=10) as client:
        resp = client.post(url, json=payload)
        resp.raise_for_status()
    return True


def _send_photo_now(image_bytes: bytes, caption: str = "", chat_id: str | None = None) -> bool:
    if not _is_configured():
        return False
    url = _TELEGRAM_PHOTO_API.format(token=settings.TELEGRAM_BOT_TOKEN)
    with httpx.Client(timeout=30) as client:
        files = {"photo": ("chart.png", image_bytes, "image/png")}
        data = {"chat_id": chat_id or settings.TELEGRAM_CHAT_ID}
        if caption:
            data["caption"] = caption[:1024]
        resp = client.post(url, data=data, files=files)
        resp.raise_for_status()
    return True


_BUY_SIGNAL_MARKERS = (
    "STRONG BUY SIGNAL",
    "PRE-MARKET SIGNALS",
    "READY TO TRADE",
)


def _is_buy_signal(text: str) -> bool:
    upper = (text or "").upper()
    return any(marker in upper for marker in _BUY_SIGNAL_MARKERS)


def _filtered_out_by_buy_only(text: str) -> bool:
    """Central buy-only gate. Honoured by every notification that reaches
    Telegram, regardless of which entry point (send_message, send_photo,
    notify(template, ...)) was used. When TELEGRAM_BUY_ONLY is True,
    suppress anything that doesn't carry a buy-signal marker."""
    if not getattr(settings, "TELEGRAM_BUY_ONLY", False):
        return False
    return not _is_buy_signal(text)


def _process_task(task: NotificationTask) -> None:
    if not _is_configured():
        return
    if task.kind == "photo" and task.image_bytes is not None:
        if _filtered_out_by_buy_only(task.caption):
            return
        _send_photo_now(task.image_bytes, caption=task.caption)
    else:
        if _filtered_out_by_buy_only(task.text):
            return
        _send_text_now(task.text)
        if task.critical and app_cfg.notify.critical_channel:
            _send_text_now(task.text, chat_id=app_cfg.notify.critical_channel)


def _worker_loop() -> None:
    while True:
        task = _queue.get()
        try:
            _process_task(task)
        except Exception as exc:
            logger.error("Notification delivery failed: %s", exc, exc_info=True)
        finally:
            _queue.task_done()


def _ensure_worker() -> None:
    global _worker_started
    if _worker_started:
        return
    with _worker_lock:
        if _worker_started:
            return
        thread = threading.Thread(target=_worker_loop, daemon=True, name="notify-worker")
        thread.start()
        _worker_started = True


def send_message(text: str, dedup_key: str = "", critical: bool = False) -> bool:
    if not text:
        return False
    if dedup_key and _seen_recently(dedup_key):
        return False
    _ensure_worker()
    _queue.put_nowait(NotificationTask(kind="text", text=text, dedup_key=dedup_key, critical=critical))
    return True


def send_photo(image_bytes: bytes, caption: str = "", dedup_key: str = "", critical: bool = False) -> bool:
    if not image_bytes:
        return False
    if dedup_key and _seen_recently(dedup_key):
        return False
    _ensure_worker()
    _queue.put_nowait(
        NotificationTask(
            kind="photo",
            image_bytes=image_bytes,
            caption=caption,
            dedup_key=dedup_key,
            critical=critical,
        )
    )
    return True


def notify(event_type: str, dedup_key: str = "", critical: bool = False, **ctx) -> bool:
    text = render(event_type, **ctx)
    return send_message(text, dedup_key=dedup_key or event_type, critical=critical)
