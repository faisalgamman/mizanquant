"""Automated trading scheduler — multi-strategy.

Runs 3 strategies on separate Alpaca paper accounts:
  A: Momentum Alpha    — trend-following, top 3 by score
  B: Mean Reversion    — dip-buying, lowest-score oversold stocks
  C: AI Ensemble       — pure ML, top 2 stocks (memory-heavy)

Strategies run SEQUENTIALLY to stay within 8GB RAM.

Schedule:
  PRE-MARKET  (9:00 AM ET):  Refresh screener, send daily briefing
  MARKET HOURS (every 30m):  Run all 3 strategies
  POST-MARKET (4:15 PM ET):  Full analysis + strategy comparison
"""

import gc
import logging
import threading
import time
from datetime import datetime

logger = logging.getLogger("screener")

_scheduler_thread = None
_scheduler_running = False


def _get_eastern_now():
    """Get current time in US Eastern timezone."""
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("America/New_York"))


def _is_weekday(now):
    return now.weekday() < 5  # Mon=0, Fri=4


def _is_market_hours(now):
    """9:30 AM - 4:00 PM ET"""
    if not _is_weekday(now):
        return False
    market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now.replace(hour=16, minute=0, second=0, microsecond=0)
    return market_open <= now <= market_close


def _is_pre_market(now):
    """9:00 AM - 9:30 AM ET"""
    return _is_weekday(now) and now.hour == 9 and now.minute < 30


def _is_post_market(now):
    """4:00 PM - 4:30 PM ET"""
    return _is_weekday(now) and now.hour == 16 and now.minute < 30


def _scheduler_loop():
    """Main scheduler loop — runs forever in a background thread."""
    global _scheduler_running

    from app.config import settings, STRATEGY_CONFIGS
    from app.services.telegram_alert import send_message as tg_send

    strategies_active = len(STRATEGY_CONFIGS)
    logger.info(f"Scheduler started — {strategies_active} strategies active")

    strategy_names = ", ".join(f"{s.strategy_id}: {s.name}" for s in STRATEGY_CONFIGS.values())
    tg_send(
        f"SCHEDULER ACTIVE\n\n"
        f"Multi-strategy scanning started.\n"
        f"Strategies: {strategy_names or 'Default only'}\n"
        f"Scans run every 30 min during market hours."
    )

    last_scan_time = 0
    last_post_market = ""
    last_pre_market = ""
    SCAN_INTERVAL = 1800  # 30 minutes between scans

    while _scheduler_running:
        try:
            now = _get_eastern_now()
            today_str = now.strftime("%Y-%m-%d")

            # --- PRE-MARKET: 9:00-9:30 AM ET (once per day) ---
            if _is_pre_market(now) and last_pre_market != today_str:
                last_pre_market = today_str
                logger.info("Pre-market: refreshing screener data...")
                try:
                    _run_pre_market()
                except Exception as e:
                    logger.error(f"Pre-market scan failed: {e}")

            # --- MARKET HOURS: scan every 30 min ---
            elif _is_market_hours(now):
                elapsed = time.time() - last_scan_time
                if elapsed >= SCAN_INTERVAL:
                    last_scan_time = time.time()
                    logger.info(f"Market hours scan ({now.strftime('%H:%M')} ET)...")
                    try:
                        _run_market_scan()
                    except Exception as e:
                        logger.error(f"Market scan failed: {e}")

            # --- POST-MARKET: 4:00-4:30 PM ET (once per day) ---
            elif _is_post_market(now) and last_post_market != today_str:
                last_post_market = today_str
                logger.info("Post-market: running full analysis...")
                try:
                    _run_post_market()
                except Exception as e:
                    logger.error(f"Post-market scan failed: {e}")

            # Sleep 60 seconds between checks
            time.sleep(60)

        except Exception as e:
            logger.error(f"Scheduler error: {e}")
            time.sleep(120)  # wait longer on error


def _run_pre_market():
    """Pre-market: refresh screener, send daily briefing + strategy comparison."""
    from app.services.telegram_alert import send_message as tg_send, alert_daily_summary
    from app.services.telegram_alert import alert_strategy_comparison
    from app.services.alpaca_client import get_account as alpaca_get_account

    import halal_screener as hs

    # Refresh screener
    hs._cache.clear()
    hs._cache_ts.clear()
    results = hs.run_screener()

    if results:
        strong_buys = [r for r in results if r.get("swing_signal") == "STRONG BUY"]
        buys = [r for r in results if r.get("swing_signal") == "BUY"]

        # Get portfolio info from default account
        account = alpaca_get_account()
        portfolio_info = None
        if account:
            daily_pl = account["equity"] - account["last_equity"]
            portfolio_info = {"equity": account["equity"], "daily_pl": daily_pl}

        alert_daily_summary(results, portfolio_info)
        logger.info(f"Pre-market briefing sent: {len(results)} stocks, {len(strong_buys)} STRONG BUY")

    # Send multi-strategy comparison
    alert_strategy_comparison()

    gc.collect()


def _run_market_scan():
    """Market hours: run all 3 strategies SEQUENTIALLY on their target stocks."""
    import halal_screener as hs
    from app.config import STRATEGY_CONFIGS

    # Run screener (uses cache if fresh)
    results = hs.run_screener()
    if not results:
        return

    # Sort stocks by swing score
    scored = [r for r in results if r.get("swing_score", 0) >= 30]
    scored.sort(key=lambda x: x.get("swing_score", 0), reverse=True)

    # ===== STRATEGY A: Momentum Alpha — top 3 trending stocks =====
    if "A" in STRATEGY_CONFIGS:
        momentum_candidates = [r for r in scored if r.get("swing_score", 0) >= 55][:3]
        for stock in momentum_candidates:
            symbol = stock["symbol"]
            try:
                logger.info(f"[A] Momentum scan: {symbol}")
                hs.run_consensus_momentum(symbol, horizon=5)
            except Exception as e:
                logger.error(f"[A] Momentum {symbol}: {e}")
            hs._flush_all_caches()
            time.sleep(2)
        gc.collect()

    # ===== STRATEGY B: Mean Reversion — oversold stocks (lowest scores) =====
    if "B" in STRATEGY_CONFIGS:
        # For mean reversion, we want stocks that have DROPPED — low swing scores
        reversion_candidates = [r for r in scored if r.get("swing_score", 0) >= 30]
        reversion_candidates.sort(key=lambda x: x.get("swing_score", 0))  # lowest first
        reversion_candidates = reversion_candidates[:5]
        for stock in reversion_candidates:
            symbol = stock["symbol"]
            try:
                logger.info(f"[B] Reversion scan: {symbol}")
                hs.run_consensus_reversion(symbol, horizon=3)
            except Exception as e:
                logger.error(f"[B] Reversion {symbol}: {e}")
            hs._flush_all_caches()
            time.sleep(2)
        gc.collect()

    # ===== STRATEGY C: AI Ensemble — top 2 stocks (ML is memory-heavy) =====
    if "C" in STRATEGY_CONFIGS:
        ml_candidates = [r for r in scored if r.get("swing_score", 0) >= 55][:2]
        for stock in ml_candidates:
            symbol = stock["symbol"]
            try:
                logger.info(f"[C] AI Ensemble scan: {symbol}")
                hs.run_consensus_ml(symbol, horizon=7, episodes=3)
            except Exception as e:
                logger.error(f"[C] AI Ensemble {symbol}: {e}")
            hs._flush_all_caches()
            time.sleep(3)
        gc.collect()

    logger.info("Multi-strategy market scan complete")


def _run_post_market():
    """Post-market: full analysis, strategy comparison, daily summary."""
    from app.services.telegram_alert import send_message as tg_send
    from app.services.telegram_alert import alert_strategy_comparison
    from app.config import STRATEGY_CONFIGS
    import halal_screener as hs

    # Clear stale cache
    hs._cache.clear()
    hs._cache_ts.clear()

    # Run screener
    results = hs.run_screener()
    if not results:
        tg_send("POST-MARKET SCAN\n\nNo screener results available.")
        return

    # Get top 5 stocks
    top = [r for r in results if r.get("swing_score", 0) >= 45]
    top.sort(key=lambda x: x.get("swing_score", 0), reverse=True)
    top = top[:5]

    if not top:
        tg_send(f"POST-MARKET SCAN\n\n{len(results)} stocks scanned.\nNo stocks above score 45.")
        return

    tg_send(
        f"POST-MARKET ANALYSIS\n"
        f"{len(results)} stocks scanned\n"
        f"{len(top)} top signals\n"
        f"\nRunning full consensus + 3 strategies..."
    )

    # Run standard consensus on top stocks
    scan_results = []
    for stock in top:
        symbol = stock["symbol"]
        try:
            hs._flush_all_caches()
            consensus = hs.run_consensus(symbol, horizon=5, episodes=3)
            if consensus and not consensus[0].get("Error"):
                summary = consensus[0]
                scan_results.append({
                    "symbol": symbol,
                    "verdict": summary.get("Verdict", "N/A"),
                    "confidence": summary.get("Confidence %", 0),
                })
        except Exception as e:
            logger.error(f"Post-market {symbol}: {e}")
            scan_results.append({"symbol": symbol, "error": str(e)})

        hs._flush_all_caches()
        time.sleep(3)

    # Summary
    strong = [r for r in scan_results if "STRONG" in r.get("verdict", "")]
    lines = []
    for r in scan_results:
        if "error" not in r:
            lines.append(f"  {r['symbol']}: {r['verdict']} ({r['confidence']:.0f}%)")

    tg_send(
        f"SCAN COMPLETE\n\n"
        f"Analyzed: {len(top)} stocks\n"
        f"STRONG signals: {len(strong)}\n\n"
        f"Results:\n" + "\n".join(lines) if lines else "No results"
    )

    # Send strategy comparison report
    alert_strategy_comparison()

    gc.collect()


def start_scheduler():
    """Start the background scheduler thread. Call once at app startup."""
    global _scheduler_thread, _scheduler_running

    if _scheduler_running:
        logger.info("Scheduler already running")
        return

    _scheduler_running = True
    _scheduler_thread = threading.Thread(target=_scheduler_loop, daemon=True, name="scheduler")
    _scheduler_thread.start()
    logger.info("Background scheduler thread started")


def stop_scheduler():
    """Stop the scheduler gracefully."""
    global _scheduler_running
    _scheduler_running = False
    logger.info("Scheduler stopping...")
