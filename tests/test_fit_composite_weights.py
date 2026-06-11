"""Tests for fit_composite_weights — adoption gate + synthetic walks."""

# Import the pure functions for unit testing
from scripts.fit_composite_weights import evaluate_adoption


def test_adoption_gate_pass_all():
    """All conditions met → PASS."""
    assert evaluate_adoption(0.15, 0.05, 2.0, 1.0, 0.01, 0.8) == "PASS"


def test_adoption_gate_fail_ic():
    """v2 IC not beating baseline → KEEP_V1."""
    assert evaluate_adoption(0.04, 0.05, 2.0, 1.0, 0.01, 0.8) == "KEEP_V1"


def test_adoption_gate_fail_spread():
    """v2 spread not beating baseline → KEEP_V1."""
    assert evaluate_adoption(0.15, 0.05, 0.5, 1.0, 0.01, 0.8) == "KEEP_V1"


def test_adoption_gate_fail_pval():
    """pval too high → KEEP_V1."""
    assert evaluate_adoption(0.15, 0.05, 2.0, 1.0, 0.10, 0.8) == "KEEP_V1"


def test_adoption_gate_fail_dsr():
    """DSR too low → KEEP_V1."""
    assert evaluate_adoption(0.15, 0.05, 2.0, 1.0, 0.01, 0.3) == "KEEP_V1"


def test_adoption_gate_edge_boundary():
    """Exactly-at-boundary: pval=0.05 → KEEP_V1 (must be < 0.05)."""
    assert evaluate_adoption(0.15, 0.05, 2.0, 1.0, 0.05, 0.6) == "KEEP_V1"
    # DSR exactly 0.6 → PASS
    assert evaluate_adoption(0.15, 0.05, 2.0, 1.0, 0.049, 0.6) == "PASS"
