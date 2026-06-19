"""Automated trading scheduler — unified pipeline.

Runs the 8-stage UnifiedPipeline on the daily schedule:

  02:00 AM  │ Model retraining (existing)
  08:00 AM  │ Data collection (Stage 1)
  08:30 AM  │ Halal + Smart filter (Stages 2-3)
  09:00 AM  │ AI consensus + Kelly + Guardian + Alpaca (Stages 4-7)
  12:00 PM  │ Mid-session signals scan
  04:00 PM  │ Post-market report (Stage 8)
  04:30 PM  │ Signal audit (existing)

Strategies run SEQUENTIALLY to stay within RAM.
"""

import gc
import logging
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("screener")

from app.services.scheduler_metrics import scheduler_metrics

_scheduler_thread = None
_scheduler_running = False


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _script_env() -> dict[str, str]:
    root = _repo_root()
    pythonpath_parts = [
        str(root / ".vendor"),
        str(root),
        str(root / "openbb_forecast"),
    ]
    existing = os.environ.get("PYTHONPATH")
    if existing:
        pythonpath_parts.append(existing)
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(part for part in pythonpath_parts if part)
    return env


def _run_repo_script(script_rel_path: str, *args: str) -> subprocess.CompletedProcess:
    root = _repo_root()
    return subprocess.run(
        [sys.executable, script_rel_path, *args],
        check=True,
        capture_output=True,
        text=True,
        cwd=str(root),
        env=_script_env(),
    )


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


def _is_active_window(now):
    """True only during active trading window: Mon-Fri 4 AM - 8 PM ET.
    Outside this window the scheduler sleeps deeply to save Railway costs."""
    if not _is_weekday(now):
        return False
    return 4 <= now.hour < 20  # 4 AM to 8 PM ET


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
        f"Scans run every 4 hours during active window (4 AM - 8 PM ET, Mon-Fri)."
    )

    last_scan_time = 0
    last_post_market = ""
    last_pre_market = ""
    last_optimizer = ""
    last_train_models = ""
    last_pretrain_ml = ""
    last_signal_audit = ""
    last_reference_refresh = ""
    last_pairs_scan = ""
    last_pipeline_data = ""
    last_model_benchmark = ""
    last_pipeline_filter = ""
    last_pipeline_full = ""
    last_paper_record = ""
    last_paper_mature = ""
    last_paper_rebalance = ""  # YYYY-MM — monthly composite rebalance (fires 1st trading day)
    last_halal_rescreen = ""   # YYYY-MM — monthly AAOIFI financial re-screen of the universe
    last_weight_fit = ""       # YYYY-MM — monthly Chan walk-forward composite-weight fit (auto-adopt)
    last_outcome_match = ""   # YYYY-MM-DD — nightly decision-outcome matching
    last_sentinel = ""        # YYYY-MM-DD:slot — 2x/day sentinel judgment cycle
    last_screener_warm = ""   # YYYY-MM-DD — daily screener cache pre-warm
    last_fund_warm = ""       # YYYY-MM-DD — daily pre-market fundamentals (halal) cache warm
    last_intraday_warm = ""  # YYYY-MM-DD:HH — intraday screener re-warm slots
    last_db_backup = ""      # YYYY-MM-DD — daily DB backup of measurement tables
    _screener_warmed_startup = False  # one-shot on boot
    SCAN_INTERVAL = 14400  # 4 hours between scans (was 30 min) — cost optimization

    # Unified pipeline schedule (US/Eastern, weekdays only).
    SIGNALS_SLOTS = [
        # Market open — immediate scan
        (9, 30, "Open"),
        # Mid-morning
        (10, 30, "Mid-Morning"),
        # Lunch / midday
        (12, 30, "Midday"),
        # Power hour kick-off
        (14, 30, "Afternoon"),
        # Close
        (16, 0, "Close"),
    ]
    last_signals_slot: dict[str, str] = {}

    while _scheduler_running:
        try:
            now = _get_eastern_now()
            today_str = now.strftime("%Y-%m-%d")

            # --- STARTUP: Pre-warm smart_screener cache once on boot ---
            if not _screener_warmed_startup:
                _screener_warmed_startup = True
                logger.info('Scheduler: pre-warming smart_screener cache on startup')
                try:
                    from app.workspace_server import _run_screener_bg, _SMART_UNIVERSE as _SU
                    threading.Thread(target=_run_screener_bg, args=(list(_SU),),
                                     daemon=True, name='screener-warm-startup').start()
                except Exception as e:
                    logger.warning('Scheduler: startup screener warm failed: %%s', e)

            # --- DAILY DB BACKUP: ~03:30 ET (every day, before active-window guard) ---
            # SignalHistory, TradeHistory, AgentDecision, ConsensusLog → gzip JSONL
            # to the Railway persistent volume. Runs EVERY day (including weekends)
            # because the data accumulates 24/7 and a DB loss on Sunday is just as
            # catastrophic as a DB loss on Tuesday.
            if now.hour == 3 and now.minute >= 30 and now.minute < 35 and last_db_backup != today_str:
                last_db_backup = today_str
                logger.info("DB backup: exporting measurement tables...")
                try:
                    from scripts.backup_db import backup_tables, rotate_backups
                    res = backup_tables()
                    rotate_backups(res.get("dir"))
                    logger.info("DB backup: %s", res.get("tables"))
                    if res.get("errors"):
                        raise RuntimeError(f"partial backup: {res['errors']}")
                except Exception as e:
                    logger.error("DB backup FAILED: %s", e)
                    try:
                        from app.services.telegram_alert import alert_system_health
                        alert_system_health(f"⚠️ DB backup failed: {str(e)[:160]}", severity="CRITICAL")
                    except Exception:
                        pass

            # --- COST OPTIMIZATION: Sleep deeply outside active window ---
            if not _is_active_window(now):
                # Weekends or late night: sleep 10 minutes between checks
                time.sleep(600)
                continue

            if _is_weekday(now) and now.hour == 1 and last_reference_refresh != today_str:
                last_reference_refresh = today_str
                logger.info("Reference-data refresh: updating tradable assets and earnings caches...")
                try:
                    scheduler_metrics.record_cycle_start("reference_data")
                    _run_reference_data_refresh()
                    scheduler_metrics.record_cycle_end("reference_data", success=True)
                except Exception as e:
                    scheduler_metrics.record_cycle_end("reference_data", success=False, error=str(e))
                    logger.error(f"Reference-data refresh failed: {e}")

            if _is_weekday(now) and now.hour == 2 and last_train_models != today_str:
                last_train_models = today_str
                logger.info("Nightly retrain: running persisted-model refresh...")
                try:
                    scheduler_metrics.record_cycle_start("train_models")
                    _run_train_models()
                    scheduler_metrics.record_cycle_end("train_models", success=True)
                except Exception as e:
                    scheduler_metrics.record_cycle_end("train_models", success=False, error=str(e))
                    logger.error(f"Nightly retrain failed: {e}")

            # --- AI SENTINEL — nightly outcome matching (02:15 ET) ---
            if _is_weekday(now) and now.hour == 2 and now.minute >= 15 and now.minute < 20 and last_outcome_match != today_str:
                last_outcome_match = today_str
                logger.info("Sentinel: matching decision outcomes to paper P&L...")
                try:
                    from app.services.ai_sentinel.journal import match_outcomes
                    result = match_outcomes()
                    logger.info("Sentinel outcome match: %s", result)
                except Exception as e:
                    logger.error(f"Sentinel outcome match failed: {e}")

            if _is_weekday(now) and now.hour == 3 and last_pretrain_ml != today_str:
                last_pretrain_ml = today_str
                logger.info("ML pretrain: pre-computing DQN/PG for universe...")
                try:
                    scheduler_metrics.record_cycle_start("pretrain_ml")
                    _run_pretrain_ml()
                    scheduler_metrics.record_cycle_end("pretrain_ml", success=True)
                except Exception as e:
                    scheduler_metrics.record_cycle_end("pretrain_ml", success=False, error=str(e))
                    logger.error(f"ML pretrain failed: {e}")

            # --- Composite-weight fit (Chan walk-forward) — monthly; auto-adopts v2 ONLY
            #     when OOS rank-IC > baseline + permutation p<0.05 + DSR>=0.6, else stays v1.
            _month_str = now.strftime("%Y-%m")
            if (_is_weekday(now) and now.day <= 5 and now.hour == 3
                    and 40 <= now.minute < 45 and last_weight_fit != _month_str):
                last_weight_fit = _month_str
                logger.info("Composite-weight fit: walk-forward Ridge (self-gated on >=3 months)...")
                try:
                    _run_weight_fit()
                except Exception as e:
                    logger.error("weight fit failed: %s", e)

            if _is_weekday(now) and now.hour == 16 and now.minute >= 30 and last_signal_audit != today_str:
                last_signal_audit = today_str
                logger.info("Signal audit: checking live verdict drift...")
                try:
                    scheduler_metrics.record_cycle_start("signal_audit")
                    _run_signal_audit()
                    scheduler_metrics.record_cycle_end("signal_audit", success=True)
                except Exception as e:
                    scheduler_metrics.record_cycle_end("signal_audit", success=False, error=str(e))
                    logger.error(f"Signal audit failed: {e}")

            # --- MODEL PERFORMANCE BENCHMARK: Sunday 11:00 AM ET (weekly) ---
            if now.weekday() == 6 and now.hour == 11 and last_model_benchmark != today_str:
                last_model_benchmark = today_str
                logger.info("Weekly model benchmark: computing real directional accuracy...")
                try:
                    from app.services.model_performance import update_all_model_performance
                    update_all_model_performance()
                    logger.info("Model benchmark complete")
                except Exception as e:
                    logger.error("Model benchmark failed: %s", e)

            # --- WEEKLY OPTIMIZER: Sunday 10:00 AM ET (once per week) ---
            if now.weekday() == 6 and now.hour == 10 and last_optimizer != today_str:
                last_optimizer = today_str
                logger.info("Weekly optimizer: tuning parameters...")
                try:
                    scheduler_metrics.record_cycle_start("optimizer")
                    _run_optimizer()
                    scheduler_metrics.record_cycle_end("optimizer", success=True)
                except Exception as e:
                    scheduler_metrics.record_cycle_end("optimizer", success=False, error=str(e))
                    logger.error(f"Weekly optimizer failed: {e}")

            # --- PRE-MARKET FUNDAMENTALS WARM: ~07:30 ET (BEFORE the 08:00 screener warm) ---
            # Refresh the stalest/missing halal-screen rows into the durable ScreeningResult
            # cache so the screener reads warm fundamentals instead of fetching live (and
            # stalling on a blocked Yahoo) during trading. Bounded + breaker-protected.
            if _is_weekday(now) and now.hour == 7 and 30 <= now.minute < 40 and last_fund_warm != today_str:
                last_fund_warm = today_str
                try:
                    from app.services.halal_screening import warm_fundamentals_cache
                    from app.services.universe import (
                        build_halal_candidates, sync_verified_halal_to_universe,
                    )

                    def _warm_fund():
                        scheduler_metrics.record_cycle_start("warm_fundamentals")
                        try:
                            # EDGAR-primary bulk screen over the EXPANDED candidate pool
                            # (no FMP quota), then sync the verified-halal names into the
                            # Universe table so BOTH scanners grow.
                            cands = build_halal_candidates()
                            out = warm_fundamentals_cache(cands, prefer_edgar=True)
                            active = sync_verified_halal_to_universe()
                            try:
                                from app.workspace_server import _refresh_smart_universe
                                _refresh_smart_universe()          # Monthly: same-day, no restart
                                import halal_screener as _hs
                                _hs._db_symbols = None              # Weekly: drop cached DB universe
                            except Exception as _re:
                                logger.debug("universe refresh after warm failed: %s", _re)
                            scheduler_metrics.record_cycle_end("warm_fundamentals", success=True)
                            logger.info("Pre-market fundamentals warm (EDGAR): %s; active_halal=%d", out, active)
                        except Exception as e:
                            scheduler_metrics.record_cycle_end("warm_fundamentals", success=False, error=str(e))
                            logger.error("Pre-market fundamentals warm failed: %s", e)

                    logger.info("Scheduler: pre-market EDGAR warm over expanded halal candidates")
                    threading.Thread(target=_warm_fund, daemon=True, name="fund-warm").start()
                except Exception as e:
                    logger.warning("Scheduler: fundamentals warm kickoff failed: %s", e)

            # --- PIPELINE STAGE 1: Data collection at 8:00 AM ET (once per day) ---
            if _is_weekday(now) and now.hour == 8 and now.minute < 5 and last_pipeline_data != today_str:
                last_pipeline_data = today_str
                logger.info("Pipeline stage 1: collecting market data...")
                try:
                    scheduler_metrics.record_cycle_start("pipeline_data")
                    from app.services.pipeline_orchestrator import run_pipeline
                    report = run_pipeline(dry_run=True, skip_stages={"halal", "smart", "consensus", "kelly", "guardian", "execute", "report"})
                    logger.info("Pipeline stage 1: collected data in %.1fs", report.elapsed_s)
                    scheduler_metrics.record_cycle_end("pipeline_data", success=True)
                except Exception as e:
                    scheduler_metrics.record_cycle_end("pipeline_data", success=False, error=str(e))
                    logger.error(f"Pipeline data collection failed: {e}")

            # --- SCREENER CACHE PRE-WARM: ~08:00 ET (so the Monthly tab opens warm) ---
            if _is_weekday(now) and now.hour == 8 and now.minute < 15 and last_screener_warm != today_str:
                last_screener_warm = today_str
                try:
                    from app.workspace_server import _run_screener_bg, _SMART_UNIVERSE as _SU
                    logger.info('Scheduler: pre-warming smart_screener cache (%d symbols)', len(_SU))
                    threading.Thread(target=_run_screener_bg, args=(list(_SU),),
                                     daemon=True, name='screener-warm').start()
                except Exception as e:
                    logger.warning('Scheduler: screener warm failed: %s', e)

            # Intraday screener re-warm — keep the dashboard fresh without a redeploy.
            # Gentle cadence (every ~2h in market hours) — the 650-symbol scan is heavy, so NOT more often.
            if _is_weekday(now) and now.hour in (11, 13, 15) and now.minute < 8                     and last_intraday_warm != f"{today_str}:{now.hour}":
                last_intraday_warm = f"{today_str}:{now.hour}"
                try:
                    from app.workspace_server import _run_screener_bg, _SMART_UNIVERSE as _SU
                    threading.Thread(target=_run_screener_bg, args=(list(_SU),), daemon=True,
                                     name="screener-warm-intraday").start()
                    logger.info("Scheduler: intraday screener re-warm (%d symbols)", len(_SU))
                except Exception as e:
                    logger.warning("Scheduler: intraday screener warm failed: %s", e)

            # --- PIPELINE STAGES 2-3: Halal + Smart filter at 8:30 AM ET (once per day) ---
            if _is_weekday(now) and now.hour == 8 and now.minute >= 30 and now.minute < 35 and last_pipeline_filter != today_str:
                last_pipeline_filter = today_str
                logger.info("Pipeline stages 2-3: halal + smart screening...")
                try:
                    scheduler_metrics.record_cycle_start("pipeline_filter")
                    from app.services.pipeline_orchestrator import run_pipeline
                    report = run_pipeline(dry_run=True, skip_stages={"consensus", "kelly", "guardian", "execute", "report"})
                    logger.info("Pipeline stages 2-3: %d halal, %d smart filter passed in %.1fs",
                                report.stages[1].count_out if len(report.stages) > 1 else 0,
                                report.stages[2].count_out if len(report.stages) > 2 else 0,
                                report.elapsed_s)
                    scheduler_metrics.record_cycle_end("pipeline_filter", success=True)
                except Exception as e:
                    scheduler_metrics.record_cycle_end("pipeline_filter", success=False, error=str(e))
                    logger.error(f"Pipeline filter failed: {e}")

            # --- AI SENTINEL — pre-market judgment (09:05 ET) ---
            if _is_weekday(now) and now.hour == 9 and now.minute >= 5 and now.minute < 10 and last_sentinel != f"{today_str}:pre":
                last_sentinel = f"{today_str}:pre"
                logger.info("Sentinel: running pre-market judgment cycle...")
                try:
                    from app.services.ai_sentinel.sentinel import run_sentinel_cycle
                    result = run_sentinel_cycle()
                    logger.info("Sentinel pre-market: %s", result)
                except Exception as e:
                    logger.error(f"Sentinel pre-market failed: {e}")

            # --- PIPELINE STAGES 4-7: Full analysis + execution at 9:45 AM ET (once per day) ---
            if _is_weekday(now) and now.hour == 9 and now.minute >= 45 and last_pipeline_full != today_str:
                last_pipeline_full = today_str
                logger.info("Pipeline stages 4-7: AI consensus + Kelly + Guardian + Alpaca...")
                try:
                    scheduler_metrics.record_cycle_start("pipeline_full")
                    from app.services.pipeline_orchestrator import run_pipeline
                    report = run_pipeline(dry_run=True)
                    logger.info("Pipeline stages 4-7: %d signals, %d executed, %d rejected in %.1fs",
                                report.signals_passed, report.signals_executed, report.signals_rejected, report.elapsed_s)
                    scheduler_metrics.record_cycle_end("pipeline_full", success=True)
                except Exception as e:
                    scheduler_metrics.record_cycle_end("pipeline_full", success=False, error=str(e))
                    logger.error(f"Pipeline full run failed: {e}")

            # --- POST-MARKET: 4:00-4:30 PM ET (once per day) ---
            elif _is_post_market(now) and last_post_market != today_str:
                last_post_market = today_str
                logger.info("Post-market: pipeline report + final summary...")
                try:
                    scheduler_metrics.record_cycle_start("post_market")
                    from app.services.pipeline_orchestrator import run_pipeline
                    report = run_pipeline(dry_run=True, skip_stages={"collect", "halal", "smart", "consensus", "kelly", "guardian", "execute"})
                    scheduler_metrics.record_cycle_end("post_market", success=True)
                except Exception as e:
                    scheduler_metrics.record_cycle_end("post_market", success=False, error=str(e))
                    logger.error(f"Post-market report failed: {e}")

            # --- AI SENTINEL — post-market judgment (16:15 ET) ---
            if _is_weekday(now) and now.hour == 16 and now.minute >= 15 and now.minute < 20 and last_sentinel != f"{today_str}:post":
                last_sentinel = f"{today_str}:post"
                logger.info("Sentinel: running post-market judgment cycle...")
                try:
                    from app.services.ai_sentinel.sentinel import run_sentinel_cycle
                    result = run_sentinel_cycle()
                    logger.info("Sentinel post-market: %s", result)
                except Exception as e:
                    logger.error(f"Sentinel post-market failed: {e}")

            # --- INTRADAY SIGNALS: 10:30 / 12:00 / 14:30 / 16:30 ET ---
            # Independent block — runs alongside any other branch above.
            # Pushes STRONG-BUY Telegram alerts (signals advisor).
            if _is_weekday(now):
                for hour, minute, label in SIGNALS_SLOTS:
                    slot_key = f"{hour:02d}:{minute:02d}"
                    if (now.hour == hour and now.minute >= minute
                            and last_signals_slot.get(slot_key) != today_str):
                        last_signals_slot[slot_key] = today_str
                        logger.info(f"Intraday signals scan: {label}")
                        try:
                            scheduler_metrics.record_cycle_start("signals_scan")
                            _run_signals_scan(label)
                            scheduler_metrics.record_cycle_end("signals_scan", success=True)
                        except Exception as e:
                            scheduler_metrics.record_cycle_end("signals_scan", success=False, error=str(e))
                            logger.error(f"Intraday signals scan failed ({label}): {e}", exc_info=True)
                        break  # only fire one slot per loop iteration

            # --- PAIRS TRADING: 11:00 AM ET (once per day, market open) ---
            # Runs during market hours so execute_buy's market-hours guard
            # passes. Long-only relative-value cointegration cycle (Phase 3).
            if _is_weekday(now) and now.hour == 11 and now.minute < 5 and last_pairs_scan != today_str:
                last_pairs_scan = today_str
                logger.info("Pairs trading: running cointegration cycle...")
                try:
                    scheduler_metrics.record_cycle_start("pairs_scan")
                    _run_pairs_scan()
                    scheduler_metrics.record_cycle_end("pairs_scan", success=True)
                except Exception as e:
                    scheduler_metrics.record_cycle_end("pairs_scan", success=False, error=str(e))
                    logger.error(f"Pairs trading cycle failed: {e}", exc_info=True)

            # --- PAPER-VALIDATION LEDGER (simulated, isolated strategy "PV") ---
            # Weekly (Mon ~09:00 ET): record this week's picks as simulated paper
            # trades. Daily (~17:00 ET, post-close): mature open trades on the
            # Option-A exit (15% stop / 20-day) so closed pnl_pct accumulates and
            # the paper_trade_gate graduation counter can move. No broker orders.
            if (_is_weekday(now) and now.weekday() == 0 and now.hour == 9
                    and now.minute < 10 and last_paper_record != today_str):
                last_paper_record = today_str
                logger.info("Paper validation: recording weekly picks...")
                try:
                    from app.services.paper_validation import record_weekly_picks
                    record_weekly_picks()
                except Exception as e:
                    logger.error(f"Paper validation record failed: {e}", exc_info=True)

            if (_is_weekday(now) and now.hour == 17 and now.minute < 5
                    and last_paper_mature != today_str):
                last_paper_mature = today_str
                logger.info("Paper validation: maturing open paper trades...")
                try:
                    from app.services.paper_validation import mature_open_paper_trades
                    mature_open_paper_trades()
                except Exception as e:
                    logger.error(f"Paper validation mature failed: {e}", exc_info=True)

            # --- MONTHLY COMPOSITE REBALANCE (simulated ledger "PVM") ---
            # Fires on the FIRST trading day of each month at ~09:30 ET: the
            # month-key dedup means the first weekday the scheduler sees in a new
            # month triggers it once. Closes names that fell out of the top-N,
            # opens new entrants, keeps the rest. No broker orders.
            month_key = now.strftime("%Y-%m")
            if (_is_weekday(now) and now.hour == 9 and 30 <= now.minute < 40
                    and last_paper_rebalance != month_key):
                last_paper_rebalance = month_key
                logger.info("Paper validation: monthly composite rebalance (%s)...", month_key)
                try:
                    from app.services.paper_validation import rebalance_monthly
                    res = rebalance_monthly()
                    logger.info("Monthly rebalance: %s", res)
                except Exception as e:
                    logger.error(f"Paper validation monthly rebalance failed: {e}", exc_info=True)

            # --- MONTHLY AAOIFI FINANCIAL RE-SCREEN (halal universe hygiene) ---
            # 1st of the month ~02:30 ET (off-hours, heavy): recompute the debt /
            # liquidity ratios for the curated universe and prune names that drifted
            # over the 33% AAOIFI limit (the curated list only proves the SECTOR
            # screen). Keeps the halal universe genuinely compliant over time.
            if (now.day <= 3 and now.hour == 2 and 30 <= now.minute < 40
                    and last_halal_rescreen != month_key):
                last_halal_rescreen = month_key
                logger.info("Halal re-screen: monthly AAOIFI financial check (%s)...", month_key)
                try:
                    from app.services.halal_rescreen import (
                        rescreen_universe, apply_rescreen, load_universe,
                    )
                    res = rescreen_universe(load_universe(), sleep=0.2)
                    plan = apply_rescreen(res, dry_run=False)
                    logger.info("Halal re-screen: pruned %d (%d->%d); unknown=%d",
                                len(plan["removed"]), plan["before_count"],
                                plan["after_count"], len(res["unknown"]))
                except Exception as e:
                    logger.error(f"Halal monthly re-screen failed: {e}", exc_info=True)

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

        # Get portfolio info from first strategy account (no legacy keys)
        from app.config import STRATEGY_CONFIGS
        _sid = next(iter(STRATEGY_CONFIGS), None)
        account = alpaca_get_account(strategy_id=_sid)
        portfolio_info = None
        if account:
            daily_pl = account["equity"] - account["last_equity"]
            portfolio_info = {"equity": account["equity"], "daily_pl": daily_pl}

        alert_daily_summary(results, portfolio_info)
        logger.info(f"Pre-market briefing sent: {len(results)} stocks, {len(strong_buys)} STRONG BUY")

    # Run Ready-to-Trade full scan (pre-warm for dashboard)
    try:
        logger.info("Pre-market: running Ready-to-Trade scan (25 stocks)...")
        ready_results = hs.run_ready_to_trade(min_swing=55, max_stocks=25)
        if ready_results:
            header = ready_results[0]
            ready_count = header.get("Ready to Trade", 0)
            rejected = header.get("Rejected by AI", "")

            # Send Telegram summary
            if ready_count > 0:
                lines = [f"READY TO TRADE — {ready_count} stocks\n"]
                for r in ready_results[1:]:
                    if "Symbol" in r:
                        lines.append(
                            f"  {r['Symbol']}: {r['Verdict']} ({r['Confidence %']}%)\n"
                            f"    Price: ${r['Price']} | SL: ${r['Stop Loss']} | TP: ${r['TP1']}"
                        )
                tg_send("\n".join(lines))
            else:
                tg_send(
                    f"READY TO TRADE — 0 stocks\n\n"
                    f"All candidates rejected by AI consensus.\n"
                    f"Rejected: {rejected[:200]}"
                )
            logger.info(f"Ready-to-Trade: {ready_count} stocks ready")
    except Exception as e:
        logger.error(f"Ready-to-Trade scan failed: {e}")

    # Send multi-strategy comparison
    alert_strategy_comparison()

    # --- Signals Advisor: STRONG BUY scan across the halal universe ---
    # Sends one Telegram alert per qualifying signal (per strategy).
    # Independent of auto-trade.
    try:
        logger.info("Pre-market: running signals advisor (full universe)...")
        from app.services.signals_advisor import scan_and_notify_strong_buys

        summary = scan_and_notify_strong_buys(
            strategy_ids=("A", "C"),  # B disabled — poor WR 25%
            min_confidence=45.0,  # Quality gate: only high-conviction signals
            account_usd=100000.0,
            skip_usx=False,  # USX V4 pre-filter enabled — quality gate
        )
        logger.info(
            "Signals advisor: sent=%s by_strategy=%s",
            summary.get("sent"),
            summary.get("by_strategy"),
        )
        # Send a header summary so the operator sees totals at a glance.
        sent = summary.get("sent", 0)
        by_strat = summary.get("by_strategy", {})
        total = sum(int(v) for v in by_strat.values())
        if total > 0:
            tg_send(
                f"PRE-MARKET SIGNALS — {total} STRONG BUY across A/B/C\n"
                f"A={by_strat.get('A',0)} B={by_strat.get('B',0)} C={by_strat.get('C',0)}\n"
                f"Sent {sent} individual Telegram alerts above. "
                "Review charts and execute via Alpaca."
            )
        else:
            tg_send("PRE-MARKET SIGNALS — no STRONG BUY met the 60% threshold today.")
    except Exception as e:
        logger.error(f"Signals advisor pre-market scan failed: {e}", exc_info=True)

    gc.collect()


def _run_market_scan():
    """Market hours: run all 3 strategies SEQUENTIALLY on their target stocks."""
    import halal_screener as hs
    from app.config import STRATEGY_CONFIGS
    from app.services.regime import refresh_regime

    try:
        refresh_regime()
    except Exception as e:
        logger.error(f"Regime refresh failed before market scan: {e}", exc_info=True)

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

    # ===== STRATEGY B: Mean Reversion — DISABLED (A-Pre.1, Sharpe -1.93, WR 26.3%) =====

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


def _run_signals_scan(label: str = "intraday"):
    """Run the signals advisor (full halal universe, all 3 strategies)
    and push STRONG BUY alerts to Telegram. Used by the intraday slots
    (10:30/12:00/14:30/16:30 ET) so the operator gets manual-execution
    candidates throughout the session, not only at pre-market."""
    from app.services.signals_advisor import scan_and_notify_strong_buys
    from app.services.telegram_alert import send_message as tg_send

    # Refresh the unified context bundle so intraday signals condition on the
    # current regime/posture (and any posture flip is logged + version-bumped).
    try:
        from app.services.market_context_bundle import get_context_bundle_sync
        _ctx = get_context_bundle_sync(force=True)
        logger.info("intraday context [%s]: regime=%s posture=%s",
                    label, _ctx.get("regime"), _ctx.get("risk_posture"))
    except Exception as _ctx_exc:
        logger.debug("intraday context bundle failed: %s", _ctx_exc)

    # Confidence threshold lowered 70 -> 60 to roughly double the
    # daily signal count. This helps the operator accumulate enough
    # trades without having to expand the universe yet.
    summary = scan_and_notify_strong_buys(
        strategy_ids=("A", "C"),  # B disabled — poor WR 25%
        min_confidence=45.0,  # Quality gate: only high-conviction signals
        account_usd=100000.0,
        skip_usx=False,  # USX V4 pre-filter enabled — quality gate
    )
    sent = summary.get("sent", 0)
    by_strat = summary.get("by_strategy", {})
    total = sum(int(v) for v in by_strat.values())
    if total > 0:
        tg_send(
            f"PRE-MARKET SIGNALS — {label}: {total} STRONG BUY across A/B/C\n"
            f"A={by_strat.get('A',0)} B={by_strat.get('B',0)} C={by_strat.get('C',0)}\n"
            f"Sent {sent} individual alerts above. Review and execute via Alpaca."
        )
    logger.info(
        f"signals advisor [{label}]: total={total} sent={sent} by_strategy={by_strat}"
    )


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

    # Roadmap 1.6 — End of Day Report
    try:
        from app.services.telegram_alert import alert_end_of_day_report
        from app.services.market_context import get_market_context, get_market_status

        mkt_status = get_market_status()
        mkt_ctx = get_market_context()

        passed = len([r for r in results if r.get("swing_score", 0) >= 65])
        qual = [r for r in results if r.get("swing_score", 0) >= 75][:5]
        watch = [r for r in results if 60 <= r.get("swing_score", 0) < 75][:3]

        alert_end_of_day_report(
            market_status={**mkt_status, **mkt_ctx},
            total_scanned=len(results),
            passed_gates=passed,
            qualified_signals=qual,
            watch_signals=watch,
            pipeline_runtime=None,
            system_status="operational",
        )
    except Exception as e:
        logger.warning("End of day report failed: %s", e)

    gc.collect()


def _run_optimizer():
    """Weekly optimizer: tune strategy parameters using walk-forward sweep."""
    from app.services.optimizer import optimize_params
    from app.services.telegram_alert import send_message as tg_send

    tg_send("WEEKLY OPTIMIZER\n\nRunning parameter optimization on 13 stocks...")

    result = optimize_params()

    if "error" in result:
        tg_send(f"OPTIMIZER FAILED\n\n{result['error']}")
        return

    baseline = result.get("baseline_performance", {})
    optimal = result.get("optimal_performance", {})
    improvement = result.get("improvement", {})
    params = result.get("optimal_params", {})

    msg = (
        f"OPTIMIZER COMPLETE\n\n"
        f"Stocks tested: {len(result.get('stocks_tested', []))}\n\n"
        f"BASELINE (current):\n"
        f"  Win Rate: {baseline.get('win_rate', 0)}%\n"
        f"  Profit Factor: {baseline.get('profit_factor', 0)}\n"
        f"  Sharpe: {baseline.get('sharpe', 0)}\n"
        f"  Trades: {baseline.get('n_trades', 0)}\n\n"
        f"OPTIMIZED (recommended):\n"
        f"  Win Rate: {optimal.get('win_rate', 0)}% ({improvement.get('win_rate_delta', 0):+.1f})\n"
        f"  Profit Factor: {optimal.get('profit_factor', 0)} ({improvement.get('profit_factor_delta', 0):+.2f})\n"
        f"  Sharpe: {optimal.get('sharpe', 0)} ({improvement.get('sharpe_delta', 0):+.2f})\n"
        f"  Trades: {optimal.get('n_trades', 0)}\n\n"
        f"BEST PARAMS:\n"
        f"  Votes: {params.get('strong_buy_votes')}\n"
        f"  Confidence: {params.get('min_confidence')}%\n"
        f"  SL ATR: {params.get('sl_atr_mult')}x\n"
        f"  TP ATR: {params.get('tp1_atr_mult')}x\n"
        f"  MC Prob: {params.get('mc_prob_buy')}\n"
        f"  BB Width: {params.get('bb_squeeze_width')}\n"
        f"  ROC: {params.get('momentum_roc_threshold')}"
    )
    tg_send(msg)

    logger.info(
        f"Optimizer: WR {baseline.get('win_rate')}% -> {optimal.get('win_rate')}% | "
        f"PF {baseline.get('profit_factor')} -> {optimal.get('profit_factor')}"
    )

    gc.collect()


_DRIFT_RETRAIN = float(os.environ.get("DRIFT_RETRAIN_THRESHOLD", "0.25"))
_DRIFT_HALT = float(os.environ.get("DRIFT_HALT_THRESHOLD", "0.35"))


def _check_drift_before_retrain() -> dict:
    """Check feature drift on representative market data (SPY).

    Returns:
        {"max_psi": float, "drifted_features": list[str], "status": str}
        where status is one of: "ok", "drift_retrain", "drift_halt".
    """
    try:
        from app.services.drift_monitor import DriftMonitor
        from app.services.market_data import fetch
        from openbb_forecast.data.preprocessing import create_features

        df = fetch("SPY", period="1y")
        if df is None or len(df) < 60:
            return {"max_psi": 0.0, "drifted_features": [], "status": "ok", "error": "no_data"}

        feat_df = create_features(df)
        monitor = DriftMonitor()
        refs = monitor.list_references()

        market_features = [c for c in feat_df.columns if c in ("returns", "volatility_5",
            "volatility_10", "momentum_5", "momentum_10", "rsi_14", "macd", "volume_ratio",
            "high_low_range", "open_close_range")]

        drifted = []
        max_psi = 0.0
        for col in market_features:
            if col not in feat_df.columns:
                continue
            ref_name = f"SPY_{col}"
            if ref_name not in refs:
                continue
            result = monitor.check_numeric(ref_name, feat_df[col].dropna().values.tolist())
            psi = result.get("psi", 0.0)
            max_psi = max(max_psi, psi)
            if result.get("drifted"):
                drifted.append(col)

        if max_psi >= _DRIFT_HALT:
            return {"max_psi": max_psi, "drifted_features": drifted, "status": "drift_halt"}
        if max_psi >= _DRIFT_RETRAIN:
            return {"max_psi": max_psi, "drifted_features": drifted, "status": "drift_retrain"}
        return {"max_psi": max_psi, "drifted_features": drifted, "status": "ok"}
    except Exception as e:
        logger.warning("Drift check skipped: %s", e)
        return {"max_psi": 0.0, "drifted_features": [], "status": "ok", "error": str(e)}


def _run_train_models():
    """Nightly persisted-model retraining hook with drift gating."""
    drift = _check_drift_before_retrain()

    if drift["status"] == "drift_halt":
        from app.services.telegram_alert import send_message as tg_send
        tg_send(
            f"⚠️ *DRIFT HALT* — retrain skipped\n\n"
            f"Max PSI={drift['max_psi']:.3f} (threshold={_DRIFT_HALT})\n"
            f"Drifted features: {', '.join(drift['drifted_features'])}\n\n"
            f"Model pipeline halted until drift resolves."
        )
        logger.warning("Drift halt (PSI=%.3f) — retrain skipped", drift["max_psi"])
        return

    if drift["status"] == "drift_retrain":
        logger.warning("Drift retrain (PSI=%.3f) — forcing retrain", drift["max_psi"])

    completed = _run_repo_script("scripts/train_models.py")
    logger.info("train_models.py completed: %s", completed.stdout.strip())


def _run_pretrain_ml():
    """Pre-train DQN/PG models for the universe and cache in DB.

    Runs at 3 AM ET (after model retrain at 2 AM). Fills
    ``ModelResultsCache`` so API/consensus calls read pre-computed
    results instead of blocking on on-the-fly training.
    """
    try:
        from app.services.ml_pretrain import pretrain_universe
        trained = pretrain_universe(max_symbols=200, episodes=10)
        logger.info("ML pretrain complete: %d models cached", trained)
    except ImportError:
        logger.warning("app.worker.ml_pretrain not available — skipping")
    except Exception as e:
        logger.error("ML pretrain failed: %s", e, exc_info=True)


def _run_signal_audit():
    """Daily signal drift audit hook."""
    completed = _run_repo_script("scripts/audit_signals.py")
    logger.info("audit_signals.py completed: %s", completed.stdout.strip())


def _run_pairs_scan():
    """Daily halal pairs cycle (Phase 3) — PAPER-validated (PVP ledger).

    Scans for cointegrated pairs and records long-only relative-value entries into the
    isolated PVP paper ledger (matures open trades on z-reversion / stop / time cap).
    NO live broker orders until PVP graduates — same discipline as weekly PV / monthly
    PVM. run_pairs_cycle runs in dry_run for the observable signal list + Telegram.
    """
    from app.services.pairs_strategy import run_pairs_cycle
    from app.services.paper_validation import record_pairs_signals, mature_pairs_paper_trades
    from app.services.telegram_alert import send_message as tg_send

    # PVP paper ledger: mature open trades first, then record new entries.
    matured = mature_pairs_paper_trades()
    recorded = record_pairs_signals()

    summary = run_pairs_cycle(dry_run=True)  # observable signals only — NO live orders
    pairs = summary.get("pairs_scanned", 0)
    rec_n = recorded.get("recorded", 0) if isinstance(recorded, dict) else 0
    clo_n = matured.get("closed", 0) if isinstance(matured, dict) else 0
    logger.info("Pairs cycle (paper): %d pairs, recorded=%s, matured=%s", pairs, recorded, matured)

    if rec_n or clo_n:
        tg_send(
            f"PAIRS PAPER LEDGER (PVP)\n\n"
            f"Cointegrated pairs scanned: {pairs}\n"
            f"New paper entries: {rec_n} | Matured/closed: {clo_n}\n"
            f"(paper-only — no real money until PVP graduates)"
        )


def _run_reference_data_refresh():
    """Nightly refresh for tradable assets and cached earnings metadata."""
    from app.services.reference_data import get_tradable_symbols, refresh_earnings_calendar

    symbols = sorted(get_tradable_symbols(force_refresh=True))
    if symbols:
        refresh_earnings_calendar(symbols[:355])
    logger.info("reference-data refresh completed: symbols=%s", len(symbols))


def _run_weight_fit():
    """Monthly walk-forward composite-weight fit (Chan, Ch.1-2-4) — the validation LOOP.

    Self-gating: needs >=100 buy signals + >=3 training months + OOS rank-IC > baseline +
    permutation p<0.05 + DSR>=0.6. Writes the artifact to the DURABLE CACHE_DIR so
    tech_score_v2 auto-adopts v2 ONLY when it passes OOS; otherwise it stays on v1
    (literature priors). No-op-safe on thin data — this OPERATIONALIZES the loop without
    overfitting on insufficient data (it does nothing until ~3 months exist).
    """
    import os as _os
    from scripts.fit_composite_weights import fit_composite_weights, write_artifact

    cache_dir = _os.environ.get("CACHE_DIR") or _os.path.join(_repo_root(), ".cache")
    _os.makedirs(cache_dir, exist_ok=True)
    artifact = _os.path.join(cache_dir, "tech_v2_weights.json")
    result = fit_composite_weights(days=365)
    write_artifact(result, path=artifact)
    logger.info("Composite-weight fit verdict=%s -> %s", result.get("verdict"), artifact)


def start_scheduler():
    """Start the background scheduler thread. Call once at app startup."""
    global _scheduler_thread, _scheduler_running

    if _scheduler_running:
        logger.info("Scheduler already running")
        return

    _scheduler_running = True
    scheduler_metrics.mark_started()
    _scheduler_thread = threading.Thread(target=_scheduler_loop, daemon=True, name="scheduler")
    _scheduler_thread.start()
    logger.info("Background scheduler thread started")


def stop_scheduler():
    """Stop the scheduler gracefully."""
    global _scheduler_running
    _scheduler_running = False
    scheduler_metrics.mark_stopped()
    logger.info("Scheduler stopping...")
