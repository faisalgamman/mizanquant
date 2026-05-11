"""Guard halting all trading when SPY closes below EMA 200 (bear regime)."""

from __future__ import annotations

from app.services.guards.base import GuardContext, GuardResult


def guard(ctx: GuardContext) -> GuardResult:
    from app.services.market_context import get_market_context
    mc = get_market_context()

    spy = mc.get("spy_regime", {})
    if spy.get("regime") == "bear":
        return GuardResult(
            "spy_bear_halt",
            False,
            True,
            f"SPY below EMA 200 ({spy.get('price_vs_ema200_pct')}%) — full trading halt.",
            "SPY_BEAR_HALT",
        )

    return GuardResult("spy_bear_halt", True, True, "SPY above EMA 200.", "SPY_OK")
