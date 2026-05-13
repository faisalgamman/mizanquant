"""Trade history / reporting — extracted from trading_engine for V1 decoupling."""

from __future__ import annotations

import logging

logger = logging.getLogger("screener")


def get_trade_history(limit: int = 50, strategy_id: str = None) -> list[dict]:
    """Get recent trade history from DB, optionally filtered by strategy."""
    try:
        from app.db.database import SessionLocal
        from app.db.models import TradeHistory

        db = SessionLocal()
        try:
            query = db.query(TradeHistory)
            if strategy_id:
                query = query.filter(TradeHistory.strategy_id == strategy_id)
            trades = query.order_by(
                TradeHistory.created_at.desc()
            ).limit(limit).all()

            return [
                {
                    "symbol": t.symbol,
                    "side": t.side,
                    "qty": t.qty,
                    "entry_price": t.entry_price,
                    "exit_price": t.exit_price,
                    "stop_loss": t.stop_loss,
                    "take_profit": t.take_profit,
                    "position_value": t.position_value,
                    "risk_pct": t.risk_pct,
                    "pnl": t.pnl,
                    "pnl_pct": t.pnl_pct,
                    "confidence": t.confidence,
                    "status": t.status,
                    "order_id": t.order_id,
                    "client_order_id": t.client_order_id,
                    "filled_qty": t.filled_qty,
                    "filled_avg_price": t.filled_avg_price,
                    "armed_at": t.armed_at.isoformat() if t.armed_at else "",
                    "strategy_id": t.strategy_id,
                    "created_at": t.created_at.isoformat() if t.created_at else "",
                }
                for t in trades
            ]
        finally:
            db.close()
    except Exception as e:
        logger.error(f"Failed to get trade history: {e}")
        return []
