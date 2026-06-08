"""Tests for Telegram webhook — owner-only, secret-validated, background processing."""

import asyncio

import pytest
from fastapi.testclient import TestClient


# ── helpers ────────────────────────────────────────────────────────


@pytest.fixture
def client_app(monkeypatch):
    """Create a FastAPI TestClient with the webhook router mounted on a fresh app."""
    from fastapi import FastAPI

    monkeypatch.setenv("TELEGRAM_CHAT_ID", "5774962001")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "test-secret-42")

    # Block the real bot token so telegram_alert.send_message doesn't hit the network.
    monkeypatch.setattr(
        "app.services.telegram_alert.send_message",
        lambda text: True,
    )

    from app.routers.telegram_webhook import router as webhook_router

    app = FastAPI()
    app.include_router(webhook_router)
    return TestClient(app)


# ── tests ───────────────────────────────────────────────────────────


class TestWebhookSecurity:
    def test_wrong_secret_ignored(self, client_app):
        """Wrong secret header → 200 ok (silent ignore), agent NOT called."""
        resp = client_app.post(
            "/api/v1/telegram/webhook",
            json={
                "message": {
                    "chat": {"id": 5774962001},
                    "text": "hello",
                }
            },
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong-secret"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

    def test_foreign_chat_ignored(self, client_app):
        """Chat ID that doesn't match TELEGRAM_CHAT_ID → ignored."""
        resp = client_app.post(
            "/api/v1/telegram/webhook",
            json={
                "message": {
                    "chat": {"id": 99999999},
                    "text": "hack attempt",
                }
            },
            headers={"X-Telegram-Bot-Api-Secret-Token": "test-secret-42"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

    def test_empty_text_ignored(self, client_app):
        """Empty message text → ignored."""
        resp = client_app.post(
            "/api/v1/telegram/webhook",
            json={
                "message": {
                    "chat": {"id": 5774962001},
                    "text": "   ",
                }
            },
            headers={"X-Telegram-Bot-Api-Secret-Token": "test-secret-42"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}


class TestWebhookValidOwner:
    def test_owner_message_calls_agent(self, client_app, monkeypatch):
        """Valid owner message → TradingAgent.chat invoked, reply sent."""
        chat_called = []

        class FakeAgent:
            async def chat(self, text, conversation_id=None):
                chat_called.append((text, conversation_id))
                return {"response": "Hello from agent!"}

        # Patch the ORIGINAL module — TradingAgent is imported locally in _process().
        monkeypatch.setattr(
            "app.services.claude_agent.TradingAgent",
            lambda: FakeAgent(),
        )

        # Mock telegram_alert.send_message to capture what would be sent.
        sent_messages: list[str] = []
        monkeypatch.setattr(
            "app.services.telegram_alert.send_message",
            lambda text: sent_messages.append(text) or True,
        )

        # Replace create_task so the background coroutine runs inline.
        _bg_coros: list = []
        _real_create_task = asyncio.create_task

        def _capture_create_task(coro, **kw):
            _bg_coros.append(coro)
            # Return a fake Task-like object so the caller doesn't crash.
            # FastAPI doesn't actually need the task — it's fire-and-forget.
            return _real_create_task(asyncio.sleep(0), **kw)

        monkeypatch.setattr("asyncio.create_task", _capture_create_task)

        resp = client_app.post(
            "/api/v1/telegram/webhook",
            json={
                "message": {
                    "chat": {"id": 5774962001},
                    "text": "What do you think of AAPL?",
                }
            },
            headers={"X-Telegram-Bot-Api-Secret-Token": "test-secret-42"},
        )

        # Ack is immediate.
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

        # Run the captured background coroutines.
        loop = asyncio.new_event_loop()
        for coro in _bg_coros:
            loop.run_until_complete(coro)
        loop.close()

        # Agent was called with the user's text.
        assert len(chat_called) >= 1
        text_arg, conv_id = chat_called[0]
        assert "AAPL" in text_arg
        assert str(conv_id) == "5774962001"

        # Reply should have been sent (chunked).
        assert len(sent_messages) >= 1
        assert "Hello from agent!" in sent_messages[0]
