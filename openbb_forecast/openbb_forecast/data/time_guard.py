"""Time guards for leak-free signal generation."""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    from backports.zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")


def signal_cutoff(now: datetime | None = None) -> datetime:
    """Return the latest timestamp safe for signal features.

    During market hours: use yesterday's close.
    After market close: use today's close.
    Before market open: use yesterday's close.
    """
    now = now.astimezone(NY) if now is not None else datetime.now(NY)
    session_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    session_close = now.replace(hour=16, minute=0, second=0, microsecond=0)

    if now.weekday() >= 5:
        days_back = now.weekday() - 4
        safe_day = (now - timedelta(days=days_back)).date()
        return datetime.combine(safe_day, time(16, 0), tzinfo=NY)

    if now < session_open or session_open <= now <= session_close:
        safe_day = now.date() - timedelta(days=1)
        while safe_day.weekday() >= 5:
            safe_day -= timedelta(days=1)
        return datetime.combine(safe_day, time(16, 0), tzinfo=NY)

    return datetime.combine(now.date(), time(16, 0), tzinfo=NY)
