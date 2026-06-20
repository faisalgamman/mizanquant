"""Unit tests for the agent's durable memory + learning loop (no real DB / LLM).

Covers the new pure logic:
  • journal.performance_summary — aggregates SCORED decisions into the agent's own track
    record (the LEARNING layer fed into the system prompt).
  • TradingAgent._auto_record_recommendations — deterministically logs BUY verdicts parsed
    from tool messages (so recording never depends on the LLM calling a tool).
"""

import json

from app.services.ai_sentinel import journal as J
from app.services import claude_agent as CA


# ── performance_summary ─────────────────────────────────────────────────────

class _Row:
    def __init__(self, verdict, snapshot):
        self.verdict, self.snapshot = verdict, snapshot


class _Query:
    def __init__(self, rows): self._rows = rows
    def filter(self, *a, **k): return self
    def all(self): return self._rows


class _FakeSession:
    def __init__(self, rows): self._rows = rows
    def query(self, *a, **k): return _Query(self._rows)
    def close(self): pass


def _patch_decisions(monkeypatch, rows):
    monkeypatch.setattr("app.db.database.SessionLocal", lambda: _FakeSession(rows))


def test_performance_summary_aggregates_by_verdict(monkeypatch):
    rows = [
        _Row("STRONG BUY", {"outcome_label": "win", "outcome_pct": 8.0}),
        _Row("STRONG BUY", {"outcome_label": "win", "outcome_pct": 4.0}),
        _Row("STRONG BUY", {"outcome_label": "loss", "outcome_pct": -3.0}),
        _Row("BUY", {"outcome_label": "loss", "outcome_pct": -5.0}),
        _Row("BUY", {"outcome_label": "win", "outcome_pct": 1.0}),
        _Row("BUY", {"snapshot_without_outcome": True}),   # unscored → ignored
    ]
    _patch_decisions(monkeypatch, rows)
    ps = J.performance_summary()
    assert ps["scored"] == 5                                # the unscored row is excluded
    assert ps["win_rate"] == 60.0                           # 3 wins of 5
    assert ps["by_verdict"]["STRONG BUY"]["n"] == 3
    assert ps["by_verdict"]["STRONG BUY"]["win_rate"] == round(2 / 3 * 100, 1)
    assert ps["by_verdict"]["BUY"]["win_rate"] == 50.0


def test_performance_summary_empty_when_unscored(monkeypatch):
    _patch_decisions(monkeypatch, [_Row("BUY", {})])
    assert J.performance_summary()["scored"] == 0


# ── _auto_record_recommendations ────────────────────────────────────────────

def _tool_msg(payload):
    return {"role": "tool", "content": json.dumps(payload)}


def test_auto_record_logs_buy_dedupes_and_skips_non_buy(monkeypatch):
    captured = []
    monkeypatch.setattr(J, "record_decision_if_new",
                        lambda *a, **k: captured.append((a[0], a[2])))

    cid = "testconv1"
    CA._conversations[cid] = {"messages": [
        _tool_msg({"symbol": "AAPL", "verdict": "STRONG BUY", "composite_score": 75}),
        _tool_msg({"symbol": "AAPL", "verdict": "STRONG BUY", "composite_score": 75}),  # dup → once
        _tool_msg({"symbol": "WMT", "verdict": "AVOID"}),                                # not a buy
        _tool_msg({"symbol": "MSFT", "verdict": "BUY", "smart_score": 60}),
        {"role": "assistant", "content": "some text"},                                  # ignored
    ], "last_access": 0}

    agent = object.__new__(CA.TradingAgent)               # bypass __init__ (no LLM key needed)
    agent._auto_record_recommendations(cid)

    syms = [c[0] for c in captured]
    assert syms == ["AAPL", "MSFT"]                        # AAPL once (deduped), MSFT; WMT skipped
    del CA._conversations[cid]


def test_auto_record_handles_anthropic_tool_result_blocks(monkeypatch):
    captured = []
    monkeypatch.setattr(J, "record_decision_if_new", lambda *a, **k: captured.append(a[0]))
    cid = "testconv2"
    CA._conversations[cid] = {"messages": [
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "x",
             "content": json.dumps({"symbol": "NVDA", "verdict": "BUY", "composite_score": 70})},
        ]},
    ], "last_access": 0}
    agent = object.__new__(CA.TradingAgent)
    agent._auto_record_recommendations(cid)
    assert captured == ["NVDA"]
    del CA._conversations[cid]
