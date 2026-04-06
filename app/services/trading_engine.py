"""Automated paper trading engine — multi-strategy.

Supports 3 concurrent strategies on separate Alpaca paper accounts:
  A: Momentum Alpha    — concentrated trend-following
  B: Mean Reversion    — diversified dip-buying
  C: AI Ensemble       — pure ML decisions

Each strategy uses its own Alpaca credentials, risk limits, and
stop-loss method (trailing vs. static).

NO real money. Paper accounts only.
"""

import logging
import time
import threading
from datetime import datetime
from typing import Optional

import httpx

from app.config import settings, STRATEGY_CONFIGS, StrategyConfig
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
# Alpaca order execution (paper only) — multi-account aware
# ---------------------------------------------------------------------------

# Per-strategy trade locks — serialize orders within each strategy
_trade_locks: dict[str, threading.Lock] = {}
_global_trade_lock = threading.Lock()  # fallback for legacy calls


def _get_trade_lock(strategy_id: str = None) -> threading.Lock:
    """Get or create a lock for the given strategy."""
    if not strategy_id:
        return _global_trade_lock
    if strategy_id not in _trade_locks:
        _trade_locks[strategy_id] = threading.Lock()
    return _trade_locks[strategy_id]


def _get_headers(strategy_id: str = None) -> dict:
    """Get Alpaca API headers for a specific strategy or default."""
    if strategy_id:
        cfg = STRATEGY_CONFIGS.get(strategy_id)
        if cfg and cfg.alpaca_api_key:
            return {
                "APCA-API-KEY-ID": cfg.alpaca_api_key,
                "APCA-API-SECRET-KEY": cfg.alpaca_secret_key,
            }
    return {
        "APCA-API-KEY-ID": settings.ALPACA_API_KEY,
        "APCA-API-SECRET-KEY": settings.ALPACA_SECRET_KEY,
    }


def _get_base_url() -> str:
    url = settings.ALPACA_BASE_URL
    if "data.alpaca" in url:
        url = url.replace("data.alpaca", "paper-api.alpaca")
    return url.rstrip("/").removesuffix("/v2")


def _submit_order(order_payload: dict, strategy_id: str = None) -> Optional[dict]:
    """Submit an order to Alpaca Trading API."""
    headers = _get_headers(strategy_id)
    if not headers.get("APCA-API-KEY-ID"):
        sid = f" [{strategy_id}]" if strategy_id else ""
        logger.error(f"Trading engine{sid}: No Alpaca API key configured")
        return None

    base = _get_base_url()
    url = f"{base}/v2/orders"
    sid = f"[{strategy_id}] " if strategy_id else ""

    try:
        with httpx.Client(timeout=15) as client:
            resp = client.post(url, headers=headers, json=order_payload)
            if resp.status_code in (200, 201):
                order = resp.json()
                logger.info(f"{sid}Order submitted: {order.get('id')} - {order_payload.get('symbol')} "
                           f"{order_payload.get('side')} {order_payload.get('qty')} shares")
                return order
            else:
                logger.error(f"{sid}Order rejected ({resp.status_code}): {resp.text[:300]}")
                return None
    except Exception as e:
        logger.error(f"{sid}Order submission error: {e}")
        return None


def _cancel_order(order_id: str, strategy_id: str = None) -> bool:
    """Cancel a pending order."""
    base = _get_base_url()
    url = f"{base}/v2/orders/{order_id}"
    try:
        with httpx.Client(timeout=10) as client:
            resp = client.delete(url, headers=_get_headers(strategy_id))
            return resp.status_code in (200, 204)
    except Exception as e:
        logger.error(f"Cancel order error: {e}")
        return False


def _close_position(symbol: str, strategy_id: str = None) -> Optional[dict]:
    """Close an entire position for a symbol."""
    base = _get_base_url()
    url = f"{base}/v2/positions/{symbol}"
    sid = f"[{strategy_id}] " if strategy_id else ""
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.delete(url, headers=_get_headers(strategy_id))
            if resp.status_code == 200:
                data = resp.json()
                logger.info(f"{sid}Position closed: {symbol}")
                return data
            else:
                logger.error(f"{sid}Close position failed ({resp.status_code}): {resp.text[:200]}")
                return None
    except Exception as e:
        logger.error(f"{sid}Close position error for {symbol}: {e}")
        return None


# ---------------------------------------------------------------------------
# Strategy name helper
# ---------------------------------------------------------------------------

def _strategy_label(strategy_id: str = None) -> str:
    """Get a human-readable label for Telegram messages."""
    if strategy_id:
        cfg = STRATEGY_CONFIGS.get(strategy_id)
        if cfg:
            return f"[{strategy_id}] {cfg.name}"
    return ""


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
    strategy_id: str = None,
) -> dict:
    """Execute a BUY trade with full risk checks.

    Strategy: Bracket order (entry + stop-loss + take-profit)
    This is a swing trade — we hold overnight to avoid PDT.

    Returns dict with trade result or rejection reason.
    """
    lock = _get_trade_lock(strategy_id)
    label = _strategy_label(strategy_id)

    with lock:
        result = {
            "symbol": symbol,
            "action": "BUY",
            "executed": False,
            "timestamp": datetime.utcnow().isoformat(),
            "strategy_id": strategy_id,
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
                logger.warning(f"{label} Trade blocked for {symbol}: {halal_reason}")
                _notify_trade(result)
                return result
        except ImportError:
            logger.warning(f"Halal verification unavailable for {symbol} — blocking trade")
            result["reason"] = "Halal verification unavailable — trade blocked for safety"
            return result

        # Get live account + positions for THIS strategy's account
        account = alpaca_get_account(strategy_id=strategy_id)
        if not account:
            result["reason"] = "Cannot fetch account info"
            return result

        positions = alpaca_get_positions(strategy_id=strategy_id)

        # Run all risk checks with strategy-specific limits
        eligibility = check_trade_eligibility(
            symbol=symbol,
            side="buy",
            price=price,
            stop_loss=stop_loss,
            account=account,
            open_positions=positions,
            strategy_id=strategy_id,
        )

        if not eligibility["eligible"]:
            result["reason"] = eligibility["reason"]
            logger.info(f"{label} Trade rejected for {symbol}: {eligibility['reason']}")
            _notify_trade(result)
            return result

        sizing = eligibility["sizing"]
        qty = sizing["qty"]

        # Determine stop-loss method from strategy config
        cfg = STRATEGY_CONFIGS.get(strategy_id) if strategy_id else None
        use_trailing = cfg.trailing_stop_enabled if cfg else settings.TRAILING_STOP_ENABLED
        trail_pct = cfg.trailing_stop_pct if cfg else settings.TRAILING_STOP_PCT

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

        if use_trailing and trail_pct > 0:
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

        order = _submit_order(order_payload, strategy_id=strategy_id)
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
            f"{label} BUY executed: {symbol} x{qty} @ ~${price:.2f} | "
            f"SL=${stop_loss:.2f} TP=${take_profit:.2f} | "
            f"Risk: ${sizing['risk_amount']:.2f} ({sizing['risk_pct']:.1f}%)"
        )

        return result


def execute_sell(symbol: str, price: float, confidence: float, strategy_id: str = None) -> dict:
    """Close a position on STRONG SELL signal.

    Only closes if we're holding the symbol.
    """
    lock = _get_trade_lock(strategy_id)
    label = _strategy_label(strategy_id)

    with lock:
        result = {
            "symbol": symbol,
            "action": "SELL",
            "executed": False,
            "timestamp": datetime.utcnow().isoformat(),
            "strategy_id": strategy_id,
        }

        if not settings.AUTO_TRADE_ENABLED:
            result["reason"] = "Auto-trading is disabled"
            return result

        # Check if we hold this position IN THIS STRATEGY'S ACCOUNT
        positions = alpaca_get_positions(strategy_id=strategy_id)
        held = [p for p in positions if p["symbol"] == symbol]

        if not held:
            result["reason"] = f"Not holding {symbol} — nothing to sell"
            return result

        position = held[0]
        close_result = _close_position(symbol, strategy_id=strategy_id)

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
            f"{label} SELL executed: {symbol} x{position['qty']} | "
            f"P&L: ${position['unrealized_pl']:.2f} ({position['unrealized_plpc']:.1f}%)"
        )

        return result


# ---------------------------------------------------------------------------
# Auto-trade hook — called from strategy consensus functions
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
    strategy_id: str = None,
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
        label = _strategy_label(strategy_id)
        logger.info(f"{label} Signal {verdict} for {symbol} ignored — market is closed")
        return None

    # Get min confidence from strategy config
    cfg = STRATEGY_CONFIGS.get(strategy_id) if strategy_id else None
    min_confidence = cfg.min_confidence if cfg else settings.MIN_TRADE_CONFIDENCE
    label = _strategy_label(strategy_id)

    buy_verdicts = ("STRONG BUY", "BUY", "WEAK BUY")
    sell_verdicts = ("STRONG SELL", "SELL", "WEAK SELL")

    if verdict in buy_verdicts and confidence >= min_confidence:
        logger.info(f"{label} Auto-trade trigger: {verdict} {symbol} (confidence={confidence}%)")
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
                "strategy_id": strategy_id,
            },
            strategy_id=strategy_id,
        )

    elif verdict in sell_verdicts and confidence >= min_confidence:
        logger.info(f"{label} Auto-trade trigger: STRONG SELL {symbol} (confidence={confidence}%)")
        return execute_sell(
            symbol=symbol,
            price=price,
            confidence=confidence,
            strategy_id=strategy_id,
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
                strategy_id=trade_result.get("strategy_id"),
            )
            db.add(trade)
            db.commit()
        finally:
            db.close()
    except Exception as e:
        logger.error(f"Failed to record trade: {e}")


# ---------------------------------------------------------------------------
# Telegram notifications — strategy-aware
# ---------------------------------------------------------------------------

def _notify_trade(trade_result: dict):
    """Send trade notification via Telegram."""
    try:
        symbol = trade_result["symbol"]
        action = trade_result["action"]
        executed = trade_result["executed"]
        strategy_id = trade_result.get("strategy_id")
        label = _strategy_label(strategy_id)

        if executed:
            if action == "BUY":
                # Determine stop type from strategy config
                cfg = STRATEGY_CONFIGS.get(strategy_id) if strategy_id else None
                use_trailing = cfg.trailing_stop_enabled if cfg else settings.TRAILING_STOP_ENABLED
                trail_pct = cfg.trailing_stop_pct if cfg else settings.TRAILING_STOP_PCT

                sl_type = "Trailing Stop" if use_trailing else "Stop Loss"
                trail_info = f" (trail {trail_pct}%)" if use_trailing else ""
                msg = (
                    f"AUTO TRADE - BUY {label}\n\n"
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
                    f"AUTO TRADE - SELL {label}\n\n"
                    f"Symbol: {symbol}\n"
                    f"Qty: {trade_result.get('qty', 0)} shares\n"
                    f"Entry: ${trade_result.get('entry_price', 0):.2f}\n"
                    f"Exit: ${trade_result.get('exit_price', 0):.2f}\n"
                    f"P&L: ${trade_result.get('unrealized_pl', 0):.2f} ({trade_result.get('unrealized_plpc', 0):.1f}%)\n"
                    f"Confidence: {trade_result.get('confidence', 0)}%"
                )
        else:
            msg = (
                f"TRADE REJECTED - {action} {label}\n\n"
                f"Symbol: {symbol}\n"
                f"Reason: {trade_result.get('reason', 'Unknown')}"
            )

        tg_send(msg)
    except Exception as e:
        logger.debug(f"Trade notification failed: {e}")


# ---------------------------------------------------------------------------
# Performance report — strategy-aware
# ---------------------------------------------------------------------------

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


def get_performance_report(strategy_id: str = None) -> dict:
    """Generate trading performance report, optionally for a specific strategy."""
    try:
        from app.db.database import SessionLocal
        from app.db.models import TradeHistory

        db = SessionLocal()
        try:
            query = db.query(TradeHistory).filter(
                TradeHistory.status == "submitted",
                TradeHistory.pnl.isnot(None),
            )
            if strategy_id:
                query = query.filter(TradeHistory.strategy_id == strategy_id)

            trades = query.all()

            if not trades:
                label = _strategy_label(strategy_id)
                return {
                    "strategy": label or "All",
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

            label = _strategy_label(strategy_id)
            return {
                "strategy": label or "All",
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
