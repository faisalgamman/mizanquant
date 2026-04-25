"""Guardrail against naive datetime helpers in service code."""

from __future__ import annotations

import re
from pathlib import Path


BAD_PATTERNS = (
    r"datetime\.utcnow\(",
    r"datetime\.now\(\s*\)",
    r"datetime\.utcfromtimestamp\(",
)


def test_service_code_has_no_naive_datetime_calls():
    root = Path(__file__).resolve().parent.parent
    targets = list((root / "app" / "services").rglob("*.py"))
    offenders = []

    for path in targets:
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern in BAD_PATTERNS:
            if re.search(pattern, text):
                offenders.append((path.name, pattern))

    assert offenders == []
