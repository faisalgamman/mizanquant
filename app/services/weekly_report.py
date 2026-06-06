"""Weekly swing-picks report (advisory, manual execution).

Produces the list of halal candidates the system favours for the week, each
enriched with the VALIDATED "Option-A" exit plan (fixed 15% catastrophe stop +
~20-trading-day time exit, DSR 0.985 in the 2026-06 paired backtest) and a
cap-aware position size. Every report carries the current validation status
(forward-PF + paper-trade graduation) so the reader never mistakes a
backtest-only edge for a proven live one.

This module is READ-ONLY and side-effect free: it never submits an order, never
sends Telegram, and never touches the live-trading flags. It reuses the existing
funnel (`run_pipeline` / `run_ready_to_trade`) and computes the Option-A plan
directly so the output is honest regardless of the server's SWING_EXIT_ENABLED
setting.

Sizing note: we mirror `risk_manager.calculate_position_size`'s cap-aware core
formula (min of risk-based / position-cap shares) but compute it directly and
deterministically from the report's risk_pct / max_pos_pct, rather than calling
the live sizer (which layers regime/Kelly/context state that would make an
advisory report non-reproducible).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone

import numpy as np

from app.config import settings

logger = logging.getLogger("screener")

# Verdicts that count as an actionable long candidate.
_BUY_VERDICTS = ("STRONG BUY", "BUY")


def build_option_a_plan(
    price: float,
    account: float,
    *,
    risk_pct: float | None = None,
    max_pos_pct: float | None = None,
    stop_pct: float | None = None,
    hold_days: int | None = None,
    asof: date | None = None,
) -> dict:
    """Option-A weekly-swing trade plan for one symbol.

    Args:
        price: entry reference (current price).
        account: account equity in USD.
        risk_pct: risk budget per trade as % of equity (default settings.TRADE_RISK_PCT).
        max_pos_pct: max single-position size as % of equity (default settings.MAX_POSITION_PCT).
        stop_pct: fixed catastrophe-stop distance % below entry (default settings.SWING_TRAIL_PCT=15).
        hold_days: trading-day time exit (default settings.SWING_MAX_HOLD_DAYS=20).
        asof: report date for the time-exit calendar date (default today, UTC).

    Returns:
        dict with entry, catastrophe_stop, far_take_profit, time_exit_date,
        risk_per_share, shares, position_value, risk_amount, risk_pct_realized.
        ``shares == 0`` (with a ``reason``) when the inputs cannot be sized.
    """
    risk_pct = settings.TRADE_RISK_PCT if risk_pct is None else risk_pct
    max_pos_pct = settings.MAX_POSITION_PCT if max_pos_pct is None else max_pos_pct
    stop_pct = getattr(settings, "SWING_TRAIL_PCT", 15.0) if stop_pct is None else stop_pct
    hold_days = getattr(settings, "SWING_MAX_HOLD_DAYS", 20) if hold_days is None else hold_days
    asof = asof or datetime.now(timezone.utc).date()

    entry = round(float(price), 2)
    catastrophe_stop = round(entry * (1.0 - stop_pct / 100.0), 2)
    far_take_profit = round(entry * (1.0 + 3.0 * stop_pct / 100.0), 2)
    # ~hold_days trading days ahead (skips weekends; holidays not modelled).
    time_exit_date = str(np.busday_offset(np.datetime64(asof, "D"), hold_days, roll="forward"))

    plan = {
        "entry": entry,
        "catastrophe_stop": catastrophe_stop,
        "far_take_profit": far_take_profit,
        "time_exit_date": time_exit_date,
        "hold_days": int(hold_days),
        "stop_pct": float(stop_pct),
    }

    risk_per_share = round(entry - catastrophe_stop, 4)
    if entry <= 0 or account <= 0 or risk_per_share <= 0:
        plan.update({"shares": 0, "position_value": 0.0, "risk_amount": 0.0,
                     "risk_pct_realized": 0.0, "risk_per_share": risk_per_share,
                     "reason": "invalid price/account/stop"})
        return plan

    # Cap-aware sizing: min(risk-based, position-cap). Mirrors risk_manager core.
    max_risk_dollars = account * (risk_pct / 100.0)
    max_position_value = account * (max_pos_pct / 100.0)
    shares_by_risk = int(max_risk_dollars / risk_per_share)
    shares_by_position = int(max_position_value / entry)
    shares = max(0, min(shares_by_risk, shares_by_position))

    position_value = round(shares * entry, 2)
    risk_amount = round(shares * risk_per_share, 2)
    plan.update({
        "risk_per_share": risk_per_share,
        "shares": shares,
        "position_value": position_value,
        "risk_amount": risk_amount,
        "risk_pct_realized": round((risk_amount / account) * 100.0, 3) if account else 0.0,
    })
    return plan


def _is_buy_candidate(row: dict, min_confidence: float) -> bool:
    if not row.get("Symbol"):
        return False
    if row.get("Verdict", "") not in _BUY_VERDICTS:
        return False
    try:
        return float(row.get("Confidence %", 0) or 0) >= min_confidence
    except (TypeError, ValueError):
        return False


def _resolve_funnel(funnel: str):
    """Lazily resolve the chosen funnel (import halal_screener only when needed)."""
    import halal_screener as hs
    if funnel == "ready":
        return lambda: hs.run_ready_to_trade()
    return lambda: hs.run_pipeline()


def _default_forward_pf():
    from app.services.signal_tracker import get_accuracy_report
    return get_accuracy_report(period_days=90)


def _default_graduation():
    # Measure graduation on the isolated paper-validation ledger ("PV") — the
    # simulated record of THESE weekly picks closed on the Option-A exit.
    from app.services.paper_trade_gate import paper_trade_status
    return paper_trade_status(strategy_id="PV").as_dict()


def _market_note() -> str:
    try:
        from app.services.regime import get_regime
        return f"market regime: {get_regime().state}"
    except Exception:
        return "market regime: unknown"


def build_weekly_report(
    account: float,
    *,
    top: int = 15,
    min_confidence: float = 45.0,
    funnel: str = "pipeline",
    asof: date | None = None,
    _funnel_fn=None,
    _forward_pf_fn=None,
    _graduation_fn=None,
) -> dict:
    """Run the funnel, keep BUY/STRONG BUY candidates, enrich with Option-A plans,
    and attach the validation status block. Side-effect free.

    The ``_*_fn`` hooks exist for testing (dependency injection); production uses
    the real funnel / forward-PF / graduation providers.
    """
    asof = asof or datetime.now(timezone.utc).date()
    funnel_fn = _funnel_fn or _resolve_funnel(funnel)
    forward_pf_fn = _forward_pf_fn or _default_forward_pf
    graduation_fn = _graduation_fn or _default_graduation

    try:
        rows = funnel_fn() or []
    except Exception as exc:  # never let a data hiccup crash the report
        logger.warning("weekly_report funnel failed: %s", exc)
        rows = []

    candidates = [r for r in rows if _is_buy_candidate(r, min_confidence)]
    # STRONG BUY first, then by confidence.
    candidates.sort(
        key=lambda r: (r.get("Verdict") == "STRONG BUY", float(r.get("Confidence %", 0) or 0)),
        reverse=True,
    )

    picks = []
    for r in candidates[:top]:
        plan = build_option_a_plan(float(r.get("Price", 0) or 0), account, asof=asof)
        picks.append({
            "symbol": r.get("Symbol"),
            "verdict": r.get("Verdict"),
            "confidence": float(r.get("Confidence %", 0) or 0),
            "votes": f"{r.get('Votes BUY', 0)}/{r.get('Votes SELL', 0)}/{r.get('Votes HOLD', 0)}",
            "swing_score": r.get("Swing Score", r.get("swing_score", "")),
            **plan,
        })

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "asof": str(asof),
        "account": float(account),
        "risk_pct": settings.TRADE_RISK_PCT,
        "max_pos_pct": settings.MAX_POSITION_PCT,
        "market_note": _market_note(),
        "forward_pf": _safe(forward_pf_fn),
        "graduation": _safe(graduation_fn),
        "advisory_banner": (
            "ADVISORY ONLY - manual execution. No live track record yet "
            "(see graduation). Exit policy: Option-A - fixed 15% catastrophe "
            "stop + ~20 trading-day time exit. Confirm price/liquidity on the "
            "broker before placing any order."
        ),
        "picks": picks,
    }


def _safe(fn):
    try:
        return fn()
    except Exception as exc:
        logger.debug("weekly_report status provider failed: %s", exc)
        return {"error": str(exc)}


def _forward_pf_summary(forward_pf) -> str:
    """One-line forward-PF summary from get_accuracy_report's output."""
    if isinstance(forward_pf, list) and forward_pf:
        first = forward_pf[0]
        if "Message" in first:
            return first["Message"]
        if "Error" in first:
            return f"error — {first['Error']}"
        return (f"OVERALL win {first.get('Win Rate %', 0)}% · "
                f"PF {first.get('Profit Factor', 0)} · "
                f"avg {first.get('Avg Return %', 0)}% "
                f"(n={first.get('Total Signals', 0)})")
    if isinstance(forward_pf, dict) and "error" in forward_pf:
        return f"unavailable — {forward_pf['error']}"
    return "no matured signals yet"


def format_report(report: dict) -> str:
    """Render a clean console/markdown report string."""
    lines: list[str] = []
    lines.append(f"MIZANQUANT - WEEKLY SWING PICKS | {report.get('asof')}")
    lines.append(report.get("advisory_banner", ""))
    lines.append("")

    grad = report.get("graduation", {}) or {}
    grad_state = "GRADUATED [OK]" if grad.get("graduated") else "NOT graduated [BLOCKED]"
    lines.append("Validation status:")
    lines.append(f"  Forward-PF : {_forward_pf_summary(report.get('forward_pf'))}")
    lines.append(f"  Graduation : {grad_state} — {grad.get('reason', 'n/a')}")
    lines.append(f"  {report.get('market_note', '')}")
    lines.append("")

    acct = report.get("account", 0)
    lines.append(
        f"Picks (account ${acct:,.0f} / risk {report.get('risk_pct')}% / "
        f"cap {report.get('max_pos_pct')}%):"
    )
    picks = report.get("picks", [])
    if not picks:
        lines.append("  No qualifying candidates this week (لا توصيات هذا الأسبوع).")
        return "\n".join(lines)

    hdr = (f"{'#':>2} {'Symbol':<7}{'Verdict':<11}{'Conf':>5} {'Entry':>9} "
           f"{'Stop-15%':>9} {'Exit~20d':>12} {'Shares':>7} {'Risk$':>9} {'Votes':>9}")
    lines.append(hdr)
    lines.append("-" * len(hdr))
    for i, p in enumerate(picks, 1):
        lines.append(
            f"{i:>2} {str(p['symbol']):<7}{str(p['verdict']):<11}"
            f"{p['confidence']:>4.0f}% {p['entry']:>9.2f} {p['catastrophe_stop']:>9.2f} "
            f"{p['time_exit_date']:>12} {p['shares']:>7} {p['risk_amount']:>9.2f} {p['votes']:>9}"
        )
    return "\n".join(lines)
