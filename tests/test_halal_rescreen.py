"""Tests for the AAOIFI financial re-screen of the halal universe.

The curated universe only proves the SECTOR screen; these pure functions enforce
the debt/liquidity ratio screens that ``verify_halal`` historically skipped (the
CLX ~39% > 33% case). Injectable screener → deterministic & offline.
"""
from __future__ import annotations

import json

from app.services import halal_rescreen as hr


def _fake_screen(mapping):
    def screen(sym):
        return mapping.get(sym)  # None → unknown
    return screen


def test_rescreen_partitions_pass_fail_unknown():
    mapping = {
        "PASS": {"debt_ratio": 10.0, "liquidity_ratio": 5.0,
                 "debt_pass": True, "liquidity_pass": True, "passes": True},
        "DEBT": {"debt_ratio": 39.4, "liquidity_ratio": 5.0,
                 "debt_pass": False, "liquidity_pass": True, "passes": False},
        "LIQ":  {"debt_ratio": 10.0, "liquidity_ratio": 40.0,
                 "debt_pass": True, "liquidity_pass": False, "passes": False},
        "NODATA": None,
    }
    out = hr.rescreen_universe(["PASS", "DEBT", "LIQ", "NODATA"],
                               sleep=0, _screen_fn=_fake_screen(mapping))
    assert out["checked"] == 4 and out["passed"] == 1
    failed = {f["symbol"]: f for f in out["failed"]}
    assert set(failed) == {"DEBT", "LIQ"}
    assert "debt 39.4% > 30%" in failed["DEBT"]["reason"]
    assert "liquidity 40.0% > 30%" in failed["LIQ"]["reason"]
    assert out["unknown"] == ["NODATA"]


def test_apply_rescreen_dry_run_does_not_write(tmp_path):
    uni = tmp_path / "u.json"
    uni.write_text(json.dumps({"symbols": ["AAA", "BBB", "CCC"]}))
    result = {"failed": [{"symbol": "BBB", "debt_ratio": 40, "liquidity_ratio": 5, "reason": "debt"}]}
    plan = hr.apply_rescreen(result, dry_run=True, path=uni)
    assert plan["removed"] == ["BBB"] and plan["after_count"] == 2
    # file untouched
    assert json.loads(uni.read_text())["symbols"] == ["AAA", "BBB", "CCC"]


def test_apply_rescreen_prunes_failed_keeps_unknown(tmp_path, monkeypatch):
    uni = tmp_path / "u.json"
    uni.write_text(json.dumps({"symbols": ["AAA", "BBB", "CCC"]}))
    monkeypatch.setattr(hr, "_EXCLUDED_PATH", tmp_path / "excluded.json")
    result = {"failed": [{"symbol": "CCC", "debt_ratio": 40, "liquidity_ratio": 5, "reason": "debt 40% > 33%"}],
              "unknown": ["ZZZ"]}
    plan = hr.apply_rescreen(result, dry_run=False, path=uni)
    assert plan["removed"] == ["CCC"]
    assert json.loads(uni.read_text())["symbols"] == ["AAA", "BBB"]   # CCC pruned, others kept
    excluded = json.loads((tmp_path / "excluded.json").read_text())
    assert excluded["excluded"][0]["symbol"] == "CCC"


def test_screen_financials_passes_threshold_logic(monkeypatch):
    # Drive screen_financials_yf through a stubbed yfinance Ticker. AAOIFI denominator =
    # total assets, so the stub provides totalAssets (=1000); debt 400/1000 = 40% > 30%.
    class _T:
        def __init__(self, sym): self.info = {"marketCap": 1000.0, "totalDebt": 400.0,
                                              "totalCash": 50.0, "totalAssets": 1000.0}
    import types
    fake_yf = types.SimpleNamespace(Ticker=_T)
    monkeypatch.setitem(__import__("sys").modules, "yfinance", fake_yf)
    r = hr.screen_financials_yf("XYZ")
    assert r["debt_ratio"] == 40.0 and r["debt_pass"] is False  # 400/1000 = 40% > 30%
    assert r["liquidity_ratio"] == 5.0 and r["liquidity_pass"] is True
    assert r["passes"] is False
