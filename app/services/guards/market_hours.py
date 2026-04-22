"""Guard enforcing regular US market hours."""

from __future__ import annotations

from datetime import datetime, timezone

from app.services.guards.base import GuardContext, GuardResult


def is_market_open(now: datetime | None = None) -> bool:
    try:
        from zoneinfo import ZoneInfo
        eastern = ZoneInfo("America/New_York")
        now = now or datetime.now(eastern)
    except Exception:
        # Fallback for environments missing tzdata; keep tests and local runs alive.
        now = now or datetime.now(timezone.utc)
    if now.weekday() >= 5:
        return False
    market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now.replace(hour=16, minute=0, second=0, microsecond=0)
    return market_open <= now <= market_close


def guard(ctx: GuardContext) -> GuardResult:
    if is_market_open():
        return GuardResult("market_hours", True, True, "Within regular market hours.", "MARKET_OPEN")
    return GuardResult(
        "market_hours",
        False,
        True,
        "Market is closed — trades only execute during NYSE hours (09:30-16:00 ET, Mon-Fri).",
        "MARKET_CLOSED",
    )
