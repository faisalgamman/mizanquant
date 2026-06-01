"""News sentiment engine — VADER + analyst consensus.

Extracted from workspace_server._sentiment_score() as a standalone service
so it can be reused by both the Stock Intelligence dashboard and the
trading-engine sizing pipeline.

Composite score: 0-20
  - 0-10 pts: VADER compound sentiment on recent news headlines
  - 0-10 pts: analyst consensus (recommendation key + price target upside)

Reference
---------
- Jansen (2020), Ch.14-16 — NLP sentiment for trading signals
- Velu/Hardy/Nehren (2021), Ch.7 — News analytics
"""

from __future__ import annotations

import logging
import math

import numpy as np

logger = logging.getLogger(__name__)


# ── helper ──────────────────────────────────────────────────────────────────


def _safe_float(val, default=0.0):
    """None-safe / nan-safe float cast."""
    if val is None:
        return default
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except (TypeError, ValueError):
        return default


# ── core sentiment scoring ──────────────────────────────────────────────────


def get_sentiment_score(
    symbol: str,
    info: dict | None = None,
) -> dict:
    """Composite sentiment score 0-20.

    10 pts from VADER on recent news headlines (FMP → yfinance fallback).
    10 pts from analyst consensus (recommendation key + price target upside).
    Never raises — returns neutral on any failure.

    Returns
    -------
    dict with keys: score, label, available, news_available, analyst_available, details
    """
    score = 0
    details: dict = {}

    # ── Part 1: News VADER (0-10) ──
    try:
        import nltk
        from nltk.sentiment.vader import SentimentIntensityAnalyzer
        try:
            nltk.data.find("sentiment/vader_lexicon.zip")
        except LookupError:
            nltk.download("vader_lexicon", quiet=True)

        headlines: list[str] = []

        # FMP primary for news
        from app.services.fmp_client import fmp_client
        fmp_news = fmp_client.get_stock_news(symbol, limit=10)
        if fmp_news:
            headlines = [n.get("title", "") or n.get("headline", "") for n in fmp_news[:10] if n]
            headlines = [h for h in headlines if h]

        if not headlines:
            # yfinance fallback
            try:
                import yfinance as yf
                ticker_obj = yf.Ticker(symbol)
                raw_news = ticker_obj.news or []
                headlines = [a.get("title", "") for a in raw_news[:10] if a.get("title")]
            except Exception:
                pass

        if headlines:
            sia = SentimentIntensityAnalyzer()
            compounds = [sia.polarity_scores(h)["compound"] for h in headlines]
            compound = float(np.mean(compounds))
            news_score = max(0, min(10, int((compound + 1.0) / 2.0 * 10)))
            details["news_compound"] = round(compound, 3)
            details["headlines_analyzed"] = len(headlines)
        else:
            news_score = 5
            details["news_compound"] = 0.0
            details["headlines_analyzed"] = 0

        score += news_score
        details["news_score"] = news_score
    except Exception as _e:
        score += 5
        details["news_score"] = 5
        details["news_error"] = str(_e)[:60]

    # ── Part 2: Analyst consensus (0-10) ──
    try:
        if not info:
            info = {}
        rec_key = (info.get("recommendationKey") or "").lower().replace(" ", "_").replace("-", "_")
        current_price = _safe_float(info.get("currentPrice") or info.get("regularMarketPrice") or info.get("previousClose"))
        target_mean = _safe_float(info.get("targetMeanPrice"))
        num_opinions = int(info.get("numberOfAnalystOpinions") or 0)

        upside_pct = 0.0
        if target_mean and current_price and current_price > 0:
            upside_pct = (target_mean - current_price) / current_price * 100.0

        _key_map = {
            "strong_buy": 10, "strongbuy": 10,
            "buy": 8, "outperform": 7, "overweight": 7, "positive": 7,
            "hold": 5, "neutral": 5, "market_perform": 5, "equal_weight": 5,
            "underperform": 2, "underweight": 2, "negative": 2,
            "sell": 1, "strong_sell": 0,
        }
        analyst_score = _key_map.get(rec_key, 5)

        if upside_pct > 25:
            analyst_score = min(10, analyst_score + 2)
        elif upside_pct > 12:
            analyst_score = min(10, analyst_score + 1)
        elif upside_pct < -10:
            analyst_score = max(0, analyst_score - 2)

        if num_opinions < 3:
            analyst_score = max(3, analyst_score - 1)

        score += analyst_score
        details["analyst_score"] = analyst_score
        details["rec_key"] = rec_key or "unknown"
        details["num_opinions"] = num_opinions
        details["target_mean"] = target_mean
        details["upside_pct"] = round(upside_pct, 1)
    except Exception as _e:
        score += 5
        details["analyst_score"] = 5
        details["analyst_error"] = str(_e)[:60]

    total = min(20, score)

    news_available = (details.get("headlines_analyzed", 0) or 0) > 0
    analyst_available = (
        ((details.get("num_opinions") or 0) > 0)
        or (details.get("target_mean") is not None)
        or (details.get("rec_key") not in (None, "", "unknown"))
    )
    available = bool(news_available or analyst_available)

    if not available:
        label = "n/a"
    elif total >= 14:
        label = "bullish"
    elif total >= 7:
        label = "neutral"
    else:
        label = "bearish"

    return {
        "score": total,
        "label": label,
        "available": available,
        "news_available": news_available,
        "analyst_available": analyst_available,
        "details": details,
    }


# ── trading-engine integration ───────────────────────────────────────────────


def get_sentiment_multiplier(symbol: str) -> float:
    """Return a position-size multiplier based on news sentiment.

    - bearish / n/a → 0.85x  (reduce exposure)
    - neutral       → 1.00x
    - bullish       → 1.15x  (increase exposure)

    Degrades silently to 1.0 on any failure.
    """
    try:
        sent = get_sentiment_score(symbol)
        label = sent.get("label", "neutral")
        if not sent.get("available"):
            return 0.85  # no data → conservative
        if label == "bullish":
            return 1.15
        elif label == "bearish":
            return 0.85
        else:
            return 1.0
    except Exception:
        logger.debug("sentiment_engine: skipped for %s", symbol, exc_info=True)
        return 1.0
