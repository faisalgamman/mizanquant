"""Guard blocking highly correlated additions in bear regimes."""

from __future__ import annotations

from app.core.config import app_cfg
from app.services.guards.base import GuardContext, GuardResult


def _returns(symbol: str, lookback: int) -> list[float] | None:
    from app.services.market_data import fetch

    df = fetch(symbol, period="1y")
    if df is None or len(df) < lookback + 5:
        return None
    close = df["close"].astype(float).tail(lookback + 1)
    returns = [float(value) for value in close.pct_change().dropna().tolist()]
    if len(returns) < lookback:
        return None
    return returns[-lookback:]


def _corr(left: list[float], right: list[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    left_mean = sum(left) / len(left)
    right_mean = sum(right) / len(right)
    cov = sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right))
    left_var = sum((a - left_mean) ** 2 for a in left)
    right_var = sum((b - right_mean) ** 2 for b in right)
    if left_var <= 0 or right_var <= 0:
        return None
    return cov / (left_var ** 0.5 * right_var ** 0.5)


def guard(ctx: GuardContext) -> GuardResult:
    if ctx.regime.state != "BEAR" or len(ctx.positions) == 0:
        return GuardResult("correlation", True, True, "Correlation guard inactive outside BEAR regime.", "CORRELATION_SKIPPED")

    target = _returns(ctx.symbol, app_cfg.risk.correlation_lookback_days)
    if target is None:
        return GuardResult("correlation", True, True, "Candidate correlation data unavailable.", "CORRELATION_NO_DATA")

    correlations: list[float] = []
    for pos in ctx.positions:
        other_symbol = str(pos.get("symbol", ""))
        other = _returns(other_symbol, app_cfg.risk.correlation_lookback_days)
        if other is None or len(other) != len(target):
            continue
        corr = _corr(target, other)
        if corr is not None:
            correlations.append(abs(float(corr)))

    if not correlations:
        return GuardResult("correlation", True, True, "No comparable position history for correlation guard.", "CORRELATION_NO_POS_DATA")

    avg_corr = float(sum(correlations) / len(correlations))
    if avg_corr >= app_cfg.risk.correlation_block_threshold:
        return GuardResult(
            "correlation",
            False,
            True,
            f"Average 20-day correlation {avg_corr:.2f} exceeds threshold {app_cfg.risk.correlation_block_threshold:.2f}.",
            "CORRELATION_BLOCK",
        )
    return GuardResult("correlation", True, True, f"Average correlation {avg_corr:.2f} within threshold.", "CORRELATION_OK")
