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
        # "B" Mean-Reversion DISABLED — A-Pre.1 (Sharpe -1.93, WR 26.3%)
        "C": (hs.run_consensus_ml, "mazem / ML"),
    }


def _is_strong_buy(row: dict, min_confidence: float = 70.0) -> bool:
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
    min_confidence: float = 70.0,
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

    lines = [
        "🎯 STRONG BUY SIGNAL",
        "━━━━━━━━━━━━━━━",
        f"Symbol:     {symbol}",
        f"Strategy:   {strategy_label}",
        f"Verdict:    {verdict}",
        f"AI conf:    {confidence:.0f}%",
    ]
    if usx_score is not None:
        lines.append(f"USX V4:     {usx_score:.0f}/100")
    lines.extend([
        "━━━━━━━━━━━━━━━",
        f"Price:      ${price:.2f}",
        f"Stop Loss:  ${sl:.2f}  (−{stop_pct*100:.1f}%)",
        f"TP1 (50%):  ${tp1:.2f}  (+{take_pct*50:.1f}%)",
        f"TP2 (30%):  ${tp2:.2f}  (+{take_pct*100:.1f}%)",
        f"TP3 (20%):  ${tp3:.2f}  (+{take_pct*200:.1f}%)",
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


def _send_signal_alert(row: dict, usx_score: float | None, usx_breakdown: dict | None,
                       account_usd: float, risk_pct: float, stop_pct: float,
                       take_pct: float, dry_run: bool) -> bool:
    """Format + send one signal (text caption with attached chart when
    available)."""
    text, levels = _format_signal(
        row, usx_score=usx_score, usx_breakdown=usx_breakdown,
        account_usd=account_usd, risk_pct=risk_pct,
        stop_pct=stop_pct, take_pct=take_pct,
    )

    if dry_run:
        return True

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

    try:
        from app.services.telegram_alert import send_message as tg_send
        from app.services.telegram_alert import send_photo as tg_send_photo

        if image_bytes:
            return bool(tg_send_photo(image_bytes, caption=text))
        return bool(tg_send(text))
    except Exception as exc:  # noqa: BLE001
        logger.warning("signals_advisor: telegram send failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Stage 1 — USX Pro V4 universe pre-filter
# ---------------------------------------------------------------------------

def _stage1_usx_filter(symbols: list[str], min_usx_score: float = 65.0,
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
    strategy_ids: tuple[str, ...] = ("A", "B", "C"),
    symbols: list[str] | None = None,
    max_workers: int = 3,
    min_confidence: float = 70.0,
    min_usx_score: float = 65.0,
    account_usd: float = _DEFAULT_ACCOUNT_USD,
    risk_pct: float = _DEFAULT_RISK_PCT,
    stop_pct: float = _DEFAULT_STOP_PCT,
    take_pct: float = _DEFAULT_TAKE_PCT,
    dry_run: bool = False,
    skip_usx: bool = False,
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
                f"PRE-MARKET SIGNALS — {summary['sent']} STRONG BUY across A/B/C\n"
                f"Stage 1 (USX V4): {summary['stage1_pass']}/{summary['scanned_total']}\n"
                f"Stage 2 (AI):     {summary['stage2_pass']}\n"
                f"By strategy: A={by.get('A',0)} B={by.get('B',0)} C={by.get('C',0)}\n"
                "Charts and trade plans posted above. Review and execute via Alpaca."
            )
        except Exception:
            pass

    return summary


__all__ = [
    "scan_universe_for_strategy",
    "scan_and_notify_strong_buys",
]
