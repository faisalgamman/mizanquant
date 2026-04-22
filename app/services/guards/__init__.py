"""Modular trading guard runner and persistence helpers."""

from __future__ import annotations

from dataclasses import asdict

from app.services.guards import (
    account_status,
    correlation,
    cross_strategy_veto,
    daily_loss,
    duplicate_symbol,
    earnings_blackout,
    max_notional,
    max_positions,
    min_notional,
    pdt,
    portfolio_drawdown,
    regime_bear_freeze,
    sector_exposure,
    vix_halt,
)
from app.services.guards.base import Guard, GuardContext, GuardResult


_GUARDS: list[Guard] = [
    account_status.guard,
    vix_halt.guard,
    daily_loss.guard,
    portfolio_drawdown.guard,
    max_positions.guard,
    duplicate_symbol.guard,
    cross_strategy_veto.guard,
    pdt.guard,
    sector_exposure.guard,
    correlation.guard,
    earnings_blackout.guard,
    max_notional.guard,
    min_notional.guard,
    regime_bear_freeze.guard,
]


def run_all(ctx: GuardContext) -> list[GuardResult]:
    return [guard(ctx) for guard in _GUARDS]


def evaluate(ctx: GuardContext) -> tuple[bool, list[GuardResult]]:
    results = run_all(ctx)
    ok = not any(result.blocking and not result.passed for result in results)
    return ok, results


def persist_results(ctx: GuardContext, results: list[GuardResult]) -> None:
    try:
        from app.db.database import SessionLocal
        from app.db.models import GuardLog

        db = SessionLocal()
        try:
            for result in results:
                db.add(
                    GuardLog(
                        strategy_id=ctx.strategy_id,
                        symbol=ctx.symbol.upper(),
                        guard_name=result.name,
                        passed=result.passed,
                        code=result.code,
                        reason=result.reason,
                        regime=ctx.regime.state,
                    )
                )
            db.commit()
        finally:
            db.close()
    except Exception as exc:
        import logging

        if "guard_log" in str(exc).lower():
            logging.getLogger("screener").debug("Skipping guard persistence; guard_log table unavailable.")
            return
        logging.getLogger("screener").exception("Failed to persist guard results")


__all__ = [
    "Guard",
    "GuardContext",
    "GuardResult",
    "asdict",
    "evaluate",
    "persist_results",
    "run_all",
]
