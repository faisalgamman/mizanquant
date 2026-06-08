"""Interactive Brokers adapter.

Connects to a running IB Gateway (or TWS) over the standard TWS API
socket using `ib_insync`. The gateway runs as a separate Railway
service (`gway`) via the `ghcr.io/gnzsnz/ib-gateway:10.46.1g` Docker
image (IBC 3.23.0, Java 17) with IBC handling auto-login; this adapter
only speaks to the socket.

Translation layer:

The rest of the trading engine speaks Alpaca's REST shapes (dict
payloads with keys like `symbol`, `qty`, `side`, `type`,
`time_in_force`, `order_class`, `stop_loss`, `take_profit`). This
class translates those into ib_insync `Contract` + `Order` objects
on the way out, and translates IB's `Trade`/`Position`/`AccountValue`
objects back into Alpaca-compatible dicts on the way in. Callers
should not need to know which broker they are talking to.

Connection model:

A single `IB()` instance is shared per process. The first call
establishes the socket; subsequent calls reuse it. On disconnection
(network blip, gateway restart) the next call reconnects lazily.
Failure modes (gateway down, auth refused) return None / empty
collections — matching the alpaca_client pattern — so the engine
degrades gracefully rather than crashing.

Environment:
    IBKR_HOST       — gateway hostname (e.g. gway.railway.internal)
    IBKR_PORT       — 4004 for paper (socat), 4003 for live (socat),
                      4002 for paper (direct), 4001 for live (direct)
                      (default: 4002)
    IBKR_CLIENT_ID  — int per-strategy (default: 1)

Per-strategy isolation:
    IBKR_CLIENT_ID_<STRATEGY> overrides per strategy so A/B/C don't
    collide on the same gateway socket.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from typing import Optional

logger = logging.getLogger("screener")

# Hard override: IBKR is DISABLED until the gateway is healthy.
# Imported by market_data.py so both data-fetch and broker paths are blocked.
_OVERRIDE_IBKR_ENABLED = True

# Throttle for IBKR-down Telegram alerts (one per 30 min per host:port).
_last_ibkr_alert: dict[str, float] = {}
_IBKR_ALERT_THROTTLE_S = 1800

# Lazy import: ib_insync is a heavy dependency we only want to load
# when an IB adapter is actually instantiated.
_ib_insync = None
_IB = None  # type: ignore[assignment]
_Stock = None  # type: ignore[assignment]
_MarketOrder = None  # type: ignore[assignment]
_LimitOrder = None  # type: ignore[assignment]
_StopOrder = None  # type: ignore[assignment]
_Order = None  # type: ignore[assignment]


import concurrent.futures as _futures
import queue

# ── Dedicated single-worker thread for ALL ib_insync operations ──
# ib_insync must always run on the SAME thread where its event loop lives.
_ib_req_queue: queue.Queue[tuple] = queue.Queue()


def _ib_worker():
    """Dedicated thread that runs every ib_insync call sent via _run_ib()."""
    asyncio.set_event_loop(asyncio.new_event_loop())
    while True:
        fn, fut = _ib_req_queue.get()
        try:
            result = fn()
        except BaseException as exc:
            fut.set_exception(exc)
            continue
        fut.set_result(result)


_ib_worker_thread = threading.Thread(target=_ib_worker, daemon=True, name="ibkr-worker")
_ib_worker_thread.start()


def _run_ib(fn, timeout=30.0):
    """Run a callable on the dedicated ib_insync worker thread and return the result."""
    fut: _futures.Future = _futures.Future()
    _ib_req_queue.put((fn, fut))
    return fut.result(timeout=timeout)


def _call_ib(ib, method, *args, timeout=15.0, **kwargs):
    """Call *method* on the ib_insync worker thread so its event loop is not polluted."""
    fn = getattr(ib, method)
    return _run_ib(lambda: fn(*args, **kwargs), timeout=timeout)


def _load_ib_insync():
    """Import ib_insync on first use; cache the module references.

    ib_insync (via eventkit) calls ``asyncio.get_event_loop()`` at module
    level, so the import MUST happen on a thread that already has an event
    loop set.
    """
    global _ib_insync, _IB, _Stock, _MarketOrder, _LimitOrder, _StopOrder, _Order
    if _ib_insync is not None:
        return _ib_insync

    def _do_import():
        import ib_insync  # type: ignore
        return ib_insync

    ib_insync_mod = _run_ib(_do_import, timeout=30)
    _ib_insync = ib_insync_mod
    _IB = ib_insync_mod.IB
    _Stock = ib_insync_mod.Stock
    _MarketOrder = ib_insync_mod.MarketOrder
    _LimitOrder = ib_insync_mod.LimitOrder
    _StopOrder = ib_insync_mod.StopOrder
    _Order = ib_insync_mod.Order
    return _ib_insync


# ---------------------------------------------------------------------------
# Per-strategy connection cache
# ---------------------------------------------------------------------------

_connections: dict[str, object] = {}  # strategy_id -> IB instance
_connect_lock = threading.Lock()


def _client_id_for(strategy_id: str | None) -> int:
    """Per-strategy client id so A/B/C don't collide on the same socket."""
    if strategy_id:
        v = os.environ.get(f"IBKR_CLIENT_ID_{strategy_id.upper()}")
        if v:
            try:
                return int(v)
            except ValueError:
                pass
    try:
        return int(os.environ.get("IBKR_CLIENT_ID", "1"))
    except ValueError:
        return 1


def _connect(strategy_id: str | None):
    """Return a connected IB() instance for this strategy, reconnecting
    lazily if the existing one has dropped. Returns None when the
    gateway is unreachable; callers must tolerate this."""
    if not _OVERRIDE_IBKR_ENABLED:
        return None
    _load_ib_insync()
    key = strategy_id or "_default"
    with _connect_lock:
        ib = _connections.get(key)
        if ib is not None:
            try:
                ok = _run_ib(lambda: ib.isConnected(), timeout=5)
                if ok:
                    return ib
            except Exception:
                pass

        # Use the validated central config (rejects invalid ports like 4004).
        from app.services.broker.ibkr_config import get_ibkr_config
        _cfg = get_ibkr_config()
        host = _cfg["host"]
        port = _cfg["port"]
        client_id = _client_id_for(strategy_id)

        def _connect_worker():
            new_ib = _IB()
            new_ib.connect(host, port, clientId=client_id, timeout=15.0)
            return new_ib

        try:
            ib = _run_ib(_connect_worker, timeout=30)
        except Exception as exc:
            logger.warning(
                "IBKR connect failed (host=%s port=%s client_id=%s strategy=%s): %s",
                host, port, client_id, strategy_id, exc,
            )
            # Alert via Telegram (throttled — one per 30 min per host:port).
            try:
                alert_key = f"{host}:{port}"
                now = time.time()
                if now - _last_ibkr_alert.get(alert_key, 0) > _IBKR_ALERT_THROTTLE_S:
                    _last_ibkr_alert[alert_key] = now
                    from app.services.telegram_alert import alert_system_health
                    alert_system_health(
                        f"IBKR connection failed\n"
                        f"Host: {host}:{port} ({_cfg.get('mode', 'unknown')})\n"
                        f"Strategy: {strategy_id or 'default'}\n"
                        f"Error: {str(exc)[:120]}\n"
                        f"Action: check Railway 'ibgateway' service + IBKR_HOST/IBKR_PORT",
                        severity="CRITICAL",
                    )
            except Exception:
                pass  # never let alerting break the adapter
            return None

        _connections[key] = ib
        logger.info(
            "IBKR connected: host=%s port=%s client_id=%s strategy=%s",
            host, port, client_id, strategy_id,
        )
        return ib


# ---------------------------------------------------------------------------
# Translation helpers
# ---------------------------------------------------------------------------

def _stock(symbol: str):
    """SMART-routed US stock. Currency hardcoded USD (matches Alpaca's
    universe and the live account base currency)."""
    return _Stock(symbol.upper(), "SMART", "USD")


def _tif(payload_tif: str | None) -> str:
    if not payload_tif:
        return "GTC"
    s = payload_tif.upper()
    return "GTC" if s == "GTC" else "DAY"


def _build_orders(payload: dict) -> list:
    """Translate an Alpaca-style payload into a list of ib_insync orders.

    For order_class=bracket we emit three orders: parent (transmit=False),
    take-profit child (LMT, transmit=False), stop-loss child (STP,
    transmit=True). The IB convention is that only the LAST child has
    transmit=True, which atomically arms the whole bracket.
    """
    side_alpaca = (payload.get("side") or "buy").lower()
    action = "BUY" if side_alpaca == "buy" else "SELL"
    qty = float(payload.get("qty") or 0)
    tif = _tif(payload.get("time_in_force"))
    parent_type = (payload.get("type") or "market").lower()
    order_class = (payload.get("order_class") or "simple").lower()

    if order_class != "bracket":
        # Simple single-leg order
        if parent_type == "market":
            o = _MarketOrder(action, qty)
        elif parent_type == "limit":
            limit_price = float(payload.get("limit_price") or 0)
            o = _LimitOrder(action, qty, limit_price)
        elif parent_type == "stop":
            stop_price = float(payload.get("stop_price") or 0)
            o = _StopOrder(action, qty, stop_price)
        else:
            raise ValueError(f"Unsupported order type: {parent_type}")
        o.tif = tif
        coid = payload.get("client_order_id")
        if coid:
            # IB uses orderRef as the free-form client tag.
            o.orderRef = str(coid)[:50]
        return [o]

    # Bracket: parent market + TP limit + SL stop, all GTC by default
    tp_block = payload.get("take_profit") or {}
    sl_block = payload.get("stop_loss") or {}
    tp_price = float(tp_block.get("limit_price") or 0)
    sl_price = float(sl_block.get("stop_price") or 0)
    if tp_price <= 0 or sl_price <= 0:
        raise ValueError("Bracket order requires both take_profit.limit_price and stop_loss.stop_price")

    # Parent (market entry, do not transmit yet)
    parent = _MarketOrder(action, qty)
    parent.tif = tif
    parent.transmit = False
    coid = payload.get("client_order_id")
    if coid:
        parent.orderRef = str(coid)[:50]

    exit_action = "SELL" if action == "BUY" else "BUY"

    take_profit = _LimitOrder(exit_action, qty, tp_price)
    take_profit.tif = "GTC"
    take_profit.transmit = False

    stop_loss = _StopOrder(exit_action, qty, sl_price)
    stop_loss.tif = "GTC"
    stop_loss.transmit = True  # transmit=True on the LAST child arms the whole bracket

    return [parent, take_profit, stop_loss]


def _trade_to_dict(trade) -> dict:
    """Translate an ib_insync Trade into an Alpaca-compatible order dict."""
    contract = getattr(trade, "contract", None)
    order = getattr(trade, "order", None)
    status = getattr(trade, "orderStatus", None)
    return {
        "id": str(getattr(order, "permId", "") or getattr(order, "orderId", "")),
        "client_order_id": getattr(order, "orderRef", "") or "",
        "symbol": getattr(contract, "symbol", ""),
        "side": (getattr(order, "action", "") or "").lower(),
        "qty": str(int(float(getattr(order, "totalQuantity", 0)))),
        "filled_qty": str(int(float(getattr(status, "filled", 0) or 0))),
        "filled_avg_price": str(float(getattr(status, "avgFillPrice", 0) or 0)),
        "status": (getattr(status, "status", "") or "").lower(),
        "type": (getattr(order, "orderType", "") or "").lower(),
        "order_class": "bracket" if getattr(order, "parentId", 0) else "simple",
        "stop_price": str(float(getattr(order, "auxPrice", 0) or 0)),
        "limit_price": str(float(getattr(order, "lmtPrice", 0) or 0)),
        "legs": [],  # IB does not expose Alpaca-style legs; bracket children appear as separate orders
    }


def _position_to_dict(pos) -> dict:
    """Translate an ib_insync Position into an Alpaca-compatible dict."""
    contract = getattr(pos, "contract", None)
    qty = float(getattr(pos, "position", 0) or 0)
    avg = float(getattr(pos, "avgCost", 0) or 0)
    market = float(getattr(pos, "marketPrice", 0) or 0) if hasattr(pos, "marketPrice") else 0.0
    market_value = float(getattr(pos, "marketValue", qty * market) or 0)
    unrealized_pl = market_value - qty * avg if qty and avg else 0.0
    cost_basis = qty * avg if qty and avg else 0.0
    unrealized_plpc = (unrealized_pl / cost_basis) if cost_basis else 0.0
    return {
        "symbol": getattr(contract, "symbol", ""),
        "qty": str(int(qty)),
        "avg_entry_price": str(avg),
        "current_price": str(market),          # dashboard "LAST" column
        "market_value": str(market_value),
        "unrealized_pl": str(unrealized_pl),
        "unrealized_plpc": str(unrealized_plpc),
        "side": "long" if qty >= 0 else "short",
    }


def _account_to_dict(values: list) -> dict:
    """Collapse the IB AccountValue list into an Alpaca-style snapshot."""
    out = {"status": "ACTIVE"}
    for v in values:
        tag = getattr(v, "tag", "")
        val = getattr(v, "value", "")
        if tag == "TotalCashValue":
            out["cash"] = str(val)
        elif tag == "NetLiquidation":
            out["equity"] = str(val)
            out["portfolio_value"] = str(val)
        elif tag == "BuyingPower":
            out["buying_power"] = str(val)
        elif tag == "UnrealizedPnL":
            out["unrealized_pl"] = str(val)      # open P&L → dashboard "Day P&L"
        elif tag == "RealizedPnL":
            out["realized_pl"] = str(val)
        elif tag == "AccountType":
            out["account_type"] = str(val)
    return out


# ---------------------------------------------------------------------------
# IBBroker — implements BrokerClient
# ---------------------------------------------------------------------------

class IBBroker:
    name = "ibkr"

    # -- read --

    def get_account(self, strategy_id: str | None = None) -> Optional[dict]:
        ib = _connect(strategy_id)
        if ib is None:
            return None
        try:
            values = _call_ib(ib, "accountValues", timeout=10)
            return _account_to_dict(values)
        except Exception as exc:
            logger.error("IBKR get_account failed: %s", exc)
            return None

    def get_positions(self, strategy_id: str | None = None) -> list[dict]:
        ib = _connect(strategy_id)
        if ib is None:
            return []
        try:
            positions = _call_ib(ib, "positions", timeout=15)
            return [_position_to_dict(p) for p in positions if float(getattr(p, "position", 0) or 0) != 0]
        except Exception as exc:
            logger.error("IBKR get_positions failed: %s", exc)
            return []

    def get_orders(
        self,
        status: str = "all",
        limit: int = 50,
        strategy_id: str | None = None,
    ) -> list[dict]:
        ib = _connect(strategy_id)
        if ib is None:
            return []
        try:
            trades = _call_ib(ib, "trades", timeout=10)
        except Exception as exc:
            logger.error("IBKR get_orders failed: %s", exc)
            return []

        out: list[dict] = []
        for t in trades:
            d = _trade_to_dict(t)
            s = d["status"]
            if status == "open" and s not in ("submitted", "presubmitted", "pendingsubmit", "apipending"):
                continue
            if status == "closed" and s not in ("filled", "canceled", "expired", "apicancelled"):
                continue
            out.append(d)
        return out[:limit]

    def get_order(self, order_id: str, strategy_id: str | None = None) -> Optional[dict]:
        ib = _connect(strategy_id)
        if ib is None:
            return None
        try:
            trades = _call_ib(ib, "trades", timeout=10)
            for t in trades:
                order = getattr(t, "order", None)
                pid = str(getattr(order, "permId", ""))
                oid = str(getattr(order, "orderId", ""))
                if order_id and order_id in (pid, oid):
                    return _trade_to_dict(t)
        except Exception as exc:
            logger.error("IBKR get_order failed: %s", exc)
        return None

    # -- write --

    def submit_order(self, payload: dict, strategy_id: str | None = None) -> Optional[dict]:
        ib = _connect(strategy_id)
        if ib is None:
            return None
        symbol = payload.get("symbol")
        if not symbol:
            return None
        try:
            contract = _stock(symbol)
            _call_ib(ib, "qualifyContracts", contract, timeout=10)
            orders = _build_orders(payload)
            if len(orders) == 3:
                parent_trade = _call_ib(ib, "placeOrder", contract, orders[0], timeout=15)
                parent_id = parent_trade.order.orderId
                orders[1].parentId = parent_id
                orders[2].parentId = parent_id
                _call_ib(ib, "placeOrder", contract, orders[1], timeout=15)
                last_trade = _call_ib(ib, "placeOrder", contract, orders[2], timeout=15)
                _call_ib(ib, "sleep", 0.5, timeout=5)
                return _trade_to_dict(parent_trade)
            else:
                trade = _call_ib(ib, "placeOrder", contract, orders[0], timeout=15)
                _call_ib(ib, "sleep", 0.5, timeout=5)
                return _trade_to_dict(trade)
        except Exception as exc:
            logger.error("IBKR submit_order failed for %s: %s", symbol, exc)
            return None

    def cancel_order(self, order_id: str, strategy_id: str | None = None) -> bool:
        ib = _connect(strategy_id)
        if ib is None:
            return False
        try:
            trades = _call_ib(ib, "trades", timeout=10)
            for t in trades:
                order = getattr(t, "order", None)
                if not order:
                    continue
                if str(getattr(order, "orderId", "")) == str(order_id) or \
                   str(getattr(order, "permId", "")) == str(order_id):
                    _call_ib(ib, "cancelOrder", order, timeout=10)
                    return True
        except Exception as exc:
            logger.error("IBKR cancel_order failed: %s", exc)
        return False

    def close_position(self, symbol: str, strategy_id: str | None = None) -> Optional[dict]:
        ib = _connect(strategy_id)
        if ib is None:
            return None
        try:
            positions = _call_ib(ib, "positions", timeout=15)
            pos = next((p for p in positions if getattr(p.contract, "symbol", "") == symbol), None)
            if not pos:
                return None
            qty = float(getattr(pos, "position", 0) or 0)
            if qty == 0:
                return None
            action = "SELL" if qty > 0 else "BUY"
            order = _MarketOrder(action, abs(qty))
            order.tif = "DAY"
            contract = _stock(symbol)
            _call_ib(ib, "qualifyContracts", contract, timeout=10)
            trade = _call_ib(ib, "placeOrder", contract, order, timeout=15)
            _call_ib(ib, "sleep", 0.5, timeout=5)
            return _trade_to_dict(trade)
        except Exception as exc:
            logger.error("IBKR close_position failed for %s: %s", symbol, exc)
            return None


# Disconnect helper for tests / shutdown
def disconnect_all() -> None:
    with _connect_lock:
        for key, ib in list(_connections.items()):
            try:
                _run_ib(lambda: ib.disconnect() if ib.isConnected() else None, timeout=10)
            except Exception:
                pass
            _connections.pop(key, None)


__all__ = ["IBBroker", "disconnect_all"]
