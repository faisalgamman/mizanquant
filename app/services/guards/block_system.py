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


def get_block_level() -> str:
    """Return the current BLOCK level based on market context."""
    from app.services.market_context import get_market_context
    mc = get_market_context()

    spy = mc.get("spy_regime", {})
    if spy.get("regime") == "bear":
        return "BEAR"

    vix_data = mc.get("vix", {})
    if vix_data.get("vix", 0) is not None and vix_data["vix"] > 25:
        return "VIX"

    credit = mc.get("credit", {})
    if credit.get("classification") == "stress":
        return "CREDIT"

    return "NORMAL"


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
