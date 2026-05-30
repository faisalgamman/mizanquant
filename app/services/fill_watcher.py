"""Background polling of broker order status transitions."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from app.config import STRATEGY_CONFIGS, settings

logger = logging.getLogger("screener")

_watcher_thread = None
_watcher_running = False


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _apply_fill_metrics(trade, filled_qty: int, filled_avg_price: float | None) -> None:
    trade.filled_qty = filled_qty
    if filled_avg_price:
        trade.filled_avg_price = filled_avg_price
        trade.entry_price = filled_avg_price
    if filled_qty > 0:
        trade.qty = filled_qty
        effective_price = filled_avg_price or float(trade.entry_price or 0) or 0.0
        if effective_price > 0:
            trade.position_value = round(filled_qty * effective_price, 2)
            stop_loss = float(trade.stop_loss or 0) if trade.stop_loss is not None else 0.0
            if 0 < stop_loss < effective_price:
                trade.risk_amount = round(filled_qty * (effective_price - stop_loss), 2)


def _update_trade_row(strategy_id: str | None, broker_order: dict) -> None:
    try:
        from app.db.database import SessionLocal
        from app.db.models import TradeHistory

        db = SessionLocal()
        try:
            query = db.query(TradeHistory).filter(TradeHistory.order_id == broker_order.get("id", ""))
            if strategy_id:
                query = query.filter(TradeHistory.strategy_id == strategy_id)
            trade = query.order_by(TradeHistory.created_at.desc()).first()
            if trade is None and broker_order.get("client_order_id"):
                query = db.query(TradeHistory).filter(TradeHistory.client_order_id == broker_order["client_order_id"])
                if strategy_id:
                    query = query.filter(TradeHistory.strategy_id == strategy_id)
                trade = query.order_by(TradeHistory.created_at.desc()).first()
            if trade is None:
                return

            trade.status = broker_order.get("status") or trade.status
            if broker_order.get("filled_qty") not in (None, ""):
                filled_qty = int(float(broker_order.get("filled_qty") or 0))
                filled_avg = (
                    float(broker_order.get("filled_avg_price") or 0)
                    if broker_order.get("filled_avg_price") not in (None, "")
                    else None
                )
                _apply_fill_metrics(trade, filled_qty, filled_avg)
                # ── 2C: TCA — compute realized slippage vs pre-trade model ──
                if filled_avg and filled_avg > 0:
                    try:
                        from app.services.tca import record_tca_to_trade
                        record_tca_to_trade(trade, db)
                    except Exception as _tca_err:
                        logger.debug("TCA recording skipped: %s", _tca_err)
            db.commit()
        finally:
            db.close()
    except Exception as exc:
        logger.error("Fill watcher DB update failed: %s", exc, exc_info=True)


def _poll_once():
    from app.services.alpaca_client import get_orders as alpaca_get_orders
    from app.services.telegram_alert import send_message as tg_send
    from app.services.trading_engine import _cancel_order

    strategy_ids = list(STRATEGY_CONFIGS.keys())
    if settings.ALPACA_API_KEY:
        strategy_ids.append(None)

    for strategy_id in strategy_ids:
        orders = alpaca_get_orders(status="open", limit=200, strategy_id=strategy_id) or []
        for order in orders:
            _update_trade_row(strategy_id, order)
            filled_qty = int(float(order.get("filled_qty") or 0))
            qty = int(float(order.get("qty") or 0))
            if qty > 0 and 0 < filled_qty < qty:
                try:
                    from app.db.database import SessionLocal
                    from app.db.models import TradeHistory

                    db = SessionLocal()
                    try:
                        trade = db.query(TradeHistory).filter(
                            TradeHistory.order_id == order.get("id", ""),
                        ).first()
                    finally:
                        db.close()
                    if trade and trade.created_at and (_utc_now() - trade.created_at) > timedelta(minutes=5):
                        fill_ratio = filled_qty / max(qty, 1)
                        if fill_ratio < 0.5:
                            _cancel_order(order.get("id", ""), strategy_id=strategy_id)
                            tg_send(
                                f"PARTIAL FILL CANCELED\n\n"
                                f"Symbol: {order.get('symbol')}\n"
                                f"Filled: {filled_qty}/{qty}"
                            )
                except Exception:
                    logger.exception("Partial-fill follow-up failed")


def _watcher_loop():
    global _watcher_running
    while _watcher_running:
        try:
            _poll_once()
        except Exception as exc:
            logger.error("Fill watcher poll failed: %s", exc, exc_info=True)
        time.sleep(20)


def start_fill_watcher():
    global _watcher_thread, _watcher_running
    if _watcher_running:
        return
    _watcher_running = True
    _watcher_thread = threading.Thread(target=_watcher_loop, daemon=True, name="fill-watcher")
    _watcher_thread.start()


def stop_fill_watcher():
    global _watcher_running
    _watcher_running = False
