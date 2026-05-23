"""Transaction Cost Analysis — post-fill slippage vs pre-trade model (Phase 2C).

After a fill arrives (fill_watcher), compute realized slippage relative to:
  - The decision price captured at signal time (``signal_details["decision_price"]``)
  - The pre-trade model estimate (``signal_details["execution_estimate"]["est_cost_bps"]``)

Results are persisted in ``signal_details["tca"]`` on the TradeHistory row.

Public API
----------
compute_tca(side, decision_price, filled_avg_price, est_bps=None) -> dict
    Pure function — no DB access.

record_tca_to_trade(trade, db) -> None
    Reads ``signal_details["decision_price"]`` and
    ``signal_details["execution_estimate"]``, calls compute_tca,
    merges result back into ``signal_details["tca"]``, and marks
    the DB row dirty for commit by the caller.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger("screener")


# ------------------------------------------------------------------
# Pure computation
# ------------------------------------------------------------------

def compute_tca(
    side: str,
    decision_price: float,
    filled_avg_price: float,
    est_bps: float | None = None,
) -> dict:
    """Compute Transaction Cost Analysis metrics.

    Parameters
    ----------
    side : str
        "buy" or "sell" (case-insensitive).
    decision_price : float
        The price at the moment the signal was generated (before any fill).
        Stored as ``signal_details["decision_price"]`` in execute_buy.
    filled_avg_price : float
        The volume-weighted average price of the actual fill.
    est_bps : float | None
        Pre-trade modelled cost in bps from ExecutionSimulator.
        Pass None if the estimate was not available.

    Returns
    -------
    dict with keys:
        decision_price          – snapshot price when signal fired
        filled_avg_price        – actual fill price
        realized_slippage_bps   – signed: positive = paid more than expected
                                  (buy: fill > decision; sell: fill < decision)
        modeled_bps             – pre-trade model estimate (or None)
        slippage_vs_model_bps   – realized − modeled (positive = worse than modeled)
    """
    if decision_price <= 0 or filled_avg_price <= 0:
        return {
            "decision_price": decision_price,
            "filled_avg_price": filled_avg_price,
            "realized_slippage_bps": None,
            "modeled_bps": est_bps,
            "slippage_vs_model_bps": None,
        }

    # Signed slippage: for BUY, paying more is positive (worse).
    # For SELL, receiving less is positive (worse).
    if side.lower() == "buy":
        realized_bps = (filled_avg_price - decision_price) / decision_price * 10_000.0
    else:
        realized_bps = (decision_price - filled_avg_price) / decision_price * 10_000.0

    vs_model = None
    if est_bps is not None:
        vs_model = round(realized_bps - est_bps, 2)

    return {
        "decision_price": decision_price,
        "filled_avg_price": filled_avg_price,
        "realized_slippage_bps": round(realized_bps, 2),
        "modeled_bps": round(est_bps, 2) if est_bps is not None else None,
        "slippage_vs_model_bps": vs_model,
    }


# ------------------------------------------------------------------
# DB helper — called from fill_watcher after a fill lands
# ------------------------------------------------------------------

def record_tca_to_trade(trade, db) -> None:  # noqa: ANN001
    """Compute TCA and persist it into trade.signal_details["tca"].

    The ``db`` session must be committed by the caller after this returns.

    trade : TradeHistory ORM row (must have signal_details, entry_price,
            filled_avg_price, side attributes).
    db    : active SQLAlchemy session (used only for ORM flag detection).
    """
    try:
        # Parse existing signal_details
        raw = getattr(trade, "signal_details", None) or {}
        if isinstance(raw, str):
            try:
                details = json.loads(raw)
            except Exception:
                details = {}
        else:
            details = dict(raw)

        # Do nothing if TCA was already recorded
        if details.get("tca"):
            return

        decision_price = float(details.get("decision_price") or 0)
        filled_avg_price = float(getattr(trade, "filled_avg_price") or 0)
        side = str(getattr(trade, "side") or "buy").lower()

        if decision_price <= 0 or filled_avg_price <= 0:
            return

        est = details.get("execution_estimate") or {}
        est_bps = est.get("est_cost_bps")

        tca_result = compute_tca(side, decision_price, filled_avg_price, est_bps)
        details["tca"] = tca_result

        # Write back — handle both JSON-string and dict column types
        try:
            import sqlalchemy
            from sqlalchemy.types import JSON
            col_type = type(trade.__table__.columns["signal_details"].type)
            if col_type is not JSON and not issubclass(col_type, JSON):
                trade.signal_details = json.dumps(details)
            else:
                trade.signal_details = details
        except Exception:
            # Fallback: store as JSON string
            trade.signal_details = json.dumps(details)

        # Mark the column dirty so SQLAlchemy picks up the mutation
        try:
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(trade, "signal_details")
        except Exception:
            pass

        logger.debug(
            "TCA recorded for trade %s: realized_slippage=%.1f bps, vs_model=%s bps",
            getattr(trade, "order_id", "?"),
            tca_result.get("realized_slippage_bps") or 0,
            tca_result.get("slippage_vs_model_bps"),
        )

    except Exception as exc:
        logger.error("TCA recording failed: %s", exc, exc_info=True)
