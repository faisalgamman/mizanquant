"""Market data fetching with rate limiting and retry logic.

Primary: Alpaca Market Data API v2 (free IEX tier)
Fallback: yfinance

Rate limiting strategy:
- Global semaphore limits concurrent Alpaca requests (default: 3)
- 0.25s minimum interval between requests
- Exponential backoff retry on 429 (Too Many Requests)
- After 3 retries, falls back to yfinance
"""

import os
import time
import logging
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import numpy as np

logger = logging.getLogger("screener")

# ---------------------------------------------------------------------------
# In-memory data cache with TTL
# ---------------------------------------------------------------------------
_data_cache = {}
_data_cache_ts = {}
# P2.4 — dynamic TTL: short in market hours, long outside (data doesn't change)
_DATA_CACHE_TTL_MARKET_OPEN = 60     # 1 minute — frequent intraday updates
_DATA_CACHE_TTL_MARKET_CLOSED = 1800  # 30 minutes — bars are final
_DATA_CACHE_TTL = _DATA_CACHE_TTL_MARKET_CLOSED  # backward-compat for old callers
_YF_CACHE_CONFIGURED = False
_YF_SESSION_INITIALIZED = False


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _session_bucket() -> str:
    try:
        from zoneinfo import ZoneInfo

        eastern = ZoneInfo("America/New_York")
        return _utc_now().astimezone(eastern).date().isoformat()
    except Exception:
        return _utc_now().date().isoformat()


def _cache_ttl_seconds() -> int:
    """Dynamic TTL: short in market hours (data changes), long outside (data stable)."""
    try:
        from app.services.guards.market_hours import is_market_open

        return _DATA_CACHE_TTL_MARKET_OPEN if is_market_open() else _DATA_CACHE_TTL_MARKET_CLOSED
    except Exception:
        return _DATA_CACHE_TTL_MARKET_CLOSED


_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
]
_ua_index = 0


def _rotate_user_agent() -> str:
    global _ua_index
    _ua_index = (_ua_index + 1) % len(_USER_AGENTS)
    return _USER_AGENTS[_ua_index]


def _init_yfinance_session(yf_module) -> None:
    global _YF_SESSION_INITIALIZED
    if _YF_SESSION_INITIALIZED:
        return
    import requests as _requests
    from http.cookiejar import MozillaCookieJar

    session = _requests.Session()
    session.headers.update({"User-Agent": _USER_AGENTS[0]})
    jar_path = Path("data") / "yfinance_cookies.txt"
    jar_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        cj = MozillaCookieJar(str(jar_path))
        if jar_path.exists():
            cj.load(ignore_discard=True, ignore_expires=True)
        session.cookies = cj
    except Exception:
        pass
    try:
        yf_module.shared._session = session
    except Exception:
        pass
    _YF_SESSION_INITIALIZED = True


def _configure_yfinance_cache(yf_module) -> None:
    global _YF_CACHE_CONFIGURED
    if _YF_CACHE_CONFIGURED:
        return
    cache_dir = Path("data") / "yfinance_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    try:
        yf_module.set_tz_cache_location(str(cache_dir))
    except Exception:
        logger.debug("Could not set yfinance timezone cache location", exc_info=True)
    _init_yfinance_session(yf_module)
    _YF_CACHE_CONFIGURED = True


def _data_cache_get(key):
    if time.time() - _data_cache_ts.get(key, 0) < _cache_ttl_seconds():
        return _data_cache.get(key)
    return None


def _data_cache_set(key, value):
    _data_cache[key] = value
    _data_cache_ts[key] = time.time()


def clear_market_data_cache():
    _data_cache.clear()
    _data_cache_ts.clear()


def _dedupe_bars(bars, key_fn):
    seen = set()
    out = []
    for bar in bars:
        key = key_fn(bar)
        if key in seen:
            continue
        seen.add(key)
        out.append(bar)
    return out


def _backoff_next(attempt: int = 0) -> float:
    return min(30.0, 1.0 * (2 ** attempt))


# ---------------------------------------------------------------------------
# Alpaca rate limiting
# ---------------------------------------------------------------------------
# Free IEX tier: 200 requests/min. With 5 workers, we need ~0.25s spacing.
_alpaca_semaphore = threading.Semaphore(3)  # max 3 concurrent Alpaca requests
_alpaca_last_call = 0.0
_alpaca_lock = threading.Lock()
_ALPACA_MIN_INTERVAL = 0.25  # seconds between requests


def _alpaca_rate_limit():
    """Enforce minimum interval between Alpaca API calls."""
    global _alpaca_last_call
    with _alpaca_lock:
        elapsed = time.time() - _alpaca_last_call
        if elapsed < _ALPACA_MIN_INTERVAL:
            time.sleep(_ALPACA_MIN_INTERVAL - elapsed)
        _alpaca_last_call = time.time()


def _alpaca_data_creds():
    """Return (key, secret) for the Alpaca MARKET-DATA API (data.alpaca.markets).

    Prefers dedicated ALPACA_DATA_KEY / ALPACA_DATA_SECRET (the data-feed
    entitlement) and falls back to the trading key only when those are unset.
    The data endpoint frequently requires different credentials than the
    trading API — authenticating to it with the trading key returns 401.
    """
    from app.config import settings
    key = getattr(settings, "ALPACA_DATA_KEY", "") or settings.ALPACA_API_KEY
    secret = getattr(settings, "ALPACA_DATA_SECRET", "") or settings.ALPACA_SECRET_KEY
    return key, secret


def _is_paper() -> bool:
    """True when running in paper-trading mode."""
    from app.config import settings
    return getattr(settings, "ALPACA_PAPER", True)


def _data_bars_url(symbols: list[str]) -> tuple[str, dict]:
    """Return (url, params) for a bars request.
    
    Paper trading uses single-symbol /v2/stocks/{s}/bars because the
    multi-symbol batch endpoint is DATA-plan only (data.alpaca.markets).
    Live with DATA keys uses the batch endpoint for efficiency.
    """
    if _is_paper():
        # Single-symbol request — paper-api doesn't support /v2/stocks/bars
        url = "https://paper-api.alpaca.markets/v2/stocks/" + symbols[0] + "/bars"
        return url, {}
    else:
        # Batch endpoint for live data subscribers
        url = "https://data.alpaca.markets/v2/stocks/bars"
        return url, {"symbols": ",".join(symbols)}



# ---------------------------------------------------------------------------
# Symbol validation
# ---------------------------------------------------------------------------
# Known bad symbols that cause issues (delisted, corrupted, etc.)
_BAD_SYMBOLS = {"LIY", "LIYY", ""}


def _validate_symbol(symbol: str) -> str:
    """Clean and validate symbol. Returns cleaned symbol or empty string if invalid."""
    if not symbol or not isinstance(symbol, str):
        return ""
    cleaned = symbol.upper().strip()
    if cleaned in _BAD_SYMBOLS:
        logger.warning(f"Blocked bad symbol: {symbol!r}")
        return ""
    # Basic format check: 1-6 uppercase letters, optionally with dots (BRK.B)
    import re
    if not re.match(r'^[A-Z]{1,6}(\.[A-Z])?$', cleaned):
        logger.warning(f"Invalid symbol format: {symbol!r}")
        return ""
    return cleaned


def fetch_alpaca(symbol, period="2y", start=None, end=None):
    """Fetch historical bars from Alpaca Market Data API v2.

    Features:
    - Rate limited (semaphore + minimum interval)
    - Retry with exponential backoff on 429
    - Max 3 retries before giving up
    """
    if _alpaca_breaker.is_open():
        logger.warning("Alpaca circuit breaker OPEN — skipping %s", symbol)
        return None

    symbol = _validate_symbol(symbol)
    if not symbol:
        return None

    # ── Paper trading: use yfinance (Alpaca paper lacks market data) ──
    if _is_paper():
        return fetch_yf(symbol, period, start, end)

    try:
        _dk, _ds = _alpaca_data_creds()
        if not _dk or not _ds:
            return None

        import httpx

        headers = {
            "APCA-API-KEY-ID": _dk,
            "APCA-API-SECRET-KEY": _ds,
        }

        # Convert period to start/end dates
        if not start or not end:
            end_dt = _utc_now()
            period_map = {"1y": 365, "2y": 730, "5y": 1825}
            days = period_map.get(period, 730)
            start_dt = end_dt - timedelta(days=days)
            start = start_dt.strftime("%Y-%m-%d")
            end = end_dt.strftime("%Y-%m-%d")

        url, _extra = _data_bars_url([symbol])
        if _extra:
            params.update(_extra)
        params = {
            "symbols": symbol,
            "timeframe": "1Day",
            "start": start,
            "end": end,
            "limit": 10000,
            "adjustment": "split",
        }
        # Omit feed param — Alpaca auto-selects best available feed.
        # "iex" caused 404 on paper accounts for 300+ symbols including NVDA, AMD.

        max_retries = 3
        retry_delay = 2.0  # initial backoff seconds

        for attempt in range(max_retries):
            # Acquire semaphore (limits concurrent requests)
            _alpaca_semaphore.acquire()
            try:
                # Enforce minimum interval
                _alpaca_rate_limit()

                with httpx.Client(timeout=30) as client:
                    all_bars = []
                    next_page = None
                    page_retry = 0

                    while True:
                        page_params = dict(params)
                        if next_page:
                            page_params["page_token"] = next_page
                        resp = client.get(url, headers=headers, params=page_params)

                        if resp.status_code == 429:
                            wait_time = _backoff_next(page_retry)
                            logger.warning(
                                f"Alpaca 429 for {symbol} page={next_page or 'first'} "
                                f"(attempt {page_retry + 1}), waiting {wait_time:.1f}s"
                            )
                            time.sleep(wait_time)
                            page_retry += 1
                            if page_retry >= max_retries:
                                break
                            continue

                        page_retry = 0
                        if resp.status_code in (401, 403):
                            body = resp.text
                            logger.error(
                                "Alpaca auth error for %s: HTTP %d — %s. "
                                "ALPACA_DATA_KEY/ALPACA_DATA_SECRET likely missing or wrong. "
                                "Data keys must be generated from Alpaca Dashboard (separate from trading keys).",
                                symbol, resp.status_code, body[:500],
                            )
                        resp.raise_for_status()
                        data = resp.json()

                        bars = data.get("bars", {}).get(symbol, [])
                        all_bars.extend(bars)

                        next_page = data.get("next_page_token")
                        if not next_page:
                            if not all_bars:
                                return None
                            all_bars = _dedupe_bars(all_bars, key_fn=lambda b: b.get("t"))
                            df = pd.DataFrame(all_bars)
                            df = df.rename(columns={
                                "t": "date", "o": "open", "h": "high",
                                "l": "low", "c": "close", "v": "volume",
                            })
                            df["date"] = pd.to_datetime(df["date"], utc=True)
                            df = df[["date", "open", "high", "low", "close", "volume"]].sort_values("date").reset_index(drop=True)
                            min_rows = 40 if start and end else 200
                            return df if len(df) >= min_rows else None
                    continue

            finally:
                _alpaca_semaphore.release()

        # All retries exhausted
        logger.error(f"Alpaca {symbol}: all {max_retries} retries failed (429)")
        return None

    except Exception as e:
        logger.error(f"Alpaca {symbol}: {e}")
        return None


def _bars_list_to_df(symbol: str, bars: list) -> "pd.DataFrame | None":
    """Convert a raw Alpaca bars list to a clean DataFrame. Shared by single + batch."""
    if not bars:
        return None
    bars = _dedupe_bars(bars, key_fn=lambda b: b.get("t"))
    df = pd.DataFrame(bars)
    df = df.rename(columns={"t": "date", "o": "open", "h": "high",
                             "l": "low",  "c": "close", "v": "volume"})
    df["date"] = pd.to_datetime(df["date"], utc=True)
    df = df[["date", "open", "high", "low", "close", "volume"]].sort_values("date").reset_index(drop=True)
    return df if len(df) >= 40 else None


def _fetch_yf_batch(symbols: list, period: str = "2y") -> dict:
    """Fetch daily bars via Yahoo Finance for paper/offline mode.

    Returns {symbol: DataFrame} for symbols with >= 40 rows.
    """
    try:
        import yfinance as yf
    except ImportError:
        logger.warning("yfinance not installed — cannot fetch data")
        return {}

    end_dt = _utc_now()
    days = {"1y": 365, "2y": 730, "5y": 1825}.get(period, 730)
    start_dt = end_dt - timedelta(days=days)

    results = {}
    for symbol in symbols:
        sym = _validate_symbol(symbol)
        if not sym:
            continue
        try:
            ticker = yf.Ticker(sym)
            df = ticker.history(start=start_dt, end=end_dt)
            if df is not None and len(df) >= 40:
                # Normalize columns to match Alpaca format
                df = df.rename(columns={
                    "Open": "o", "High": "h", "Low": "l",
                    "Close": "c", "Volume": "v"
                })
                df.index.name = "t"
                results[sym] = df
        except Exception:
            pass  # skip failed symbols silently
    return results



def fetch_alpaca_batch(symbols: list, period: str = "2y") -> dict:
    """Fetch daily bars for multiple symbols in one Alpaca API call (batch mode).

    Alpaca's /v2/stocks/bars already accepts symbols as a comma-separated list
    (up to ~100). This reduces 650 individual round-trips to ~7 batched requests,
    virtually eliminating the 429 storm during screener scans.

    Returns:
        {symbol: DataFrame}  — only symbols with ≥40 rows are included.
    """
    if _alpaca_breaker.is_open():
        logger.warning("Alpaca circuit breaker OPEN — skipping batch of %d symbols", len(symbols))
        return {}

    try:
        _dk, _ds = _alpaca_data_creds()
        if not _dk or not _ds:
            return {}
    except Exception:
        return {}

    # ── Yahoo Finance fallback for paper trading ──────────────────────
    # Alpaca paper accounts don't include market data (v2/stocks/*/bars
    # returns 401). For paper mode, use yfinance which is free and
    # reliable enough for USX V4 filtering.
    if _is_paper():
        return _fetch_yf_batch(symbols, period)
    # ──────────────────────────────────────────────────────────────────

    try:
        import httpx
    except ImportError:
        return {}

    headers = {
        "APCA-API-KEY-ID":     _dk,
        "APCA-API-SECRET-KEY": _ds,
    }

    end_dt   = _utc_now()
    days     = {"1y": 365, "2y": 730, "5y": 1825}.get(period, 730)
    start_dt = end_dt - timedelta(days=days)
    start_s  = start_dt.strftime("%Y-%m-%d")
    end_s    = end_dt.strftime("%Y-%m-%d")

    results: dict = {}
    chunk_size = 100  # Alpaca accepts <= 100 symbols per request
    # Batch endpoint URL (data.alpaca.markets for live, paper uses yfinance above)
    batch_url = "https://data.alpaca.markets/v2/stocks/bars"

    for i in range(0, len(symbols), chunk_size):
        batch = symbols[i:i + chunk_size]
        params = {
            "symbols":    ",".join(batch),
            "timeframe":  "1Day",
            "start":      start_s,
            "end":        end_s,
            "limit":      10000,
            "adjustment": "split",
        }
        # Omit feed param — Alpaca auto-selects best available feed.

        # Retry loop (batch-level, same backoff as single-symbol fetch)
        for attempt in range(3):
            _alpaca_semaphore.acquire()
            try:
                _alpaca_rate_limit()
                all_bars: dict[str, list] = {s: [] for s in batch}
                next_page = None

                while True:
                    page_params = dict(params)
                    if next_page:
                        page_params["page_token"] = next_page
                    with httpx.Client(timeout=45) as client:
                        resp = client.get(batch_url, headers=headers, params=page_params)

                    if resp.status_code == 429:
                        wait = _backoff_next(attempt)
                        logger.warning("Alpaca batch 429 (chunk %d–%d, attempt %d), waiting %.1fs",
                                       i, i + len(batch), attempt + 1, wait)
                        time.sleep(wait)
                        break  # retry the whole chunk

                    if resp.status_code in (401, 403):
                        body = resp.text
                        logger.error(
                            "Alpaca batch auth error (chunk %d–%d): HTTP %d — %s. "
                            "ALPACA_DATA_KEY/ALPACA_DATA_SECRET likely missing or wrong. "
                            "Generate separate data-feed keys from Alpaca Dashboard.",
                            i, i + len(batch), resp.status_code, body[:500],
                        )
                    resp.raise_for_status()
                    data = resp.json()
                    for sym, sym_bars in (data.get("bars") or {}).items():
                        all_bars.setdefault(sym, []).extend(sym_bars)

                    next_page = data.get("next_page_token")
                    if not next_page:
                        # Successful page exhaustion — convert to DataFrames
                        for sym, bar_list in all_bars.items():
                            df = _bars_list_to_df(sym, bar_list)
                            if df is not None:
                                results[sym] = df
                        break
                else:
                    continue  # 429 → retry
                break  # success

            except Exception as exc:
                logger.warning("Alpaca batch chunk %d-%d error: %s", i, i + len(batch), exc)
                break
            finally:
                _alpaca_semaphore.release()

        # Small inter-chunk pause — stays well under 200 req/min
        time.sleep(0.4)

    logger.info("fetch_alpaca_batch: returned %d/%d symbols", len(results), len(symbols))
    return results


def fetch_alpaca_intraday(symbol, timeframe="15Min", days_back=10, start=None, end=None):
    """Fetch intraday bars from Alpaca Market Data API v2.

    Supports: 1Min, 5Min, 15Min, 1Hour timeframes.
    Free IEX tier provides intraday data for last 5+ years.

    Args:
        symbol: Stock ticker
        timeframe: Bar size (1Min, 5Min, 15Min, 1Hour)
        days_back: Number of calendar days to fetch (max ~30 for minute data)

    Returns:
        DataFrame with [date, open, high, low, close, volume] or None
    """
    symbol = _validate_symbol(symbol)
    if not symbol:
        return None

    cache_key = f"{symbol}|intraday|{timeframe}|{days_back}|{start}|{end}|{_session_bucket()}"
    cached = _data_cache_get(cache_key)
    if cached is not None:
        return cached

    # ── Paper trading: use yfinance ───────────────────────────────────
    if _is_paper():
        return fetch_yf(symbol, str(days_back) + "d", start, end)

    try:
        _dk, _ds = _alpaca_data_creds()
        if not _dk or not _ds:
            return None

        import httpx
        headers = {
            "APCA-API-KEY-ID": _dk,
            "APCA-API-SECRET-KEY": _ds,
        }

        end_dt = _utc_now() if end is None else pd.Timestamp(end).to_pydatetime()
        start_dt = (end_dt - timedelta(days=days_back)) if start is None else pd.Timestamp(start).to_pydatetime()

        url, _extra = _data_bars_url([symbol])
        if _extra:
            params.update(_extra)
        params = {
            "symbols": symbol,
            "timeframe": timeframe,
            "start": start_dt.strftime("%Y-%m-%dT00:00:00Z"),
            "end": end_dt.strftime("%Y-%m-%dT23:59:59Z"),
            "limit": 10000,
            "adjustment": "split",
        }
        # Omit feed param — Alpaca auto-selects best available feed.

        _alpaca_semaphore.acquire()
        try:
            _alpaca_rate_limit()
            with httpx.Client(timeout=30) as client:
                all_bars = []
                next_page = None
                page_retry = 0

                while True:
                    page_params = dict(params)
                    if next_page:
                        page_params["page_token"] = next_page
                    resp = client.get(url, headers=headers, params=page_params)

                    if resp.status_code == 429:
                        wait_time = _backoff_next(page_retry)
                        logger.warning(
                            f"Intraday 429 for {symbol} page={next_page or 'first'} "
                            f"(attempt {page_retry + 1}), waiting {wait_time:.1f}s"
                        )
                        time.sleep(wait_time)
                        page_retry += 1
                        if page_retry >= 5:
                            logger.error(f"Intraday max retries reached for {symbol}, aborting")
                            break
                        continue

                    page_retry = 0
                    resp.raise_for_status()
                    data = resp.json()
                    bars = data.get("bars", {}).get(symbol, [])
                    all_bars.extend(bars)

                    next_page = data.get("next_page_token")
                    if not next_page:
                        break

                if not all_bars:
                    return None

                all_bars = _dedupe_bars(all_bars, key_fn=lambda b: b.get("t"))
                df = pd.DataFrame(all_bars)
                df = df.rename(columns={
                    "t": "date", "o": "open", "h": "high",
                    "l": "low", "c": "close", "v": "volume",
                })
                df["date"] = pd.to_datetime(df["date"], utc=True)
                df = df[["date", "open", "high", "low", "close", "volume"]].sort_values("date").reset_index(drop=True)

                if len(df) > 0:
                    _data_cache_set(cache_key, df)
                    return df
                return None
        finally:
            _alpaca_semaphore.release()

    except Exception as e:
        logger.error(f"Alpaca intraday {symbol}: {e}")
        return None


def fetch_yf(symbol, period="2y", start=None, end=None):
    """Fetch historical data from yfinance (fallback)."""
    if _yfinance_breaker.is_open():
        logger.warning("yfinance circuit breaker OPEN — skipping %s", symbol)
        return None
    
    # Throttle check: if breaker is in THROTTLE mode, enforce delay
    _delay = _yfinance_breaker.throttle_delay()
    if _delay > 0:
        time.sleep(_delay)
    _yfinance_breaker.mark_request()

    symbol = _validate_symbol(symbol)
    if not symbol:
        return None

    import yfinance as yf
    _configure_yfinance_cache(yf)

    max_retries = 3
    for attempt in range(max_retries):
        try:
            t = yf.Ticker(symbol)
            if start and end:
                df = t.history(start=start, end=end, auto_adjust=True)
            else:
                df = t.history(period=period, auto_adjust=True)
            if df.empty:
                if attempt < max_retries - 1:
                    _yfinance_breaker.record_failure()
                    continue
                _yfinance_breaker.record_failure()
                return None

            df.columns = [c.lower() for c in df.columns]
            df = df.reset_index()
            df.columns = [c.lower() for c in df.columns]
            for col in ["dividends", "stock splits", "capital gains"]:
                df.drop(columns=[col], errors="ignore", inplace=True)
            min_rows = 40
            result = df if len(df) >= min_rows else None
            if result is not None:
                _yfinance_breaker.record_success()
            else:
                _yfinance_breaker.record_failure()
            return result
        except Exception as e:
            estr = str(e)
            # Auto-blacklist delisted/invalid symbols so they're never retried
            if "delisted" in estr.lower() or "no data found" in estr.lower() or                "symbol may be" in estr.lower():
                _BAD_SYMBOLS.add(symbol)
                logger.warning("yfinance %s: auto-blacklisted (delisted/invalid) — %s", symbol, estr)
                return None
            is_auth = "401" in estr or "Unauthorized" in estr or "Invalid Crumb" in estr
            if is_auth and attempt < max_retries - 1:
                ua = _rotate_user_agent()
                logger.warning("yfinance %s: %s — rotating UA to %s (attempt %d/%d)",
                               symbol, estr, ua[:60], attempt + 2, max_retries)
                try:
                    yf.shared._session.headers.update({"User-Agent": ua})
                except Exception:
                    pass
                _backoff_next(attempt)
                continue
            logger.error(f"yfinance {symbol}: {e}")
            _yfinance_breaker.record_failure()
            return None

    _yfinance_breaker.record_failure()
    return None


# ---------------------------------------------------------------------------
# Circuit breaker for external services
# ---------------------------------------------------------------------------


class CircuitBreaker:
    """Tracks failures with 3 states: closed → throttle → open.
    
    - closed:     normal operation, no delay.
    - throttle:   add delay between requests (slow down, don't skip).
    - open:       skip all requests until reset timeout.
    
    Progression:  5 consecutive failures → throttle → 10 more → open."""
    
    THROTTLE_DELAY = 1.0       # seconds between requests in throttle mode
    THROTTLE_MULTIPLIER = 1.5  # exponential backoff multiplier per failure

    def __init__(self, name: str, failure_threshold: int = 5, reset_timeout: float = 60.0):
        self.name = name
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self.failures = 0
        self.successes_since_throttle = 0
        self.last_failure_time = 0.0
        self.last_request_time = 0.0
        self.state = "closed"
        self._lock = threading.Lock()

    def record_failure(self) -> None:
        with self._lock:
            self.failures += 1
            self.last_failure_time = time.time()
            self.successes_since_throttle = 0
            if self.failures >= self.failure_threshold * 3:
                self.state = "open"
            elif self.failures >= self.failure_threshold:
                self.state = "throttle"

    def record_success(self) -> None:
        with self._lock:
            if self.state == "throttle":
                self.successes_since_throttle += 1
                # Back to closed after 5 consecutive successes in throttle mode
                if self.successes_since_throttle >= 5:
                    self.failures = 0
                    self.state = "closed"
                    self.successes_since_throttle = 0
            else:
                self.failures = 0
                self.state = "closed"
                self.successes_since_throttle = 0

    def is_open(self) -> bool:
        with self._lock:
            if self.state == "open":
                if time.time() - self.last_failure_time > self.reset_timeout:
                    self.state = "throttle"
                    self.failures = self.failure_threshold  # stay in throttle
                    return False
                return True
            return False

    def throttle_delay(self) -> float:
        """Return seconds to delay before next request. 0 = no delay needed."""
        with self._lock:
            if self.state == "throttle":
                elapsed = time.time() - self.last_request_time
                if elapsed < self.THROTTLE_DELAY:
                    return self.THROTTLE_DELAY - elapsed
            return 0.0

    def mark_request(self) -> None:
        """Call before each request to track timing."""
        with self._lock:
            self.last_request_time = time.time()


# Global circuit breakers
# yfinance: uses 3-stage (closed→throttle→open). Threshold 50: first 50 failures
# → THROTTLE (1s delay between requests). 150 failures → OPEN (skip all, 5 min).
# This prevents a handful of bad/delisted symbols from killing the entire scan.
_yfinance_breaker = CircuitBreaker("yfinance", failure_threshold=50, reset_timeout=300.0)
_alpaca_breaker   = CircuitBreaker("alpaca",   failure_threshold=10, reset_timeout=60.0)
_ibkr_breaker     = CircuitBreaker("ibkr",     failure_threshold=3,  reset_timeout=600.0)
# Tiingo: 403 = key blocked (permanent). Trip after 3 failures, stay open for 2 h.
# This short-circuits the per-symbol Tiingo attempt for the whole session.
_tiingo_breaker   = CircuitBreaker("tiingo",   failure_threshold=3,  reset_timeout=7200.0)

# Hard override: IBKR data is DISABLED until the gateway is healthy.
# Flip to True (and ensure IBKR_DATA_ENABLED=true) to re-enable.
# Imported from ibkr_adapter so one flag blocks ALL ib_insync connections.
from app.services.broker.ibkr_adapter import _OVERRIDE_IBKR_ENABLED  # noqa: F811


def fetch_ibkr(symbol, period="2y", start=None, end=None):
    """Fetch historical bars from Interactive Brokers via ib_insync.

    Uses delayed market data (reqMarketDataType(3)) — data may be 15-20 min
    delayed, which is acceptable for screening/analysis. Returns None when
    IBKR data source is disabled, circuit breaker is open, connection
    fails, or any error occurs — so callers degrade gracefully to Alpaca
    or yfinance fallback.

    IBKR as a DATA source is independent of IBKR as a TRADING broker.
    Set IBKR_DATA_ENABLED=true to enable; default OFF to avoid 15-30s
    timeouts when the gateway is unreachable.

    NOTE: Hard-disabled via module-level ``_OVERRIDE_IBKR_ENABLED`` because
    the IBKR gateway on Railway (gway.railway.internal:4004) is persistently
    unreachable. Set ``_OVERRIDE_IBKR_ENABLED = True`` (and ensure the gateway
    is healthy) before flipping the env var back on.
    """
    if not _OVERRIDE_IBKR_ENABLED:
        logger.warning("IBKR data DISABLED by _OVERRIDE_IBKR_ENABLED=False")
        return None
    if os.environ.get("IBKR_DATA_ENABLED", "false").lower() not in ("true", "1", "yes"):
        return None

    if _ibkr_breaker.is_open():
        logger.warning("IBKR circuit breaker OPEN — skipping %s", symbol)
        return None

    symbol = _validate_symbol(symbol)
    if not symbol:
        return None

    # Fast socket pre-check — avoids 15-30s ib_insync timeout when gateway is down.
    import socket as _socket
    from app.services.broker.ibkr_config import get_ibkr_config as _get_ibkr_config
    _ic = _get_ibkr_config()
    try:
        _s = _socket.create_connection((_ic["host"], _ic["port"]), timeout=3)
        _s.close()
    except Exception:
        logger.warning("IBKR gateway %s:%d unreachable (pre-check) — skipping %s",
                       _ic["host"], _ic["port"], symbol)
        _ibkr_breaker.record_failure()
        return None

    try:
        from app.services.broker.ibkr_adapter import _connect, _stock, _call_ib, _load_ib_insync

        _load_ib_insync()
        ib = _connect(strategy_id="market_data")
        if ib is None:
            _ibkr_breaker.record_failure()
            return None

        contract = _stock(symbol)

        # Delayed market data (no subscription needed)
        _call_ib(ib, "reqMarketDataType", 3, timeout=10)

        # Map period / start+end to IBKR durationStr
        if start and end:
            days = (pd.Timestamp(end) - pd.Timestamp(start)).days
            duration_str = f"{max(days, 1)} D"
        else:
            period_map = {"1y": "1 Y", "2y": "2 Y", "5y": "5 Y"}
            duration_str = period_map.get(period, "2 Y")

        bars = _call_ib(
            ib, "reqHistoricalData",
            contract,
            endDateTime="",
            durationStr=duration_str,
            barSizeSetting="1 day",
            whatToShow="TRADES",
            useRTH=True,
            formatDate=1,
            timeout=30,
        )

        if not bars:
            _ibkr_breaker.record_failure()
            return None

        rows = [{
            "date": b.date,
            "open": b.open,
            "high": b.high,
            "low": b.low,
            "close": b.close,
            "volume": b.volume,
        } for b in bars]

        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"], utc=True)
        df = df.sort_values("date").reset_index(drop=True)

        min_rows = 40 if start and end else 200
        if len(df) < min_rows:
            _ibkr_breaker.record_failure()
            return None

        _ibkr_breaker.record_success()
        return df

    except Exception as e:
        logger.error("IBKR %s: %s", symbol, e)
        _ibkr_breaker.record_failure()
        return None


def fetch(symbol, period="2y", start=None, end=None):
    """Fetch market data. Tries IBKR → Alpaca → Tiingo → yfinance. Caches results."""
    try:
        from app.services.metrics import metrics as _m
    except Exception:
        _m = None
    symbol = _validate_symbol(symbol)
    if not symbol:
        return None

    cache_key = f"{symbol}|{period}|{start}|{end}|{_session_bucket()}"
    cached = _data_cache_get(cache_key)
    if cached is not None:
        if _m:
            _m.cache_hit("market_data_cache")
        return cached
    if _m:
        _m.cache_miss("market_data_cache")

    # Try IBKR first (only if IBKR_DATA_ENABLED=true, otherwise returns None instantly)
    if _m:
        _m.incr("api_calls_total", provider="ibkr")
    df = fetch_ibkr(symbol, period=period, start=start, end=end)

    # Fallback to Alpaca
    if df is None:
        if _m:
            _m.incr("api_calls_total", provider="alpaca")
        df = fetch_alpaca(symbol, period=period, start=start, end=end)

    # Fallback to Tiingo — skip entirely when circuit breaker is open (key blocked/403)
    if df is None and not _tiingo_breaker.is_open():
        try:
            from app.config import settings as _cfg
            if _cfg.TIINGO_TOKEN:
                if _m:
                    _m.incr("api_calls_total", provider="tiingo")
                from app.services.openbb_data import fetch_ohlcv_tiingo
                df = fetch_ohlcv_tiingo(symbol, period=period)
                if df is not None and not df.empty:
                    logger.debug("Market data for %s: Tiingo (%d rows)", symbol, len(df))
                    _tiingo_breaker.record_success()
                else:
                    # Empty/None after a call = soft failure, let breaker count it
                    _tiingo_breaker.record_failure()
        except Exception as _e:
            logger.debug("Tiingo fallback for %s failed: %s", symbol, _e)
            _tiingo_breaker.record_failure()
            df = None

    # Final fallback to yfinance
    if df is None:
        if _m:
            _m.incr("api_calls_total", provider="yfinance")
            _m.incr("api_errors_total", provider="ibkr")
        df = fetch_yf(symbol, period=period, start=start, end=end)
    elif _m:
        _m.incr("api_success_total", provider="ibkr")

    if df is not None:
        _data_cache_set(cache_key, df)

    return df
