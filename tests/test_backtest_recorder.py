"""Tests for backtest recorder (DB persistence with reproducibility seal)."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.services.backtest_recorder import record_backtest


@pytest.fixture
def test_session():
    engine = create_engine("sqlite://", echo=False)
    from app.db.models import Base
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine)
    session = TestSession()
    try:
        yield session
    finally:
        session.close()


def test_record_backtest_returns_int_id(test_session):
    summary = {
        "sharpe": 1.2, "sortino": 1.5, "max_drawdown": 0.12,
        "annualized_return": 0.08, "win_rate": 55.0, "profit_factor": 1.4,
        "n_trades": 10, "total_commissions": 15.0, "total_slippage": 8.0,
    }
    qc = {"deflated_sharpe": 0.95, "permutation_pvalue": 0.03, "bootstrap_lower_5pct": 0.001}
    row_id = record_backtest(
        strategy_name="momentum",
        symbols=["AAPL", "MSFT"],
        summary=summary,
        qc=qc,
        profile="momentum",
        extra={"agent": "test"},
        _session=test_session,
    )
    assert isinstance(row_id, int)
    assert row_id > 0


def test_record_backtest_stores_seal(test_session):
    summary = {"sharpe": 0.5, "n_trades": 5}
    row_id = record_backtest(
        strategy_name="reversion", symbols=["AAPL"], summary=summary, _session=test_session,
    )
    from app.db.models import BacktestRun
    row = test_session.get(BacktestRun, row_id)
    assert row is not None
    assert row.git_sha != ""
    assert row.code_hash != ""
    assert row.config_hash != ""
    assert len(row.git_sha) >= 7


def test_record_backtest_no_qc(test_session):
    summary = {"sharpe": 0.8, "n_trades": 3}
    row_id = record_backtest(
        strategy_name="ml", symbols=["AAPL", "GOOG"], summary=summary, _session=test_session,
    )
    assert isinstance(row_id, int)


def test_record_backtest_stores_extra(test_session):
    summary = {"sharpe": 1.0, "n_trades": 7}
    row_id = record_backtest(
        strategy_name="ensemble", symbols=["AAPL"], summary=summary,
        extra={"model_version": "v2", "epochs": 100}, _session=test_session,
    )
    from app.db.models import BacktestRun
    row = test_session.get(BacktestRun, row_id)
    assert row.extra == {"model_version": "v2", "epochs": 100}
