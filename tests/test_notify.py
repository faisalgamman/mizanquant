"""Tests for queued notifications."""
from __future__ import annotations

import queue

from app.services import notify


def test_send_message_queues_without_blocking(monkeypatch):
    q = queue.Queue()
    monkeypatch.setattr(notify, "_queue", q)
    monkeypatch.setattr(notify, "_ensure_worker", lambda: None)
    monkeypatch.setattr(notify.settings, "TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setattr(notify.settings, "TELEGRAM_CHAT_ID", "chat")
    monkeypatch.setattr(notify.app_cfg.notify, "telegram_enabled", True)

    sent = notify.send_message("hello world", dedup_key="unit-test")

    assert sent is True
    assert q.qsize() == 1
    task = q.get_nowait()
    assert task.text == "hello world"


def test_dedup_blocks_same_bucket(monkeypatch):
    q = queue.Queue()
    monkeypatch.setattr(notify, "_queue", q)
    monkeypatch.setattr(notify, "_ensure_worker", lambda: None)
    monkeypatch.setattr(notify.settings, "TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setattr(notify.settings, "TELEGRAM_CHAT_ID", "chat")
    monkeypatch.setattr(notify.app_cfg.notify, "telegram_enabled", True)
    notify._dedup.clear()

    assert notify.send_message("one", dedup_key="same") is True
    assert notify.send_message("two", dedup_key="same") is False
