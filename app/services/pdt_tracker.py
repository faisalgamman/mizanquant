"""Pattern day-trade tracking shared by risk management and guards."""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

MAX_DAY_TRADES = 2
PDT_WINDOW_DAYS = 5

_pdt_lock = threading.Lock()
_day_trades: list[datetime] = []


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def record_day_trade() -> None:
    with _pdt_lock:
        _day_trades.append(_utc_now())


def get_day_trade_count() -> int:
    with _pdt_lock:
        cutoff = _utc_now() - timedelta(days=7)
        normalized = []
        for dt in _day_trades:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            normalized.append(dt)
        _day_trades[:] = [dt for dt in normalized if dt > cutoff]
        return len(_day_trades)


def can_day_trade() -> bool:
    return get_day_trade_count() < MAX_DAY_TRADES
