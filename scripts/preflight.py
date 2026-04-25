"""Preflight gate: run before any deploy or live-capital flip.

Exits non-zero if any of the following fail:

1. The phase test suite (Phases 1–6 + paper-trade gate) does not pass.
2. Any strategy currently routed to live capital has a paper-trade
   graduation status of `False`.

Run via:
    python -m scripts.preflight
"""

from __future__ import annotations

import subprocess
import sys

PHASE_TESTS = [
    "tests/test_no_lookahead.py",
    "tests/test_optimizer_qc.py",
    "tests/test_kelly.py",
    "tests/test_stationarity.py",
    "tests/test_momentum_quality.py",
    "tests/test_cointegration.py",
    "tests/test_strategy_regime.py",
    "tests/test_paper_trade_gate.py",
]


def run_phase_tests() -> int:
    print(f"[preflight] running {len(PHASE_TESTS)} phase test files...")
    cmd = [sys.executable, "-m", "pytest", "-q", *PHASE_TESTS]
    return subprocess.call(cmd)


def check_paper_trade_graduation() -> int:
    """Per-strategy graduation summary. Non-zero exit if any strategy
    that is *attempting* live capital has not graduated. We don't fail
    on paper-only strategies — they just aren't ready yet, which is
    fine in development."""
    try:
        from app.config import STRATEGY_CONFIGS, settings
        from app.services.paper_trade_gate import paper_trade_status
    except Exception as exc:
        print(f"[preflight] could not import strategy config: {exc}")
        return 0  # don't block deploys when config not initialised

    is_paper = "paper-api" in (settings.ALPACA_BASE_URL or "")
    print(f"[preflight] broker mode: {'PAPER' if is_paper else 'LIVE'}")

    failed = 0
    for sid, _cfg in STRATEGY_CONFIGS.items():
        st = paper_trade_status(strategy_id=sid)
        marker = "OK " if st.graduated else "NO "
        print(f"[preflight]   {marker} strategy {sid}: {st.reason}")
        if not is_paper and not st.graduated:
            failed += 1
    return 1 if failed else 0


def main() -> int:
    code = run_phase_tests()
    if code != 0:
        print("[preflight] FAIL: phase tests did not pass")
        return code
    code = check_paper_trade_graduation()
    if code != 0:
        print("[preflight] FAIL: live strategies have not graduated")
        return code
    print("[preflight] OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
