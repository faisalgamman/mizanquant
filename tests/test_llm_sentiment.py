"""Finance-aware LLM sentiment — parsing, cache, and fail-open (no network)."""

import app.services.llm_sentiment as ls


class _FakeAgent:
    _llm_available = True

    def __init__(self):
        pass

    def _call_llm(self, system, user, max_tokens=0, temperature=0.0):
        return '{"score": 0.7, "label": "bullish", "why": "beat estimates, raised guidance"}'


def test_parse_valid_clamps_and_infers():
    assert ls._parse('{"score": 0.7, "label":"bullish","why":"x"}')["score"] == 0.7
    assert ls._parse('noise {"score": 2.5, "label":"bullish"} tail')["score"] == 1.0    # clamp
    assert ls._parse('{"score": -0.9, "label":"weird"}')["label"] == "bearish"           # inferred
    assert ls._parse("not json") is None
    assert ls._parse(None) is None


def test_uses_llm_agent(monkeypatch):
    ls._cache.clear()
    monkeypatch.setattr(ls, "LLM_SENTIMENT", True)
    monkeypatch.setattr("app.ai_agent.AIAgent", _FakeAgent)
    out = ls.llm_news_sentiment("AAA", headlines=["Company beats estimates, raises guidance"])
    assert out and out["method"] == "llm" and out["label"] == "bullish" and out["n"] == 1


def test_disabled_returns_none(monkeypatch):
    monkeypatch.setattr(ls, "LLM_SENTIMENT", False)
    assert ls.llm_news_sentiment("BBB", headlines=["x"]) is None


def test_no_headlines_returns_none(monkeypatch):
    ls._cache.clear()
    monkeypatch.setattr(ls, "LLM_SENTIMENT", True)
    monkeypatch.setattr("app.ai_agent.AIAgent", _FakeAgent)
    assert ls.llm_news_sentiment("CCC", headlines=[]) is None


def test_fail_open_on_bad_output(monkeypatch):
    ls._cache.clear()
    monkeypatch.setattr(ls, "LLM_SENTIMENT", True)

    class _Bad(_FakeAgent):
        def _call_llm(self, *a, **k):
            return "garbage, not json"
    monkeypatch.setattr("app.ai_agent.AIAgent", _Bad)
    assert ls.llm_news_sentiment("DDD", headlines=["x"]) is None


def test_result_is_cached(monkeypatch):
    ls._cache.clear()
    monkeypatch.setattr(ls, "LLM_SENTIMENT", True)
    calls = {"n": 0}

    class _Counting(_FakeAgent):
        def _call_llm(self, *a, **k):
            calls["n"] += 1
            return '{"score":0.5,"label":"bullish"}'
    monkeypatch.setattr("app.ai_agent.AIAgent", _Counting)
    ls.llm_news_sentiment("EEE", headlines=["x"])
    ls.llm_news_sentiment("EEE", headlines=["x"])
    assert calls["n"] == 1   # second call served from cache
