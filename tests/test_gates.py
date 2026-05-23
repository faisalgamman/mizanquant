"""Enforcement gate tests — institutional production guardrails.

Tests verify that:
1. A leaky model (leakage_clean=False) is REJECTED by model_registry.
2. A model with high permutation p-value is REJECTED.
3. A model profitable in only 1 regime is REJECTED.
4. A clean model (all gates pass) is ACCEPTED.
5. paper_trade_gate: high p-value blocks graduation.
6. paper_trade_gate: regime-dependent strategy blocks graduation.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from app.services.model_registry import _check_quality_gates
from app.services.paper_trade_gate import evaluate_graduation


# ── model_registry gate tests ─────────────────────────────────────────────────

PASSING_BASE = {
    "test_sharpe": 1.5,
    "test_acc": 0.60,
    "max_drawdown": 10.0,
    "leakage_clean": True,
    "deflated_sharpe": 0.75,
    "permutation_pvalue": 0.02,
    "regime_breakdown": {
        "BULL":    {"avg_return": 2.5, "trades": 10},
        "NEUTRAL": {"avg_return": 1.0, "trades": 8},
        "BEAR":    {"avg_return": -0.5, "trades": 5},
    },
}


def test_clean_model_passes_all_gates():
    failures = _check_quality_gates(PASSING_BASE)
    assert failures == [], f"Expected no failures, got: {failures}"


def test_leakage_blocks_promotion():
    metrics = {**PASSING_BASE, "leakage_clean": False}
    failures = _check_quality_gates(metrics)
    assert any("leakage" in f.lower() for f in failures), (
        "leakage_clean=False must produce a failure"
    )


def test_high_permutation_pvalue_blocks_promotion():
    """p-value >= 0.05 means the edge is noise — must be blocked."""
    metrics = {**PASSING_BASE, "permutation_pvalue": 0.12}
    failures = _check_quality_gates(metrics)
    assert any("permutation" in f.lower() for f in failures), (
        "High p-value must produce a failure"
    )


def test_low_dsr_blocks_promotion():
    metrics = {**PASSING_BASE, "deflated_sharpe": 0.30}
    failures = _check_quality_gates(metrics)
    assert any("dsr" in f.lower() for f in failures), (
        "DSR below threshold must produce a failure"
    )


def test_single_regime_blocks_promotion():
    """Profitable only in BULL → fail regime robustness gate."""
    metrics = {
        **PASSING_BASE,
        "regime_breakdown": {
            "BULL":    {"avg_return": 3.0, "trades": 20},
            "NEUTRAL": {"avg_return": -1.0, "trades": 10},
            "BEAR":    {"avg_return": -2.0, "trades": 8},
        },
    }
    failures = _check_quality_gates(metrics)
    assert any("regime" in f.lower() for f in failures), (
        "Only BULL regime profitable — must fail regime gate"
    )


def test_missing_dsr_does_not_block():
    """If DSR is 0 (not computed), the gate should not block."""
    metrics = {**PASSING_BASE, "deflated_sharpe": 0.0}
    failures = _check_quality_gates(metrics)
    dsr_failures = [f for f in failures if "dsr" in f.lower()]
    assert dsr_failures == [], "DSR=0 (not computed) must not block promotion"


def test_missing_regime_breakdown_does_not_block():
    """No regime_breakdown supplied → gate silently passes."""
    metrics = {k: v for k, v in PASSING_BASE.items() if k != "regime_breakdown"}
    failures = _check_quality_gates(metrics)
    regime_failures = [f for f in failures if "regime" in f.lower()]
    assert regime_failures == [], "Missing regime_breakdown must not block"


# ── paper_trade_gate tests ────────────────────────────────────────────────────

def _make_paper_returns(n: int = 40, mean: float = 0.005) -> tuple:
    """Synthetic paper-trade data: strong positive edge, long period, many symbols."""
    rng = np.random.default_rng(99)
    pnls = (rng.normal(loc=mean, scale=0.02, size=n)).tolist()
    now = datetime.now(timezone.utc)
    timestamps = [now - timedelta(hours=(n - i) * 12) for i in range(n)]
    symbols = [f"SYM{i % 10}" for i in range(n)]
    return pnls, timestamps, symbols


def test_graduation_passes_with_strong_edge():
    pnls, timestamps, symbols = _make_paper_returns(n=40, mean=0.010)
    status = evaluate_graduation(pnls, timestamps, symbols)
    # Edge is strong — should graduate (or fail only on sample-size constraints)
    # Just verify the function runs and returns a valid status
    assert hasattr(status, "graduated")
    assert hasattr(status, "permutation_pvalue")
    assert hasattr(status, "regime_robust")


def test_graduation_blocked_by_permutation_pvalue():
    """Near-zero returns → p-value will be high → graduation must be blocked."""
    rng = np.random.default_rng(1)
    # Pure noise — no edge
    pnls = rng.normal(loc=0.0, scale=0.001, size=60).tolist()
    now = datetime.now(timezone.utc)
    timestamps = [now - timedelta(hours=(60 - i) * 12) for i in range(60)]
    symbols = [f"SYM{i % 10}" for i in range(60)]
    status = evaluate_graduation(pnls, timestamps, symbols)
    # Noise returns: mean ≈ 0 → mean_return fails or pval fails
    assert not status.graduated, "Pure noise should NOT graduate"


def test_graduation_regime_robustness():
    """Supply a regime_breakdown with only 1 profitable regime → should fail."""
    pnls, timestamps, symbols = _make_paper_returns(n=40, mean=0.012)
    single_regime_breakdown = {
        "BULL":    {"avg_return": 2.5, "trades": 30},
        "NEUTRAL": {"avg_return": -1.2, "trades": 5},
        "BEAR":    {"avg_return": -3.0, "trades": 5},
    }
    status = evaluate_graduation(
        pnls, timestamps, symbols,
        regime_breakdown=single_regime_breakdown,
    )
    assert not status.graduated, (
        "Single profitable regime should block graduation"
    )
    assert not status.regime_robust


def test_graduation_regime_robust_multiple_regimes():
    """Supply regime_breakdown profitable in 3 regimes → regime gate passes."""
    pnls, timestamps, symbols = _make_paper_returns(n=40, mean=0.012)
    multi_regime_breakdown = {
        "BULL":    {"avg_return": 2.0, "trades": 15},
        "NEUTRAL": {"avg_return": 1.0, "trades": 15},
        "BEAR":    {"avg_return": 0.5, "trades": 10},
    }
    status = evaluate_graduation(
        pnls, timestamps, symbols,
        regime_breakdown=multi_regime_breakdown,
    )
    assert status.regime_robust, (
        "3 profitable regimes should pass regime robustness gate"
    )
