"""Persisted scan-alert rules + fired events (research/informational only).

SCOPE / SAFETY: alerts are purely informational — they never place an order or touch the
ledgers. Rules + a rolling event log live as JSON in CACHE_DIR (survives restarts), mirroring
gate_config/factor_weights. Evaluation diffs the CURRENT scan's qualifiers against the last-
seen set per rule, so an alert fires once when a name *enters* a condition (not every scan),
and never floods on the first run (no baseline yet → just records the baseline). Fail-safe:
every read/write is guarded and never raises.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time

logger = logging.getLogger("screener")

_LOCK = threading.Lock()
_MAX_EVENTS = 60

# Built-in default rule so the feature is useful out of the box.
DEFAULT_RULES = [
    {"id": "sb", "type": "new_strong_buy", "label": "سهم جديد يدخل «شراء قوي» (درجة ≥ 72)"},
]


def _path() -> str:
    base = os.environ.get("CACHE_DIR") or "/data"
    try:
        os.makedirs(base, exist_ok=True)
    except Exception:
        base = "."
    return os.path.join(base, "scan_alerts.json")


def _read() -> dict:
    try:
        with open(_path(), "r", encoding="utf-8") as fh:
            return json.load(fh) or {}
    except Exception:
        return {}


def _write(d: dict) -> None:
    try:
        with open(_path(), "w", encoding="utf-8") as fh:
            json.dump(d, fh, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.debug("alerts_store write failed: %s", e)


def get_rules() -> list:
    r = _read().get("rules")
    return r if isinstance(r, list) else list(DEFAULT_RULES)


def add_rule(rule: dict) -> list:
    """Add/replace a rule. Supported types: new_strong_buy, score_above (needs `threshold`)."""
    if not isinstance(rule, dict) or rule.get("type") not in ("new_strong_buy", "score_above"):
        return get_rules()
    with _LOCK:
        d = _read()
        rules = d.get("rules") if isinstance(d.get("rules"), list) else list(DEFAULT_RULES)
        rid = str(rule.get("id") or ("r" + str(int(time.time() * 1000))))
        clean = {"id": rid, "type": rule["type"]}
        if rule["type"] == "score_above":
            try:
                clean["threshold"] = max(0, min(100, int(rule.get("threshold", 80))))
            except (TypeError, ValueError):
                clean["threshold"] = 80
            clean["label"] = rule.get("label") or f"درجة سهم ≥ {clean['threshold']}"
        else:
            clean["label"] = rule.get("label") or "سهم جديد يدخل «شراء قوي» (درجة ≥ 72)"
        rules = [r for r in rules if r.get("id") != rid] + [clean]
        d["rules"] = rules
        _write(d)
        return rules


def remove_rule(rid) -> list:
    with _LOCK:
        d = _read()
        rules = [r for r in (d.get("rules") if isinstance(d.get("rules"), list) else list(DEFAULT_RULES)) if r.get("id") != rid]
        d["rules"] = rules
        _write(d)
        return rules


def mark_seen() -> dict:
    with _LOCK:
        d = _read()
        d["seen_ts"] = time.time()
        _write(d)
        return {"seen_ts": d["seen_ts"]}


def _qualifiers(rule: dict, results: list) -> set:
    t = rule.get("type")
    thr = 72 if t == "new_strong_buy" else float(rule.get("threshold", 80))
    out = set()
    for r in results:
        sym = r.get("symbol")
        if sym and (r.get("composite_score") or 0) >= thr:
            out.add(sym)
    return out


def evaluate(results: list) -> list:
    """Diff current qualifiers vs the last-seen set per rule; append events for NEW entries.
    Returns the newly-fired events. No baseline yet ⇒ records the baseline, fires nothing."""
    if not results:
        return []
    with _LOCK:
        d = _read()
        rules = d.get("rules") if isinstance(d.get("rules"), list) else list(DEFAULT_RULES)
        last = d.get("last_symbols") or {}
        events = d.get("events") or []
        by_sym = {r.get("symbol"): r for r in results if r.get("symbol")}
        new_events = []
        for rule in rules:
            rid = rule.get("id")
            cur = _qualifiers(rule, results)
            had_baseline = rid in last
            prev = set(last.get(rid) or [])
            if had_baseline:
                for sym in sorted(cur - prev):
                    r = by_sym.get(sym, {})
                    new_events.append({
                        "ts": time.time(), "rule_id": rid, "rule": rule.get("label", rid),
                        "symbol": sym, "score": round(r.get("composite_score") or 0),
                        "sector": r.get("sector"), "price": r.get("price"),
                    })
            last[rid] = sorted(cur)
        if new_events:
            events = (new_events + events)[:_MAX_EVENTS]
        d["last_symbols"] = last
        d["events"] = events
        _write(d)
        return new_events


def get_state(results: list | None = None) -> dict:
    """Current rules + recent events + unread count. If `results` is given, evaluate first."""
    if results:
        evaluate(results)
    d = _read()
    events = d.get("events") or []
    seen_ts = d.get("seen_ts") or 0
    unread = sum(1 for e in events if (e.get("ts") or 0) > seen_ts)
    return {
        "rules": d.get("rules") if isinstance(d.get("rules"), list) else list(DEFAULT_RULES),
        "events": events[:30],
        "unread": unread,
        "seen_ts": seen_ts,
    }


__all__ = ["get_rules", "add_rule", "remove_rule", "mark_seen", "evaluate", "get_state"]
