"""Backtest result recorder — persists backtest runs with reproducibility seal.

Writes to the ``backtest_runs`` table and, when MLflow is configured,
also logs the run to the MLflow tracking server.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from app.core.reproducibility import reproducibility_seal
from app.db.database import SessionLocal
from app.db.models import BacktestRun


def record_backtest(
    strategy_name: str,
    symbols: list[str],
    summary: dict[str, Any],
    qc: Optional[dict[str, Any]] = None,
    prices=None,
    profile: Optional[str] = None,
    extra: Optional[dict[str, Any]] = None,
    _session=None,
) -> int:
    """Persist a backtest run with its reproducibility seal.

    Returns the new row's primary-key ID.
    """
    seal = reproducibility_seal(prices=prices)

    def _parse_dt(val) -> Optional[datetime]:
        if val is None:
            return None
        if isinstance(val, datetime):
            return val
        if isinstance(val, str):
            try:
                return datetime.fromisoformat(val)
            except Exception:
                pass
        return None

    row = BacktestRun(
        strategy_name=strategy_name,
        symbols=symbols,
        profile=profile,
        sharpe=summary.get("sharpe"),
        sortino=summary.get("sortino"),
        max_drawdown_pct=summary.get("max_drawdown"),
        calmar=summary.get("calmar"),
        annualized_return_pct=summary.get("annualized_return"),
        win_rate=summary.get("win_rate"),
        profit_factor=summary.get("profit_factor"),
        total_trades=summary.get("n_trades") or summary.get("total_trades"),
        total_commissions=summary.get("total_commissions"),
        total_slippage=summary.get("total_slippage"),
        test_start=_parse_dt(summary.get("test_start")),
        test_end=_parse_dt(summary.get("test_end")),
        deflated_sharpe=qc.get("deflated_sharpe") if qc else None,
        permutation_pvalue=qc.get("permutation_pvalue") if qc else None,
        bootstrap_lower_5pct=qc.get("bootstrap_lower_5pct") if qc else None,
        git_sha=seal["git_sha"],
        is_dirty=seal["is_dirty"],
        data_hash=seal["data_hash"],
        code_hash=seal["code_hash"],
        config_hash=seal["config_hash"],
        extra=extra,
    )

    if _session:
        _session.add(row)
        _session.flush()
    else:
        db = SessionLocal()
        try:
            db.add(row)
            db.commit()
        finally:
            db.close()

    return row.id  # type: ignore[return-value]
