"""Paper-validation ledgers — accumulate REAL track records for BOTH scanners.

The paper_trade_gate graduation counter only moves on CLOSED trades that carry a
``pnl_pct``. The live broker close-recording path is incomplete, so instead this
keeps TWO ISOLATED simulated ledgers, one per scanner:

  WEEKLY ledger (strategy_id="PV") — the weekly swing scanner, Option-A exit:
    * record_weekly_picks(): take this week's `build_weekly_report` picks and
      insert them as OPEN paper trades (fixed 15% catastrophe stop), one per
      symbol (deduped against still-open trades).
    * mature_open_paper_trades(): for each open trade, replay the symbol's REAL
      price path and apply the validated Option-A exit (15% stop OR 20 trading-day
      time exit) via the already-tested `signal_tracker._simulate_fixed_exit`;
      when it matures, write pnl / pnl_pct / exit_price / closed_at.

  MONTHLY ledger (strategy_id="PVM") — the monthly composite scanner, rebalanced:
    * rebalance_monthly(): pull the live composite (deep-picks) ranking; CLOSE any
      held name that fell out of the top-N at its current price (pnl_pct written),
      OPEN the new entrants at their current price, and KEEP the rest. This is the
      classic cross-sectional rebalance — the "exit" is leaving the top-N, not a
      stop or a clock.

No Alpaca orders, no live-trading flags — simulated ledgers using REAL prices and
the exact policies we run. Two isolated ledgers = an honest A/B: "does the weekly
technical scanner have an edge?" vs "does the monthly fundamental scanner?" The
real money for either half is released ONLY after its ledger graduates.

NOTE: the weekly ledger keeps the existing id "PV" (not "PVW") so the already
deployed history and the paper_trade_gate counter survive without a migration.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from sqlalchemy.exc import SQLAlchemyError

from app.config import settings
from app.db.database import SessionLocal
from app.db.models import TradeHistory

logger = logging.getLogger("screener")

# Isolated paper-validation strategy ids (TradeHistory.strategy_id is <=5 chars).
PV_WEEKLY = "PV"     # weekly swing scanner — Option-A exit (15% stop / 20-day time)
PV_MONTHLY = "PVM"   # monthly composite scanner — rebalanced (hold top-N, drop the rest)
PV_PAIRS = "PVP"     # halal long-only relative-value pairs (cointegration) — z-reversion exit
PV_STRATEGY = PV_WEEKLY  # backward-compat alias (older callers / tests use PV_STRATEGY)


def _strategy_for(scanner: str | None) -> str:
    """Map a 'weekly' | 'monthly' | 'pairs' scanner label to its ledger strategy id."""
    s = str(scanner or "").lower()
    if s.startswith("pair"):
        return PV_PAIRS
    if s.startswith("month"):
        return PV_MONTHLY
    return PV_WEEKLY


# ── Ledger inception (non-destructive reset) ──────────────────────────────────
# A ledger's pre-inception rows are EXCLUDED from the reported stats + graduation
# (the rows stay in the DB for audit — this only resets what the ledger *reports*).
# The weekly (PV) ledger's pre-2026-06-27 rows were a single corrupt batch: duplicate
# weekly picks recorded 2026-06-11/12 right before a market drop, every one closed at a
# loss (0% win rate, several at the −15% catastrophe stop). They poisoned the win-rate
# and graduation gate, so we move the weekly ledger's inception forward and let it
# rebuild a clean track record. Per-strategy; env-tunable (PV_WEEKLY_INCEPTION, empty
# disables the cutoff). Monthly (PVM) / pairs (PVP) keep their full history.
_INCEPTION_ENV: dict[str, tuple[str, str]] = {
    PV_WEEKLY: ("PV_WEEKLY_INCEPTION", "2026-06-27"),
}


def ledger_inception(strategy_id: str) -> "datetime | None":
    """Stats/graduation cutoff for a ledger — trades created before it are ignored
    (kept in the DB, just not reported). None = count all history. Naive UTC to match
    the naive TradeHistory.created_at column. Read at call time so the cutoff can be
    retuned via env without a redeploy."""
    spec = _INCEPTION_ENV.get(strategy_id)
    if not spec:
        return None
    env_key, default = spec
    iso = (os.environ.get(env_key, default) or "").strip()
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso)
    except ValueError:
        logger.warning("bad ledger inception %r for %s — ignoring", iso, strategy_id)
        return None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _paper_row_from_pick(pick: dict) -> dict:
    """Map a weekly-report pick to TradeHistory column kwargs (pure, testable)."""
    return {
        "strategy_id": PV_STRATEGY,
        "symbol": pick.get("symbol"),
        "side": "buy",
        "qty": float(pick.get("shares") or 0),
        "entry_price": float(pick.get("entry") or 0),
        "stop_loss": pick.get("catastrophe_stop"),
        "take_profit": pick.get("far_take_profit"),
        "position_value": pick.get("position_value"),
        "risk_amount": pick.get("risk_amount"),
        "risk_pct": pick.get("risk_pct_realized"),
        "confidence": float(pick.get("confidence") or 0),
        "status": "open",
        "signal_details": {
            "source": "paper_validation",
            "verdict": pick.get("verdict"),
            "hold_days": pick.get("hold_days"),
            "stop_pct": pick.get("stop_pct"),
            "time_exit_date": pick.get("time_exit_date"),
            "votes": pick.get("votes"),
        },
    }


def _default_regime() -> dict:
    """Broad-market regime ({known, spy_bearish, spy_price, spy_ema21}) — SPY vs EMA21,
    the same signal the Analyze-card downtrend warning uses. Never raises."""
    try:
        from app.services.external_signals import _market_regime
        return _market_regime()
    except Exception:
        return {"known": False}


def record_weekly_picks(account: float = 10000.0, top: int = 15,
                        min_confidence: float = 45.0, funnel: str = "swing",
                        _regime_fn=None) -> dict:
    """Record this week's picks as OPEN paper trades (one per symbol, deduped).

    Sources from the SWING screener (funnel="swing") — the same picks the Weekly tab
    shows — not the AI-consensus pipeline, which yielded 0 BUY verdicts on Fly and left
    the ledger empty. So the recorded ledger now matches the displayed weekly scanner.
    """
    # Broad-market regime gate — the corrupt 2026-06-11/12 batch was a swing-long pile-in
    # right before a market drop, every trade a loss. Skip recording NEW weekly paper
    # entries when SPY is below its EMA21 (don't open swing longs into a downtrend). The
    # Weekly tab still SHOWS the signals + the downtrend warning — this gates ONLY what is
    # auto-recorded as a paper trade. Fail-open on unknown regime; env WEEKLY_REGIME_GATE
    # (default on), set false to record regardless (pure-validation data collection).
    if os.environ.get("WEEKLY_REGIME_GATE", "true").strip().lower() in ("true", "1", "yes", "on"):
        try:
            regime = (_regime_fn or _default_regime)()
            if regime.get("known") and regime.get("spy_bearish"):
                logger.info("weekly record skipped — SPY %.2f < EMA21 %.2f (regime gate)",
                            regime.get("spy_price") or 0.0, regime.get("spy_ema21") or 0.0)
                return {"recorded": 0, "skipped": 0, "reason": "spy_bearish",
                        "spy_price": regime.get("spy_price"), "spy_ema21": regime.get("spy_ema21")}
        except Exception as e:
            logger.debug("weekly regime gate failed (allowing record): %s", e)

    from app.services.weekly_report import build_weekly_report

    report = build_weekly_report(account, top=top, min_confidence=min_confidence, funnel=funnel)
    picks = report.get("picks", []) if isinstance(report, dict) else []
    if not picks:
        return {"recorded": 0, "skipped": 0, "reason": "no picks"}

    db = SessionLocal()
    try:
        open_syms = {
            r[0] for r in db.query(TradeHistory.symbol).filter(
                TradeHistory.strategy_id == PV_STRATEGY,
                TradeHistory.pnl_pct.is_(None),
            ).all()
        }
        recorded = skipped = 0
        for p in picks:
            sym = p.get("symbol")
            if not sym or float(p.get("shares") or 0) <= 0 or sym in open_syms:
                skipped += 1
                continue
            db.add(TradeHistory(created_at=_utc_now(), **_paper_row_from_pick(p)))
            open_syms.add(sym)
            # C4: Also persist to SignalHistory for the weekly scanner's track record
            try:
                from app.background.cache_manager import record_signal
                record_signal(
                    symbol=sym,
                    signal_type="swing",
                    signal=str(p.get("verdict") or "BUY"),
                    score=float(p.get("confidence") or 0),
                    price=float(p.get("entry") or 0),
                    stop_loss=float(p.get("catastrophe_stop") or 0),
                    take_profit=float(p.get("far_take_profit") or 0),
                    confidence=float(p.get("confidence") or 0),
                    details={"source": "weekly_scanner", "hold_days": p.get("hold_days")},
                    breakdown={k: p.get(k) for k in
                               ("usx_score", "usx_pass", "usx_signals", "usx_version", "usx_shadow", "swing_score")
                               if p.get(k) is not None},
                )
            except Exception:
                logger.debug("weekly pick SignalHistory record skipped", exc_info=True)
            recorded += 1
        db.commit()
        logger.info("weekly paper recorded: %d new, %d skipped (funnel=%s, picks=%d)",
                    recorded, skipped, funnel, len(picks))
        return {"recorded": recorded, "skipped": skipped}
    except SQLAlchemyError as e:
        db.rollback()
        logger.error("paper_validation record failed: %s", e)
        return {"error": str(e)}
    finally:
        db.close()


def _split_partial(db, t, price: float, fraction: float) -> bool:
    """Bank ``fraction`` of an open position at ``price``: record the sold slice as
    a CLOSED trade, shrink the open row to the remainder, and mark ``tp1_taken`` so
    the partial fires exactly once. The remainder keeps riding the smart exit."""
    try:
        entry = float(t.entry_price or 0)
        full_qty = float(t.qty or 0)
        sold = round(full_qty * float(fraction), 6)
        remain = round(full_qty - sold, 6)
        if entry <= 0 or sold <= 0 or remain <= 0:
            return False
        db.add(TradeHistory(
            strategy_id=t.strategy_id, symbol=t.symbol, side=t.side or "buy",
            qty=sold, entry_price=entry, exit_price=round(price, 4),
            pnl=round((price - entry) * sold, 2),
            pnl_pct=round((price / entry - 1.0) * 100.0, 4),
            position_value=round(sold * entry, 2),
            stop_loss=t.stop_loss, take_profit=t.take_profit,
            created_at=t.created_at, closed_at=_utc_now(), status="closed",
            signal_details={**(t.signal_details or {}), "exit_reason": "tp1_partial", "partial": True},
        ))
        t.qty = remain
        t.position_value = round(remain * entry, 2)
        sd = dict(t.signal_details or {})
        sd["tp1_taken"] = True
        t.signal_details = sd
        return True
    except Exception as e:
        logger.debug("partial split failed for %s: %s", getattr(t, "symbol", "?"), e)
        return False


def mature_open_paper_trades() -> dict:
    """Close/scale open PV (weekly swing) trades whose exit has fired.

    With SMART_EXIT on (default): a partial take-profit banks a slice at +EXIT_TP1_PCT
    (keeps the rest riding), then the full exit — catastrophe stop, trailing stop,
    technical-weakening, time backstop. With it off, the legacy fixed-stop/time exit.
    Trigger reasons are stored on signal_details."""
    from app.services.market_data import fetch as fetch_market_data
    from app.services.signal_tracker import _simulate_fixed_exit
    from app.services.smart_exit import (
        SMART_EXIT, compute_exit_indicators, simulate_smart_exit, post_entry_bars,
        partial_tp_hit, EXIT_TP_ENABLED, EXIT_TP1_PCT, EXIT_TP1_FRACTION)

    hold_days = int(os.environ.get("EXIT_HOLD_DAYS", getattr(settings, "SWING_MAX_HOLD_DAYS", 20)))
    stop_pct = float(os.environ.get("EXIT_STOP_PCT", getattr(settings, "SWING_TRAIL_PCT", 15.0)))

    db = SessionLocal()
    try:
        open_trades = db.query(TradeHistory).filter(
            TradeHistory.strategy_id == PV_STRATEGY,
            TradeHistory.pnl_pct.is_(None),
        ).all()
        checked = closed = partial = 0
        for t in open_trades:
            checked += 1
            try:
                entry = float(t.entry_price or 0)
                if entry <= 0:
                    continue
                df = fetch_market_data(t.symbol, period="6mo")
                if df is None or len(df) == 0:
                    continue
                dfi = compute_exit_indicators(df) if SMART_EXIT else df
                post = post_entry_bars(dfi, t.created_at, hold_days + 5)

                # (a) Partial take-profit — bank a slice at the target, let the rest ride.
                if SMART_EXIT and EXIT_TP_ENABLED and not (t.signal_details or {}).get("tp1_taken"):
                    hit = partial_tp_hit(post, entry, EXIT_TP1_PCT)
                    if hit is not None and _split_partial(db, t, float(hit[0]), EXIT_TP1_FRACTION):
                        partial += 1
                        continue  # remainder stays open, re-checked next run

                # (b) Full exit
                exit_reason = None
                if SMART_EXIT:
                    be = bool((t.signal_details or {}).get("tp1_taken"))  # remainder rides at break-even
                    sim = simulate_smart_exit(post, entry, stop_pct=stop_pct, hold_days=hold_days, breakeven=be)
                    if sim is None:
                        continue  # not matured yet
                    ret_pct, exit_price, exit_reason = sim
                else:
                    sim = _simulate_fixed_exit(post, entry, hold_days, stop_pct, is_sell=False)
                    if sim is None:
                        continue  # not matured yet
                    ret_pct, exit_price = sim
                    exit_reason = "stop_or_time"
                t.exit_price = exit_price
                t.pnl = round((exit_price - entry) * float(t.qty or 0), 2)
                t.pnl_pct = ret_pct
                t.closed_at = _utc_now()
                t.status = "closed"
                try:  # audit: which trigger closed it (trailing / weakening / stop / time)
                    sd = dict(t.signal_details or {})
                    sd["exit_reason"] = exit_reason
                    t.signal_details = sd
                except Exception:
                    pass
                closed += 1
            except Exception as e:  # one bad symbol must not abort the batch
                logger.debug("paper_validation mature %s failed: %s", t.symbol, e)
        db.commit()
        return {"checked": checked, "closed": closed, "partial": partial}
    except SQLAlchemyError as e:
        db.rollback()
        logger.error("paper_validation mature failed: %s", e)
        return {"error": str(e)}
    finally:
        db.close()


def paper_ledger_status(strategy_id: str = PV_WEEKLY) -> dict:
    """Open/closed counts + graduation status for one ledger (weekly PV or monthly PVM)."""
    from app.services.paper_trade_gate import paper_trade_status

    open_n = closed_n = 0
    inception = ledger_inception(strategy_id)
    db = SessionLocal()
    try:
        oq = db.query(TradeHistory).filter(
            TradeHistory.strategy_id == strategy_id, TradeHistory.pnl_pct.is_(None))
        cq = db.query(TradeHistory).filter(
            TradeHistory.strategy_id == strategy_id, TradeHistory.pnl_pct.isnot(None))
        if inception is not None:  # ignore the pre-reset (corrupt) batch
            oq = oq.filter(TradeHistory.created_at >= inception)
            cq = cq.filter(TradeHistory.created_at >= inception)
        open_n = oq.count()
        closed_n = cq.count()
    except SQLAlchemyError as e:
        logger.debug("paper_ledger_status count failed: %s", e)
    finally:
        db.close()
    return {
        "scanner": ("pairs" if strategy_id == PV_PAIRS
                    else "monthly" if strategy_id == PV_MONTHLY else "weekly"),
        "strategy_id": strategy_id,
        "open": open_n,
        "closed": closed_n,
        "graduation": paper_trade_status(strategy_id=strategy_id).as_dict(),
    }


# ── Monthly composite ledger (PVM) — cross-sectional rebalance ─────────────────

def _monthly_row_from_pick(pick: dict, account: float, top_n: int,
                           budget: float | None = None) -> dict:
    """Map a monthly composite pick → TradeHistory kwargs for an OPEN PVM trade.

    ``budget`` is the dollar capital for THIS name. When None it falls back to
    equal-weight (~account/top_n); rebalance_monthly passes a conviction × inverse-vol
    weighted budget instead (risk-parity sizing — raises risk-adjusted return without
    changing which names are held). No stop / take-profit — the monthly exit is *leaving
    the top-N* at the next rebalance, not a price stop (so those columns stay null).
    """
    price = float(pick.get("price") or pick.get("current_price") or 0)
    if budget is None:
        budget = (float(account) / max(int(top_n), 1)) if account else 0.0
    qty = float(int(budget // price)) if price > 0 else 0.0
    return {
        "strategy_id": PV_MONTHLY,
        "symbol": pick.get("symbol"),
        "side": "buy",
        "qty": qty,
        "entry_price": round(price, 4) if price else 0.0,
        "position_value": round(qty * price, 2) if price else 0.0,
        "confidence": float(pick.get("score") or pick.get("composite_score") or 0),
        "status": "open",
        "signal_details": {
            "source": "paper_validation_monthly",
            "score": pick.get("score") or pick.get("composite_score"),
            "signal": pick.get("signal") or pick.get("signal_composite"),
            "asof": _utc_now().isoformat(),
            # Composite parts at entry → enables read-only component attribution later
            # (signal_calibration.component_attribution). Additive; no behavior change.
            **(pick.get("parts") or {}),
        },
    }


def _normalize_picks(picks) -> list[dict]:
    """Coerce a picks payload into [{symbol, price, score}, ...] (drop unusable rows)."""
    out: list[dict] = []
    for p in picks or []:
        if not isinstance(p, dict):
            continue
        sym = p.get("symbol")
        price = p.get("price")
        if price is None:
            price = p.get("current_price")
        score = p.get("score")
        if score is None:
            score = p.get("composite_score")
        try:
            price = float(price)
        except (TypeError, ValueError):
            continue
        if not sym or price <= 0:
            continue
        # Carry the composite component parts through (when the source row has them) so
        # the monthly ledger can store them for read-only component attribution. IDEMPOTENT:
        # preserve an already-normalized `parts` dict — _default_monthly_picks normalizes
        # the deep-picks output and rebalance_monthly normalizes AGAIN; a naive second pass
        # read top-level sub-scores (now nested under `parts`) and wiped them to {}, which
        # is why component_attribution saw n=0 on every trade.
        if isinstance(p.get("parts"), dict):
            parts = p["parts"]
        else:
            parts = {k: p.get(k) for k in
                     ("score_tech", "score_fund", "score_sentiment", "score_halal",
                      "conviction_score", "confirmation_count", "score_momentum")
                     if isinstance(p.get(k), (int, float))}
        out.append({"symbol": sym, "price": price, "score": float(score or 0), "parts": parts})
    return out


def _default_monthly_picks(top_n: int = 15) -> list[dict]:
    """Best-effort: read the live composite (deep-picks) ranking → [{symbol, price, score}].

    Production default for the scheduled monthly rebalance. Pulls a few times top_n
    so a name that just fell out of the top-N still has a current price to close at.
    Returns [] on any failure (the rebalance then no-ops and logs) — never fabricates.
    """
    import asyncio

    def _call():
        from app.workspace_server import screener_deep_picks
        res = asyncio.run(screener_deep_picks(limit=max(top_n * 3, 45), use_cache="true"))
        return res if isinstance(res, dict) else {}

    try:
        try:
            res = _call()
        except RuntimeError:
            # Already inside a running event loop — run in a worker thread.
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                res = ex.submit(_call).result()
    except Exception as e:
        logger.warning("monthly picks: deep-picks unavailable: %s", e)
        return []
    return _normalize_picks(res.get("results", []))


def _current_price(symbol: str, price_map: dict) -> float | None:
    """Current price for a symbol — from the scan's price map, else a fresh fetch."""
    px = price_map.get(symbol)
    if px and px > 0:
        return float(px)
    try:
        from app.services.market_data import fetch as fetch_market_data
        df = fetch_market_data(symbol, period="5d")
        if df is not None and len(df) and "close" in getattr(df, "columns", []):
            val = float(df["close"].iloc[-1])
            return val if val > 0 else None
    except Exception as e:
        logger.debug("monthly price fetch %s failed: %s", symbol, e)
    return None


def _realized_vol(symbol: str) -> "float | None":
    """Annualized realized volatility — std of ~3mo daily log returns. None on any
    failure (caller then treats the name as median-vol, so missing data never distorts
    the weights). Monthly cadence → the per-name fetch cost is negligible."""
    try:
        import numpy as np
        from app.services.market_data import fetch as fetch_market_data
        df = fetch_market_data(symbol, period="3mo")
        if df is None or len(df) < 20 or "close" not in getattr(df, "columns", []):
            return None
        c = df["close"].astype(float)
        rets = np.log(c / c.shift(1)).dropna()
        if len(rets) < 15:
            return None
        v = float(rets.std() * (252 ** 0.5))
        return v if v > 0 else None
    except Exception as e:
        logger.debug("realized_vol %s failed: %s", symbol, e)
        return None


def _conviction_vol_weights(picks: list, top_n: int, vol_fn=None) -> dict:
    """Portfolio weights over the top-N target names: a risk-parity base (inverse
    volatility) with a gentle conviction tilt (sqrt of score / mean-score). Each weight
    is clamped to ~[floor, cap]×equal so no single name dominates or becomes dust, then
    renormalized to sum to 1. Falls back to plain equal weight when the master flag is
    off or volatilities are unavailable — so the change only ever *reallocates* capital
    across the same picks, raising risk-adjusted return without altering selection.

    Env: MONTHLY_VOL_WEIGHT (master, default on), MONTHLY_WEIGHT_CAP (2.0),
    MONTHLY_WEIGHT_FLOOR (0.4). ``vol_fn`` is injectable for offline tests.
    """
    names = [p["symbol"] for p in picks][:top_n]
    n = len(names)
    if n == 0:
        return {}
    eq = 1.0 / n
    if os.environ.get("MONTHLY_VOL_WEIGHT", "true").strip().lower() not in ("true", "1", "yes", "on"):
        return {s: eq for s in names}
    vf = vol_fn or _realized_vol
    scores = {p["symbol"]: max(float(p.get("score") or 0.0), 0.0) for p in picks[:top_n]}
    mean_score = (sum(scores.values()) / n) or 1.0
    vols = {s: vf(s) for s in names}
    known = sorted(v for v in vols.values() if v)
    median_vol = known[len(known) // 2] if known else None
    raw: dict = {}
    for s in names:
        v = vols.get(s) or median_vol
        if not v or v <= 0:
            raw[s] = eq                      # no vol info anywhere → neutral weight
            continue
        tilt = (scores.get(s, 0.0) / mean_score) ** 0.5 if mean_score else 1.0
        raw[s] = (1.0 / v) * tilt
    tot = sum(raw.values()) or 1.0
    w = {s: raw[s] / tot for s in names}
    cap = float(os.environ.get("MONTHLY_WEIGHT_CAP", "2.0")) * eq
    floor = float(os.environ.get("MONTHLY_WEIGHT_FLOOR", "0.4")) * eq
    w = {s: min(max(x, floor), cap) for s, x in w.items()}    # soft concentration guard
    tot2 = sum(w.values()) or 1.0
    return {s: x / tot2 for s, x in w.items()}


def rebalance_monthly(top_n: int = 15, account: float = 10000.0, _picks_fn=None,
                      _vol_fn=None) -> dict:
    """Rebalance the monthly composite ledger (PVM) to the current top-N.

    1. Pull the composite ranking (`_picks_fn` injected in tests; defaults to the
       live deep-picks scan).
    2. CLOSE each held PVM name that fell PAST the exit buffer (top-N × MONTHLY_EXIT_BUFFER,
       default 1.5×N) — a hysteresis band so a name hovering at the top-N edge isn't churned
       in/out every rebalance — OR that hit the loose catastrophe stop (down ≥
       MONTHLY_CAT_STOP_PCT% from entry, default 30 — a wide safety net for an in-rank
       blowup). Closes at current price; writes pnl_pct, pnl, exit_price.
    3. OPEN the new entrants (top-N not already held) at their current price.
    4. KEEP the held names still within the buffer and not stopped (untouched / open).

    Returns {target, opened, closed, held, stopped}. Simulated only — no broker orders.
    """
    fn = _picks_fn or _default_monthly_picks
    try:
        picks = _normalize_picks(fn(top_n))
    except Exception as e:
        logger.error("rebalance_monthly: picks fn failed: %s", e)
        return {"error": f"picks_fn failed: {e}"}
    if not picks:
        return {"target": 0, "opened": 0, "closed": 0, "held": 0, "reason": "no picks"}

    ranked = sorted(picks, key=lambda p: p["score"], reverse=True)
    price_map = {p["symbol"]: p["price"] for p in picks}
    target = [p["symbol"] for p in ranked][:top_n]
    # Hysteresis exit band: keep a held name until it falls PAST top-N × buffer
    # (default 1.5×N), not the instant it slips to rank N+1 — cuts turnover / whipsaw.
    exit_buffer = float(os.environ.get("MONTHLY_EXIT_BUFFER", "1.5"))
    exit_rank = max(top_n, int(round(top_n * exit_buffer)))
    keep_set = set([p["symbol"] for p in ranked][:exit_rank])
    # Conviction × inverse-vol weights for the target book (computed before the DB
    # session so the vol fetches don't hold a connection). New entrants are sized by it.
    weights = _conviction_vol_weights(ranked, top_n, vol_fn=_vol_fn)

    db = SessionLocal()
    try:
        open_trades = db.query(TradeHistory).filter(
            TradeHistory.strategy_id == PV_MONTHLY, TradeHistory.pnl_pct.is_(None)).all()
        held_syms = {t.symbol for t in open_trades}

        cat_stop_pct = float(os.environ.get("MONTHLY_CAT_STOP_PCT", "30"))
        closed = held = stopped = 0
        for t in open_trades:
            cur = _current_price(t.symbol, price_map)
            if cur is None:
                held += 1                       # can't price honestly → leave open
                continue
            entry = float(t.entry_price or 0)
            # Loose catastrophe-stop OVERLAY: exit a held name that has crashed from entry
            # even while it's still in-rank (the rank exit alone can't catch an in-rank
            # blowup). Deliberately WIDE (≈30%) so it never whipsaws like the weekly stop.
            hit_stop = (cat_stop_pct > 0 and entry > 0
                        and cur <= entry * (1.0 - cat_stop_pct / 100.0))
            if t.symbol in keep_set and not hit_stop:
                held += 1                       # within buffer & no blowup → keep open
                continue
            t.exit_price = round(cur, 4)
            t.pnl_pct = round((cur / entry - 1.0) * 100, 2) if entry > 0 else None
            t.pnl = round((cur - entry) * float(t.qty or 0), 2)
            t.closed_at = _utc_now()
            t.status = "closed"
            closed += 1
            if hit_stop:
                stopped += 1

        opened = 0
        for sym in target:
            if sym in held_syms:
                continue                        # already held → no double-open
            price = price_map.get(sym, 0)
            if not price or price <= 0:
                continue
            pick = next((p for p in ranked if p["symbol"] == sym), {"symbol": sym, "price": price})
            budget = float(account) * weights.get(sym, 1.0 / max(int(top_n), 1))
            db.add(TradeHistory(created_at=_utc_now(),
                                **_monthly_row_from_pick(pick, account, top_n, budget=budget)))
            opened += 1

        db.commit()
        return {"target": len(target), "opened": opened, "closed": closed,
                "held": held, "stopped": stopped}
    except SQLAlchemyError as e:
        db.rollback()
        logger.error("rebalance_monthly failed: %s", e)
        return {"error": str(e)}
    finally:
        db.close()


# ── Pairs ledger (PVP) — halal long-only relative-value, cointegration ─────────

def _pairs_row_from_signal(sig) -> dict:
    """Map a pairs ENTRY signal → TradeHistory kwargs for an OPEN PVP trade.

    Long-only relative-value: BUY the relatively-undervalued cointegrated leg. The
    exit is spread z-reversion (handled in mature_pairs_paper_trades), with the
    long-leg ATR stop / a max-hold cap as safety. Nominal sizing (~$2k) — the
    graduation metric is pnl_pct, not absolute pnl.
    """
    entry = float(getattr(sig, "entry", 0) or 0)
    qty = float(int(2000.0 // entry)) if entry > 0 else 0.0
    return {
        "strategy_id": PV_PAIRS,
        "symbol": sig.long_symbol,
        "side": "buy",
        "qty": qty,
        "entry_price": round(entry, 4),
        "stop_loss": (float(sig.stop_loss) if getattr(sig, "stop_loss", 0) else None),
        "take_profit": (float(sig.take_profit) if getattr(sig, "take_profit", 0) else None),
        "position_value": round(qty * entry, 2),
        "confidence": float(getattr(sig, "confidence", 0) or 0),
        "status": "open",
        "signal_details": {
            "source": "pairs_paper",
            "pair": sig.pair,
            "long_symbol": sig.long_symbol,
            "zscore": round(float(getattr(sig, "zscore", 0) or 0), 3),
            "hedge_ratio": round(float(getattr(sig, "hedge_ratio", 0) or 0), 4),
            "half_life": round(float(getattr(sig, "half_life", 0) or 0), 2),
            "reason": getattr(sig, "reason", ""),
            "asof": _utc_now().isoformat(),
        },
    }


def record_pairs_signals(max_pairs: int | None = None) -> dict:
    """Record the pairs strategy's ENTRY signals as OPEN PVP paper trades.

    Deduped against open PVP trades on the long-leg symbol. NO live broker orders —
    this is the isolated PVP validation ledger (does cointegrated relative-value
    have a halal long-only edge?). Mirrors record_weekly_picks.
    """
    from app.services.pairs_strategy import compute_signals

    try:
        signals = compute_signals(max_pairs=max_pairs)
    except Exception as e:
        logger.warning("record_pairs_signals: compute_signals failed: %s", e)
        return {"error": str(e)}
    entries = [s for s in signals
               if getattr(s, "action", "") in ("long_y", "long_x") and getattr(s, "long_symbol", None)]
    if not entries:
        return {"recorded": 0, "skipped": 0, "reason": "no entry signals"}

    db = SessionLocal()
    try:
        open_syms = {
            r[0] for r in db.query(TradeHistory.symbol).filter(
                TradeHistory.strategy_id == PV_PAIRS,
                TradeHistory.pnl_pct.is_(None),
            ).all()
        }
        recorded = skipped = 0
        for sig in entries:
            sym = sig.long_symbol
            if not sym or float(getattr(sig, "entry", 0) or 0) <= 0 or sym in open_syms:
                skipped += 1
                continue
            db.add(TradeHistory(created_at=_utc_now(), **_pairs_row_from_signal(sig)))
            open_syms.add(sym)
            try:
                from app.background.cache_manager import record_signal
                record_signal(
                    symbol=sym, signal_type="pairs", signal="BUY",
                    score=float(getattr(sig, "confidence", 0) or 0),
                    price=float(getattr(sig, "entry", 0) or 0),
                    stop_loss=float(getattr(sig, "stop_loss", 0) or 0),
                    take_profit=float(getattr(sig, "take_profit", 0) or 0),
                    confidence=float(getattr(sig, "confidence", 0) or 0),
                    details={"source": "pairs_scanner", "pair": sig.pair},
                    breakdown={"zscore": sig.zscore, "hedge_ratio": sig.hedge_ratio,
                               "half_life": sig.half_life},
                )
            except Exception:
                logger.debug("pairs signal SignalHistory record skipped", exc_info=True)
            recorded += 1
        db.commit()
        return {"recorded": recorded, "skipped": skipped}
    except SQLAlchemyError as e:
        db.rollback()
        logger.error("record_pairs_signals failed: %s", e)
        return {"error": str(e)}
    finally:
        db.close()


def mature_pairs_paper_trades() -> dict:
    """Close any open PVP trade whose pairs exit fired: spread z-reversion
    (|z| <= PAIRS_EXIT_Z), a cointegration BREAKDOWN (spread blowout / decay —
    cuts a thesis that's failing), the long-leg catastrophe stop, or a max-hold
    cap. The exit reason is stored on signal_details for later attribution."""
    from app.services.market_data import fetch as fetch_market_data
    from app.services.signal_tracker import _simulate_fixed_exit
    from app.services.cointegration import spread_zscore
    from app.services.pairs_strategy import PAIRS_EXIT_Z, pair_breakdown, pairs_spread
    from app.services.smart_exit import post_entry_bars

    hold_days = int(os.environ.get("PAIRS_MAX_HOLD_DAYS", "30"))
    db = SessionLocal()
    try:
        open_trades = db.query(TradeHistory).filter(
            TradeHistory.strategy_id == PV_PAIRS,
            TradeHistory.pnl_pct.is_(None),
        ).all()
        checked = closed = 0
        for t in open_trades:
            checked += 1
            try:
                entry = float(t.entry_price or 0)
                if entry <= 0:
                    continue
                details = t.signal_details or {}
                pair = str(details.get("pair") or "")
                df = fetch_market_data(t.symbol, period="6mo")
                if df is None or len(df) == 0:
                    continue
                post = post_entry_bars(df, t.created_at, hold_days + 5)

                exit_price = ret_pct = exit_reason = None
                broke_why = None
                # 1) long-leg stop / max-hold time cap (safety)
                stop_pct = max(1.0, (entry - float(t.stop_loss)) / entry * 100.0) if t.stop_loss else 15.0
                sim = _simulate_fixed_exit(post, entry, hold_days, stop_pct, is_sell=False)
                if sim is not None:
                    ret_pct, exit_price = sim
                    exit_reason = "stop_or_time"
                else:
                    # 2) spread z-reversion OR cointegration breakdown (the pairs policy)
                    z_now = None
                    broke = False
                    if "/" in pair:
                        y_sym, x_sym = pair.split("/", 1)
                        ydf = fetch_market_data(y_sym, period="1y")
                        xdf = fetch_market_data(x_sym, period="1y")
                        if (ydf is not None and xdf is not None
                                and len(ydf) > 60 and len(xdf) > 60):
                            import pandas as pd
                            j = pd.concat([ydf["close"].reset_index(drop=True).rename("y"),
                                           xdf["close"].reset_index(drop=True).rename("x")],
                                          axis=1).dropna()
                            if len(j) > 60:
                                spr = pairs_spread(j["y"].tolist(), j["x"].tolist())
                                z_now = spread_zscore(spr)
                                broke, broke_why = pair_breakdown(
                                    z_now, j["y"].tolist(), j["x"].tolist())
                    if z_now is not None and (abs(z_now) <= PAIRS_EXIT_Z or broke):
                        last = float(df["close"].iloc[-1])
                        exit_price = last
                        ret_pct = round((last - entry) / entry * 100.0, 4)
                        exit_reason = ("breakdown" if (broke and abs(z_now) > PAIRS_EXIT_Z)
                                       else "z_revert")

                if exit_price is None or ret_pct is None:
                    continue  # still open
                t.exit_price = round(float(exit_price), 4)
                t.pnl = round((float(exit_price) - entry) * float(t.qty or 0), 2)
                t.pnl_pct = ret_pct
                t.closed_at = _utc_now()
                t.status = "closed"
                # audit: why it closed (breakdown vs reversion vs stop/time)
                try:
                    sd = dict(t.signal_details or {})
                    sd["exit_reason"] = exit_reason
                    if exit_reason == "breakdown" and broke_why:
                        sd["breakdown_reason"] = broke_why
                    t.signal_details = sd
                except Exception:
                    pass
                closed += 1
            except Exception as e:  # one bad pair must not abort the batch
                logger.debug("mature_pairs %s failed: %s", t.symbol, e)
        db.commit()
        return {"checked": checked, "closed": closed}
    except SQLAlchemyError as e:
        db.rollback()
        logger.error("mature_pairs_paper_trades failed: %s", e)
        return {"error": str(e)}
    finally:
        db.close()
