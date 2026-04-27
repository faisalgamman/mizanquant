"""Signals-advisor service — scans the halal universe via the three
strategies (A/B/C), keeps only STRONG BUY consensus verdicts above a
confidence threshold, and pushes a fully-formed Telegram alert per
signal so the operator can execute the trade manually (e.g. on
Interactive Brokers).

Independent of the auto-trader: it does not place broker orders, does
not write to TradeHistory, does not respect AUTO_TRADE_ENABLED. It
runs purely as a notification pipeline.

Each Telegram alert contains everything a human needs to enter the
position by hand:
- Symbol, strategy, confidence
- Live price + entry-style guidance
- Stop-loss + take-profit prices
- Suggested share count for a $5k account (configurable)
- Vote breakdown
- Time stamp (US/Eastern)

Usage:
    from app.services.signals_advisor import scan_and_notify_strong_buys
    summary = scan_and_notify_strong_buys()
    # {"sent": 3, "scanned": 84, "by_strategy": {"A": 1, "B": 2, "C": 0}}
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from zoneinfo import ZoneInfo

logger = logging.getLogger("screener")

# Default risk parameters used to size the alert recommendation.
# These mirror Strategy-A defaults; the operator can override at call time.
_DEFAULT_ACCOUNT_USD = 5000.0
_DEFAULT_RISK_PCT = 0.01      # 1% risk per trade
_DEFAULT_STOP_PCT = 0.03      # 3% stop loss
_DEFAULT_TAKE_PCT = 0.06      # 6% take profit (2:1 R:R)


# ---------------------------------------------------------------------------
# Core scanning
# ---------------------------------------------------------------------------

def _strategy_runners() -> dict:
    """Return {strategy_id: callable} for each available consensus."""
    import halal_screener as hs

    return {
        "A": (hs.run_consensus_momentum, "HANA / Momentum"),
        "B": (hs.run_consensus_reversion, "marem / Mean-Reversion"),
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
    """Run a single consensus call; return the row only if STRONG BUY."""
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
    max_workers: int = 4,
    min_confidence: float = 70.0,
) -> list[dict]:
    """Scan the halal universe with one strategy. Returns the rows
    that pass the STRONG BUY filter."""
    runners = _strategy_runners()
    if strategy_id not in runners:
        return []
    runner, label = runners[strategy_id]

    if symbols is None:
        import halal_screener as hs
        symbols = list(hs.HALAL_STOCKS)

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
    # Highest confidence first
    out.sort(key=lambda r: float(r.get("Confidence %", 0) or 0), reverse=True)
    return out


# ---------------------------------------------------------------------------
# Telegram formatting
# ---------------------------------------------------------------------------

def _format_signal(
    row: dict,
    account_usd: float = _DEFAULT_ACCOUNT_USD,
    risk_pct: float = _DEFAULT_RISK_PCT,
    stop_pct: float = _DEFAULT_STOP_PCT,
    take_pct: float = _DEFAULT_TAKE_PCT,
) -> str:
    """Format a single STRONG BUY row as a Telegram-ready message.

    Numbers are rounded conservatively; the operator places the final
    order and can adjust as desired.
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
        # Without a live price we can't size the position cleanly.
        sl = tp = 0.0
        shares = 0
        risk_dollars = 0.0
        notional = 0.0
    else:
        sl = round(price * (1.0 - stop_pct), 2)
        tp = round(price * (1.0 + take_pct), 2)
        risk_dollars = account_usd * risk_pct
        risk_per_share = max(price - sl, 0.01)
        shares_by_risk = int(risk_dollars / risk_per_share)
        max_position_value = account_usd * 0.20  # cap each position at 20% of account
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
        f"Confidence: {confidence:.0f}%",
        "━━━━━━━━━━━━━━━",
        f"Price:      ${price:.2f}",
        f"Stop Loss:  ${sl:.2f}  (−{stop_pct*100:.1f}%)",
        f"Take Prof:  ${tp:.2f}  (+{take_pct*100:.1f}%)",
        f"Shares:     {shares}  (≈ ${notional:.2f} notional)",
        f"Risk:       ${risk_dollars:.2f}  ({risk_pct*100:.1f}% of ${account_usd:.0f})",
        "━━━━━━━━━━━━━━━",
        f"Votes:      BUY {votes_buy} / SELL {votes_sell} / HOLD {votes_hold}",
        f"Time:       {now_et}",
        "",
        "Manual execution on IBKR — review before placing.",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def scan_and_notify_strong_buys(
    strategy_ids: tuple[str, ...] = ("A", "B", "C"),
    symbols: list[str] | None = None,
    max_workers: int = 4,
    min_confidence: float = 70.0,
    account_usd: float = _DEFAULT_ACCOUNT_USD,
    risk_pct: float = _DEFAULT_RISK_PCT,
    stop_pct: float = _DEFAULT_STOP_PCT,
    take_pct: float = _DEFAULT_TAKE_PCT,
    dry_run: bool = False,
) -> dict:
    """Scan all strategies, filter to STRONG BUY at confidence >= threshold,
    push one Telegram message per signal, return a summary."""
    from app.services.telegram_alert import send_message as tg_send

    summary = {
        "sent": 0,
        "scanned_strategies": list(strategy_ids),
        "by_strategy": {},
        "signals": [],
    }

    for sid in strategy_ids:
        rows = scan_universe_for_strategy(
            sid, symbols=symbols, max_workers=max_workers, min_confidence=min_confidence
        )
        summary["by_strategy"][sid] = len(rows)
        for row in rows:
            text = _format_signal(
                row,
                account_usd=account_usd,
                risk_pct=risk_pct,
                stop_pct=stop_pct,
                take_pct=take_pct,
            )
            summary["signals"].append({
                "strategy_id": sid,
                "symbol": row.get("Symbol"),
                "confidence": float(row.get("Confidence %", 0) or 0),
                "preview": text,
            })
            if dry_run:
                continue
            try:
                if tg_send(text):
                    summary["sent"] += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("signals_advisor: telegram send failed: %s", exc)

    if not dry_run and summary["sent"] == 0 and any(summary["by_strategy"].values()):
        # We had signals but Telegram refused them — surface a header so the
        # operator notices.
        try:
            tg_send(
                "Signals advisor: signals were generated but Telegram delivery "
                f"failed. Check logs. by_strategy={summary['by_strategy']}"
            )
        except Exception:
            pass

    return summary


__all__ = [
    "scan_universe_for_strategy",
    "scan_and_notify_strong_buys",
]
