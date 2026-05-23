"""Smart Order Routing for large entry orders (Phase 2B).

Wraps SmartOrderRouter to split large BUY entries into N slices,
then places a single OCO exit (stop-loss + take-profit) covering
the *full filled quantity* — the institutionally correct pattern.

For orders smaller than SMART_ROUTING_THRESHOLD, returns None so
the caller falls through to the existing single-bracket-order path.

Environment variables
---------------------
SMART_ROUTING_ENABLED        Enable smart routing (default "true").
SMART_ROUTING_THRESHOLD      Min shares to trigger slicing (default 500).
SMART_ROUTING_STRATEGY       "twap" | "vwap" | "simple" (default "twap").
SMART_ROUTING_MAX_SLICES     Maximum number of entry slices (default 10).
SMART_ROUTING_INTERVAL       Seconds between slices, 0 = fire immediately
                              in sequence (default 0).

Public API
----------
should_route(qty) -> bool
    True when smart routing is enabled and qty >= threshold.

route_and_submit_entry(
    symbol, qty, price, stop_loss, take_profit, strategy_id,
    use_trailing, trail_pct
) -> dict | None
    Submits N entry slices + one OCO exit.
    Returns a result dict on success, None when routing is not needed
    (small order — let caller use normal bracket).
"""

from __future__ import annotations

import logging
import os
import threading
import time

logger = logging.getLogger("screener")


# ------------------------------------------------------------------
# Environment flags
# ------------------------------------------------------------------

SMART_ROUTING_ENABLED: bool = (
    os.environ.get("SMART_ROUTING_ENABLED", "true").lower() not in ("false", "0", "no")
)
SMART_ROUTING_THRESHOLD: int = int(os.environ.get("SMART_ROUTING_THRESHOLD", "500"))
SMART_ROUTING_STRATEGY: str = os.environ.get("SMART_ROUTING_STRATEGY", "twap").lower()
SMART_ROUTING_MAX_SLICES: int = int(os.environ.get("SMART_ROUTING_MAX_SLICES", "10"))
SMART_ROUTING_INTERVAL: float = float(os.environ.get("SMART_ROUTING_INTERVAL", "0"))


# ------------------------------------------------------------------
# ADV lookup (backed by market_data)
# ------------------------------------------------------------------

def _adv_lookup(symbol: str) -> int:
    """Return average daily volume (shares) over last 20 days."""
    try:
        from app.services.market_data import fetch as fetch_market_data

        df = fetch_market_data(symbol, period="30d")
        if df is None or df.empty:
            return 0

        col_map = {c.lower(): c for c in df.columns}
        vol_col = col_map.get("volume") or col_map.get("vol")
        if not vol_col:
            return 0

        recent = df[vol_col].dropna().tail(20)
        return int(recent.mean()) if len(recent) > 0 else 0

    except Exception as exc:
        logger.debug("smart_execution: ADV lookup failed for %s: %s", symbol, exc)
        return 0


# ------------------------------------------------------------------
# Slice threshold check
# ------------------------------------------------------------------

def should_route(qty: int) -> bool:
    """Return True when qty warrants smart routing."""
    return SMART_ROUTING_ENABLED and qty >= SMART_ROUTING_THRESHOLD


# ------------------------------------------------------------------
# Main routing function
# ------------------------------------------------------------------

def route_and_submit_entry(
    symbol: str,
    qty: int,
    price: float,
    stop_loss: float,
    take_profit: float,
    strategy_id: str | None,
    *,
    use_trailing: bool = False,
    trail_pct: float = 0.0,
    client_order_id_prefix: str = "",
) -> dict | None:
    """Submit *qty* shares of *symbol* via sliced entry + single OCO exit.

    Parameters
    ----------
    symbol, qty, price, stop_loss, take_profit, strategy_id
        Same semantics as execute_buy().
    use_trailing, trail_pct
        Forwarded to OCO exit order for the trailing-stop branch.
    client_order_id_prefix
        Prefix for child order IDs (derived from parent COID).

    Returns
    -------
    dict  with keys: slices_submitted, slices_filled, filled_qty,
                     oco_order_id, oco_status, reason
    None  if qty < threshold (caller should use normal bracket path).
    """
    if not should_route(qty):
        return None

    from app.services.smart_order_router import SmartOrderRouter
    from app.services.trading_engine import _submit_order

    router = SmartOrderRouter(
        default_num_slices=min(SMART_ROUTING_MAX_SLICES, max(2, qty // 100)),
        default_interval=SMART_ROUTING_INTERVAL,
        adv_lookup=_adv_lookup,
    )

    plan = router.plan(
        symbol=symbol,
        side="buy",
        total_qty=qty,
        strategy=SMART_ROUTING_STRATEGY,
        # For immediate slicing, use market orders; timed uses limit
        order_type="market" if SMART_ROUTING_INTERVAL == 0 else "limit",
        time_in_force="gtc",
    )

    logger.info(
        "SmartRouter: %s x%d → %d slices via %s (interval=%.0fs)",
        symbol, qty, plan.num_slices, plan.strategy, SMART_ROUTING_INTERVAL,
    )

    # ── Submit entry slices ────────────────────────────────────────────
    slices_submitted = 0
    filled_qty_total = 0
    slice_order_ids: list[str] = []

    def _submit_slice(spec, idx: int) -> dict | None:
        payload: dict = {
            "symbol": symbol,
            "qty": str(spec.target_qty),
            "side": "buy",
            "type": spec.order_type,
            "time_in_force": spec.time_in_force,
        }
        if client_order_id_prefix:
            payload["client_order_id"] = (
                f"{client_order_id_prefix}-s{idx:02d}"
            )[:48]
        return _submit_order(payload, strategy_id=strategy_id)

    if SMART_ROUTING_INTERVAL == 0:
        # Fire all slices immediately in sequence (no daemon thread needed)
        for idx, spec in enumerate(plan.slices):
            order = _submit_slice(spec, idx)
            if order:
                slices_submitted += 1
                filled_qty_total += spec.target_qty
                oid = order.get("id", "")
                if oid:
                    slice_order_ids.append(oid)
            else:
                logger.warning(
                    "SmartRouter: slice %d/%d failed for %s",
                    idx + 1, plan.num_slices, symbol,
                )
    else:
        # Time-spaced execution: run in daemon thread
        def _timed_slices():
            nonlocal slices_submitted, filled_qty_total
            for idx, spec in enumerate(plan.slices):
                order = _submit_slice(spec, idx)
                if order:
                    slices_submitted += 1
                    filled_qty_total += spec.target_qty
                    oid = order.get("id", "")
                    if oid:
                        slice_order_ids.append(oid)
                if idx < len(plan.slices) - 1 and SMART_ROUTING_INTERVAL > 0:
                    time.sleep(SMART_ROUTING_INTERVAL)

        t = threading.Thread(target=_timed_slices, daemon=True, name=f"smart-route-{symbol}")
        t.start()
        # Wait briefly so we know if at least the first slice landed
        t.join(timeout=min(5.0, SMART_ROUTING_INTERVAL + 2))
        # filled_qty_total reflects what completed in that window;
        # remaining slices continue asynchronously

    if slices_submitted == 0:
        return {
            "slices_submitted": 0,
            "slices_filled": 0,
            "filled_qty": 0,
            "oco_order_id": None,
            "oco_status": "skipped",
            "reason": "All entry slices failed — smart routing aborted",
        }

    # ── Place single OCO exit for the total filled quantity ───────────
    oco_payload: dict = {
        "symbol": symbol,
        "qty": str(filled_qty_total),
        "side": "sell",
        "type": "market",
        "time_in_force": "gtc",
        "order_class": "oco",
        "take_profit": {"limit_price": str(round(take_profit, 2))},
    }

    if use_trailing and trail_pct > 0:
        oco_payload["stop_loss"] = {
            "stop_price": str(round(stop_loss, 2)),
            "trail_percent": str(round(trail_pct, 1)),
        }
    else:
        oco_payload["stop_loss"] = {"stop_price": str(round(stop_loss, 2))}

    if client_order_id_prefix:
        oco_payload["client_order_id"] = f"{client_order_id_prefix}-oco"[:48]

    oco_order = _submit_order(oco_payload, strategy_id=strategy_id)
    oco_id = None
    oco_status = "failed"

    if oco_order:
        oco_id = oco_order.get("id", "")
        oco_status = "submitted"
        logger.info(
            "SmartRouter: OCO exit placed for %s x%d (sl=%.2f tp=%.2f) → broker_id=%s",
            symbol, filled_qty_total, stop_loss, take_profit, oco_id,
        )
    else:
        # ── Emergency fallback: plain stop-loss to ensure no naked position ─
        logger.error(
            "SmartRouter: OCO placement FAILED for %s x%d — "
            "placing emergency stop-loss to protect position",
            symbol, filled_qty_total,
        )
        emergency_sl: dict = {
            "symbol": symbol,
            "qty": str(filled_qty_total),
            "side": "sell",
            "type": "stop",
            "stop_price": str(round(stop_loss, 2)),
            "time_in_force": "gtc",
        }
        if client_order_id_prefix:
            emergency_sl["client_order_id"] = f"{client_order_id_prefix}-esl"[:48]
        sl_order = _submit_order(emergency_sl, strategy_id=strategy_id)
        if sl_order:
            oco_id = sl_order.get("id", "")
            oco_status = "emergency_sl"
            logger.warning(
                "SmartRouter: Emergency stop-loss placed for %s at %.2f → broker_id=%s",
                symbol, stop_loss, oco_id,
            )
            # Notify via Telegram
            try:
                from app.services.telegram_alert import send_message as tg_send
                tg_send(
                    f"⚠️ SMART ROUTING OCO FAILED\n\n"
                    f"Symbol: {symbol}\nQty: {filled_qty_total}\n"
                    f"Emergency stop-loss placed at {stop_loss:.2f}\n"
                    f"Take-profit NOT set — manual review required"
                )
            except Exception:
                pass
        else:
            oco_status = "emergency_sl_failed"
            logger.critical(
                "SmartRouter: BOTH OCO and emergency SL failed for %s x%d. "
                "NAKED POSITION — immediate manual intervention required!",
                symbol, filled_qty_total,
            )
            try:
                from app.services.telegram_alert import send_message as tg_send
                tg_send(
                    f"\U0001f6a8 CRITICAL: OCO + EMERGENCY SL BOTH FAILED\n\n"
                    f"Symbol: {symbol}\nQty: {filled_qty_total}\n"
                    f"NAKED POSITION — manual intervention required NOW!"
                )
            except Exception:
                pass

    return {
        "slices_submitted": slices_submitted,
        "slices_filled": slices_submitted,
        "filled_qty": filled_qty_total,
        "oco_order_id": oco_id,
        "oco_status": oco_status,
        "reason": (
            f"SmartRouter: {slices_submitted} slices submitted, "
            f"OCO exit {oco_status}"
        ),
    }
