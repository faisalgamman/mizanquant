"""Portfolio Stop — multi-tier drawdown protection.

Tiers:
  WARNING (−10%): Position sizing reduced to 50%
  HALT    (−15%): Close all positions, block new entries
  EMERGENCY (−20%): Hard close + kill switch

Peak equity persisted in portfolio_stop.json for restart survival.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("screener")

_STOP_FILE = Path("portfolio_stop.json")
_lock = threading.Lock()

WARNING_DD = 10.0
HALT_DD = 15.0
EMERGENCY_DD = 20.0


def _load_state() -> dict:
    if _STOP_FILE.exists():
        try:
            return json.loads(_STOP_FILE.read_text())
        except Exception as exc:
            logger.debug("Failed to load portfolio stop state: %s", exc)
    default = {
        "peak_equity": 0.0,
        "peak_date": None,
        "last_equity": 0.0,
        "current_dd_pct": 0.0,
        "tier": "none",
        "triggered_at": None,
    }
    try:
        _STOP_FILE.write_text(json.dumps(default, indent=2))
    except Exception:
        pass
    return default


def _save_state(state: dict) -> None:
    try:
        _STOP_FILE.write_text(json.dumps(state, indent=2, default=str))
    except Exception as exc:
        logger.debug("Failed to save portfolio stop state: %s", exc)


def _get_total_equity() -> float | None:
    """Aggregate equity across all configured Alpaca strategies."""
    try:
        from app.config import STRATEGY_CONFIGS
        from app.services.alpaca_client import get_account as alpaca_get_account
    except ImportError:
        return None
    total = 0.0
    for sid in STRATEGY_CONFIGS:
        try:
            account = alpaca_get_account(strategy_id=sid)
            if account:
                total += float(account.get("equity", 0) or 0)
        except Exception:
            continue
    return total if total > 0 else None


def check_drawdown(equity: float | None = None) -> dict:
    """Check current portfolio drawdown and return tier + action.

    Call this periodically (e.g. every position check, every 5 min in
    background loop) to keep peak-equity tracking up to date.

    Args:
        equity: Current total portfolio equity. If None, auto-fetches.

    Returns:
        Dict with tier, drawdown_pct, peak_equity, action_required, etc.
    """
    if equity is None:
        equity = _get_total_equity()
    if equity is None or equity <= 0:
        return {"tier": "unknown", "drawdown_pct": 0.0, "action_required": False}

    with _lock:
        state = _load_state()
        peak = state.get("peak_equity", 0.0)
        now = datetime.now(timezone.utc).isoformat()

        # Update peak only if this is the new all-time high
        if equity > peak:
            peak = equity
            state["peak_equity"] = peak
            state["peak_date"] = now
            state["current_dd_pct"] = 0.0
            state["tier"] = "none"
            state["triggered_at"] = None
            _save_state(state)
            return {
                "tier": "none",
                "drawdown_pct": 0.0,
                "peak_equity": peak,
                "current_equity": equity,
                "action_required": False,
                "message": "New peak equity — no drawdown.",
            }

        # Compute drawdown from peak
        dd_pct = (peak - equity) / peak * 100.0
        state["current_dd_pct"] = round(dd_pct, 2)
        state["last_equity"] = equity

        # Determine tier
        if dd_pct >= EMERGENCY_DD:
            new_tier = "emergency"
        elif dd_pct >= HALT_DD:
            new_tier = "halt"
        elif dd_pct >= WARNING_DD:
            new_tier = "warning"
        else:
            new_tier = "none"

        messages = {
            "none": f"Drawdown {dd_pct:.1f}% \u2014 within normal range.",
            "warning": f"DRAWDOWN WARNING: {dd_pct:.1f}% \u2014 reducing position sizing to 50%.",
            "halt": f"DRAWDOWN HALT: {dd_pct:.1f}% \u2014 closing all positions, blocking new entries.",
            "emergency": f"EMERGENCY DRAWDOWN: {dd_pct:.1f}% \u2014 kill switch activated, closing all.",
        }

        # Only set triggered_at on first entry into a tier
        tier_changed = new_tier != state.get("tier")
        if tier_changed:
            state["tier"] = new_tier
            state["triggered_at"] = now
        state["current_dd_pct"] = round(dd_pct, 2)
        _save_state(state)

        # Notify Telegram on tier entry
        if tier_changed and new_tier != "none":
            try:
                from app.services.telegram_alert import send_message as tg_send
                tg_msg = (
                    f"\u26a0\ufe0f PORTFOLIO STOP TRIGGERED\n\n"
                    f"Tier: {new_tier.upper()}\n"
                    f"Drawdown: {dd_pct:.1f}%\n"
                    f"Peak: ${peak:,.2f}\n"
                    f"Current: ${equity:,.2f}\n"
                    f"Action: {messages.get(new_tier, 'N/A')}"
                )
                tg_send(tg_msg)
            except Exception as exc:
                logger.debug("Portfolio stop Telegram notification failed: %s", exc)

    action_required = new_tier in ("halt", "emergency")

    return {
        "tier": new_tier,
        "drawdown_pct": round(dd_pct, 2),
        "peak_equity": round(peak, 2),
        "current_equity": round(equity, 2),
        "action_required": action_required,
        "message": messages.get(new_tier, f"Drawdown {dd_pct:.1f}%."),
    }


def reduce_sizing(qty: int) -> int:
    """Halve position size if in WARNING tier."""
    with _lock:
        state = _load_state()
        tier = state.get("tier", "none")
    if tier == "warning":
        return max(1, qty // 2)
    return qty


def get_status() -> dict:
    """Return current portfolio stop status without side effects."""
    with _lock:
        state = _load_state()
    tier = state.get("tier", "none")
    dd = state.get("current_dd_pct", 0.0)
    messages = {
        "none": "Within limits.",
        "warning": f"Sizing reduced to 50% ({dd:.1f}% drawdown).",
        "halt": f"All entries blocked, positions closing ({dd:.1f}% drawdown).",
        "emergency": f"Kill switch active ({dd:.1f}% drawdown).",
    }
    return {
        "tier": tier,
        "drawdown_pct": dd,
        "peak_equity": state.get("peak_equity", 0.0),
        "last_equity": state.get("last_equity", 0.0),
        "peak_date": state.get("peak_date"),
        "triggered_at": state.get("triggered_at"),
        "message": messages.get(tier, "Unknown state."),
        "thresholds": {
            "warning_pct": WARNING_DD,
            "halt_pct": HALT_DD,
            "emergency_pct": EMERGENCY_DD,
        },
    }


__all__ = [
    "check_drawdown",
    "reduce_sizing",
    "get_status",
    "WARNING_DD",
    "HALT_DD",
    "EMERGENCY_DD",
]
