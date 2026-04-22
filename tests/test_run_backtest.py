"""Tests for multi-symbol backtest aggregation."""

from __future__ import annotations

from scripts import run_backtest as rb


def test_run_one_aggregates_multiple_symbols(monkeypatch):
    def fake_symbol_run(profile, symbol, start, end):
        return {
            "summary": {
                "total_return": 0.1 if symbol == "AAPL" else 0.2,
                "annualized_return": 0.1,
                "sharpe_ratio": 1.0 if symbol == "AAPL" else 2.0,
                "sortino_ratio": 1.0,
                "max_drawdown": 0.05 if symbol == "AAPL" else 0.07,
                "calmar_ratio": 1.0,
                "win_rate": 0.5,
                "profit_factor": 1.5,
                "total_trades": 2,
                "avg_trade_return": 0.01,
                "benchmark_return": 0.03,
                "alpha": 0.02,
                "total_commissions": 1.0,
                "total_slippage": 2.0,
            },
            "trade_returns": [0.1, -0.05] if symbol == "AAPL" else [0.2, 0.1],
            "in_sample": {"start": "2024-01-01", "end": "2024-03-01", "rows": 10},
            "out_of_sample": {"start": "2024-03-02", "end": "2024-04-01", "rows": 5},
            "costs_bps": {"commission": 1.0, "slippage": 2.0, "spread": 3.0},
            "trade_distribution": {"buy": 20.0, "hold": 80.0},
            "signal_expectation": {"BUY": 20.0, "NEUTRAL": 80.0},
        }

    monkeypatch.setattr(rb, "run_one_symbol", fake_symbol_run)

    result = rb.run_one("momentum", ["AAPL", "MSFT"], "2024-01-01", "2024-04-01")

    assert result["symbol_count"] == 2
    assert result["summary"]["sharpe_ratio"] == 1.5
    assert result["summary"]["max_drawdown"] == 0.07
    assert result["summary"]["total_trades"] == 4
    assert result["signal_expectation"] == {"BUY": 20.0, "NEUTRAL": 80.0}
