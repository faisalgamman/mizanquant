"""BLOCK System — aggregate market-wide trading halt levels.

BLOCK levels (ascending severity):
    NORMAL  → All conditions met, trading allowed
    CREDIT  → HY/IG credit stress detected → no new signals
    VIX     → VIX > 25 → no new signals
    BEAR    → SPY below EMA 200 → full trading halt
"""

from __future__ import annotations

from app.services.guards.base import GuardContext, GuardResult

BLOCK_LEVELS = ["NORMAL", "CREDIT", "VIX", "BEAR"]

# State tracker so we only fire market-block alerts on transition
_previous_block_level: str = "NORMAL"


def get_block_level() -> str:
    """Return the current BLOCK level based on market context."""
    from app.services.market_context import get_market_context
    mc = get_market_context()

    spy = mc.get("spy_regime", {})
    if spy.get("regime") == "bear":
        level = "BEAR"
    elif mc.get("vix", {}).get("vix", 0) is not None and mc["vix"]["vix"] > 25:
        level = "VIX"
    elif mc.get("credit", {}).get("classification") == "stress":
        level = "CREDIT"
    else:
        level = "NORMAL"

    # Fire Telegram alert on transition TO a blocking state
    global _previous_block_level
    if level != "NORMAL" and _previous_block_level != level:
        try:
            from app.services.telegram_alert import alert_market_block
            vix_data = mc.get("vix", {})
            credit_data = mc.get("credit", {})
            spy_data = mc.get("spy_regime", {})
            alert_market_block(
                status=level,
                reason=_block_reason(level, mc),
                vix=vix_data.get("vix", 0) or 0,
                vix_threshold=25,
                hyg_lqd_ratio=credit_data.get("ratio"),
                spy_regime=spy_data.get("regime"),
            )
        except Exception:
            pass
    _previous_block_level = level

    return level


def _block_reason(level: str, mc: dict) -> str:
    reasons = {
        "BEAR": f"SPY ({mc.get('spy_regime', {}).get('price', 'N/A')}) below EMA 200",
        "VIX": f"VIX at {mc.get('vix', {}).get('vix', 'N/A')} (threshold: 25)",
        "CREDIT": f"HY/IG credit classification: {mc.get('credit', {}).get('classification', 'N/A')}",
    }
    return reasons.get(level, "Unknown block reason")


def guard(ctx: GuardContext) -> GuardResult:
    level = get_block_level()
    if level == "BEAR":
        return GuardResult(
            "block_system", False, True,
            "BLOCK=BEAR — SPY below EMA 200, full trading halt.",
            "BLOCK_BEAR",
        )
    if level == "VIX":
        return GuardResult(
            "block_system", False, True,
            f"BLOCK=VIX — VIX > 25, no new signals.",
            "BLOCK_VIX",
        )
    if level == "CREDIT":
        return GuardResult(
            "block_system", False, True,
            "BLOCK=CREDIT — HY/IG credit stress, no new signals.",
            "BLOCK_CREDIT",
        )
    return GuardResult("block_system", True, True, "BLOCK=NORMAL — trading allowed.", "BLOCK_OK")
