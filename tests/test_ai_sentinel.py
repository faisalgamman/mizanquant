"""Tests for ai_sentinel sentinel — fully offline, injectable LLM/send."""

import json
from unittest.mock import patch

from app.services.ai_sentinel.sentinel import (
    _conf_num,
    _format_opportunity,
    _format_risk_question,
    _parse_json_lenient,
    build_prompt,
    run_sentinel_cycle,
)


# ── unit tests ──────────────────────────────────────────────────────


class TestParseJsonLenient:
    def test_valid_json(self):
        data = _parse_json_lenient('{"a": 1}')
        assert data == {"a": 1}

    def test_json_with_markdown_fence(self):
        data = _parse_json_lenient('```json\n{"a": 1}\n```')
        assert data == {"a": 1}

    def test_malformed_json(self):
        data = _parse_json_lenient("not json at all")
        assert data == {}

    def test_empty_string(self):
        data = _parse_json_lenient("")
        assert data == {}

    def test_none(self):
        data = _parse_json_lenient(None)
        assert data == {}


class TestConfNum:
    def test_low(self):
        assert _conf_num("low") == 0.3

    def test_medium(self):
        assert _conf_num("medium") == 0.6

    def test_high(self):
        assert _conf_num("high") == 0.85

    def test_unknown(self):
        assert _conf_num("blah") == 0.5


class TestFormatOpportunity:
    def test_has_disclaimer(self):
        msg = _format_opportunity({
            "symbol": "AAPL",
            "headline": "Strong momentum",
            "reasoning": "RSI + MACD",
            "confidence": "high",
            "uncertainty": "Low volume",
        })
        assert "AAPL" in msg
        assert "coin-flip base accuracy" in msg
        assert "not advice" in msg
        assert "paper ledger not graduated" in msg


class TestFormatRiskQuestion:
    def test_formats_topic(self):
        msg = _format_risk_question({
            "topic": "drawdown",
            "question": "Reduce position size?",
            "reasoning": "Drawdown at 15% tier.",
        })
        assert "drawdown" in msg
        assert "Reduce position size?" in msg


class TestBuildPrompt:
    def test_returns_tuple(self):
        system, user = build_prompt({"market": {}, "opportunities": [], "recent_journal": []})
        assert isinstance(system, str)
        assert isinstance(user, str)
        assert "cautious trading sentinel" in system.lower()
        assert "STRICT JSON" in system


# ── integration test — run_sentinel_cycle with injected mocks ────────


class TestRunSentinelCycleOffline:
    def test_happy_path(self):
        """Injected LLM returns valid JSON → send() sees all messages with disclaimer."""
        canned_json = json.dumps({
            "summary": "Bullish pre-market",
            "opportunities": [
                {
                    "symbol": "AAPL",
                    "headline": "Breakout above 200",
                    "reasoning": "Volume surge",
                    "confidence": "high",
                    "uncertainty": "Earnings next week",
                },
                {
                    "symbol": "MSFT",
                    "headline": "Momentum continuation",
                    "reasoning": "RSI trending up",
                    "confidence": "medium",
                    "uncertainty": "Sector rotation risk",
                },
            ],
            "risk_questions": [
                {
                    "topic": "drawdown",
                    "reasoning": "Portfolio at 12% drawdown",
                    "question": "Should we reduce position size from 3% to 2%?",
                },
            ],
        })

        sent: list[str] = []
        def fake_llm(_system, _user):
            return canned_json
        def fake_send(text, dedup_key=""):
            sent.append(text)
            return True

        # Mock journal.record_decision to avoid DB dependency.
        calls = []
        def fake_record(symbol, kind, verdict, confidence, rationale, context=None):
            calls.append((symbol, kind))
            return 1

        with patch("app.services.ai_sentinel.journal.record_decision", fake_record):
            result = run_sentinel_cycle(_llm=fake_llm, _send=fake_send)

        assert result["opportunities_sent"] == 2
        assert result["questions_sent"] == 1
        assert result["summary"] == "Bullish pre-market"

        # 2 opportunities + 1 risk question = 3 messages
        assert len(sent) == 3
        # Every opportunity message MUST have the honesty disclaimer.
        for msg in sent:
            if "📊" in msg:  # opportunity message
                assert "coin-flip base accuracy" in msg
                assert "not advice" in msg

        # 3 record_decision calls (2 opps + 1 risk)
        assert len(calls) == 3

    def test_malformed_json_fallback(self):
        """Malformed LLM output → sends summary-only fallback, returns zeros."""
        sent: list[str] = []
        def fake_llm(_system, _user):
            return "The market looks good today, I recommend buying AAPL and MSFT."
        def fake_send(text, dedup_key=""):
            sent.append(text)
            return True

        result = run_sentinel_cycle(_llm=fake_llm, _send=fake_send)

        assert result["opportunities_sent"] == 0
        assert result["questions_sent"] == 0
        assert len(sent) >= 1  # fallback message sent

    def test_empty_llm_response(self):
        """Empty LLM response → no crash, returns zeros."""
        def fake_llm(_system, _user):
            return ""

        sent: list[str] = []
        def fake_send(text, dedup_key=""):
            sent.append(text)
            return True

        result = run_sentinel_cycle(_llm=fake_llm, _send=fake_send)
        assert result["opportunities_sent"] == 0
        assert result["questions_sent"] == 0
