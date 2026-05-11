"""Guard blocking buys when VIX > 25 or HY/IG credit stress detected."""

from __future__ import annotations

from app.services.guards.base import GuardContext, GuardResult


def guard(ctx: GuardContext) -> GuardResult:
    from app.services.market_context import get_market_context
    mc = get_market_context()

    vix_data = mc.get("vix", {})
    vix = vix_data.get("vix")
    if vix is not None and vix > 25:
        return GuardResult(
            "market_conditions",
            False,
            True,
            f"VIX={vix:.1f} exceeds 25 threshold — all buys blocked.",
            "VIX_25_HALT",
        )

    credit = mc.get("credit", {})
    if credit.get("classification") == "stress":
        return GuardResult(
            "market_conditions",
            False,
            True,
            f"Credit stress detected (HYG/LQD daily change={credit.get('daily_change_pct'):.2f}%) — all buys blocked.",
            "CREDIT_STRESS_HALT",
        )

    return GuardResult("market_conditions", True, True, "Market conditions OK.", "MKT_OK")
