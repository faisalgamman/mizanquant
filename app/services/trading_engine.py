"""Automated paper trading engine.

Stage 1: Execute trades on Alpaca Paper account based on consensus signals.
- STRONG BUY  → open long position with stop-loss + take-profit
- STRONG SELL → close position if held
- Swing trading only (hold overnight to avoid PDT)
- All risk checks enforced before every trade

NO real money. Paper account only.
"""

import logging
import time
import threading
from datetime import datetime
from typing import Optional

import httpx

from app.config import settings
from app.services.risk_manager import (
    check_trade_eligibility,
    calculate_position_size,
    record_day_trade,
    can_day_trade,
    get_risk_status,
)
from app.services.alpaca_client import (
    get_account as alpaca_get_account,
    get_positions as alpaca_get_positions,
)
from app.services.telegram_alert import send_message as tg_send

logger = logging.getLogger("screener")

# ---------------------------------------------------------------------------
# Alpaca order execution (paper only)
# ---------------------------------------------------------------------------

_trade_lock = threading.Lock()  # serialize order submissions


def _get_headers() -> dict:
    return {
        "APCA-API-KEY-ID": settings.ALPACA_API_KEY,
        "APCA-API-SECRET-KEY": settings.ALPACA_SECRET_KEY,
    }


def _get_base_url() -> str:
    url = settings.ALPACA_BASE_URL
    if "data.alpaca" in url:
        url = url.replace("data.alpaca", "paper-api.alpaca")
    return url.rstrip("/").removesuffix("/v2")


def _submit_order(order_payload: dict) -> Optional[dict]:
    """Submit an order to Alpaca Trading API."""
    if not settings.ALPACA_API_KEY:
        logger.error("Trading engine: No Alpaca API key configured")
        return None

    base = _get_base_url()
    url = f"{base}/v2/orders"

    try:
        with httpx.Client(timeout=15) as client:
            resp = client.post(url, headers=_get_headers(), json=order_payload)
            if resp.status_code in (200, 201):
                order = resp.json()
                logger.info(f"Order submitted: {order.get('id')} - {order_payload.get('symbol')} "
                           f"{order_payload.get('side')} {order_payload.get('qty')} shares")
                return order
            else:
                logger.error(f"Order rejected ({resp.status_code}): {resp.text[:300]}")
                return None
    except Exception as e:
        logger.error(f"Order submission error: {e}")
        return None


def _cancel_order(order_id: str) -> bool:
    """Cancel a pending order."""
    base = _get_base_url()
    url = f"{base}/v2/orders/{order_id}"
    try:
        with httpx.Client(timeout=10) as client:
            resp = client.delete(url, headers=_get_headers())
            return resp.status_code in (200, 204)
    except Exception as e:
        logger.error(f"Cancel order error: {e}")
        return False


def _close_position(symbol: str) -> Optional[dict]:
    """Close an entire position for a symbol."""
    base = _get_base_url()
    url = f"{base}/v2/positions/{symbol}"
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.delete(url, headers=_get_headers())
            if resp.status_code == 200:
                data = resp.json()
                logger.info(f"Position closed: {symbol}")
                return data
            else:
                logger.error(f"Close position failed ({resp.status_code}): {resp.text[:200]}")
                return None
    except Exception as e:
        logger.error(f"Close position error for {symbol}: {e}")
        return None


# ---------------------------------------------------------------------------
# Trade execution logic
# ---------------------------------------------------------------------------

def execute_buy(
    symbol: str,
    price: float,
    stop_loss: float,
    take_profit: float,
    confidence: float,
    signal_details: dict,
) -> dict:
    """Execute a BUY trade with full risk checks.

    Strategy: Bracket order (entry + stop-loss + take-profit)
    This is a swing trade — we hold overnight to avoid PDT.

    Returns dict with trade result or rejection reason.
    """
    with _trade_lock:
        result = {
            "symbol": symbol,
            "action": "BUY",
            "executed": False,
            "timestamp": datetime.utcnow().isoformat(),
        }

        # Auto-trading enabled?
        if not settings.AUTO_TRADE_ENABLED:
            result["reason"] = "Auto-trading is disabled (AUTO_TRADE_ENABLED=false)"
            return result

        # Halal verification gate — MUST pass before buying
        try:
            from halal_screener import verify_halal
            is_halal, halal_reason = verify_halal(symbol)
            if not is_halal:
                result["reason"] = f"BLOCKED — not halal: {halal_reason}"
                logger.warning(f"Trade blocked for {symbol}: {halal_reason}")
                _notify_trade(result)
                return result
        except ImportError:
            logger.warning(f"Halal verification unavailable for {symbol} — blocking trade")
            result["reason"] = "Halal verification unavailable — trade blocked for safety"
            return result

        # Get live account + positions
        account = alpaca_get_account()
        if not account:
            result["reason"] = "Cannot fetch account info"
            return result

        positions = alpaca_get_positions()

        # Run all risk checks
        eligibility = check_trade_eligibility(
            symbol=symbol,
            side="buy",
            price=price,
            stop_loss=stop_loss,
            account=account,
            open_positions=positions,
        )

        if not eligibility["eligible"]:
            result["reason"] = eligibility["reason"]
            logger.info(f"Trade rejected for {symbol}: {eligibility['reason']}")
            _notify_trade(result)
            return result

        sizing = eligibility["sizing"]
        qty = sizing["qty"]

        # Submit bracket order (market entry + stop-loss + take-profit)
        # Use trailing stop if enabled — locks in gains as price rises
        use_trailing = settings.TRAILING_STOP_ENABLED
        trail_pct = settings.TRAILING_STOP_PCT

        order_payload = {
            "symbol": symbol,
            "qty": str(qty),
            "side": "buy",
            "type": "market",
            "time_in_force": "day",
            "order_class": "bracket",
            "take_profit": {
                "limit_price": str(round(take_profit, 2)),
            },
        }

        if use_trailing:
            # Trailing stop: follows price up, triggers when price drops trail_pct% from peak
            order_payload["stop_loss"] = {
                "stop_price": str(round(stop_loss, 2)),  # initial floor
                "trail_percent": str(round(trail_pct, 1)),
            }
        else:
            # Static stop-loss at fixed price
            order_payload["stop_loss"] = {
                "stop_price": str(round(stop_loss, 2)),
            }

        order = _submit_order(order_payload)
        if not order:
            result["reason"] = "Order submission failed"
            _notify_trade(result)
            return result

        # Success
        result["executed"] = True
        result["order_id"] = order.get("id", "")
        result["qty"] = qty
        result["entry_price"] = price
        result["stop_loss"] = stop_loss
        result["take_profit"] = take_profit
        result["position_value"] = sizing["position_value"]
        result["risk_amount"] = sizing["risk_amount"]
        result["risk_pct"] = sizing["risk_pct"]
        result["confidence"] = confidence
        result["reason"] = "Order submitted successfully"

        # Record trade in DB
        _record_trade(result, signal_details)

        # Notify via Telegram
        _notify_trade(result)

        logger.info(
            f"BUY executed: {symbol} x{qty} @ ~${price:.2f} | "
            f"SL=${stop_loss:.2f} TP=${take_profit:.2f} | "
            f"Risk: ${sizing['risk_amount']:.2f} ({sizing['risk_pct']:.1f}%)"
        )

        return result


def execute_sell(symbol: str, price: float, confidence: float) -> dict:
    """Close a position on STRONG SELL signal.

    Only closes if we're holding the symbol.
    """
    with _trade_lock:
        result = {
            "symbol": symbol,
            "action": "SELL",
            "executed": False,
            "timestamp": datetime.utcnow().isoformat(),
        }

        if not settings.AUTO_TRADE_ENABLED:
            result["reason"] = "Auto-trading is disabled"
            return result

        # Check if we hold this position
        positions = alpaca_get_positions()
        held = [p for p in positions if p["symbol"] == symbol]

        if not held:
            result["reason"] = f"Not holding {symbol} — nothing to sell"
            return result

        position = held[0]
        close_result = _close_position(symbol)

        if not close_result:
            result["reason"] = "Failed to close position"
            _notify_trade(result)
            return result

        result["executed"] = True
        result["qty"] = position["qty"]
        result["entry_price"] = position["avg_entry_price"]
        result["exit_price"] = price
        result["unrealized_pl"] = position["unrealized_pl"]
        result["unrealized_plpc"] = position["unrealized_plpc"]
        result["confidence"] = confidence
        result["reason"] = "Position closed"

        _record_trade(result, {})
        _notify_trade(result)

        logger.info(
            f"SELL executed: {symbol} x{position['qty']} | "
            f"P&L: ${position['unrealized_pl']:.2f} ({position['unrealized_plpc']:.1f}%)"
        )

        return result


# ---------------------------------------------------------------------------
# Auto-trade hook — called from run_consensus()
# ---------------------------------------------------------------------------

def on_signal(
    symbol: str,
    verdict: str,
    confidence: float,
    price: float,
    stop_loss: float,
    take_profit: float,
    votes_buy: int,
    votes_sell: int,
    votes_hold: int,
    details: dict,
) -> Optional[dict]:
    """Called automatically when consensus generates a signal.

    Only acts on STRONG BUY/SELL with confidence >= threshold.
    This is the main entry point from halal_screener.py.
    """
    if not settings.AUTO_TRADE_ENABLED:
        return None

    # Block trades outside market hours (before making any API calls)
    from app.services.risk_manager import is_market_open
    if not is_market_open():
        logger.info(f"Signal {verdict} for {symbol} ignored — market is closed")
        return None

    min_confidence = settings.MIN_TRADE_CONFIDENCE

    if verdict == "STRONG BUY" and confidence >= min_confidence:
        logger.info(f"Auto-trade trigger: STRONG BUY {symbol} (confidence={confidence}%)")
        return execute_buy(
            symbol=symbol,
            price=price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            confidence=confidence,
            signal_details={
                "verdict": verdict,
                "votes_buy": votes_buy,
                "votes_sell": votes_sell,
                "votes_hold": votes_hold,
            },
        )

    elif verdict == "STRONG SELL" and confidence >= min_confidence:
        logger.info(f"Auto-trade trigger: STRONG SELL {symbol} (confidence={confidence}%)")
        return execute_sell(
            symbol=symbol,
            price=price,
            confidence=confidence,
        )

    return None


# ---------------------------------------------------------------------------
# Trade recording (to DB)
# ---------------------------------------------------------------------------

def _record_trade(trade_result: dict, signal_details: dict):
    """Persist trade to database for performance tracking."""
    try:
        from app.db.database import SessionLocal
        from app.db.models import TradeHistory

        db = SessionLocal()
        try:
            trade = TradeHistory(
                symbol=trade_result["symbol"],
                side=trade_result["action"].lower(),
                qty=trade_result.get("qty", 0),
                entry_price=trade_result.get("entry_price", 0),
                stop_loss=trade_result.get("stop_loss"),
                take_profit=trade_result.get("take_profit"),
                exit_price=trade_result.get("exit_price"),
                position_value=trade_result.get("position_value"),
                risk_amount=trade_result.get("risk_amount"),
                risk_pct=trade_result.get("risk_pct"),
                confidence=trade_result.get("confidence", 0),
                order_id=trade_result.get("order_id", ""),
                status="submitted" if trade_result["executed"] else "rejected",
                signal_details=signal_details,
                pnl=trade_result.get("unrealized_pl"),
                pnl_pct=trade_result.get("unrealized_plpc"),
            )
            db.add(trade)
            db.commit()
        finally:
            db.close()
    except Exception as e:
        logger.error(f"Failed to record trade: {e}")


# ---------------------------------------------------------------------------
# Telegram notifications
# ---------------------------------------------------------------------------

def _notify_trade(trade_result: dict):
    """Send trade notification via Telegram."""
    try:
        symbol = trade_result["symbol"]
        action = trade_result["action"]
        executed = trade_result["executed"]

        if executed:
            if action == "BUY":
                sl_type = "Trailing Stop" if settings.TRAILING_STOP_ENABLED else "Stop Loss"
                trail_info = f" (trail {settings.TRAILING_STOP_PCT}%)" if settings.TRAILING_STOP_ENABLED else ""
                msg = (
                    f"AUTO TRADE - BUY\n\n"
                    f"Symbol: {symbol}\n"
                    f"Qty: {trade_result.get('qty', 0)} shares\n"
                    f"Price: ${trade_result.get('entry_price', 0):.2f}\n"
                    f"Value: ${trade_result.get('position_value', 0):.2f}\n"
                    f"{sl_type}: ${trade_result.get('stop_loss', 0):.2f}{trail_info}\n"
                    f"Take Profit: ${trade_result.get('take_profit', 0):.2f}\n"
                    f"Risk: ${trade_result.get('risk_amount', 0):.2f} ({trade_result.get('risk_pct', 0):.1f}%)\n"
                    f"Confidence: {trade_result.get('confidence', 0)}%\n"
                    f"Order ID: {trade_result.get('order_id', 'N/A')}"
                )
            else:
                msg = (
                    f"AUTO TRADE - SELL\n\n"
                    f"Symbol: {symbol}\n"
                    f"Qty: {trade_result.get('qty', 0)} shares\n"
                    f"Entry: ${trade_result.get('entry_price', 0):.2f}\n"
                    f"Exit: ${trade_result.get('exit_price', 0):.2f}\n"
                    f"P&L: ${trade_result.get('unrealized_pl', 0):.2f} ({trade_result.get('unrealized_plpc', 0):.1f}%)\n"
                    f"Confidence: {trade_result.get('confidence', 0)}%"
                )
        else:
            msg = (
                f"TRADE REJECTED - {action}\n\n"
                f"Symbol: {symbol}\n"
                f"Reason: {trade_result.get('reason', 'Unknown')}"
            )

        tg_send(msg)
    except Exception as e:
        logger.debug(f"Trade notification failed: {e}")


# ---------------------------------------------------------------------------
# Performance report
# ---------------------------------------------------------------------------

def get_trade_history(limit: int = 50) -> list[dict]:
    """Get recent trade history from DB."""
    try:
        from app.db.database import SessionLocal
        from app.db.models import TradeHistory

        db = SessionLocal()
        try:
            trades = db.query(TradeHistory).order_by(
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
                    "created_at": t.created_at.isoformat() if t.created_at else "",
                }
                for t in trades
            ]
        finally:
            db.close()
    except Exception as e:
        logger.error(f"Failed to get trade history: {e}")
        return []


def get_performance_report() -> dict:
    """Generate trading performance report.

    Returns win rate, avg return, Sharpe, max drawdown, profit factor.
    """
    try:
        from app.db.database import SessionLocal
        from app.db.models import TradeHistory

        db = SessionLocal()
        try:
            trades = db.query(TradeHistory).filter(
                TradeHistory.status == "submitted",
                TradeHistory.pnl.isnot(None),
            ).all()

            if not trades:
                return {
                    "total_trades": 0,
                    "message": "No completed trades yet",
                }

            pnls = [t.pnl for t in trades if t.pnl is not None]
            pnl_pcts = [t.pnl_pct for t in trades if t.pnl_pct is not None]

            wins = [p for p in pnls if p > 0]
            losses = [p for p in pnls if p <= 0]

            total = len(pnls)
            win_rate = len(wins) / total * 100 if total > 0 else 0
            avg_win = sum(wins) / len(wins) if wins else 0
            avg_loss = sum(losses) / len(losses) if losses else 0
            total_pnl = sum(pnls)

            # Profit factor
            gross_profit = sum(wins) if wins else 0
            gross_loss = abs(sum(losses)) if losses else 0
            profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

            # Max drawdown (from PnL series)
            cumulative = 0
            peak = 0
            max_dd = 0
            for pnl in pnls:
                cumulative += pnl
                peak = max(peak, cumulative)
                dd = peak - cumulative
                max_dd = max(max_dd, dd)

            # Simple Sharpe (annualized, assuming daily trades)
            import numpy as np
            if len(pnl_pcts) > 1:
                returns = np.array(pnl_pcts) / 100
                sharpe = (np.mean(returns) / np.std(returns)) * np.sqrt(252) if np.std(returns) > 0 else 0
            else:
                sharpe = 0

            buys = len([t for t in trades if t.side == "buy"])
            sells = len([t for t in trades if t.side == "sell"])

            return {
                "total_trades": total,
                "buys": buys,
                "sells": sells,
                "win_rate": round(win_rate, 1),
                "avg_win": round(avg_win, 2),
                "avg_loss": round(avg_loss, 2),
                "total_pnl": round(total_pnl, 2),
                "profit_factor": round(profit_factor, 2),
                "max_drawdown": round(max_dd, 2),
                "sharpe_ratio": round(sharpe, 2),
                "best_trade": round(max(pnls), 2) if pnls else 0,
                "worst_trade": round(min(pnls), 2) if pnls else 0,
            }
        finally:
            db.close()
    except Exception as e:
        logger.error(f"Performance report error: {e}")
        return {"error": str(e)}
