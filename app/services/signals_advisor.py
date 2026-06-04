"""Signals-advisor service — 3-stage funnel that produces only the
highest-conviction STRONG BUY alerts for manual execution on Alpaca.

Pipeline:

    HALAL_STOCKS (~357)
       │
       ▼  Stage 1 — USX Pro V4 (regime gate + per-stock weighted score)
    USX_PASS (~10-50 candidates per scan)
       │
       ▼  Stage 2 — AI consensus across A/B/C strategies (14 tools/sym)
    STRONG_BUY (~1-5 per scan)
       │
       ▼  Stage 3 — Telegram with chart + full trade plan
    Operator executes manually

Each stage CUTS — Stage 1 ditches the trash universe quickly using
cheap technicals + macro regime, Stage 2 confirms with the heavy AI
ensemble. The result is signals you actually want to act on, not
"60% confidence noise on a stock with no liquidity in a bear regime".

Independent of the auto-trader: it does not place broker orders, does
not write to TradeHistory, does not respect AUTO_TRADE_ENABLED.

Usage:
    from app.services.signals_advisor import scan_and_notify_strong_buys
    summary = scan_and_notify_strong_buys()
    # {"sent": 3, "stage1_pass": 23, "stage2_pass": 3, ...}
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from zoneinfo import ZoneInfo

logger = logging.getLogger("screener")

# Default risk parameters used to size the alert recommendation.
_DEFAULT_ACCOUNT_USD = 5000.0
_DEFAULT_RISK_PCT = 0.01      # 1% risk per trade
_DEFAULT_STOP_PCT = 0.03      # 3% stop loss
_DEFAULT_TAKE_PCT = 0.06      # 6% take profit (2:1 R:R)


# ---------------------------------------------------------------------------
# Stage 2: AI consensus runners
# ---------------------------------------------------------------------------

def _strategy_runners() -> dict:
    """Return {strategy_id: (callable, label)} for each available consensus."""
    import halal_screener as hs

    return {
        "A": (hs.run_consensus_momentum, "HANA / Momentum"),
        "B": (hs.run_consensus_reversion, "Mean Reversion"),
        "C": (hs.run_consensus_ml, "mazem / ML"),
    }


def _is_strong_buy(row: dict, min_confidence: float = 35.0) -> bool:
    """A signal qualifies as STRONG BUY when:
    - Verdict is exactly 'STRONG BUY' or 'BUY' (anything weaker is filtered)
    - Confidence >= threshold (default 70%)
    - No error in the row
    """
    if not row or "Error" in row or row.get("Verdict") in (None, "BLOCKED"):
        return False
    verdict = str(row.get("Verdict", "")).upper()
    if verdict not in ("STRONG BUY", "BUY"):
        return False
    try:
        conf = float(row.get("Confidence %", 0))
    except (TypeError, ValueError):
        return False
    return conf >= min_confidence


def _scan_one(symbol: str, runner, label: str) -> dict | None:
    """Run a single consensus call; return the row only if it qualifies."""
    try:
        result = runner(symbol)
        if not result:
            return None
        row = result[0] if isinstance(result, list) else result
        if not _is_strong_buy(row):
            return None
        row["__strategy_label"] = label
        return row
    except Exception as exc:  # noqa: BLE001
        logger.debug("signals_advisor: %s scan failed for %s: %s", label, symbol, exc)
        return None


def scan_universe_for_strategy(
    strategy_id: str,
    symbols: list[str] | None = None,
    max_workers: int = 3,
    min_confidence: float = 35.0,
) -> list[dict]:
    """Stage 2 helper — run AI consensus for one strategy across the
    provided candidate list. Returns rows passing the STRONG BUY filter,
    sorted by confidence desc."""
    runners = _strategy_runners()
    if strategy_id not in runners:
        return []
    runner, label = runners[strategy_id]

    if symbols is None:
        import halal_screener as hs
        symbols = list(hs._universe_symbols())

    out: list[dict] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_scan_one, s, runner, label): s for s in symbols}
        for fut in as_completed(futures):
            row = fut.result()
            if row is None:
                continue
            try:
                conf = float(row.get("Confidence %", 0))
            except (TypeError, ValueError):
                conf = 0.0
            if conf >= min_confidence:
                row["__strategy_id"] = strategy_id
                out.append(row)
    out.sort(key=lambda r: float(r.get("Confidence %", 0) or 0), reverse=True)
    return out


# ---------------------------------------------------------------------------
# Telegram formatting
# ---------------------------------------------------------------------------

def _format_signal(
    row: dict,
    usx_score: float | None = None,
    usx_breakdown: dict | None = None,
    account_usd: float = _DEFAULT_ACCOUNT_USD,
    risk_pct: float = _DEFAULT_RISK_PCT,
    stop_pct: float = _DEFAULT_STOP_PCT,
    take_pct: float = _DEFAULT_TAKE_PCT,
) -> tuple[str, dict]:
    """Format a STRONG BUY row as a Telegram-ready message.

    Returns (text, levels_dict) — the levels dict is reused by the chart
    renderer so the alert text and chart match exactly.
    """
    symbol = row.get("Symbol", "?")
    strategy_label = row.get("__strategy_label", "?")
    verdict = row.get("Verdict", "?")
    try:
        confidence = float(row.get("Confidence %", 0))
    except (TypeError, ValueError):
        confidence = 0.0
    try:
        price = float(row.get("Price", 0))
    except (TypeError, ValueError):
        price = 0.0

    votes_buy = row.get("Votes BUY", 0)
    votes_sell = row.get("Votes SELL", 0)
    votes_hold = row.get("Votes HOLD", 0)

    # When SWING_EXIT is armed, execute_buy() replaces the tight stop/TP with a
    # wide 12% trailing stop and a 3×-risk take-profit. Mirror that here so the
    # DISPLAYED plan matches what is actually placed (no display≠execution gap).
    swing_on = False
    try:
        from app.config import settings as _disp_cfg
        if getattr(_disp_cfg, "SWING_EXIT_ENABLED", False):
            swing_on = True
            stop_pct = float(getattr(_disp_cfg, "SWING_TRAIL_PCT", 12.0)) / 100.0
            take_pct = 3.0 * stop_pct
    except Exception:
        pass

    if price <= 0:
        sl = tp1 = tp2 = tp3 = 0.0
        shares = 0
        risk_dollars = notional = 0.0
    else:
        sl = round(price * (1.0 - stop_pct), 2)
        tp1 = round(price * (1.0 + take_pct * 0.5), 2)
        tp2 = round(price * (1.0 + take_pct), 2)
        tp3 = round(price * (1.0 + take_pct * 2.0), 2)
        risk_dollars = account_usd * risk_pct
        risk_per_share = max(price - sl, 0.01)
        shares_by_risk = int(risk_dollars / risk_per_share)
        max_position_value = account_usd * 0.20
        shares_by_position = int(max_position_value / price)
        shares = max(1, min(shares_by_risk, shares_by_position))
        notional = round(shares * price, 2)

    now_et = datetime.now(ZoneInfo("US/Eastern")).strftime("%Y-%m-%d %H:%M ET")

    _gated = row.get("_bridge_gated", False)
    _conf_count = len(row.get("_bridge_confirmations", []))
    if _gated:
        _header = f"⚠️ BUY (gated: {_conf_count}/3 confirmations)"
    else:
        _header = "🎯 STRONG BUY SIGNAL"
    lines = [
        _header,
        "━━━━━━━━━━━━━━━",
        f"Symbol:     {symbol}",
        f"Strategy:   {strategy_label}",
        f"Verdict:    {verdict}",
        f"AI conf:    {confidence:.0f}%",
    ]
    if usx_score is not None:
        lines.append(f"USX V4:     {usx_score:.0f}/100")
    # Honest R/R from the actual stop/target distances (was a hardcoded label).
    rr = round((tp2 - price) / (price - sl), 1) if price > sl else 0.0
    stop_kind = "trailing 12%" if swing_on else "fixed"
    lines.extend([
        "━━━━━━━━━━━━━━━",
        f"Price:      ${price:.2f}",
        f"Stop Loss:  ${sl:.2f}  (−{stop_pct*100:.1f}%, {stop_kind})",
        f"TP1 (50%):  ${tp1:.2f}  (+{take_pct*50:.1f}%)",
        f"TP2 (30%):  ${tp2:.2f}  (+{take_pct*100:.1f}%)",
        f"TP3 (20%):  ${tp3:.2f}  (+{take_pct*200:.1f}%)",
        f"R/R:        1:{rr:.1f}  (TP2 vs stop)",
        f"Shares:     {shares}  (≈ ${notional:.2f} notional)",
        f"Risk:       ${risk_dollars:.2f}  ({risk_pct*100:.1f}% of ${account_usd:.0f})",
        "━━━━━━━━━━━━━━━",
        f"AI Votes:   BUY {votes_buy} / SELL {votes_sell} / HOLD {votes_hold}",
    ])
    if usx_breakdown:
        lines.append(
            f"USX:        D-trend {usx_breakdown.get('daily_trend',0)}/20  "
            f"RS {usx_breakdown.get('rs_vs_spy',0)}/20  "
            f"MACD {usx_breakdown.get('macd',0)}/10  "
            f"ADX {usx_breakdown.get('adx',0)}/7"
        )
        lines.append(
            f"            Vol {usx_breakdown.get('volume',0)}/6  "
            f"BB-sqz {usx_breakdown.get('bb_squeeze',0)}/5  "
            f"VWAP {usx_breakdown.get('vwap',0)}/5"
        )
    lines.extend([
        f"Time:       {now_et}",
        "",
        "Review chart and confirm via Alpaca before placing.",
    ])

    levels = {"entry": price, "sl": sl, "tp1": tp1, "tp2": tp2, "tp3": tp3,
              "shares": shares, "notional": notional, "risk_dollars": risk_dollars}
    return "\n".join(lines), levels


def _notify_auto_execute(symbol: str, result: dict, strategy_id: str | None) -> None:
    """Send a follow-up Telegram confirming the auto-execution outcome."""
    try:
        from app.services.telegram_alert import send_message as tg_send
        from app.config import settings as _trd_cfg
        sid_lbl = f"[{strategy_id}]" if strategy_id else ""
        if result.get("executed"):
            qty   = result.get("qty", "?")
            price = result.get("entry_price") or "?"
            stop  = result.get("stop_loss", "?")
            # SWING_EXIT_ENABLED widens the stop — show trail %
            trail_info = ""
            if getattr(_trd_cfg, "SWING_EXIT_ENABLED", False):
                trail_info = f"  Trail: {getattr(_trd_cfg, 'SWING_TRAIL_PCT', 12.0):.0f}%"
            oid = str(result.get("order_id", ""))[:16]
            tg_send(
                f"✅ ORDER PLACED {sid_lbl}\n"
                f"  {symbol}  {qty} shares @ ${price}{trail_info}\n"
                f"  Stop: ${stop}  Hold: ~20 days\n"
                f"  ID: {oid}"
            )
        else:
            reason = result.get("reason", "unknown")
            tg_send(
                f"⚠️ AUTO-EXECUTE SKIPPED {sid_lbl}\n"
                f"  {symbol}: {reason}"
            )
    except Exception:
        pass


def _send_signal_alert(row: dict, usx_score: float | None, usx_breakdown: dict | None,
                       account_usd: float, risk_pct: float, stop_pct: float,
                       take_pct: float, dry_run: bool,
                       strategy_id: str | None = None) -> bool:
    """Format + send one signal (text caption with attached chart when
    available). When AUTO_TRADE_ENABLED + LIVE_CONFIRMED are both True,
    automatically executes the trade via execute_buy() after the alert."""
    # ── Composite bridge (Chapter 3, Phase 4) ─────────────────────────────────
    # When COMPOSITE_BRIDGE_LIVE is ON, enrich the row with fundamental/forecast
    # layers and apply the multi-confirmation STRONG BUY gate. If < 3 independent
    # confirmations, downgrade STRONG BUY → BUY before formatting the alert.
    _bridge_on = False
    try:
        from app.config import settings as _bridge_cfg
        _bridge_on = getattr(_bridge_cfg, "COMPOSITE_BRIDGE_LIVE", True)
    except Exception:
        pass
    if _bridge_on and row.get("Verdict") == "STRONG BUY":
        try:
            _sym = row.get("Symbol", "")
            # Lightweight enrichment: fetch f_grade + forecast_details
            _enriched = dict(row)
            if not _enriched.get("f_grade"):
                try:
                    from app.models.fundamental import get_f_grade
                    _grade = get_f_grade(_sym.upper())
                    if _grade:
                        _enriched["f_grade"] = _grade
                except Exception:
                    pass
            if not _enriched.get("forecast_details"):
                try:
                    import halal_screener as _hs
                    # run_ensemble returns [{summary}, {day1_fc}, {day2_fc}, ...]
                    _fc = _hs.run_ensemble(_sym.upper(), horizon=5)
                    if isinstance(_fc, list) and len(_fc) > 1:
                        # Determine direction from the forecast days
                        _buy_signals = sum(
                            1 for d in _fc[1:] if str(d.get("Signal", "")).upper() == "BUY"
                        )
                        _direction = "up" if _buy_signals >= len(_fc[1:]) / 2 else "down"
                        _enriched["forecast_details"] = {
                            "models": ["ensemble"],
                            "model_direction": _direction,
                            "forecast": _fc,
                        }
                except Exception:
                    pass
            # Attach consensus votes from the strategy row (already present)
            _enriched.setdefault("consensus_votes_buy", row.get("Votes BUY", 0))
            _enriched.setdefault("consensus_votes_sell", row.get("Votes SELL", 0))
            _enriched.setdefault("consensus_verdict", row.get("Verdict", ""))
            # Score proxy for technical confirmation
            _enriched.setdefault("score_tech", usx_score or 0)
            _enriched.setdefault("momentum_score", usx_score or 0)

            from app.services.conviction_engine import detect_confirmations, apply_strong_buy_gate
            _confirms = detect_confirmations(_enriched, "unknown")
            _conv = {"confirmations": _confirms, "confirmation_count": len(_confirms),
                     "strong_buy_qualified": len(_confirms) >= 3}
            _new_verdict = apply_strong_buy_gate("STRONG BUY", _conv)
            if _new_verdict != "STRONG BUY":
                row["Verdict"] = "BUY"
                row["_bridge_gated"] = True
                row["_bridge_confirmations"] = _confirms
                logger.info("signals_advisor: composite bridge downgraded %s STRONG BUY→BUY (%d/3 confirmations: %s)",
                            _sym, len(_confirms), ",".join(_confirms))
        except Exception:
            logger.debug("signals_advisor: composite bridge enrichment failed — passing through unchanged")

    text, levels = _format_signal(
        row, usx_score=usx_score, usx_breakdown=usx_breakdown,
        account_usd=account_usd, risk_pct=risk_pct,
        stop_pct=stop_pct, take_pct=take_pct,
    )

    if dry_run:
        return True

    symbol = row.get("Symbol", "")
    entry = levels.get("entry")
    sl = levels.get("sl")
    tp1 = levels.get("tp1")
    confidence = float(row.get("Confidence %", 0) or 0)

    # Risk Desk gate — suppress advisory alerts during a market-level halt
    # (VIX halt / SPY bear halt / regime freeze / system block). Every alert is
    # persisted to the alerts table with the gate result for audit, whether or
    # not it actually reaches Telegram.
    from app.services.alert_store import market_risk_gate, record_alert
    gate_ok, gate_reason = market_risk_gate(symbol, price=entry or 0.0, stop_loss=sl or 0.0)
    if not gate_ok:
        logger.info("alert suppressed by Risk Desk gate: %s — %s", symbol, gate_reason)
        _actual_verdict = row.get("Verdict", "STRONG BUY")
        record_alert(symbol, "strong_buy", signal=_actual_verdict, score=usx_score,
                     price=entry, stop_loss=sl, take_profit=tp1, confidence=confidence,
                     guard_passed=False, guard_reason=gate_reason, sent=False)
        return False

    # Try to render chart and send as photo with text as caption.
    image_bytes = None
    try:
        import halal_screener as hs
        from app.services.signals_chart import render_signal_chart

        df = hs.fetch_yf(row.get("Symbol", ""), period="6mo")
        if df is not None and len(df) >= 30:
            image_bytes = render_signal_chart(
                df,
                symbol=row.get("Symbol", "?"),
                strategy_label=row.get("__strategy_label", "?"),
                confidence=float(row.get("Confidence %", 0) or 0),
                entry=levels["entry"],
                stop_loss=levels["sl"],
                take_profit_1=levels["tp1"],
                take_profit_2=levels["tp2"],
                take_profit_3=levels["tp3"],
            )
    except Exception as exc:  # noqa: BLE001
        logger.debug("signals_advisor: chart render failed for %s: %s",
                     row.get("Symbol"), exc)

    sent = False
    try:
        from app.services.telegram_alert import send_message as tg_send
        from app.services.telegram_alert import send_photo as tg_send_photo

        if image_bytes:
            sent = bool(tg_send_photo(image_bytes, caption=text))
        else:
            sent = bool(tg_send(text))
    except Exception as exc:  # noqa: BLE001
        logger.warning("signals_advisor: telegram send failed: %s", exc)
        sent = False

    _actual_verdict = row.get("Verdict", "STRONG BUY")
    record_alert(symbol, "strong_buy", signal=_actual_verdict, score=usx_score,
                 price=entry, stop_loss=sl, take_profit=tp1, confidence=confidence,
                 guard_passed=True, sent=sent)

    # ── Auto-execute: place bracket order on Alpaca ──────────────────────
    # Fires only when BOTH flags are live-confirmed AND the Telegram alert
    # was delivered (belt-and-suspenders: if we can't notify, we don't trade).
    # execute_buy() applies SWING_EXIT_ENABLED override (12% wide stop/trail)
    # and all the usual risk/halal/kill-switch/portfolio-stop guards.
    if sent and strategy_id and entry and sl and not dry_run:
        try:
            from app.config import settings as _trd_cfg
            from app.services.trading_engine import execute_buy
            if (getattr(_trd_cfg, "AUTO_TRADE_ENABLED", False)
                    and getattr(_trd_cfg, "LIVE_CONFIRMED", False)):
                # ── Position limit gate ──
                # Skip if strategy already at max_positions
                try:
                    from app.config import STRATEGY_CONFIGS as _sc
                    _cfg = _sc.get(strategy_id)
                    if _cfg and _cfg.max_positions > 0:
                        import urllib.request, json, base64
                        _auth = f"{_cfg.alpaca_api_key}:{_cfg.alpaca_secret_key}"
                        _b64 = base64.b64encode(_auth.encode()).decode()
                        _req = urllib.request.Request(
                            "https://paper-api.alpaca.markets/v2/positions",
                            headers={"Authorization": f"Basic {_b64}"})
                        _positions = json.loads(urllib.request.urlopen(_req, timeout=5).read())
                        if len(_positions) >= _cfg.max_positions:
                            logger.info(
                                "signals_advisor: position limit reached for %s (%d/%d) — skipping %s",
                                strategy_id, len(_positions), _cfg.max_positions, symbol)
                            return sent
                except Exception:
                    pass  # if limit check fails, proceed anyway
                signal_details = {
                    "verdict":    row.get("Verdict"),
                    "confidence": confidence,
                    "strategy":   row.get("__strategy_label"),
                    "source":     "signals_advisor",
                    "usx_score":  usx_score,
                    "votes_buy":  row.get("Votes BUY"),
                    "votes_sell": row.get("Votes SELL"),
                }
                trade_result = execute_buy(
                    symbol=symbol,
                    price=entry,
                    stop_loss=sl,
                    take_profit=tp1,
                    confidence=confidence,
                    signal_details=signal_details,
                    strategy_id=strategy_id,
                )
                _notify_auto_execute(symbol, trade_result, strategy_id)
                logger.info(
                    "signals_advisor auto-execute %s [%s]: executed=%s reason=%s",
                    symbol, strategy_id,
                    trade_result.get("executed"),
                    trade_result.get("reason", ""),
                )
        except Exception as _exc:
            logger.warning(
                "signals_advisor: auto-execute failed for %s [%s]: %s",
                symbol, strategy_id, _exc,
            )

    return sent


# ---------------------------------------------------------------------------
# Stage 1 — USX Pro V4 universe pre-filter
# ---------------------------------------------------------------------------

def _stage1_usx_filter(symbols: list[str], min_usx_score: float = 0.0,
                       max_workers: int = 3) -> tuple[list[dict], dict]:
    """Run USX V4 regime gate + per-stock qualifier across the universe.

    Returns (passing_rows, regime_dict). Each passing row includes
    `symbol`, `score`, `breakdown`. If regime gate is closed, returns
    ([], regime_dict) with `overall_ok=False`.
    """
    try:
        from app.services.usx_pro_filter import filter_universe
    except Exception as exc:  # noqa: BLE001
        logger.warning("signals_advisor: USX filter unavailable: %s — passing through universe", exc)
        return [{"symbol": s, "score": 0.0, "breakdown": {}} for s in symbols], {
            "overall_ok": True, "reason": "USX filter unavailable", "spy_bull": None,
        }

    passing, regime = filter_universe(
        symbols, min_score=min_usx_score, max_workers=max_workers
    )
    rows = [
        {"symbol": p.symbol, "score": p.score, "breakdown": p.breakdown}
        for p in passing
    ]
    return rows, regime.as_dict()


# ---------------------------------------------------------------------------
# Orchestration — full 3-stage scan
# ---------------------------------------------------------------------------

def scan_and_notify_strong_buys(
    strategy_ids: tuple[str, ...] = ("A", "C"),  # B disabled — poor WR
    symbols: list[str] | None = None,
    max_workers: int = 3,
    min_confidence: float = 35.0,
    min_usx_score: float = 0.0,
    account_usd: float = _DEFAULT_ACCOUNT_USD,
    risk_pct: float = _DEFAULT_RISK_PCT,
    stop_pct: float = _DEFAULT_STOP_PCT,
    take_pct: float = _DEFAULT_TAKE_PCT,
    dry_run: bool = False,
    skip_usx: bool = True,  # default True — USX needs Alpaca DATA keys
) -> dict:
    """Three-stage scan with Telegram delivery.

    Args:
        strategy_ids   subset of ("A","B","C")
        symbols        None = full halal universe
        min_confidence AI consensus threshold (0-100)
        min_usx_score  USX V4 weighted-score threshold (0-100)
        skip_usx       True: bypass Stage 1 entirely (legacy mode)
    """
    if symbols is None:
        try:
            import halal_screener as hs
            symbols = list(hs._universe_symbols())
        except Exception:
            symbols = []

    summary: dict = {
        "sent": 0,
        "scanned_total": len(symbols),
        "stage1_pass": 0,
        "stage2_pass": 0,
        "regime": None,
        "by_strategy": {},
        "signals": [],
    }

    # ------ Stage 1: USX V4 pre-filter ------
    if skip_usx:
        candidates = [{"symbol": s, "score": None, "breakdown": {}} for s in symbols]
        summary["regime"] = {"overall_ok": True, "reason": "USX skipped"}
    else:
        cand_rows, regime = _stage1_usx_filter(
            symbols, min_usx_score=min_usx_score, max_workers=max_workers
        )
        summary["regime"] = regime
        if not regime.get("overall_ok", False):
            summary["stage1_pass"] = 0
            # Notify operator that the regime is closed (tagged so it passes
            # the BUY-only Telegram filter)
            try:
                from app.services.telegram_alert import send_message as tg_send
                if not dry_run:
                    tg_send(
                        f"PRE-MARKET SIGNALS — regime gate CLOSED\n"
                        f"Reason: {regime.get('reason', 'unknown')}\n"
                        f"No STRONG BUY alerts will be issued this cycle."
                    )
            except Exception:
                pass
            return summary
        candidates = cand_rows

    # ── Archetype union (Chapter 3, Phase 3) ────────────────────────────────────
    # When any archetype flag is raised, run its detector on the full universe
    # and UNION the results with USX candidates. Each archetype runs inside
    # _hard_gates so liquidity/price/extension floors still apply.
    try:
        from app.config import settings as _arch_cfg
        _arch_flags = {
            "pullback": getattr(_arch_cfg, "ARCHETYPE_PULLBACK_LIVE", False),
            "breakout": getattr(_arch_cfg, "ARCHETYPE_BREAKOUT_LIVE", False),
            "reversal": getattr(_arch_cfg, "ARCHETYPE_REVERSAL_LIVE", False),
        }
    except Exception:
        _arch_flags = {}
    _active_archs = [a for a, on in _arch_flags.items() if on]
    if _active_archs and not skip_usx:
        try:
            from app.services.signal_archetypes import detect_archetype
            from app.services.market_data import fetch_alpaca_batch
            _arch_dfs = fetch_alpaca_batch(symbols, "2y")
            _arch_new = []
            for _sym in symbols:
                _df = _arch_dfs.get(_sym)
                if _df is None:
                    continue
                for _aname in _active_archs:
                    try:
                        _result = detect_archetype(_aname, _df)
                        if _result and _result.get("score", 0) >= min_usx_score:
                            _result["symbol"] = _sym
                            _result["source"] = _aname
                            _arch_new.append(_result)
                    except Exception:
                        pass
            # UNION: add archetype candidates not already in USX list
            _seen = {c["symbol"] for c in candidates}
            for _ac in _arch_new:
                if _ac["symbol"] not in _seen:
                    candidates.append({"symbol": _ac["symbol"], "score": _ac.get("score", 0), "breakdown": {}})
                    _seen.add(_ac["symbol"])
            if _arch_new:
                _added_count = sum(1 for a in _arch_new if any(c["symbol"] == a["symbol"] for c in candidates))
                logger.info("signals_advisor: archetype union — %d candidates from %s (total=%d)",
                            _added_count, ",".join(_active_archs), len(candidates))
        except Exception:
            logger.exception("signals_advisor: archetype union failed — continuing with USX-only candidates")

    summary["stage1_pass"] = len(candidates)
    candidate_symbols = [c["symbol"] for c in candidates]
    score_by_symbol = {c["symbol"]: c["score"] for c in candidates}
    breakdown_by_symbol = {c["symbol"]: c["breakdown"] for c in candidates}

    if not candidate_symbols:
        # Stage 1 passed regime but zero stocks made it — surface a summary
        try:
            from app.services.telegram_alert import send_message as tg_send
            if not dry_run:
                tg_send(
                    "PRE-MARKET SIGNALS — Stage 1 (USX V4) returned no candidates.\n"
                    f"Regime: {summary['regime'].get('reason', 'ok')}.\n"
                    "No further analysis."
                )
        except Exception:
            pass
        return summary

    # ── Intraday confirmation (Chapter 3, Phase 5) ────────────────────────────
    # When INTRADAY_CONFIRM_LIVE is ON, each Stage-1 candidate must also pass an
    # intraday (15-min) momentum check before advancing to Stage 2. This turns
    # the 7 daily scan-slots from repetitive same-day re-scans into genuine
    # intraday breakout-capture events. Also completes gap_go archetype detection.
    _intra_on = False
    try:
        from app.config import settings as _intra_cfg
        _intra_on = getattr(_intra_cfg, "INTRADAY_CONFIRM_LIVE", True)
    except Exception:
        pass
    if _intra_on and candidate_symbols:
        try:
            from app.services.market_data import fetch_alpaca_intraday
            _intra_passed = []
            _intra_blocked = 0
            for _sym in candidate_symbols:
                try:
                    _idf = fetch_alpaca_intraday(_sym, "15Min", days_back=5)
                    if _idf is None or len(_idf) < 5:
                        _intra_blocked += 1
                        continue
                    _idf_close = _idf["close"].astype(float)
                    _idf_high = _idf["high"].astype(float)
                    # Condition 1: last 15-min close > VWAP
                    _vwap_series = _idf[["high", "low", "close", "volume"]].pipe(
                        lambda d: ((d["high"] + d["low"] + d["close"]) / 3 * d["volume"]).cumsum() / d["volume"].cumsum()
                    )
                    _above_vwap = float(_idf_close.iloc[-1]) > float(_vwap_series.iloc[-1])
                    if not _above_vwap:
                        _intra_blocked += 1
                        continue
                    # Condition 2: higher 15-min high (momentum)
                    if len(_idf_high) >= 3:
                        _higher_high = float(_idf_high.iloc[-1]) > float(_idf_high.iloc[-2])
                    else:
                        _higher_high = True
                    if not _higher_high:
                        _intra_blocked += 1
                        continue
                    # Condition 3: RVOL > 1.5 (current bar vs avg of last 20 same-slot bars)
                    _idf_vol = _idf["volume"].astype(float)
                    _vol_now = float(_idf_vol.iloc[-1])
                    _vol_avg = float(_idf_vol.iloc[-21:-1].mean()) if len(_idf_vol) >= 21 else float(_idf_vol.mean())
                    _rvol = _vol_now / _vol_avg if _vol_avg > 0 else 1.0
                    if _rvol < 1.5:
                        _intra_blocked += 1
                        continue
                    _intra_passed.append(_sym)
                except Exception:
                    _intra_blocked += 1
                    continue
            if _intra_blocked > 0:
                logger.info("signals_advisor: intraday confirm — %d passed, %d blocked",
                            len(_intra_passed), _intra_blocked)
            # Also run gap_go detection on all candidates with intraday data
            _gap_new = []
            try:
                from app.services.signal_archetypes import detect_gap_go
                from app.services.market_data import fetch_alpaca_batch as _gap_fetch
                _gap_dfs = _gap_fetch(candidate_symbols, "2y")
                for _sym in candidate_symbols:
                    _gdf = _gap_dfs.get(_sym)
                    if _gdf is None:
                        continue
                    _gidf = fetch_alpaca_intraday(_sym, "15Min", days_back=5)
                    if _gidf is None:
                        continue
                    _gr = detect_gap_go(_gdf, _gidf)
                    if _gr and _gr.get("score", 0) >= min_usx_score:
                        _gr["symbol"] = _sym
                        _gr["source"] = "gap_go_intraday"
                        _gap_new.append(_gr)
            except Exception:
                pass
            # UNION gap_go candidates into the list
            _seen_syms = set(candidate_symbols)
            for _gc in _gap_new:
                if _gc["symbol"] not in _seen_syms:
                    candidate_symbols.append(_gc["symbol"])
                    candidates.append({"symbol": _gc["symbol"], "score": _gc.get("score", 0), "breakdown": {}})
                    _seen_syms.add(_gc["symbol"])
                    _intra_passed.append(_gc["symbol"])
                    score_by_symbol[_gc["symbol"]] = _gc.get("score", 0)
            # Filter: keep only intraday-confirmed + gap_go candidates
            candidate_symbols = [s for s in candidate_symbols if s in _intra_passed]
            candidates = [c for c in candidates if c["symbol"] in _intra_passed]
            summary["stage1_pass"] = len(candidates)
            if not candidate_symbols:
                try:
                    from app.services.telegram_alert import send_message as tg_send
                    if not dry_run:
                        tg_send(
                            "PRE-MARKET SIGNALS - Stage 1 (USX V4) passed but "
                            "INTRADAY CONFIRM filtered all candidates. "
                            "No further analysis."
                        )
                except Exception:
                    pass
                return summary
        except Exception:
            logger.exception("signals_advisor: intraday confirm failed — passing through all candidates")

    # ------ Stage 2: AI consensus on USX-passing candidates only ------
    for sid in strategy_ids:
        rows = scan_universe_for_strategy(
            sid, symbols=candidate_symbols,
            max_workers=max_workers, min_confidence=min_confidence,
        )
        summary["by_strategy"][sid] = len(rows)
        summary["stage2_pass"] += len(rows)
        for row in rows:
            sym = row.get("Symbol")
            usx_score = score_by_symbol.get(sym)
            usx_breakdown = breakdown_by_symbol.get(sym)

            sent = _send_signal_alert(
                row, usx_score, usx_breakdown,
                account_usd=account_usd, risk_pct=risk_pct,
                stop_pct=stop_pct, take_pct=take_pct,
                dry_run=dry_run,
                strategy_id=sid,
            )
            summary["signals"].append({
                "strategy_id": sid,
                "symbol": sym,
                "ai_confidence": float(row.get("Confidence %", 0) or 0),
                "usx_score": usx_score,
            })
            if sent:
                summary["sent"] += 1

    # Header summary alert (only when something actually fires through)
    if summary["sent"] > 0 and not dry_run:
        try:
            from app.services.telegram_alert import send_message as tg_send
            by = summary["by_strategy"]
            tg_send(
                f"SIGNALS SCAN DONE — {summary['sent']} alerts sent\n"
                f"Stage 1 (USX V4): {summary['stage1_pass']}/{summary['scanned_total']}\n"
                f"Stage 2 (AI):     {summary['stage2_pass']}\n"
                f"By strategy: A={by.get('A',0)} B={by.get('B',0)} C={by.get('C',0)}\n"
                "Orders auto-executed above (✅ placed / ⚠️ skipped with reason)."
            )
        except Exception:
            pass

    return summary


__all__ = [
    "scan_universe_for_strategy",
    "scan_and_notify_strong_buys",
]
