"""Risk manager with regime-aware sizing and modular guardrails."""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Optional

from app.config import settings
from app.core.config import app_cfg
from app.services.guards.base import GuardResult
from app.services.guards import GuardContext, evaluate as evaluate_guards, persist_results
from app.services.guards.market_hours import is_market_open
from app.services.pdt_tracker import (
    MAX_DAY_TRADES,
    PDT_WINDOW_DAYS,
    _day_trades,
    can_day_trade,
    get_day_trade_count,
    record_day_trade,
)
from app.services.regime import RegimeSnapshot, get_regime

logger = logging.getLogger("screener")


def _get_strategy_params(strategy_id: str = None, regime_state: str = "NEUTRAL") -> dict:
    """Resolve risk parameters from centralized config, with strategy overrides when present."""
    if strategy_id:
        from app.config import STRATEGY_CONFIGS

        cfg = STRATEGY_CONFIGS.get(strategy_id)
        if cfg:
            return {
                "trade_risk_pct": cfg.trade_risk_pct / 100.0,
                "max_position_pct": min(cfg.position_pct / 100.0, app_cfg.risk.max_position_pct / 100.0),
                "max_positions": min(
                    cfg.max_positions,
                    app_cfg.risk.max_positions.get(regime_state, app_cfg.risk.max_positions["NEUTRAL"]),
                ),
                "daily_loss_limit_pct": min(cfg.daily_loss_limit_pct, app_cfg.risk.daily_loss_limit_pct),
            }
    return {
        "trade_risk_pct": app_cfg.risk.risk_per_trade_pct.get(regime_state, app_cfg.risk.risk_per_trade_pct["NEUTRAL"]) / 100.0,
        "max_position_pct": app_cfg.risk.max_position_pct / 100.0,
        "max_positions": app_cfg.risk.max_positions.get(regime_state, app_cfg.risk.max_positions["NEUTRAL"]),
        "daily_loss_limit_pct": app_cfg.risk.daily_loss_limit_pct,
    }


def _pnls_pct_from_db(strategy_id: str = None, lookback_trades: int = 50) -> list[float]:
    """Return per-trade returns expressed as fractions (0.05 = +5%).

    These feed the continuous Kelly estimator. We read `pnl_pct` (already
    a percentage), divide by 100, and skip rows where it is null or
    non-finite. Returned newest-first to match the SQL order, but Kelly
    is order-invariant so it doesn't matter.
    """
    try:
        from app.db.database import SessionLocal
        from app.db.models import TradeHistory

        db = SessionLocal()
        try:
            query = db.query(TradeHistory).filter(TradeHistory.pnl_pct.isnot(None))
            if strategy_id:
                query = query.filter(TradeHistory.strategy_id == strategy_id)
            trades = query.order_by(TradeHistory.created_at.desc()).limit(lookback_trades).all()
        finally:
            db.close()
    except Exception as exc:
        logger.debug("Could not load Kelly stats from DB: %s", exc)
        return []

    out: list[float] = []
    for trade in trades:
        try:
            v = float(trade.pnl_pct) / 100.0
        except (TypeError, ValueError):
            continue
        if v == v and v != float("inf") and v != float("-inf"):
            out.append(v)
    return out


def calculate_position_size(
    price: float,
    stop_loss: float,
    available_cash: float,
    total_equity: float,
    strategy_id: str = None,
    regime: RegimeSnapshot | None = None,
) -> dict:
    """Calculate regime-adaptive position size with optional half-Kelly blending."""
    if price <= 0 or stop_loss <= 0 or stop_loss >= price:
        return {"qty": 0, "reason": "Invalid price/stop_loss"}

    regime = regime or get_regime()
    params = _get_strategy_params(strategy_id, regime.state)
    risk_pct = params["trade_risk_pct"]

    from app.services.kelly import kelly_from_db_pnls

    pnls_pct = _pnls_pct_from_db(strategy_id=strategy_id, lookback_trades=50)
    kelly_diag: dict = {"kelly_used": False, "kelly_fraction": 0.0, "n_trades": len(pnls_pct)}
    if len(pnls_pct) >= app_cfg.risk.kelly_enabled_after_trades:
        risk_pct, kelly_diag = kelly_from_db_pnls(
            pnls_pct,
            static_risk_pct=risk_pct,
            fractional=0.5,
            n_prior=30,
            n_min=app_cfg.risk.kelly_enabled_after_trades,
        )
        kelly_diag["n_trades"] = len(pnls_pct)

    max_risk_dollars = total_equity * risk_pct
    max_position_value = min(
        total_equity * params["max_position_pct"],
        app_cfg.execution.max_notional_per_order_usd,
    )

    risk_per_share = price - stop_loss
    if risk_per_share <= 0:
        return {"qty": 0, "reason": "Stop loss >= price"}

    shares_by_risk = int(max_risk_dollars / risk_per_share)
    shares_by_position = int(max_position_value / price)
    shares_by_cash = int(available_cash / price)

    qty = max(1, min(shares_by_risk, shares_by_position, shares_by_cash))
    if price > available_cash:
        return {"qty": 0, "reason": f"Price ${price:.2f} > available cash ${available_cash:.2f}"}

    position_value = qty * price
    risk_amount = qty * risk_per_share
    risk_pct_realized = (risk_amount / total_equity) * 100 if total_equity > 0 else 0

    return {
        "qty": qty,
        "position_value": round(position_value, 2),
        "risk_amount": round(risk_amount, 2),
        "risk_pct": round(risk_pct_realized, 2),
        "risk_per_share": round(risk_per_share, 2),
        "max_risk_budget": round(max_risk_dollars, 2),
        "regime": regime.state,
        "effective_risk_pct": round(risk_pct * 100, 4),
        "kelly": kelly_diag,
    }


def _legacy_check_trade_eligibility(
    symbol: str,
    side: str,
    price: float,
    stop_loss: float,
    account: dict,
    open_positions: list[dict],
    strategy_id: str = None,
) -> dict:
    """Pre-modular-guard fallback retained behind feature flag."""
    result = {"eligible": False, "reason": "", "sizing": {}}
    regime = get_regime()
    params = _get_strategy_params(strategy_id, regime.state)

    if not is_market_open():
        result["reason"] = "Market is closed — trades only execute during NYSE hours (9:30 AM - 4:00 PM ET, Mon-Fri)"
        return result

    if account.get("trading_blocked") or account.get("account_blocked"):
        result["reason"] = "Account is blocked"
        return result

    max_positions = params["max_positions"]
    current_positions = len(open_positions)
    if side == "buy" and current_positions >= max_positions:
        result["reason"] = f"Max positions reached ({current_positions}/{max_positions})"
        return result

    held_symbols = [p["symbol"] for p in open_positions]
    if side == "buy" and symbol in held_symbols:
        result["reason"] = f"Already holding {symbol}"
        return result

    equity = account.get("equity", 0)
    last_equity = account.get("last_equity", 0)
    if last_equity > 0:
        daily_pnl_pct = ((equity - last_equity) / last_equity) * 100
        if daily_pnl_pct <= -params["daily_loss_limit_pct"]:
            result["reason"] = f"Daily loss limit hit ({daily_pnl_pct:.1f}% vs -{params['daily_loss_limit_pct']}%)"
            return result

    sizing = calculate_position_size(
        price,
        stop_loss,
        account.get("cash", 0),
        account.get("equity", 0),
        strategy_id=strategy_id,
        regime=regime,
    )
    if sizing.get("qty", 0) == 0:
        result["reason"] = sizing.get("reason", "Position sizing returned 0 shares")
        return result

    if sizing["position_value"] < 50:
        result["reason"] = f"Position too small (${sizing['position_value']:.2f} < $50 minimum)"
        return result

    result["eligible"] = True
    result["reason"] = "All checks passed"
    result["sizing"] = sizing
    return result


def check_trade_eligibility(
    symbol: str,
    side: str,
    price: float,
    stop_loss: float,
    account: dict,
    open_positions: list[dict],
    strategy_id: str = None,
) -> dict:
    """Run modular guard evaluation and return sizing / audit trail."""
    if app_cfg.killed:
        return {"eligible": False, "reason": "Kill-switch active", "sizing": {}, "guards": []}
    if not app_cfg.risk.use_modular_guards:
        return _legacy_check_trade_eligibility(
            symbol=symbol,
            side=side,
            price=price,
            stop_loss=stop_loss,
            account=account,
            open_positions=open_positions,
            strategy_id=strategy_id,
        )

    regime = get_regime()
    sizing = calculate_position_size(
        price=price,
        stop_loss=stop_loss,
        available_cash=float(account.get("cash", 0) or 0),
        total_equity=float(account.get("equity", 0) or 0),
        strategy_id=strategy_id,
        regime=regime,
    )
    market_result = (
        GuardResult("market_hours", True, True, "Within regular market hours.", "MARKET_OPEN")
        if is_market_open()
        else GuardResult(
            "market_hours",
            False,
            True,
            "Market is closed — trades only execute during NYSE hours (09:30-16:00 ET, Mon-Fri).",
            "MARKET_CLOSED",
        )
    )
    ctx = GuardContext(
        strategy_id=strategy_id,
        symbol=symbol.upper(),
        side=side,
        price=price,
        stop_loss=stop_loss,
        qty=int(sizing.get("qty", 0) or 0),
        account=account,
        positions=open_positions,
        regime=regime,
    )
    if not market_result.passed:
        persist_results(ctx, [market_result])
        return {
            "eligible": False,
            "reason": market_result.reason,
            "sizing": sizing,
            "guards": [asdict(market_result)],
        }
    ok, results = evaluate_guards(ctx)
    results = [market_result] + results
    persist_results(ctx, results)

    if not ok:
        first_block = next(result for result in results if result.blocking and not result.passed)
        return {
            "eligible": False,
            "reason": f"{first_block.name}: {first_block.reason}",
            "sizing": sizing,
            "guards": [asdict(result) for result in results],
        }

    return {
        "eligible": True,
        "reason": "All guards passed",
        "sizing": sizing,
        "guards": [asdict(result) for result in results],
    }


def get_risk_status(account: dict, positions: list[dict], strategy_id: str = None) -> dict:
    """Get current risk dashboard status."""
    regime = get_regime()
    params = _get_strategy_params(strategy_id, regime.state)
    equity = float(account.get("equity", 0) or 0)
    last_equity = float(account.get("last_equity", 0) or 0)
    daily_pnl = equity - last_equity if last_equity > 0 else 0
    daily_pnl_pct = (daily_pnl / last_equity * 100) if last_equity > 0 else 0

    total_exposure = sum(abs(float(position.get("market_value", 0) or 0)) for position in positions)
    exposure_pct = (total_exposure / equity * 100) if equity > 0 else 0

    return {
        "equity": round(equity, 2),
        "cash": round(float(account.get("cash", 0) or 0), 2),
        "daily_pnl": round(daily_pnl, 2),
        "daily_pnl_pct": round(daily_pnl_pct, 2),
        "daily_loss_limit": f"-{params['daily_loss_limit_pct']}%",
        "open_positions": len(positions),
        "max_positions": params["max_positions"],
        "total_exposure": round(total_exposure, 2),
        "exposure_pct": round(exposure_pct, 2),
        "day_trades_used": get_day_trade_count(),
        "day_trades_max": MAX_DAY_TRADES,
        "pdt_safe": can_day_trade(),
        "trading_blocked": account.get("trading_blocked", False),
        "market_open": is_market_open(),
        "regime": regime.state,
        "modular_guards": app_cfg.risk.use_modular_guards,
        "kill_switch": app_cfg.killed,
        "portfolio_dd_cap_pct": app_cfg.risk.portfolio_dd_cap_pct,
        "correlation_threshold": app_cfg.risk.correlation_block_threshold,
    }


__all__ = [
    "MAX_DAY_TRADES",
    "PDT_WINDOW_DAYS",
    "_day_trades",
    "can_day_trade",
    "calculate_position_size",
    "check_trade_eligibility",
    "get_day_trade_count",
    "get_risk_status",
    "is_market_open",
    "record_day_trade",
    "settings",
]
