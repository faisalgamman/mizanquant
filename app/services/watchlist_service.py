"""Watchlist service — save/load user watchlist to JSON file."""
from __future__ import annotations

import json
import logging
import os
import threading

logger = logging.getLogger("screener")

_WATCHLIST_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "app", ".cache", "watchlist.json",
)
_lock = threading.Lock()


def _ensure_dir() -> None:
    os.makedirs(os.path.dirname(_WATCHLIST_FILE), exist_ok=True)


def get_watchlist() -> list[str]:
    """Return list of symbols in watchlist."""
    _ensure_dir()
    if not os.path.exists(_WATCHLIST_FILE):
        return []
    try:
        with _lock:
            data = json.loads(open(_WATCHLIST_FILE, encoding="utf-8").read())
        return data if isinstance(data, list) else []
    except Exception:
        return []


def set_watchlist(symbols: list[str]) -> list[str]:
    """Replace entire watchlist. Returns saved list."""
    clean = [s.strip().upper() for s in symbols if s.strip()]
    clean = list(dict.fromkeys(clean))  # dedup preserving order
    _ensure_dir()
    try:
        with _lock:
            with open(_WATCHLIST_FILE, "w", encoding="utf-8") as f:
                f.write(json.dumps(clean))
    except Exception as e:
        logger.error("Failed to save watchlist: %s", e)
    return clean


def add_symbol(symbol: str) -> list[str]:
    """Add a symbol to watchlist. Returns updated list."""
    current = get_watchlist()
    sym = symbol.strip().upper()
    if sym and sym not in current:
        current.append(sym)
        set_watchlist(current)
    return current


def remove_symbol(symbol: str) -> list[str]:
    """Remove a symbol from watchlist. Returns updated list."""
    current = get_watchlist()
    sym = symbol.strip().upper()
    if sym in current:
        current.remove(sym)
        set_watchlist(current)
    return current
