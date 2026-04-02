"""Signal accuracy tracking and reporting.

Records every signal, checks outcomes after N days, and reports
hit rates, average returns, and breakdown by signal source.

This is CRITICAL for validating the system before real money trading.
Must run for 2-4 weeks on paper trading to establish track record.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
from sqlalchemy.exc import SQLAlchemyError

from app.config import settings
from app.db.database import SessionLocal
from app.db.models import SignalHistory

logger = logging.getLogger("screener")


def check_signal_outcomes(lookback_days: int = 5):
    """Check outcomes for signals that are old enough to evaluate.

    For each signal that is lookback_days old and has no outcome yet,
    fetches the current price and records the result.
    """
    from app.services.market_data import fetch as fetch_market_data

    cutoff = datetime.utcnow() - timedelta(days=lookback_days)

    try:
        db = SessionLocal()
        try:
            # Find signals without outcomes that are old enough
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
                    if df is not None and len(df) > 0:
                        current_price = float(df["close"].iloc[-1])
                        ret_pct = ((current_price - signal.price) / signal.price) * 100

                        signal.outcome_price = current_price
                        signal.outcome_date = datetime.utcnow()
                        signal.outcome_return_pct = round(ret_pct, 2)
                        updated += 1
                except Exception as e:
                    logger.debug(f"Could not check outcome for {signal.symbol}: {e}")

            db.commit()
            return {"checked": len(pending), "updated": updated}
        finally:
            db.close()
    except SQLAlchemyError as e:
        logger.error(f"Signal outcome check failed: {e}")
        return {"error": str(e)}


def get_accuracy_report(period_days: int = 30) -> list[dict]:
    """Generate signal accuracy report.

    Returns breakdown by signal type with hit rates and avg returns.
    """
    cutoff = datetime.utcnow() - timedelta(days=period_days)

    try:
        db = SessionLocal()
        try:
            # All signals with outcomes in the period
            signals = db.query(SignalHistory).filter(
                SignalHistory.created_at >= cutoff,
                SignalHistory.outcome_price.isnot(None),
            ).all()

            if not signals:
                return [{"Message": f"No evaluated signals in last {period_days} days. Signals need {5}+ days to mature."}]

            # Group by signal_type
            by_type = {}
            for s in signals:
                key = s.signal_type
                if key not in by_type:
                    by_type[key] = []
                by_type[key].append(s)

            results = []
            # Overall stats
            all_returns = [s.outcome_return_pct for s in signals if s.outcome_return_pct is not None]
            buy_signals = [s for s in signals if "BUY" in (s.signal or "").upper()]
            buy_returns = [s.outcome_return_pct for s in buy_signals if s.outcome_return_pct is not None]

            buy_wins = [r for r in buy_returns if r > 0]
            buy_losses = [r for r in buy_returns if r <= 0]

            results.append({
                "Source": "OVERALL",
                "Total Signals": len(signals),
                "Buy Signals": len(buy_signals),
                "Avg Return %": round(np.mean(all_returns), 2) if all_returns else 0,
                "Buy Win Rate %": round(len(buy_wins) / len(buy_returns) * 100, 1) if buy_returns else 0,
                "Avg Win %": round(np.mean(buy_wins), 2) if buy_wins else 0,
                "Avg Loss %": round(np.mean(buy_losses), 2) if buy_losses else 0,
                "Profit Factor": round(abs(sum(buy_wins)) / abs(sum(buy_losses)), 2) if buy_losses and sum(buy_losses) != 0 else 0,
                "Period": f"{period_days} days",
            })

            # Per-type breakdown
            for signal_type, type_signals in sorted(by_type.items()):
                returns = [s.outcome_return_pct for s in type_signals if s.outcome_return_pct is not None]
                buys = [s for s in type_signals if "BUY" in (s.signal or "").upper()]
                buy_ret = [s.outcome_return_pct for s in buys if s.outcome_return_pct is not None]
                wins = [r for r in buy_ret if r > 0]
                losses = [r for r in buy_ret if r <= 0]

                results.append({
                    "Source": signal_type.upper(),
                    "Total Signals": len(type_signals),
                    "Buy Signals": len(buys),
                    "Avg Return %": round(np.mean(returns), 2) if returns else 0,
                    "Buy Win Rate %": round(len(wins) / len(buy_ret) * 100, 1) if buy_ret else 0,
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
