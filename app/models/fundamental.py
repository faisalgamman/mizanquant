"""Fundamental analysis models and grade computation.

Provides FundamentalSnapshot for persistence and compute_fundamental_grade()
for on-the-fly grading from FMP key-metrics data.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import Column, Integer, Float, String, DateTime, Boolean, Index

from app.db.database import Base

logger = logging.getLogger("screener")


def _utcnow():
    return datetime.now(timezone.utc)


class FundamentalSnapshot(Base):
    """Cached fundamental snapshot with computed F-Grade (A/B/C/D/F)."""

    __tablename__ = "fundamental_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(10), nullable=False, index=True)
    grade = Column(String(2), nullable=True)  # A, B, C, D, F

    # Raw metrics used for grading (for audit/debug)
    roe = Column(Float, nullable=True)
    roa = Column(Float, nullable=True)
    debt_to_equity = Column(Float, nullable=True)
    current_ratio = Column(Float, nullable=True)
    gross_margin = Column(Float, nullable=True)
    net_margin = Column(Float, nullable=True)
    pe_ratio = Column(Float, nullable=True)
    pb_ratio = Column(Float, nullable=True)
    revenue_growth = Column(Float, nullable=True)
    earnings_growth = Column(Float, nullable=True)

    as_of = Column(DateTime, nullable=False, index=True)
    created_at = Column(DateTime, default=_utcnow)

    __table_args__ = (
        Index("ix_fs_symbol_as_of", "symbol", "as_of"),
    )


# ── Grade computation ──────────────────────────────────────────────────────

def _grader_metric(val: any, default: float = 0.0) -> float:
    """Safe float conversion for grader inputs."""
    if val is None:
        return default
    try:
        f = float(val)
        if f != f or f == float("inf") or f == float("-inf"):  # NaN / Inf
            return default
        return f
    except (TypeError, ValueError):
        return default


def compute_grade_from_metrics(metrics: dict) -> str | None:
    """Compute fundamental grade (A/B/C/D/F) from FMP key-metrics dict.

    Returns None if metrics dict is empty or insufficient data.
    """
    if not metrics:
        return None

    roe         = _grader_metric(metrics.get("roe"), 0)
    roa         = _grader_metric(metrics.get("roa"), 0)
    debt_eq     = _grader_metric(metrics.get("debtToEquity"), 999)
    net_margin  = _grader_metric(metrics.get("netProfitMargin"), 0)
    rev_growth  = _grader_metric(metrics.get("revenueGrowth"), 0)
    earn_growth = _grader_metric(metrics.get("earningsGrowth"), 0)
    pe          = _grader_metric(metrics.get("peRatio"), 0)
    pb          = _grader_metric(metrics.get("priceToBookRatio"), 0)

    # If no meaningful data at all, return None
    if roe == 0 and roa == 0 and net_margin == 0 and rev_growth == 0:
        return None

    # Grade rules — weighted composite:
    score = 0

    # ROE (max 25 points)
    if roe >= 25:
        score += 25
    elif roe >= 20:
        score += 20
    elif roe >= 12:
        score += 14
    elif roe >= 5:
        score += 8
    elif roe >= 0:
        score += 2

    # Net profit margin (max 20 points)
    if net_margin >= 20:
        score += 20
    elif net_margin >= 12:
        score += 16
    elif net_margin >= 8:
        score += 12
    elif net_margin >= 3:
        score += 6

    # Revenue growth (max 15 points)
    if rev_growth >= 20:
        score += 15
    elif rev_growth >= 10:
        score += 12
    elif rev_growth >= 5:
        score += 8
    elif rev_growth >= 0:
        score += 3

    # Earnings growth (max 15 points)
    if earn_growth >= 20:
        score += 15
    elif earn_growth >= 10:
        score += 12
    elif earn_growth >= 5:
        score += 8
    elif earn_growth >= 0:
        score += 3

    # Debt ratio (max 15 points) — lower is better
    if debt_eq >= 0:  # valid debt
        if debt_eq <= 0.5:
            score += 15
        elif debt_eq <= 1.0:
            score += 12
        elif debt_eq <= 2.0:
            score += 8
        elif debt_eq <= 3.0:
            score += 4
        elif debt_eq <= 5.0:
            score += 1

    # ROA bonus (max 10 points)
    if roa >= 15:
        score += 10
    elif roa >= 8:
        score += 7
    elif roa >= 4:
        score += 4
    elif roa >= 0:
        score += 1

    # Convert score → letter grade
    if score >= 70:
        return "A"
    elif score >= 50:
        return "B"
    elif score >= 30:
        return "C"
    elif score >= 10:
        return "D"
    else:
        return "F"


def fetch_and_grade(symbol: str) -> dict | None:
    """Fetch FMP key-metrics for symbol, compute grade, persist snapshot.

    Returns dict with at least {'grade': 'A'|'B'|'C'|'D'|'F'} or None on failure.
    """
    try:
        from app.services.fmp_client import FMPClient

        client = FMPClient()
        metrics_list = client.get_key_metrics(symbol, limit=1)
        if not metrics_list:
            logger.debug("fundamental: no FMP key-metrics for %s", symbol)
            return None

        metrics = metrics_list[0]
        grade = compute_grade_from_metrics(metrics)
        if grade is None:
            logger.debug("fundamental: insufficient metrics for %s", symbol)
            return None

        # Persist snapshot
        try:
            from app.db.database import SessionLocal

            session = SessionLocal()
            try:
                snap = FundamentalSnapshot(
                    symbol=symbol.upper(),
                    grade=grade,
                    roe=_grader_metric(metrics.get("roe")),
                    roa=_grader_metric(metrics.get("roa")),
                    debt_to_equity=_grader_metric(metrics.get("debtToEquity")),
                    current_ratio=_grader_metric(metrics.get("currentRatio")),
                    gross_margin=_grader_metric(metrics.get("grossProfitMargin")),
                    net_margin=_grader_metric(metrics.get("netProfitMargin")),
                    pe_ratio=_grader_metric(metrics.get("peRatio")),
                    pb_ratio=_grader_metric(metrics.get("priceToBookRatio")),
                    revenue_growth=_grader_metric(metrics.get("revenueGrowth")),
                    earnings_growth=_grader_metric(metrics.get("earningsGrowth")),
                    as_of=datetime.now(timezone.utc),
                )
                session.add(snap)
                session.commit()
            except Exception:
                session.rollback()
            finally:
                session.close()
        except Exception as exc:
            logger.debug("fundamental: failed to persist snapshot for %s: %s", symbol, exc)

        logger.info("fundamental: %s → grade %s (score from metrics)", symbol, grade)
        return {"grade": grade}

    except Exception as exc:
        logger.debug("fundamental: fetch_and_grade failed for %s: %s", symbol, exc)
        return None


def get_f_grade(symbol: str, max_age_hours: int = 168) -> str | None:
    """Get F-Grade for symbol — checks DB snapshot first, falls back to live fetch.

    Returns 'A', 'B', 'C', 'D', 'F' or None.
    """
    from datetime import timedelta

    try:
        from app.db.database import SessionLocal

        cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
        session = SessionLocal()
        try:
            snap = (
                session.query(FundamentalSnapshot)
                .filter(
                    FundamentalSnapshot.symbol == symbol.upper(),
                    FundamentalSnapshot.grade.isnot(None),
                    FundamentalSnapshot.as_of >= cutoff,
                )
                .order_by(FundamentalSnapshot.as_of.desc())
                .first()
            )
            if snap and snap.grade:
                return snap.grade
        finally:
            session.close()
    except Exception as exc:
        logger.debug("fundamental: DB lookup failed for %s: %s", symbol, exc)

    # Fall back to live fetch
    result = fetch_and_grade(symbol)
    if result:
        return result.get("grade")
    return None
