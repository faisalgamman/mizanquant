"""Test multi-provider agent + error surfacing."""

import pytest


class TestAgentProvider:
    def test_groq_when_only_groq_key(self, monkeypatch):
        """With only GROQ_API_KEY set, provider=groq."""
        monkeypatch.setattr("halal_screener.settings.DEEPSEEK_API_KEY", "")
        monkeypatch.setattr("halal_screener.settings.ANTHROPIC_API_KEY", "")
        monkeypatch.setattr("halal_screener.settings.GROQ_API_KEY", "gsk_test")
        monkeypatch.setattr("halal_screener.settings.GROQ_MODEL", "llama-3.3-70b-versatile")

        from app.services.claude_agent import TradingAgent
        agent = TradingAgent()
        assert agent._provider == "groq"
        assert agent.model == "llama-3.3-70b-versatile"

    def test_override_wins(self, monkeypatch):
        """With AGENT_PROVIDER=groq and all keys set, provider=groq."""
        monkeypatch.setenv("AGENT_PROVIDER", "groq")
        monkeypatch.setattr("halal_screener.settings.DEEPSEEK_API_KEY", "sk_test")
        monkeypatch.setattr("halal_screener.settings.GROQ_API_KEY", "gsk_test")
        monkeypatch.setattr("halal_screener.settings.ANTHROPIC_API_KEY", "ant_test")
        monkeypatch.setattr("halal_screener.settings.GROQ_MODEL", "llama-3.3-70b-versatile")

        # Force fresh agent (reset singleton)
        import app.services.claude_agent as ca
        ca._agent = None

        agent = ca.TradingAgent()
        assert agent._provider == "groq"

    def test_no_keys_raises(self, monkeypatch):
        """With all keys empty, TradingAgent() raises ValueError."""
        monkeypatch.setattr("halal_screener.settings.DEEPSEEK_API_KEY", "")
        monkeypatch.setattr("halal_screener.settings.GROQ_API_KEY", "")
        monkeypatch.setattr("halal_screener.settings.OPENROUTER_API_KEY", "")
        monkeypatch.setattr("halal_screener.settings.ANTHROPIC_API_KEY", "")

        # Force fresh agent
        import app.services.claude_agent as ca
        ca._agent = None

        with pytest.raises(ValueError):
            ca.TradingAgent()

    def test_openrouter_when_only_openrouter_key(self, monkeypatch):
        """With only OPENROUTER_API_KEY set, provider=openrouter, default model."""
        monkeypatch.setattr("halal_screener.settings.DEEPSEEK_API_KEY", "")
        monkeypatch.setattr("halal_screener.settings.ANTHROPIC_API_KEY", "")
        monkeypatch.setattr("halal_screener.settings.GROQ_API_KEY", "")
        monkeypatch.setattr("halal_screener.settings.OPENROUTER_API_KEY", "sk-or-test")

        import app.services.claude_agent as ca
        ca._agent = None
        agent = ca.TradingAgent()
        assert agent._provider == "openrouter"
        assert agent.model == "anthropic/claude-sonnet-4.6"

    def test_openrouter_with_agent_model_override(self, monkeypatch):
        """AGENT_MODEL overrides openrouter default model."""
        monkeypatch.setattr("halal_screener.settings.DEEPSEEK_API_KEY", "")
        monkeypatch.setattr("halal_screener.settings.ANTHROPIC_API_KEY", "")
        monkeypatch.setattr("halal_screener.settings.GROQ_API_KEY", "")
        monkeypatch.setattr("halal_screener.settings.OPENROUTER_API_KEY", "sk-or-test")
        monkeypatch.setattr("halal_screener.settings.AGENT_MODEL", "openai/gpt-4o")

        import app.services.claude_agent as ca
        ca._agent = None
        agent = ca.TradingAgent()
        assert agent._provider == "openrouter"
        assert agent.model == "openai/gpt-4o"

    def test_deepseek_with_agent_model_override(self, monkeypatch):
        """AGENT_MODEL overrides deepseek default model."""
        monkeypatch.setattr("halal_screener.settings.DEEPSEEK_API_KEY", "sk-test")
        monkeypatch.setattr("halal_screener.settings.ANTHROPIC_API_KEY", "")
        monkeypatch.setattr("halal_screener.settings.GROQ_API_KEY", "")
        monkeypatch.setattr("halal_screener.settings.AGENT_MODEL", "deepseek-v4-pro")

        import app.services.claude_agent as ca
        ca._agent = None
        agent = ca.TradingAgent()
        assert agent._provider == "deepseek"
        assert agent.model == "deepseek-v4-pro"


class TestAgentChatError:
    def test_returns_error_dict_on_exception(self, monkeypatch):
        """When agent.chat raises, /agent/chat returns {error: ...}."""
        import app.routers.admin as admin_mod

        monkeypatch.setattr("halal_screener.settings.DEEPSEEK_API_KEY", "sk_test")

        async def fake_chat(self, msg, conversation_id=None):
            raise RuntimeError("Insufficient Balance")

        monkeypatch.setattr(
            "app.services.claude_agent.TradingAgent.chat", fake_chat,
        )

        import asyncio
        result = asyncio.run(admin_mod.agent_chat({"message": "hello"}))
        assert "error" in result
        assert "RuntimeError" in result["error"]
        assert "Insufficient Balance" in result["error"]
