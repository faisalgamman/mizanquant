"""Telegram Bot webhook — two-way discussion.

Receives POST updates from Telegram when the owner replies to a sentinel message.
Only the owner's chat is answered; all other chats are silently ignored.

After deploy, register the webhook ONE TIME:
  curl "https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/setWebhook?url=https://<railway-app>/api/v1/telegram/webhook&secret_token=<TELEGRAM_WEBHOOK_SECRET>"
"""

import asyncio
import logging
import os

from fastapi import APIRouter, Request

logger = logging.getLogger("screener")
router = APIRouter(tags=["telegram"])


@router.post("/api/v1/telegram/webhook")
async def telegram_webhook(request: Request):
    """Handle inbound Telegram messages (owner-only, secret-validated)."""
    # 1. Validate secret header.
    secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")
    if secret and request.headers.get("X-Telegram-Bot-Api-Secret-Token", "") != secret:
        return {"ok": True}  # ignore silently — never reveal

    # 2. Parse the update body.
    try:
        update = await request.json()
    except Exception:
        return {"ok": True}

    msg = update.get("message") or update.get("edited_message") or {}
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    text = (msg.get("text") or "").strip()
    owner = os.environ.get("TELEGRAM_CHAT_ID", "")

    # 3. SECURITY: only the owner's chat is answered.
    if not text:
        return {"ok": True}
    if not chat_id:
        return {"ok": True}
    if owner and str(chat_id) != str(owner):
        return {"ok": True}

    # 4. Ack FAST; do the heavy LLM work in the background.
    async def _process() -> None:
        try:
            from app.services.claude_agent import TradingAgent
            res = await TradingAgent().chat(text, conversation_id=str(chat_id))
            reply = (res or {}).get("response") or "—"
        except Exception as exc:
            logger.error("telegram webhook agent error: %s", exc)
            reply = "Sorry — I hit an error processing that."

        # Chunk to Telegram's 4096-char limit.
        from app.services.telegram_alert import send_message
        for i in range(0, len(reply), 4000):
            send_message(reply[i : i + 4000])

    asyncio.create_task(_process())
    return {"ok": True}
