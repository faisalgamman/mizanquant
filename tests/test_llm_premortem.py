"""LLM pre-mortem — parse, cache, fail-open (no network)."""
import app.services.llm_premortem as pm


class _FakeAgent:
    _llm_available = True

    def __init__(self):
        pass

    def _call_llm(self, system, user, max_tokens=0, temperature=0.0):
        return '{"risk":"high","flags":["stretched valuation","earnings next week","sector weak","crowded trade"]}'


def test_parse_caps_flags_and_infers_risk():
    r = pm._parse('{"risk":"high","flags":["a","b","c","d","e"]}')
    assert r["risk"] == "high" and len(r["flags"]) == 4        # capped at 4
    assert pm._parse('{"risk":"weird","flags":[]}')["risk"] == "medium"
    assert pm._parse("not json") is None


def test_uses_agent(monkeypatch):
    pm._cache.clear()
    monkeypatch.setattr(pm, "LLM_PREMORTEM", True)
    monkeypatch.setattr("app.ai_agent.AIAgent", _FakeAgent)
    r = pm.llm_premortem("AAA")
    assert r and r["risk"] == "high" and r["method"] == "llm" and len(r["flags"]) == 4


def test_disabled(monkeypatch):
    monkeypatch.setattr(pm, "LLM_PREMORTEM", False)
    assert pm.llm_premortem("BBB") is None


def test_fail_open(monkeypatch):
    pm._cache.clear()
    monkeypatch.setattr(pm, "LLM_PREMORTEM", True)

    class _Bad(_FakeAgent):
        def _call_llm(self, *a, **k):
            return "garbage"
    monkeypatch.setattr("app.ai_agent.AIAgent", _Bad)
    assert pm.llm_premortem("CCC") is None


def test_caches(monkeypatch):
    pm._cache.clear()
    monkeypatch.setattr(pm, "LLM_PREMORTEM", True)
    calls = {"n": 0}

    class _C(_FakeAgent):
        def _call_llm(self, *a, **k):
            calls["n"] += 1
            return '{"risk":"low","flags":["x"]}'
    monkeypatch.setattr("app.ai_agent.AIAgent", _C)
    pm.llm_premortem("DDD")
    pm.llm_premortem("DDD")
    assert calls["n"] == 1
