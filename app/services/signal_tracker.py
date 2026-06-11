"""Signal accuracy tracking and reporting.

Records every signal, checks outcomes after N days, and reports
hit rates, average returns, and breakdown by signal source.

This is CRITICAL for validating the system before real money trading.
Must run for 2-4 weeks on paper trading to establish track record.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np
from sqlalchemy.exc import SQLAlchemyError

from app.config import settings
from app.db.database import SessionLocal
from app.db.models import SignalHistory

logger = logging.getLogger("screener")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _simulate_fixed_exit(post_bars, entry_price: float, hold_days: int,
                         stop_pct: float, is_sell: bool):
    """Realized return of holding from entry until the FIRST of:
      • the fixed catastrophe stop is hit (BUY: low<=entry·(1−s); SELL: high>=entry·(1+s)), or
      • `hold_days` trading days elapse (time exit at that day's close).

    Mirrors the live Option-A exit so the measured PF reflects what is traded.
    Returns (ret_pct, exit_price) or None when the signal has NOT matured yet
    (fewer than hold_days bars available and no stop hit).
    """
    if entry_price <= 0 or post_bars is None or len(post_bars) == 0:
        return None
    s = stop_pct / 100.0
    n_avail = len(post_bars)
    n = min(hold_days, n_avail)
    exit_price = None
    if is_sell:
        stop_price = entry_price * (1.0 + s)
        for i in range(n):
            if float(post_bars["high"].iloc[i]) >= stop_price:
                exit_price = stop_price
                break
    else:
        stop_price = entry_price * (1.0 - s)
        for i in range(n):
            if float(post_bars["low"].iloc[i]) <= stop_price:
                exit_price = stop_price
                break
    if exit_price is None:
        if n_avail >= hold_days:
            exit_price = float(post_bars["close"].iloc[hold_days - 1])  # time exit
        else:
            return None  # not matured, no stop hit yet → leave pending
    chg = (exit_price / entry_price - 1.0) * 100.0
    ret = -chg if is_sell else chg
    return round(ret, 2), round(exit_price, 2)


def check_signal_outcomes(lookback_days: int | None = None):
    """Evaluate matured signals on the ACTUAL exit policy (Option A).

    A signal is scored by simulating the live exit — a fixed catastrophe stop
    (SIGNAL_OUTCOME_STOP_PCT) or the time exit at SIGNAL_OUTCOME_HOLD_DAYS — not a
    stale current-price snapshot. Signals younger than the hold window that have
    not hit their stop are left pending until they mature.
    """
    from app.services.market_data import fetch as fetch_market_data
    from app.config import settings as _cfg

    hold_days = int(getattr(_cfg, "SIGNAL_OUTCOME_HOLD_DAYS", 20))
    stop_pct = float(getattr(_cfg, "SIGNAL_OUTCOME_STOP_PCT", 15.0))
    # Only consider signals old enough that the hold window could have elapsed
    # (trading days ≈ calendar·5/7, plus a holiday buffer). The per-signal
    # maturity check in _simulate_fixed_exit is the exact gate.
    maturity_days = lookback_days if lookback_days is not None else int(hold_days * 1.5) + 3
    cutoff = _utc_now() - timedelta(days=maturity_days)

    try:
        db = SessionLocal()
        try:
            pending = db.query(SignalHistory).filter(
                SignalHistory.outcome_price.is_(None),
                SignalHistory.created_at <= cutoff,
                SignalHistory.price > 0,
            ).all()

            if not pending:
                return {"checked": 0, "updated": 0}

            updated = 0
            for signal in pending:
                try:
                    df = fetch_market_data(signal.symbol, period="1y")
                    if df is None or len(df) == 0:
                        continue
                    # Bars strictly AFTER the signal date (the forward path).
                    try:
                        post = df[df.index > signal.created_at]
                    except Exception:
                        post = df.tail(hold_days + 5)
                    sig_text = (signal.signal or "").upper()
                    is_sell = "SELL" in sig_text and "BUY" not in sig_text
                    sim = _simulate_fixed_exit(
                        post, float(signal.price), hold_days, stop_pct, is_sell
                    )
                    if sim is None:
                        continue  # not matured yet
                    ret_pct, exit_price = sim
                    signal.outcome_price = exit_price
                    signal.outcome_date = _utc_now()
                    signal.outcome_return_pct = ret_pct
                    updated += 1
                except Exception as e:
                    logger.debug(f"Could not check outcome for {signal.symbol}: {e}")

            db.commit()
            return {"checked": len(pending), "updated": updated,
                    "hold_days": hold_days, "stop_pct": stop_pct}
        finally:
            db.close()
    except SQLAlchemyError as e:
        logger.error(f"Signal outcome check failed: {e}")
        return {"error": str(e)}


def get_accuracy_report(period_days: int = 30) -> list[dict]:
    """Generate signal accuracy report.

    Returns breakdown by signal type with hit rates and avg returns.
    """
    cutoff = _utc_now() - timedelta(days=period_days)

    try:
        db = SessionLocal()
        try:
            # All signals with outcomes in the period
            signals = db.query(SignalHistory).filter(
                SignalHistory.created_at >= cutoff,
                SignalHistory.outcome_price.isnot(None),
            ).all()

            if not signals:
                _hd = int(getattr(__import__("app.config", fromlist=["settings"]).settings,
                                  "SIGNAL_OUTCOME_HOLD_DAYS", 20))
                return [{"Message": f"No evaluated signals in last {period_days} days. "
                                    f"Signals mature at the {_hd}-day exit (or earlier if the catastrophe stop is hit)."}]

            # Group by signal_type
            by_type = {}
            for s in signals:
                key = s.signal_type
                if key not in by_type:
                    by_type[key] = []
                by_type[key].append(s)

            results = []
            # outcome_return_pct is direction-normalised: positive = profitable for
            # both BUY and SELL signals (SELL returns are negated at recording time).
            all_returns = [s.outcome_return_pct for s in signals if s.outcome_return_pct is not None]
            buy_signals = [s for s in signals if "BUY" in (s.signal or "").upper()]
            sell_signals = [s for s in signals
                            if "SELL" in (s.signal or "").upper()
                            and "BUY" not in (s.signal or "").upper()]
            actionable = buy_signals + sell_signals
            act_returns = [s.outcome_return_pct for s in actionable if s.outcome_return_pct is not None]
            wins = [r for r in act_returns if r > 0]
            losses = [r for r in act_returns if r <= 0]

            results.append({
                "Source": "OVERALL",
                "Total Signals": len(signals),
                "Buy Signals": len(buy_signals),
                "Sell Signals": len(sell_signals),
                "Avg Return %": round(np.mean(all_returns), 2) if all_returns else 0,
                "Win Rate %": round(len(wins) / len(act_returns) * 100, 1) if act_returns else 0,
                "Avg Win %": round(np.mean(wins), 2) if wins else 0,
                "Avg Loss %": round(np.mean(losses), 2) if losses else 0,
                "Profit Factor": round(abs(sum(wins)) / abs(sum(losses)), 2) if losses and sum(losses) != 0 else 0,
                "Period": f"{period_days} days",
            })

            # B5: Buy-side only (honest split — separates from broken sell side)
            buy_returns = [s.outcome_return_pct for s in buy_signals if s.outcome_return_pct is not None]
            buy_wins = [r for r in buy_returns if r > 0]
            buy_losses = [r for r in buy_returns if r <= 0]
            results.append({
                "Source": "BUY-SIDE",
                "Total Signals": len(buy_signals),
                "Buy Signals": len(buy_signals),
                "Sell Signals": 0,
                "Avg Return %": round(np.mean(buy_returns), 2) if buy_returns else 0,
                "Win Rate %": round(len(buy_wins) / len(buy_returns) * 100, 1) if buy_returns else 0,
                "Avg Win %": round(np.mean(buy_wins), 2) if buy_wins else 0,
                "Avg Loss %": round(np.mean(buy_losses), 2) if buy_losses else 0,
                "Profit Factor": round(abs(sum(buy_wins)) / abs(sum(buy_losses)), 2) if buy_losses and sum(buy_losses) != 0 else (999 if not buy_losses else 0),
                "Period": f"{period_days} days",
            })

            # Per-type breakdown
            for signal_type, type_signals in sorted(by_type.items()):
                returns = [s.outcome_return_pct for s in type_signals if s.outcome_return_pct is not None]
                buys = [s for s in type_signals if "BUY" in (s.signal or "").upper()]
                sells = [s for s in type_signals
                         if "SELL" in (s.signal or "").upper()
                         and "BUY" not in (s.signal or "").upper()]
                act = buys + sells
                act_ret = [s.outcome_return_pct for s in act if s.outcome_return_pct is not None]
                wins = [r for r in act_ret if r > 0]
                losses = [r for r in act_ret if r <= 0]

                results.append({
                    "Source": signal_type.upper(),
                    "Total Signals": len(type_signals),
                    "Buy Signals": len(buys),
                    "Sell Signals": len(sells),
                    "Avg Return %": round(np.mean(returns), 2) if returns else 0,
                    "Win Rate %": round(len(wins) / len(act_ret) * 100, 1) if act_ret else 0,
                    "Avg Win %": round(np.mean(wins), 2) if wins else 0,
                    "Avg Loss %": round(np.mean(losses), 2) if losses else 0,
                    "Profit Factor": round(abs(sum(wins)) / abs(sum(losses)), 2) if losses and sum(losses) != 0 else 0,
                    "Period": f"{period_days} days",
                })

            return results
        finally:
            db.close()
    except SQLAlchemyError as e:
        logger.error(f"Accuracy report failed: {e}")
        return [{"Error": str(e)}]


def get_signal_history(symbol: str = None, limit: int = 50) -> list[dict]:
    """Get recent signal history, optionally filtered by symbol."""
    try:
        db = SessionLocal()
        try:
            query = db.query(SignalHistory).order_by(SignalHistory.created_at.desc())
            if symbol:
                query = query.filter(SignalHistory.symbol == symbol.upper())
            signals = query.limit(limit).all()

            results = []
            for s in signals:
                row = {
                    "Symbol": s.symbol,
                    "Type": s.signal_type,
                    "Signal": s.signal,
                    "Score": s.score,
                    "Price": round(s.price, 2) if s.price else 0,
                    "SL": round(s.stop_loss, 2) if s.stop_loss else 0,
                    "TP": round(s.take_profit, 2) if s.take_profit else 0,
                    "Date": s.created_at.strftime("%Y-%m-%d %H:%M") if s.created_at else "",
                }
                if s.outcome_price is not None:
                    row["Outcome Price"] = round(s.outcome_price, 2)
                    row["Return %"] = s.outcome_return_pct
                    # outcome_return_pct is direction-normalised: positive = profit
                    row["Result"] = "WIN" if s.outcome_return_pct and s.outcome_return_pct > 0 else "LOSS"
                else:
                    row["Outcome Price"] = "Pending"
                    row["Return %"] = "—"
                    row["Result"] = "Pending"
                results.append(row)

            return results if results else [{"Message": "No signals recorded yet"}]
        finally:
            db.close()
    except SQLAlchemyError as e:
        logger.error(f"Signal history query failed: {e}")
        return [{"Error": str(e)}]
