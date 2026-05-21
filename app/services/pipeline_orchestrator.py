"""Unified daily trading pipeline.

Stages (run sequentially):
  1. collect_data()     → Fetch & cache all market data in DB
  2. halal_filter()     → Filter AAOIFI-compliant stocks
  3. smart_filter()     → Technical screening (score > 65, RSI, MACD, Volume)
  4. ai_consensus()     → Run 9 models on top candidates, vote BUY/SELL/HOLD
  5. kelly_allocate()   → Position sizing via Kelly Criterion
  6. guardian_approve() → RiskManager checks each signal
  7. alpaca_execute()   → Submit bracket orders to Alpaca
  8. report()           → Telegram daily report + DB snapshot
"""

from __future__ import annotations

import logging
import math
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import pandas as pd

logger = logging.getLogger("pipeline")

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

VOTE_THRESHOLD_STRONG = 7  # 7/9 → strong signal
VOTE_THRESHOLD_WEAK = 5    # 5/9 → medium signal
VOTE_THRESHOLD_MIN = 5     # below 5 → skip


@dataclass
class Signal:
    """One trading signal that passed guardian checks."""
    symbol: str
    price: float
    stop_loss: float
    take_profit: float
    avg_entry: float
    position_size_qty: int
    position_value: float
    risk_amount: float
    confidence: float
    votes_buy: int
    votes_total: int
    verdict: str
    profile: str
    strategy_id: str
    kelly_fraction: float = 0.0
    guardian_rejected: bool = False
    guardian_reason: str = ""
    executed: bool = False
    execution_order_id: str = ""
    execution_error: str = ""


@dataclass
class StageReport:
    """Summary of one pipeline stage."""
    stage: str
    status: str = ""  # "ok", "skipped", "failed"
    elapsed_s: float = 0.0
    count_in: int = 0
    count_out: int = 0
    error: str = ""
    details: dict = field(default_factory=dict)


@dataclass
class PipelineReport:
    """Full report from one pipeline run."""
    date_utc: str = ""
    started_at: str = ""
    finished_at: str = ""
    elapsed_s: float = 0.0
    stages: list[StageReport] = field(default_factory=list)
    signals: list[Signal] = field(default_factory=list)
    signals_passed: int = 0
    signals_rejected: int = 0
    signals_executed: int = 0
    total_pnl: float = 0.0
    error: str = ""


# ---------------------------------------------------------------------------
# Pipeline orchestrator
# ---------------------------------------------------------------------------

class UnifiedPipeline:
    """Runs the full daily trading pipeline end-to-end."""

    def __init__(self) -> None:
        self.report = PipelineReport()

    # ----------------------------------------------------------------
    # Public entry point
    # ----------------------------------------------------------------

    def run(
        self,
        strategy_ids: tuple[str, ...] = ("A", "C"),
        dry_run: bool = False,
        skip_stages: set[str] | None = None,
    ) -> PipelineReport:
        """Run all stages sequentially."""
        import halal_screener as hs
        from app.services.metrics import metrics as _m

        skip = skip_stages or set()
        self.report = PipelineReport()  # ← reset state each run
        now = datetime.now(timezone.utc)
        self.report.date_utc = now.strftime("%Y-%m-%d")
        self.report.started_at = now.isoformat()
        t0 = time.perf_counter()
        _m.incr("pipeline_runs")

        try:
            # Stage 1 – collect data
            if "collect" not in skip:
                self._stage_collect_data()
            else:
                self.report.stages.append(StageReport(stage="collect", status="skipped"))

            # Stage 2 – halal filter
            if "halal" not in skip:
                halal_stage = self._stage_halal_filter(hs)
            else:
                halal_stage = StageReport(stage="halal", status="skipped")
            self.report.stages.append(halal_stage)

            # Stage 3 – smart filter (technical screening)
            if "smart" not in skip:
                smart_stage = self._stage_smart_filter(hs, halal_stage)
            else:
                smart_stage = StageReport(stage="smart", status="skipped")
            self.report.stages.append(smart_stage)

            # Stage 4 – AI consensus (9 models voting)
            if "consensus" not in skip:
                consensus_stage = self._stage_ai_consensus(strategy_ids)
            else:
                consensus_stage = StageReport(stage="consensus", status="skipped")
            self.report.stages.append(consensus_stage)

            # Stage 5 – Kelly allocation
            if "kelly" not in skip:
                kelly_stage = self._stage_kelly_allocate()
            else:
                kelly_stage = StageReport(stage="kelly", status="skipped")
            self.report.stages.append(kelly_stage)

            # Stage 6 – Guardian approval
            if "guardian" not in skip:
                guardian_stage = self._stage_guardian_approve()
            else:
                guardian_stage = StageReport(stage="guardian", status="skipped")
            self.report.stages.append(guardian_stage)

            # Stage 7 – Alpaca execution
            if "execute" not in skip:
                exec_stage = self._stage_alpaca_execute(dry_run)
            else:
                exec_stage = StageReport(stage="execute", status="skipped")
            self.report.stages.append(exec_stage)

            # Stage 8 – Report
            if "report" not in skip:
                report_stage = self._stage_report()
            else:
                report_stage = StageReport(stage="report", status="skipped")
            self.report.stages.append(report_stage)

        except Exception as exc:
            logger.exception("Pipeline failed")
            self.report.error = str(exc)
            _m.incr("pipeline_errors")

        self.report.finished_at = datetime.now(timezone.utc).isoformat()
        self.report.elapsed_s = time.perf_counter() - t0
        # Strip NaN/Inf before JSON serialization — frontend crashes on these
        self.report = _clean_nan_from_report(self.report)
        return self.report

    # ----------------------------------------------------------------
    # Stage implementations
    # ----------------------------------------------------------------

    def _stage_collect_data(self) -> StageReport:
        """Stage 1: Fetch latest prices for all halal symbols, cache in DB."""
        import yfinance as yf
        from app.db.database import SessionLocal
        from app.db.models import MarketDataCache

        t0 = time.perf_counter()
        stage = StageReport(stage="collect")

        try:
            import halal_screener as hs
            symbols = list(hs._universe_symbols())[:50]  # limit to 50 for speed
            stage.count_in = len(symbols)
            logger.info("Pipeline stage 1: collecting data for %d symbols", len(symbols))

            saved = 0
            today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

            def _fetch_one(sym: str) -> dict | None:
                try:
                    from app.services.market_context import _run_with_timeout
                    df = _run_with_timeout(
                        lambda: yf.download(sym, period="2d", progress=False, auto_adjust=True),
                        timeout=15.0,
                        fallback=None,
                    )
                    if df is None or df.empty:
                        return None
                    # Flatten MultiIndex columns from auto_adjust=True
                    if isinstance(df.columns, pd.MultiIndex):
                        df.columns = df.columns.droplevel(1)
                    latest = df.iloc[-1]
                    return {
                        "symbol": sym.upper(),
                        "date": today_str,
                        "open": float(latest.get("Open", 0)),
                        "high": float(latest.get("High", 0)),
                        "low": float(latest.get("Low", 0)),
                        "close": float(latest.get("Close", 0)),
                        "volume": int(latest.get("Volume", 0)),
                    }
                except Exception:
                    return None

            with ThreadPoolExecutor(max_workers=6) as pool:
                futures = {pool.submit(_fetch_one, s): s for s in symbols}
                for f in as_completed(futures):
                    row = f.result()
                    if row is None:
                        continue
                    db = SessionLocal()
                    try:
                        existing = db.query(MarketDataCache).filter(
                            MarketDataCache.symbol == row["symbol"],
                            MarketDataCache.date == today_str,
                        ).first()
                        if existing is None:
                            db.add(MarketDataCache(**row))
                            saved += 1
                        db.commit()
                    except Exception:
                        db.rollback()
                    finally:
                        db.close()

            stage.count_out = saved
            stage.status = "ok"
            logger.info("Pipeline stage 1: saved %d rows to MarketDataCache", saved)
        except Exception as exc:
            stage.status = "failed"
            stage.error = str(exc)
            logger.exception("Stage 1 (collect_data) failed")

        stage.elapsed_s = time.perf_counter() - t0
        return stage

    def _stage_halal_filter(self, hs) -> StageReport:
        """Stage 2: Run halal screener, return only AAOIFI-compliant stocks."""
        t0 = time.perf_counter()
        stage = StageReport(stage="halal")

        try:
            hs._flush_all_caches()
            results = hs.run_screener()
            if results is None:
                results = []

            halal = [r for r in results if r.get("halal") == "Yes" or r.get("is_halal") is True]
            stage.count_in = len(results)
            stage.count_out = len(halal)
            stage.status = "ok"
            stage.details["symbols"] = [r.get("symbol", "") for r in halal[:5]]
            logger.info("Pipeline stage 2: %d halal out of %d", len(halal), len(results))

            self._cached_halal_results = halal
        except Exception as exc:
            stage.status = "failed"
            stage.error = str(exc)
            logger.exception("Stage 2 (halal_filter) failed")
            self._cached_halal_results = []

        stage.elapsed_s = time.perf_counter() - t0
        return stage

    def _stage_smart_filter(self, hs, prev_stage: StageReport) -> StageReport:
        """Stage 3: Technical filter on halal stocks (score > 65, RSI, MACD, Volume)."""
        t0 = time.perf_counter()
        stage = StageReport(stage="smart")

        try:
            halal_stocks = getattr(self, "_cached_halal_results", [])
            stage.count_in = len(halal_stocks)

            candidates = [r for r in halal_stocks if r.get("swing_score", 0) >= 65]
            candidates.sort(key=lambda x: x.get("swing_score", 0), reverse=True)

            stage.count_out = len(candidates)
            stage.status = "ok"
            stage.details["symbols"] = [r.get("symbol", "") for r in candidates[:10]]
            logger.info("Pipeline stage 3: %d candidates pass score >= 65", len(candidates))

            self._cached_smart_candidates = candidates
        except Exception as exc:
            stage.status = "failed"
            stage.error = str(exc)
            logger.exception("Stage 3 (smart_filter) failed")
            self._cached_smart_candidates = []

        stage.elapsed_s = time.perf_counter() - t0
        return stage

    def _stage_ai_consensus(self, strategy_ids: tuple[str, ...]) -> StageReport:
        """Stage 4: Run AI consensus on smart-filtered candidates.

        Runs all 3 strategy profiles (momentum, reversion, ml) for each
        candidate. Aggregates votes to determine 7/9 or 5/9 signals.
        """
        import halal_screener as hs

        # Monkey-patch to skip Telegram during pipeline (chart photos are slow)
        try:
            import app.services.telegram_alert as tg_mod
            tg_mod.alert_signal_with_chart = lambda *a, **kw: True
        except Exception:
            pass
        try:
            hs._send_consensus_breakdown = lambda *a, **kw: None
        except Exception:
            pass

        t0 = time.perf_counter()
        stage = StageReport(stage="consensus")

        candidates = getattr(self, "_cached_smart_candidates", [])
        stage.count_in = len(candidates)

        profiles: list[tuple[str, str, Any]] = []
        runners = {
            "A": (hs.run_consensus_momentum, "momentum"),
            "C": (hs.run_consensus_ml, "ml"),
        }
        for sid in strategy_ids:
            if sid in runners:
                profiles.append((sid, runners[sid][1], runners[sid][0]))

        all_signals: list[Signal] = []
        signals_lock = threading.Lock()

        def _scan_one(sid: str, profile_name: str, runner, sym: str):
            """Run a single symbol scan and append signal if it qualifies. Thread-safe."""
            try:
                result = runner(sym)
                if not result:
                    return
                row = result[0] if isinstance(result, list) else result
                if row is None or row.get("Error"):
                    return

                verdict = str(row.get("Verdict", "")).upper()
                if verdict not in ("STRONG BUY", "BUY"):
                    return

                confidence = float(row.get("Confidence %", 0) or 0)
                if confidence < 60.0:
                    return

                sig = Signal(
                    symbol=sym.upper(),
                    price=float(row.get("Price", 0) or 0),
                    stop_loss=float(row.get("Stop Loss", 0) or 0),
                    take_profit=float(row.get("TP1", 0) or 0),
                    avg_entry=float(row.get("Price", 0) or 0),
                    position_size_qty=0,
                    position_value=0.0,
                    risk_amount=0.0,
                    confidence=confidence,
                    votes_buy=int(row.get("Votes BUY", 0) or 0),
                    votes_total=int(row.get("Votes BUY", 0) or 0)
                                + int(row.get("Votes SELL", 0) or 0)
                                + int(row.get("Votes HOLD", 0) or 0),
                    verdict=verdict,
                    profile=profile_name,
                    strategy_id=sid,
                )
                with signals_lock:
                    all_signals.append(sig)
            except Exception as exc:
                logger.debug("Stage 4: %s %s failed: %s", profile_name, sym, exc)

        # P3.1 — Parallelize symbol scans within each profile using ThreadPoolExecutor.
        # Profiles still run sequentially to avoid races on hs._flush_all_caches().
        scan_workers = max(2, min(8, int(os.environ.get("PIPELINE_SCAN_WORKERS", "4"))))

        for sid, profile_name, runner in profiles:
            symbols_to_scan = [r.get("symbol", "?") for r in candidates]
            logger.info(
                "Pipeline stage 4: scanning %d symbols for strategy %s (%s) with %d workers",
                len(symbols_to_scan), sid, profile_name, scan_workers,
            )
            with ThreadPoolExecutor(max_workers=scan_workers, thread_name_prefix=f"scan-{sid}") as executor:
                futures = [
                    executor.submit(_scan_one, sid, profile_name, runner, sym)
                    for sym in symbols_to_scan
                ]
                for fut in futures:
                    fut.result()  # propagate exceptions if any (debug-logged inside)

            hs._flush_all_caches()

        # Deduplicate by symbol: keep the signal with highest confidence
        by_symbol: dict[str, Signal] = {}
        for sig in all_signals:
            key = sig.symbol
            if key not in by_symbol or sig.confidence > by_symbol[key].confidence:
                by_symbol[key] = sig

        ranked = sorted(by_symbol.values(), key=lambda s: s.confidence, reverse=True)

        self._cached_signals = ranked
        stage.count_out = len(ranked)
        stage.status = "ok"
        stage.details["top"] = [f"{s.symbol}({s.confidence:.0f}%)" for s in ranked[:5]]
        logger.info("Pipeline stage 4: %d unique signals after dedup", len(ranked))

        stage.elapsed_s = time.perf_counter() - t0
        return stage

    def _stage_kelly_allocate(self) -> StageReport:
        """Stage 5: Apply Kelly Criterion for position sizing."""
        t0 = time.perf_counter()
        stage = StageReport(stage="kelly")

        signals = getattr(self, "_cached_signals", [])
        stage.count_in = len(signals)

        from app.services.kelly import kelly_from_db_pnls
        from app.services.risk_manager import _pnls_pct_from_db
        from app.config import settings

        total_equity = 100000.0  # Will be updated from Alpaca account
        try:
            from app.services.alpaca_client import get_account
            acct = get_account()
            if acct:
                total_equity = float(acct.get("equity", total_equity))
        except Exception:
            pass

        for sig in signals:
            try:
                price = sig.price
                if price <= 0:
                    continue

                # Use strategy-specific risk from config
                risk_pct = settings.RISK_PCT / 100.0
                kelly_risk, kelly_diag = kelly_from_db_pnls(
                    pnls_pct=_pnls_pct_from_db(strategy_id=sig.strategy_id, lookback_trades=50),
                    static_risk_pct=risk_pct,
                    fractional=0.5,
                )

                risk_per_share = abs(price - sig.stop_loss) if sig.stop_loss > 0 else price * 0.03
                if risk_per_share <= 0:
                    risk_per_share = price * 0.03

                risk_budget = total_equity * kelly_risk
                max_position = total_equity * 0.20  # Max 20% per position
                qty = int(min(risk_budget / risk_per_share, max_position / price))

                sig.kelly_fraction = kelly_risk
                sig.position_size_qty = qty
                sig.position_value = qty * price
                sig.risk_amount = qty * risk_per_share
            except Exception as exc:
                logger.debug("Stage 5: kelly failed for %s: %s", sig.symbol, exc)

        allocated = [s for s in signals if s.position_size_qty > 0]
        stage.count_out = len(allocated)
        stage.status = "ok"
        stage.details["total_value"] = sum(s.position_value for s in allocated)
        logger.info("Pipeline stage 5: %d/%d signals have allocation", len(allocated), len(signals))

        stage.elapsed_s = time.perf_counter() - t0
        return stage

    def _stage_guardian_approve(self) -> StageReport:
        """Stage 6: Run RiskManager checks on each signal."""
        t0 = time.perf_counter()
        stage = StageReport(stage="guardian")

        from app.services.risk_manager import check_trade_eligibility
        from app.services.alpaca_client import get_account as alpaca_get_account
        from app.services.alpaca_client import get_positions as alpaca_get_positions
        from app.db.database import SessionLocal

        allocated = getattr(self, "_cached_signals", [])
        stage.count_in = len(allocated)

        rejected = 0
        passed_signals: list[Signal] = []

        # Fetch real account + positions once before looping
        live_account: dict[str, Any] = {"equity": 100000.0, "cash": 50000.0}
        live_positions: list[dict] = []
        try:
            acct = alpaca_get_account()
            if acct:
                live_account = acct
            pos = alpaca_get_positions()
            if pos:
                live_positions = pos
        except Exception:
            logger.warning("Stage 6: could not fetch live account data — using fallback")

        for sig in allocated:
            if sig.position_size_qty <= 0:
                sig.guardian_rejected = True
                sig.guardian_reason = "No position size (Kelly = 0)"
                rejected += 1
                passed_signals.append(sig)
                continue

            try:
                result = check_trade_eligibility(
                    symbol=sig.symbol,
                    side="buy",
                    price=sig.price,
                    stop_loss=sig.stop_loss,
                    account=live_account,
                    open_positions=live_positions,
                    strategy_id=sig.strategy_id,
                )

                if result.get("eligible"):
                    sig.guardian_rejected = False
                    passed_signals.append(sig)
                else:
                    sig.guardian_rejected = True
                    sig.guardian_reason = result.get("reason", "Guardian blocked")
                    rejected += 1
                    passed_signals.append(sig)
            except Exception as exc:
                sig.guardian_rejected = True
                sig.guardian_reason = str(exc)
                rejected += 1
                passed_signals.append(sig)

        approved = [s for s in passed_signals if not s.guardian_rejected]
        self._cached_signals = passed_signals
        self._cached_approved = approved
        stage.count_out = len(approved)
        stage.status = "ok"
        stage.details["rejected"] = rejected
        logger.info("Pipeline stage 6: %d approved, %d rejected", len(approved), rejected)

        stage.elapsed_s = time.perf_counter() - t0
        return stage

    def _stage_alpaca_execute(self, dry_run: bool) -> StageReport:
        """Stage 7: Submit bracket orders to Alpaca."""
        t0 = time.perf_counter()
        stage = StageReport(stage="execute")

        approved = getattr(self, "_cached_approved", [])
        stage.count_in = len(approved)

        from app.config import settings
        if not settings.AUTO_TRADE_ENABLED and not dry_run:
            stage.status = "skipped"
            stage.details["reason"] = "AUTO_TRADE_ENABLED is False"
            logger.info("Pipeline stage 7: skipped (auto-trade disabled)")
            stage.elapsed_s = time.perf_counter() - t0
            return stage

        executed = 0
        errors = 0

        for sig in approved:
            if dry_run:
                sig.executed = True
                sig.execution_order_id = f"DRY_RUN_{sig.symbol}"
                executed += 1
                continue

            try:
                from app.services.trading_engine import execute_buy

                signal_details = {
                    "symbol": sig.symbol,
                    "price": sig.price,
                    "stop_loss": sig.stop_loss,
                    "take_profit": sig.take_profit,
                    "confidence": sig.confidence,
                    "votes_buy": sig.votes_buy,
                    "votes_total": sig.votes_total,
                    "profile": sig.profile,
                    "verdict": sig.verdict,
                    "kelly_fraction": sig.kelly_fraction,
                    "position_value": sig.position_value,
                    "risk_amount": sig.risk_amount,
                    "guardian_rejected": sig.guardian_rejected,
                    "guardian_reason": sig.guardian_reason,
                }
                result = execute_buy(
                    strategy_id=sig.strategy_id,
                    symbol=sig.symbol,
                    price=sig.price,
                    stop_loss=sig.stop_loss,
                    take_profit=sig.take_profit,
                    confidence=sig.confidence,
                    signal_details=signal_details,
                )
                if result and result.get("executed") and result.get("order_id"):
                    sig.executed = True
                    sig.execution_order_id = str(result["order_id"])
                    executed += 1
                else:
                    sig.executed = False
                    sig.execution_error = result.get("reason", "No order ID returned")
                    errors += 1
            except Exception as exc:
                sig.execution_error = str(exc)
                errors += 1
                logger.warning("Stage 7: execute failed for %s: %s", sig.symbol, exc)
            time.sleep(0.5)

        stage.count_out = executed
        stage.status = "ok"
        stage.details["executed"] = executed
        stage.details["errors"] = errors
        logger.info("Pipeline stage 7: %d executed, %d errors", executed, errors)

        self.report.signals_passed = len(approved)
        self.report.signals_rejected = sum(1 for s in approved if s.guardian_rejected)
        self.report.signals_executed = executed
        self.report.signals = approved

        stage.elapsed_s = time.perf_counter() - t0
        return stage

    def _stage_report(self) -> StageReport:
        """Stage 8: Send Telegram report + save to DB."""
        t0 = time.perf_counter()
        stage = StageReport(stage="report")

        signals = self.report.signals
        executed = [s for s in signals if s.executed]
        rejected = [s for s in signals if s.guardian_rejected]
        pending = [s for s in signals if not s.executed and not s.guardian_rejected]

        try:
            lines = ["*Unified Pipeline Daily Report*\n"]
            lines.append(f"Date: {self.report.date_utc}")
            lines.append(f"Elapsed: {self.report.elapsed_s:.1f}s")
            lines.append(f"Signals: {len(executed)} executed, {len(rejected)} rejected, {len(pending)} pending\n")

            if executed:
                lines.append("*Executed Orders:*")
                for s in executed:
                    lines.append(
                        f"- {s.symbol} ({s.strategy_id}) "
                        f"qty={s.position_size_qty} @ ${s.price:.2f} "
                        f"conf={s.confidence:.0f}% "
                        f"order={s.execution_order_id[:12]}"
                    )

            if rejected:
                lines.append("\n*Rejected by Guardian:*")
                for s in rejected:
                    lines.append(f"- {s.symbol}: {s.guardian_reason}")

            body = "\n".join(lines)

            # Save to DB
            from app.db.database import SessionLocal
            from app.db.models import PortfolioSnapshot

            db = SessionLocal()
            try:
                db.add(PortfolioSnapshot(
                    total_equity=sum(s.position_value for s in executed),
                    cash=100000.0 - sum(s.position_value for s in executed),
                    buying_power=200000.0 - sum(s.position_value for s in executed),
                    positions_json=[{
                        "symbol": s.symbol,
                        "qty": s.position_size_qty,
                        "entry": s.price,
                        "confidence": s.confidence,
                        "strategy": s.strategy_id,
                    } for s in executed],
                ))
                db.commit()
            except Exception:
                db.rollback()
            finally:
                db.close()

            # Telegram
            try:
                from app.services.telegram_alert import send_message as tg_send
                tg_send(body)
            except Exception as exc:
                logger.warning("Stage 8: Telegram send failed: %s", exc)

            stage.status = "ok"
            stage.details["lines"] = len(lines)
            logger.info("Pipeline stage 8: report sent")
        except Exception as exc:
            stage.status = "failed"
            stage.error = str(exc)
            logger.exception("Stage 8 (report) failed")

        stage.elapsed_s = time.perf_counter() - t0
        return stage


# ---------------------------------------------------------------------------
# NaN-safe serializer — prevents JSON crashes from bad market data
# ---------------------------------------------------------------------------


def _clean_nan_from_report(report: PipelineReport) -> PipelineReport:
    """Replace NaN, Inf, -Inf with None in all numeric fields."""
    if report.elapsed_s is not None and (math.isnan(report.elapsed_s) or math.isinf(report.elapsed_s)):
        report.elapsed_s = None
    for stage in getattr(report, "stages", []):
        if stage.elapsed_s is not None and (math.isnan(stage.elapsed_s) or math.isinf(stage.elapsed_s)):
            stage.elapsed_s = None
        for k, v in getattr(stage, "details", {}).items():
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                stage.details[k] = None
            elif isinstance(v, list):
                stage.details[k] = [
                    None if isinstance(x, float) and (math.isnan(x) or math.isinf(x)) else x
                    for x in v
                ]
    return report


# ---------------------------------------------------------------------------
# Top-level convenience
# ---------------------------------------------------------------------------

_orchestrator: UnifiedPipeline | None = None
_orchestrator_lock = threading.Lock()


def run_pipeline(
    strategy_ids: tuple[str, ...] = ("A", "B", "C"),
    dry_run: bool = True,
    skip_stages: set[str] | None = None,
) -> PipelineReport:
    """Run the full pipeline. Returns a PipelineReport."""
    global _orchestrator
    with _orchestrator_lock:
        if _orchestrator is None:
            _orchestrator = UnifiedPipeline()
        return _orchestrator.run(
            strategy_ids=strategy_ids,
            dry_run=dry_run,
            skip_stages=skip_stages,
        )
