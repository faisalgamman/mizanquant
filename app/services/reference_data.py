"""Cached reference data used by guards, validators, and scheduler jobs."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from app.config import STRATEGY_CONFIGS, settings

logger = logging.getLogger("screener")

DATA_DIR = Path("data")
TRADABLE_SYMBOLS_FILE = DATA_DIR / "tradable_symbols.json"
PROFILE_CACHE_FILE = DATA_DIR / "profile_cache.json"
EARNINGS_CACHE_FILE = DATA_DIR / "earnings_calendar.json"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _load_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Failed reading cache %s: %s", path, exc)
    return default


def _save_json(path: Path, payload: Any) -> None:
    _ensure_data_dir()
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _primary_alpaca_headers() -> dict[str, str]:
    if settings.ALPACA_API_KEY and settings.ALPACA_SECRET_KEY:
        return {
            "APCA-API-KEY-ID": settings.ALPACA_API_KEY,
            "APCA-API-SECRET-KEY": settings.ALPACA_SECRET_KEY,
        }
    for cfg in STRATEGY_CONFIGS.values():
        if cfg.alpaca_api_key and cfg.alpaca_secret_key:
            return {
                "APCA-API-KEY-ID": cfg.alpaca_api_key,
                "APCA-API-SECRET-KEY": cfg.alpaca_secret_key,
            }
    return {}


def get_tradable_symbols(force_refresh: bool = False) -> set[str]:
    payload = _load_json(TRADABLE_SYMBOLS_FILE, {})
    updated_at = payload.get("updated_at")
    stale = True
    if updated_at:
        try:
            stale = _utc_now() - datetime.fromisoformat(updated_at) > timedelta(hours=24)
        except ValueError:
            stale = True
    if force_refresh or stale or "symbols" not in payload:
        refreshed = refresh_tradable_assets()
        if refreshed:
            return refreshed
    return {str(sym).upper() for sym in payload.get("symbols", [])}


def refresh_tradable_assets() -> set[str]:
    headers = _primary_alpaca_headers()
    if not headers:
        return {str(sym).upper() for sym in _load_json(TRADABLE_SYMBOLS_FILE, {}).get("symbols", [])}
    url = f"{settings.ALPACA_BASE_URL.rstrip('/').removesuffix('/v2')}/v2/assets"
    params = {"status": "active", "asset_class": "us_equity"}
    try:
        with httpx.Client(timeout=20) as client:
            resp = client.get(url, headers=headers, params=params)
            resp.raise_for_status()
            data = resp.json()
        symbols = sorted({
            str(item.get("symbol", "")).upper()
            for item in data
            if item.get("tradable") and item.get("status") == "active"
        })
        _save_json(
            TRADABLE_SYMBOLS_FILE,
            {"updated_at": _utc_now().isoformat(), "symbols": symbols},
        )
        return set(symbols)
    except Exception as exc:
        logger.warning("Failed to refresh tradable assets cache: %s", exc)
        return {str(sym).upper() for sym in _load_json(TRADABLE_SYMBOLS_FILE, {}).get("symbols", [])}


def is_symbol_tradable(symbol: str) -> bool | None:
    symbols = get_tradable_symbols()
    if not symbols:
        return None
    return symbol.upper() in symbols


def _fmp_get_profile(symbol: str) -> dict | None:
    if not settings.FMP_API_KEY:
        return None
    url = "https://financialmodelingprep.com/stable/profile"
    params = {"symbol": symbol.upper(), "apikey": settings.FMP_API_KEY}
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
        if isinstance(data, list) and data:
            return data[0]
        return None
    except Exception as exc:
        logger.warning("Failed to fetch FMP profile for %s: %s", symbol, exc)
        return None


def get_symbol_sector(symbol: str) -> str:
    symbol = symbol.upper()
    cache = _load_json(PROFILE_CACHE_FILE, {"profiles": {}})
    profiles = cache.setdefault("profiles", {})
    entry = profiles.get(symbol)
    if entry:
        return str(entry.get("sector", "") or "")
    profile = _fmp_get_profile(symbol)
    if not profile:
        return ""
    sector = str(profile.get("sector", "") or "")
    profiles[symbol] = {
        "sector": sector,
        "industry": str(profile.get("industry", "") or ""),
        "updated_at": _utc_now().isoformat(),
    }
    cache["updated_at"] = _utc_now().isoformat()
    _save_json(PROFILE_CACHE_FILE, cache)
    return sector


def _business_days_between(left: date, right: date) -> int:
    if left == right:
        return 0
    sign = 1 if right > left else -1
    start, end = (left, right) if sign == 1 else (right, left)
    days = 0
    cur = start
    while cur < end:
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            days += 1
    return days * sign


def refresh_earnings_calendar(symbols: list[str] | None = None) -> dict[str, str]:
    symbols = [s.upper() for s in (symbols or []) if s]
    if not settings.FMP_API_KEY:
        return _load_json(EARNINGS_CACHE_FILE, {}).get("earnings", {})
    today = _utc_now().date()
    params = {
        "from": today.isoformat(),
        "to": (today + timedelta(days=30)).isoformat(),
        "apikey": settings.FMP_API_KEY,
    }
    url = "https://financialmodelingprep.com/stable/earning-calendar"
    try:
        with httpx.Client(timeout=20) as client:
            resp = client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
        earnings: dict[str, str] = {}
        for item in data if isinstance(data, list) else []:
            sym = str(item.get("symbol", "")).upper()
            if symbols and sym not in symbols:
                continue
            dt = item.get("date") or item.get("earningDate")
            if sym and dt:
                earnings[sym] = str(dt)[:10]
        _save_json(
            EARNINGS_CACHE_FILE,
            {"updated_at": _utc_now().isoformat(), "earnings": earnings},
        )
        return earnings
    except Exception as exc:
        logger.warning("Failed to refresh earnings calendar: %s", exc)
        return _load_json(EARNINGS_CACHE_FILE, {}).get("earnings", {})


def get_earnings_date(symbol: str) -> date | None:
    symbol = symbol.upper()
    payload = _load_json(EARNINGS_CACHE_FILE, {"earnings": {}})
    updated_at = payload.get("updated_at")
    stale = True
    if updated_at:
        try:
            stale = _utc_now() - datetime.fromisoformat(updated_at) > timedelta(hours=24)
        except ValueError:
            stale = True
    earnings = payload.get("earnings", {})
    if stale or symbol not in earnings:
        earnings = refresh_earnings_calendar([symbol])
    raw = earnings.get(symbol)
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def business_days_until_earnings(symbol: str, today: date | None = None) -> int | None:
    earnings_date = get_earnings_date(symbol)
    if earnings_date is None:
        return None
    today = today or _utc_now().date()
    return _business_days_between(today, earnings_date)
