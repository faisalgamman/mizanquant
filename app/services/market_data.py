"""Market data fetching with rate limiting and retry logic.

Primary: Alpaca Market Data API v2 (free IEX tier)
Fallback: yfinance

Rate limiting strategy:
- Global semaphore limits concurrent Alpaca requests (default: 3)
- 0.25s minimum interval between requests
- Exponential backoff retry on 429 (Too Many Requests)
- After 3 retries, falls back to yfinance
"""

import time
import logging
import threading
import pandas as pd
import numpy as np

logger = logging.getLogger("screener")

# ---------------------------------------------------------------------------
# In-memory data cache with TTL
# ---------------------------------------------------------------------------
_data_cache = {}
_data_cache_ts = {}
_DATA_CACHE_TTL = 300  # 5 minutes


def _data_cache_get(key):
    if time.time() - _data_cache_ts.get(key, 0) < _DATA_CACHE_TTL:
        return _data_cache.get(key)
    return None


def _data_cache_set(key, value):
    _data_cache[key] = value
    _data_cache_ts[key] = time.time()


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
    symbol = _validate_symbol(symbol)
    if not symbol:
        return None

    try:
        from app.config import settings
        if not settings.ALPACA_API_KEY or not settings.ALPACA_SECRET_KEY:
            return None

        import httpx

        headers = {
            "APCA-API-KEY-ID": settings.ALPACA_API_KEY,
            "APCA-API-SECRET-KEY": settings.ALPACA_SECRET_KEY,
        }

        # Convert period to start/end dates
        if not start or not end:
            from datetime import datetime, timedelta
            end_dt = datetime.now()
            period_map = {"1y": 365, "2y": 730, "5y": 1825}
            days = period_map.get(period, 730)
            start_dt = end_dt - timedelta(days=days)
            start = start_dt.strftime("%Y-%m-%d")
            end = end_dt.strftime("%Y-%m-%d")

        url = "https://data.alpaca.markets/v2/stocks/bars"
        params = {
            "symbols": symbol,
            "timeframe": "1Day",
            "start": start,
            "end": end,
            "limit": 10000,
            "adjustment": "split",
            "feed": "iex",
        }

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

                    while True:
                        if next_page:
                            params["page_token"] = next_page
                        resp = client.get(url, headers=headers, params=params)

                        # Handle 429 rate limit
                        if resp.status_code == 429:
                            wait_time = retry_delay * (2 ** attempt)
                            logger.warning(
                                f"Alpaca 429 for {symbol} (attempt {attempt+1}/{max_retries}), "
                                f"waiting {wait_time:.1f}s"
                            )
                            time.sleep(wait_time)
                            break  # break inner loop, retry outer loop

                        resp.raise_for_status()
                        data = resp.json()

                        bars = data.get("bars", {}).get(symbol, [])
                        all_bars.extend(bars)

                        next_page = data.get("next_page_token")
                        if not next_page:
                            # Success — return data
                            if not all_bars:
                                return None

                            df = pd.DataFrame(all_bars)
                            df = df.rename(columns={
                                "t": "date", "o": "open", "h": "high",
                                "l": "low", "c": "close", "v": "volume",
                            })
                            df["date"] = pd.to_datetime(df["date"])
                            df = df[["date", "open", "high", "low", "close", "volume"]].sort_values("date").reset_index(drop=True)
                            return df if len(df) >= 200 else None
                    else:
                        # while loop completed without break — should not reach here normally
                        continue

                    # If we broke out of inner loop (429), continue to next retry attempt
                    continue

            finally:
                _alpaca_semaphore.release()

        # All retries exhausted
        logger.error(f"Alpaca {symbol}: all {max_retries} retries failed (429)")
        return None

    except Exception as e:
        logger.error(f"Alpaca {symbol}: {e}")
        return None


def fetch_yf(symbol, period="2y", start=None, end=None):
    """Fetch historical data from yfinance (fallback)."""
    symbol = _validate_symbol(symbol)
    if not symbol:
        return None

    try:
        import yfinance as yf
        t = yf.Ticker(symbol)
        if start and end:
            df = t.history(start=start, end=end, auto_adjust=True)
        else:
            df = t.history(period=period, auto_adjust=True)
        if df.empty:
            return None
        df.columns = [c.lower() for c in df.columns]
        df = df.reset_index()
        df.columns = [c.lower() for c in df.columns]
        for col in ["dividends", "stock splits", "capital gains"]:
            df.drop(columns=[col], errors="ignore", inplace=True)
        return df if len(df) >= 200 else None
    except Exception as e:
        logger.error(f"yfinance {symbol}: {e}")
        return None


def fetch(symbol, period="2y", start=None, end=None):
    """Fetch market data. Tries Alpaca first, falls back to yfinance. Caches results."""
    symbol = _validate_symbol(symbol)
    if not symbol:
        return None

    cache_key = f"{symbol}|{period}|{start}|{end}"
    cached = _data_cache_get(cache_key)
    if cached is not None:
        return cached

    # Try Alpaca first (rate-limited with retry)
    df = fetch_alpaca(symbol, period=period, start=start, end=end)

    # Fallback to yfinance
    if df is None:
        df = fetch_yf(symbol, period=period, start=start, end=end)

    if df is not None:
        _data_cache_set(cache_key, df)

    return df
