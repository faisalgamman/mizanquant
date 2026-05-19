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
import hashlib
import json
import math
import time
import threading
from datetime import datetime, timezone
from typing import Optional

import httpx

from app.config import settings, STRATEGY_CONFIGS, StrategyConfig
from app.core.config import app_cfg
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
    get_orders as alpaca_get_orders,
)
from app.services.telegram_alert import send_message as tg_send
from app.services.trade_logger import log_trade_event
from app.services.validators import validate_entry, make_client_order_id

logger = logging.getLogger("screener")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _finite_round(value: float | None, digits: int = 2) -> float | None:
    """Round floats for JSON responses, dropping NaN/inf to avoid 500s."""
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric):
        return None
    return round(numeric, digits)


_PENDING_ORDER_STATUSES = (
    "submitted",
    "armed",
    "new",
    "accepted",
    "pending_new",
    "partially_filled",
)

# ---------------------------------------------------------------------------
# Alpaca order execution (paper only) — multi-account aware
# ---------------------------------------------------------------------------

# Per-strategy trade locks — serialize orders within each strategy.
# `_locks_lock` protects creation of new per-strategy locks (race fix, M1 #12).
_trade_locks: dict[str, threading.Lock] = {}
_locks_lock = threading.Lock()
_global_trade_lock = threading.Lock()  # fallback for legacy calls


def _get_trade_lock(strategy_id: str = None) -> threading.Lock:
    """Get or create a lock for the given strategy. Thread-safe."""
    if not strategy_id:
        return _global_trade_lock
    # Fast path without the global lock
    lock = _trade_locks.get(strategy_id)
    if lock is not None:
        return lock
    # Slow path — double-checked locking under the meta-lock
    with _locks_lock:
        lock = _trade_locks.get(strategy_id)
        if lock is None:
            lock = threading.Lock()
            _trade_locks[strategy_id] = lock
        return lock


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

    # Idempotency: if the caller didn't pre-set client_order_id (legacy call
    # sites), synthesize one here so retries can't duplicate orders.
    if not order_payload.get("client_order_id"):
        order_payload["client_order_id"] = make_client_order_id(
            strategy_id,
            order_payload.get("symbol", ""),
            order_payload.get("side", "buy"),
        )
    coid = order_payload["client_order_id"]

    try:
        with httpx.Client(timeout=15) as client:
            resp = client.post(url, headers=headers, json=order_payload)
            if resp.status_code in (200, 201):
                order = resp.json()
                logger.info(
                    f"{sid}Order submitted: broker_id={order.get('id')} coid={coid} "
                    f"{order_payload.get('symbol')} {order_payload.get('side')} "
                    f"{order_payload.get('qty')} shares"
                )
                return order
            # 422 + "client_order_id already exists" means the order was
            # already accepted on a previous attempt — look it up and return it.
            duplicate_text = resp.text.lower()
            if (
                resp.status_code == 422
                and "already exists" in duplicate_text
                and "client_order_id" in duplicate_text
            ):
                logger.warning(
                    f"{sid}Order with coid={coid} already exists on broker — "
                    f"fetching existing order instead of re-submitting."
                )
                existing = _get_order_by_client_id(coid, strategy_id)
                if existing:
                    return existing
            logger.error(
                f"{sid}Order rejected ({resp.status_code}) coid={coid}: {resp.text[:300]}"
            )
            return None
    except httpx.TimeoutException as e:
        logger.error(
            f"{sid}Order submission TIMEOUT coid={coid}: {e}. "
            f"Broker state unknown — reconciliation job will resolve on next scan.",
            exc_info=True,
        )
        return None
    except httpx.HTTPError as e:
        logger.error(f"{sid}Order submission HTTP error coid={coid}: {e}", exc_info=True)
        return None
    except Exception as e:
        logger.error(f"{sid}Order submission unexpected error coid={coid}: {e}", exc_info=True)
        return None


def _stable_client_order_id(
    strategy_id: str | None,
    symbol: str,
    side: str,
    *,
    price: float | None = None,
    stop_loss: float | None = None,
    take_profit: float | None = None,
    confidence: float | None = None,
    signal_details: dict | None = None,
) -> str:
    """Build a deterministic client_order_id for a single trade intent.

    This keeps retries after a crash pointed at the same broker order
    when the signal payload is replayed with identical inputs.
    """
    payload = dict(signal_details or {})
    existing = str(payload.get("client_order_id") or "").strip()
    if existing:
        return existing[: app_cfg.execution.client_order_id_max_len]

    seed = {
        "strategy_id": (strategy_id or "").upper(),
        "symbol": symbol.upper(),
        "side": side.lower(),
        "price": round(float(price or 0.0), 4),
        "stop_loss": round(float(stop_loss or 0.0), 4),
        "take_profit": round(float(take_profit or 0.0), 4),
        "confidence": round(float(confidence or 0.0), 2),
        "verdict": str(payload.get("verdict") or ""),
        "votes_buy": int(payload.get("votes_buy") or 0),
        "votes_sell": int(payload.get("votes_sell") or 0),
        "votes_hold": int(payload.get("votes_hold") or 0),
        "strategy_hint": str(payload.get("strategy_id") or strategy_id or ""),
        "session_date": str(payload.get("session_date") or _utc_now().date().isoformat()),
    }
    digest = hashlib.sha1(
        json.dumps(seed, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:12]
    prefix = f"{(strategy_id or 'X').upper()[:3]}-{symbol.upper()[:6]}-{side.upper()[:1]}-"
    return (prefix + digest)[: app_cfg.execution.client_order_id_max_len]


def _get_order_by_client_id(client_order_id: str, strategy_id: str = None) -> Optional[dict]:
    """Look up an order by its client_order_id. Used for idempotent retries."""
    base = _get_base_url()
    url = f"{base}/v2/orders:by_client_order_id"
    try:
        with httpx.Client(timeout=10) as client:
            resp = client.get(
                url,
                headers=_get_headers(strategy_id),
                params={"client_order_id": client_order_id},
            )
            if resp.status_code == 200:
                return resp.json()
            return None
    except Exception as e:
        logger.warning(f"Could not look up order by coid={client_order_id}: {e}")
        return None


def _get_order(order_id: str, strategy_id: str = None) -> Optional[dict]:
    """Fetch a broker order by id with nested legs when available."""
    base = _get_base_url()
    url = f"{base}/v2/orders/{order_id}"
    try:
        with httpx.Client(timeout=10) as client:
            resp = client.get(
                url,
                headers=_get_headers(strategy_id),
                params={"nested": "true"},
            )
            if resp.status_code == 200:
                return resp.json()
            return None
    except Exception as e:
        logger.warning(f"Could not fetch order {order_id}: {e}")
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


def _arm_bracket(order: dict, strategy_id: str = None) -> bool:
    """Verify the broker attached both bracket legs; cancel parent if not."""
    parent_id = order.get("id")
    if not parent_id:
        return False
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        fresh = _get_order(parent_id, strategy_id)
        legs = (fresh or {}).get("legs") or []
        has_stop = any(leg.get("type") == "stop" for leg in legs)
        has_limit = any(leg.get("type") == "limit" for leg in legs)
        if has_stop and has_limit:
            return True
        time.sleep(0.5)
    _cancel_order(parent_id, strategy_id)
    tg_send(
        f"BRACKET LEG MISSING\n\n"
        f"Parent order {parent_id} canceled because stop/limit legs never attached."
    )
    return False


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
        signal_payload = dict(signal_details or {})
        result = {
            "symbol": symbol,
            "action": "BUY",
            "executed": False,
            "timestamp": _utc_now().isoformat(),
            "strategy_id": strategy_id,
        }

        # Auto-trading enabled?
        if not settings.AUTO_TRADE_ENABLED:
            result["reason"] = "Auto-trading is disabled (AUTO_TRADE_ENABLED=false)"
            return result

        # ── Emergency Kill Switch ──
        if settings.KILL_SWITCH:
            result["reason"] = "BLOCKED: KILL_SWITCH=active — all trading halted"
            logger.warning(f"{label} KILL_SWITCH blocked BUY {symbol}")
            _notify_trade(result)
            return result

        # Input validation — fail-fast before any broker/DB contact (M1 #9)
        validation = validate_entry(
            symbol=symbol,
            price=price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            side="buy",
        )
        if not validation.ok:
            result["reason"] = f"VALIDATION[{validation.code}]: {validation.reason}"
            logger.warning(f"{label} Trade input invalid for {symbol}: {result['reason']}")
            _notify_trade(result)
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

        # Portfolio Stop drawdown check — reduce sizing or block
        from app.services.portfolio_stop import check_drawdown, reduce_sizing
        dd_status = check_drawdown()
        if dd_status["tier"] in ("halt", "emergency"):
            result["reason"] = (
                f"Portfolio Stop {dd_status['tier'].upper()}: "
                f"{dd_status['message']}"
            )
            logger.warning(f"{label} Trade blocked for {symbol}: {result['reason']}")
            _notify_trade(result)
            return result
        if dd_status["tier"] == "warning":
            orig_qty = qty
            qty = reduce_sizing(qty)
            if qty != orig_qty:
                logger.info(
                    f"{label} Portfolio Stop WARNING: reduced {symbol} "
                    f"qty from {orig_qty} to {qty} (50% sizing)"
                )
                sizing["qty"] = qty
                sizing["note"] = "Portfolio stop warning: sizing halved"

        # Determine stop-loss method from strategy config
        cfg = STRATEGY_CONFIGS.get(strategy_id) if strategy_id else None
        use_trailing = cfg.trailing_stop_enabled if cfg else settings.TRAILING_STOP_ENABLED
        trail_pct = cfg.trailing_stop_pct if cfg else app_cfg.execution.trail_percent_default
        trail_pct = min(trail_pct, app_cfg.execution.trail_percent_max)

        order_payload = {
            "symbol": symbol,
            "qty": str(qty),
            "side": "buy",
            "type": "market",
            # GTC, not day — bracket child legs (SL/TP) inherit the parent's
            # time-in-force, and `day` causes Alpaca to expire any unfilled
            # SL/TP at session close. The position then survives into the
            # next session with no automatic exit, which is the
            # "buys but never sells" symptom on paper trading. With GTC
            # the SL/TP remain armed until they trigger or are canceled.
            "time_in_force": "gtc",
            "order_class": "bracket",
            "client_order_id": _stable_client_order_id(
                strategy_id,
                symbol,
                "buy",
                price=price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                confidence=confidence,
                signal_details=signal_payload,
            ),
            "take_profit": {
                "limit_price": str(round(take_profit, 2)),
            },
        }
        signal_payload.setdefault("client_order_id", order_payload["client_order_id"])

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

        # Build result inside the lock, record trade
        result["executed"] = True
        result["order_id"] = order.get("id", "")
        result["client_order_id"] = order.get("client_order_id", order_payload.get("client_order_id", ""))
        result["qty"] = qty
        result["entry_price"] = price
        result["stop_loss"] = stop_loss
        result["take_profit"] = take_profit
        result["position_value"] = sizing["position_value"]
        result["risk_amount"] = sizing["risk_amount"]
        result["risk_pct"] = sizing["risk_pct"]
        result["confidence"] = confidence
        result["reason"] = "Order submitted successfully"
        result["status"] = "armed"
        result["armed_at"] = _utc_now()
        result["filled_qty"] = 0
        result["filled_avg_price"] = None

        _record_trade(result, signal_payload)

    # ═══════════════ LOCK RELEASED ═══════════════

    # Verify bracket legs outside the lock — polling may take up to 10s
    # and should not block other trades for this strategy.
    if app_cfg.execution.bracket_required and order_payload.get("order_class") == "bracket":
        if not _arm_bracket(order, strategy_id=strategy_id):
            result["status"] = "canceled_leg_missing"
            result["reason"] = "Bracket legs failed to attach; parent order canceled"
            _notify_trade(result)
            log_trade_event(
                "canceled_leg_missing",
                coid=result.get("client_order_id", ""),
                strategy=strategy_id,
                symbol=symbol,
                qty=qty,
                price=price,
                sl=stop_loss,
                tp=take_profit,
                regime=sizing.get("regime"),
                confidence=confidence,
                guards_passed=len(eligibility.get("guards", [])),
            )
            return result

    # Notify + log outside lock
    _notify_trade(result)
    log_trade_event(
        "submitted",
        coid=result.get("client_order_id", ""),
        strategy=strategy_id,
        symbol=symbol,
        qty=qty,
        price=price,
        sl=stop_loss,
        tp=take_profit,
        regime=sizing.get("regime"),
        confidence=confidence,
        guards_passed=len(eligibility.get("guards", [])),
    )

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
            "timestamp": _utc_now().isoformat(),
            "strategy_id": strategy_id,
        }

        if not settings.AUTO_TRADE_ENABLED:
            result["reason"] = "Auto-trading is disabled"
            return result

        # ── Emergency Kill Switch ──
        if settings.KILL_SWITCH:
            result["reason"] = "BLOCKED: KILL_SWITCH=active — all trading halted"
            logger.warning(f"{label} KILL_SWITCH blocked SELL {symbol}")
            _notify_trade(result)
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
        log_trade_event(
            "closed",
            coid=result.get("client_order_id", ""),
            strategy=strategy_id,
            symbol=symbol,
            qty=position["qty"],
            price=price,
            confidence=confidence,
        )

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

    Acts on BUY/WEAK BUY/SELL/WEAK SELL with confidence >= threshold.
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
    min_confidence = cfg.min_confidence if cfg else app_cfg.thresholds.min_buy_confidence
    label = _strategy_label(strategy_id)

    buy_verdicts = ("STRONG BUY", "BUY", "WEAK BUY")
    sell_verdicts = ("STRONG SELL", "SELL", "WEAK SELL")

    if verdict in buy_verdicts and confidence >= min_confidence:
        logger.info(f"{label} Auto-trade trigger: {verdict} {symbol} (confidence={confidence}%, min={min_confidence}%)")
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
        logger.info(f"{label} Auto-trade trigger: {verdict} {symbol} (confidence={confidence}%, min={min_confidence}%)")
        return execute_sell(
            symbol=symbol,
            price=price,
            confidence=confidence,
            strategy_id=strategy_id,
        )

    else:
        # Log WHY signal was rejected
        if verdict in buy_verdicts or verdict in sell_verdicts:
            logger.info(
                f"{label} Signal REJECTED: {verdict} {symbol} — "
                f"confidence {confidence}% < min {min_confidence}%"
            )
        else:
            logger.info(
                f"{label} Signal SKIPPED: {verdict} {symbol} — "
                f"not a buy/sell verdict (confidence={confidence}%)"
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

        signal_payload = dict(signal_details or {})
        client_order_id = trade_result.get("client_order_id")
        if client_order_id and "client_order_id" not in signal_payload:
            signal_payload["client_order_id"] = client_order_id

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
                client_order_id=client_order_id or "",
                status=trade_result.get("status") or ("submitted" if trade_result["executed"] else "rejected"),
                signal_details=signal_payload,
                pnl=trade_result.get("unrealized_pl"),
                pnl_pct=trade_result.get("unrealized_plpc"),
                strategy_id=trade_result.get("strategy_id"),
                filled_qty=int(trade_result.get("filled_qty")) if trade_result.get("filled_qty") is not None else None,
                filled_avg_price=trade_result.get("filled_avg_price"),
                armed_at=trade_result.get("armed_at"),
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
        logger.warning("Trade notification failed: %s", e)


# ---------------------------------------------------------------------------
# Performance report — strategy-aware
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Startup reconciliation (M1 #3) — align DB with broker reality
# ---------------------------------------------------------------------------

def reconcile_positions(strategy_id: str = None) -> dict:
    """Compare broker positions/orders against the local DB and reconcile.

    Detects:
      * Broker-side positions with no matching DB row  -> writes audit row.
      * DB trades marked 'submitted' whose broker order is filled/canceled
        -> updates status + fill qty + avg fill price.
      * Broker-side bracket parents whose children (SL/TP) are missing
        -> logs a CRITICAL alert so an operator can re-attach manually.

    This is a READ + update-DB routine — it will NEVER place new broker orders.
    Safe to run at boot, after restarts, and on a recurring schedule.
    """
    from app.db.database import SessionLocal
    from app.db.models import TradeHistory

    label = _strategy_label(strategy_id) or "(default)"
    summary = {
        "strategy": label,
        "broker_positions": 0,
        "broker_orders": 0,
        "db_submitted": 0,
        "updated": 0,
        "orphan_positions": [],
        "orphan_brackets": [],
        "errors": [],
    }

    try:
        positions = alpaca_get_positions(strategy_id=strategy_id) or []
        orders = alpaca_get_orders(status="all", limit=200, strategy_id=strategy_id) or []
    except Exception as e:
        logger.error(f"Reconciliation: broker fetch failed for {label}: {e}", exc_info=True)
        summary["errors"].append(f"broker_fetch: {e}")
        return summary

    summary["broker_positions"] = len(positions)
    summary["broker_orders"] = len(orders)
    broker_by_coid = {o.get("client_order_id"): o for o in orders if o.get("client_order_id")}
    broker_by_id = {o.get("id"): o for o in orders if o.get("id")}

    try:
        db = SessionLocal()
    except Exception as e:
        logger.error(f"Reconciliation: DB unavailable: {e}")
        summary["errors"].append(f"db_open: {e}")
        return summary

    try:
        q = db.query(TradeHistory).filter(TradeHistory.status.in_(_PENDING_ORDER_STATUSES))
        if strategy_id:
            q = q.filter(TradeHistory.strategy_id == strategy_id)
        pending = q.all()
        summary["db_submitted"] = len(pending)
        summary["db_pending_statuses"] = list(_PENDING_ORDER_STATUSES)

        tracked_q = db.query(TradeHistory).filter(
            TradeHistory.status.notin_(("rejected", "canceled", "expired"))
        )
        if strategy_id:
            tracked_q = tracked_q.filter(TradeHistory.strategy_id == strategy_id)
        tracked_symbols = {t.symbol for t in tracked_q.all()}

        for trade in pending:
            trade_coid = str(trade.client_order_id or "")
            if isinstance(trade.signal_details, dict):
                trade_coid = trade_coid or str(trade.signal_details.get("client_order_id") or "")
            broker = broker_by_id.get(trade.order_id) or broker_by_coid.get(trade_coid)
            if broker is None:
                continue
            status = broker.get("status")
            if status in ("filled", "partially_filled"):
                try:
                    trade.status = status
                    filled_qty = int(float(broker.get("filled_qty") or 0))
                    filled_avg = float(broker.get("filled_avg_price") or 0) or None
                    if filled_qty and filled_qty != trade.qty:
                        logger.warning(
                            f"Reconcile {label}: {trade.symbol} partial fill "
                            f"{filled_qty}/{trade.qty} @ {filled_avg}"
                        )
                        trade.qty = filled_qty
                    trade.filled_qty = filled_qty
                    if filled_avg:
                        trade.entry_price = filled_avg
                        trade.filled_avg_price = filled_avg
                    summary["updated"] += 1
                except Exception as e:
                    summary["errors"].append(f"update {trade.order_id}: {e}")
            elif status in ("canceled", "rejected", "expired"):
                trade.status = status
                summary["updated"] += 1

        # Orphan detection — broker has position we never recorded
        for pos in positions:
            if pos["symbol"] not in tracked_symbols:
                summary["orphan_positions"].append(pos["symbol"])

        # Bracket leg check (simple: look for parent with missing legs)
        for o in orders:
            if o.get("order_class") == "bracket" and o.get("status") in ("filled", "new", "accepted"):
                legs = o.get("legs") or []
                has_stop = any(l.get("type") == "stop" for l in legs)
                has_limit = any(l.get("type") == "limit" for l in legs)
                if not (has_stop and has_limit):
                    summary["orphan_brackets"].append(o.get("symbol"))

        summary["orphan_positions"] = sorted(set(summary["orphan_positions"]))
        summary["orphan_brackets"] = sorted(set(summary["orphan_brackets"]))

        db.commit()
    except Exception as e:
        logger.error(f"Reconciliation DB update failed: {e}", exc_info=True)
        summary["errors"].append(f"db_commit: {e}")
        db.rollback()
    finally:
        db.close()

    if summary["orphan_positions"] or summary["orphan_brackets"]:
        try:
            tg_send(
                "RECONCILIATION ALERT " + label + "\n"
                f"orphan_positions={summary['orphan_positions']}\n"
                f"orphan_brackets={summary['orphan_brackets']}\n"
                f"pending_in_db={summary['db_submitted']} updated={summary['updated']}"
            )
        except Exception:
            logger.exception("Reconciliation Telegram alert failed")

    logger.info(
        f"Reconcile {label}: positions={summary['broker_positions']} "
        f"orders={summary['broker_orders']} db_pending={summary['db_submitted']} "
        f"updated={summary['updated']} orphans={len(summary['orphan_positions'])}"
    )
    return summary


def reconcile_all_strategies() -> dict[str, dict]:
    """Run reconciliation for every configured strategy + default account."""
    results: dict[str, dict] = {}
    for sid in list(STRATEGY_CONFIGS.keys()):
        results[sid] = reconcile_positions(strategy_id=sid)
    # Legacy / default account (if configured without a strategy id)
    if settings.ALPACA_API_KEY:
        results["_default"] = reconcile_positions(strategy_id=None)
    return results


# ---------------------------------------------------------------------------
# Orphan position re-armer (positions whose bracket legs expired)
# ---------------------------------------------------------------------------

def rearm_orphan_position(
    symbol: str,
    strategy_id: str | None = None,
    fallback_stop_pct: float = 0.03,
    fallback_take_pct: float = 0.06,
) -> dict:
    """Submit standalone GTC stop-loss and take-profit orders for a
    position whose original bracket legs expired (the legacy bug where
    bracket parents were submitted with time_in_force=day).

    Looks up the original trade in DB to recover the intended stop and
    target; falls back to ±3%/+6% off the current entry price when the
    DB row is missing.

    Returns a dict summarising what was submitted. Idempotent: if the
    position already has an open stop and limit on the broker side,
    skips re-arming.
    """
    label = _strategy_label(strategy_id) or "(default)"
    result = {"symbol": symbol, "rearmed": False, "reason": "", "stop_loss": None, "take_profit": None}

    positions = alpaca_get_positions(strategy_id=strategy_id) or []
    pos = next((p for p in positions if p["symbol"] == symbol), None)
    if not pos:
        result["reason"] = "not holding symbol"
        return result

    qty = int(float(pos.get("qty") or 0))
    entry = float(pos.get("avg_entry_price") or 0)
    if qty <= 0 or entry <= 0:
        result["reason"] = "invalid qty/entry"
        return result

    # Skip if a stop and limit are already open for this symbol.
    open_orders = alpaca_get_orders(status="open", limit=200, strategy_id=strategy_id) or []
    has_stop = any(
        (o.get("symbol") == symbol and o.get("side") == "sell" and "stop" in (o.get("type") or ""))
        for o in open_orders
    )
    has_limit = any(
        (o.get("symbol") == symbol and o.get("side") == "sell" and o.get("type") == "limit")
        for o in open_orders
    )
    if has_stop and has_limit:
        result["reason"] = "already armed"
        return result

    # Recover the intended SL/TP from DB (latest trade row for this symbol).
    sl, tp = None, None
    try:
        from app.db.database import SessionLocal
        from app.db.models import TradeHistory

        db = SessionLocal()
        try:
            q = db.query(TradeHistory).filter(TradeHistory.symbol == symbol)
            if strategy_id:
                q = q.filter(TradeHistory.strategy_id == strategy_id)
            tr = q.order_by(TradeHistory.created_at.desc()).first()
            if tr:
                sl = float(tr.stop_loss) if tr.stop_loss else None
                tp = float(tr.take_profit) if tr.take_profit else None
        finally:
            db.close()
    except Exception as exc:
        logger.warning(f"{label} rearm: DB lookup failed for {symbol}: {exc}")

    if not sl or sl >= entry:
        sl = round(entry * (1.0 - fallback_stop_pct), 2)
    if not tp or tp <= entry:
        tp = round(entry * (1.0 + fallback_take_pct), 2)

    submitted: list[str] = []

    if not has_stop:
        stop_payload = {
            "symbol": symbol,
            "qty": str(qty),
            "side": "sell",
            "type": "stop",
            "time_in_force": "gtc",
            "stop_price": str(sl),
        }
        if _submit_order(stop_payload, strategy_id=strategy_id):
            submitted.append(f"STOP@{sl}")

    if not has_limit:
        tp_payload = {
            "symbol": symbol,
            "qty": str(qty),
            "side": "sell",
            "type": "limit",
            "time_in_force": "gtc",
            "limit_price": str(tp),
        }
        if _submit_order(tp_payload, strategy_id=strategy_id):
            submitted.append(f"TP@{tp}")

    result["rearmed"] = bool(submitted)
    result["reason"] = ", ".join(submitted) if submitted else "no submission"
    result["stop_loss"] = sl
    result["take_profit"] = tp
    logger.info(f"{label} rearm {symbol}: {result['reason']}")
    return result


def rearm_all_orphans(strategy_id: str | None = None) -> list[dict]:
    """Walk every position in the strategy's account and call
    rearm_orphan_position for any that lacks a stop+limit pair.
    Returns a list of result dicts (one per position acted on)."""
    out: list[dict] = []
    positions = alpaca_get_positions(strategy_id=strategy_id) or []
    open_orders = alpaca_get_orders(status="open", limit=200, strategy_id=strategy_id) or []

    armed_symbols: set[str] = set()
    by_sym: dict[str, dict] = {}
    for o in open_orders:
        if o.get("side") != "sell":
            continue
        sym = o.get("symbol")
        if not sym:
            continue
        slot = by_sym.setdefault(sym, {"stop": False, "limit": False})
        if "stop" in (o.get("type") or ""):
            slot["stop"] = True
        if o.get("type") == "limit":
            slot["limit"] = True
    for sym, flags in by_sym.items():
        if flags["stop"] and flags["limit"]:
            armed_symbols.add(sym)

    for pos in positions:
        sym = pos["symbol"]
        if sym in armed_symbols:
            continue
        out.append(rearm_orphan_position(sym, strategy_id=strategy_id))
    return out


# ---------------------------------------------------------------------------
# Performance reporting
# ---------------------------------------------------------------------------

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
            profit_factor = gross_profit / gross_loss if gross_loss > 0 else None

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
                rf_daily = 0.043 / 252  # Current US T-bill ~4.3% annualized
                sharpe = ((np.mean(returns) - rf_daily) / np.std(returns)) * np.sqrt(252) if np.std(returns) > 0 else 0
            else:
                sharpe = 0

            buys = len([t for t in trades if t.side == "buy"])
            sells = len([t for t in trades if t.side == "sell"])

            label = _strategy_label(strategy_id)
            report = {
                "strategy": label or "All",
                "total_trades": total,
                "buys": buys,
                "sells": sells,
                "win_rate": round(win_rate, 1),
                "avg_win": round(avg_win, 2),
                "avg_loss": round(avg_loss, 2),
                "total_pnl": round(total_pnl, 2),
                "profit_factor": _finite_round(profit_factor, 2),
                "max_drawdown": _finite_round(max_dd, 2) or 0.0,
                "sharpe_ratio": _finite_round(sharpe, 2) or 0.0,
                "best_trade": round(max(pnls), 2) if pnls else 0,
                "worst_trade": round(min(pnls), 2) if pnls else 0,
            }
            if profit_factor is None and gross_profit > 0:
                report["profit_factor_note"] = "No losing trades yet"
            return report
        finally:
            db.close()
    except Exception as e:
        logger.error(f"Performance report error: {e}")
        return {"error": str(e)}
