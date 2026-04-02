import time
import logging
import pandas as pd
import numpy as np

logger = logging.getLogger("screener")

# In-memory data cache with TTL
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


def fetch_alpaca(symbol, period="2y", start=None, end=None):
    """Fetch historical bars from Alpaca Market Data API v2."""
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

        # Alpaca bars endpoint
        url = "https://data.alpaca.markets/v2/stocks/bars"
        params = {
            "symbols": symbol,
            "timeframe": "1Day",
            "start": start,
            "end": end,
            "limit": 10000,
            "adjustment": "split",
            "feed": "iex",  # free tier
        }

        with httpx.Client(timeout=30) as client:
            all_bars = []
            next_page = None

            while True:
                if next_page:
                    params["page_token"] = next_page
                resp = client.get(url, headers=headers, params=params)
                resp.raise_for_status()
                data = resp.json()

                bars = data.get("bars", {}).get(symbol, [])
                all_bars.extend(bars)

                next_page = data.get("next_page_token")
                if not next_page:
                    break

            if not all_bars:
                return None

            df = pd.DataFrame(all_bars)
            df = df.rename(columns={"t": "date", "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
            df["date"] = pd.to_datetime(df["date"])
            df = df[["date", "open", "high", "low", "close", "volume"]].sort_values("date").reset_index(drop=True)

            return df if len(df) >= 200 else None
    except Exception as e:
        logger.error(f"Alpaca {symbol}: {e}")
        return None


def fetch_yf(symbol, period="2y", start=None, end=None):
    """Fetch historical data from yfinance (fallback)."""
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
    cache_key = f"{symbol}|{period}|{start}|{end}"
    cached = _data_cache_get(cache_key)
    if cached is not None:
        return cached

    # Try Alpaca first
    df = fetch_alpaca(symbol, period=period, start=start, end=end)

    # Fallback to yfinance
    if df is None:
        df = fetch_yf(symbol, period=period, start=start, end=end)

    if df is not None:
        _data_cache_set(cache_key, df)

    return df
