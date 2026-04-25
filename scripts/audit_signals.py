"""Audit recent consensus signal drift against an expected baseline."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.db.database import SessionLocal, init_db
from app.db.models import ConsensusLog
from app.services.notify import notify


COLLAPSED_BUCKETS = {"BUY", "SELL", "NEUTRAL"}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def canonicalize_verdict(verdict: str) -> str:
    verdict = str(verdict or "").strip().upper()
    if "BUY" in verdict:
        return "BUY"
    if "SELL" in verdict:
        return "SELL"
    return "NEUTRAL"


def detect_drift(actual: dict[str, float], expected: dict[str, float], threshold: float = 15.0) -> dict[str, float]:
    deltas = {}
    for key in sorted(set(actual) | set(expected)):
        deltas[key] = round(actual.get(key, 0.0) - expected.get(key, 0.0), 2)
    return {key: delta for key, delta in deltas.items() if abs(delta) > threshold}


def recent_signal_distribution(
    days: int = 30,
    profile: str | None = None,
    *,
    collapsed: bool = False,
) -> dict[str, float]:
    db = SessionLocal()
    try:
        cutoff = _utc_now() - timedelta(days=days)
        query = db.query(ConsensusLog).filter(ConsensusLog.created_at >= cutoff)
        if profile:
            query = query.filter(ConsensusLog.profile == profile)
        rows = query.all()
    finally:
        db.close()

    counts = Counter(
        canonicalize_verdict(row.verdict) if collapsed else row.verdict
        for row in rows
    )
    total = max(sum(counts.values()), 1)
    return {signal: round(count / total * 100.0, 2) for signal, count in sorted(counts.items())}


def _expected_uses_collapsed_buckets(expected: dict[str, float]) -> bool:
    return bool(expected) and set(expected).issubset(COLLAPSED_BUCKETS)


def audit(expected: dict[str, dict[str, float]] | None = None, *, days: int = 30, threshold: float = 15.0) -> dict:
    expected = expected or {}
    profiles = sorted(expected) if expected else ["base", "momentum", "reversion", "ml"]
    actual = {}
    drift = {}

    for profile in profiles:
        expected_profile = expected.get(profile, {})
        collapsed = _expected_uses_collapsed_buckets(expected_profile)
        actual_profile = recent_signal_distribution(days=days, profile=profile, collapsed=collapsed)
        actual[profile] = actual_profile
        diff = detect_drift(actual_profile, expected_profile, threshold=threshold)
        if diff:
            drift[profile] = diff

    if drift:
        notify(
            "critical_error",
            dedup_key="signal-drift",
            message=f"Signal drift detected: {drift}",
        )
    return {"actual": actual, "expected": expected, "drift": drift}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected", default="calibration/signal_expectation.json")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--threshold", type=float, default=15.0)
    args = parser.parse_args()
    init_db()

    expected_path = Path(args.expected)
    expected = json.loads(expected_path.read_text(encoding="utf-8")) if expected_path.exists() else {}
    result = audit(expected, days=args.days, threshold=args.threshold)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
