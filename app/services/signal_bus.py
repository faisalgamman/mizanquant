"""Unified trading-signal schema + signal bus.

A single canonical TradingSignal across the platform (forecasting → strategies →
consensus → backtest), replacing the ad-hoc per-module dicts. It carries:
  - a correlation_id for END-TO-END LINEAGE (screener → forecast → strategy →
    risk → guard → alert → trade), and
  - optional forecast fields so a strategy/consensus signal records whether the
    forecasting layer agreed with it.

persist() writes a row to the signal_history table (details JSON holds the
forecast + lineage fields). Designed to be additive and never raise.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from typing import Optional


def new_correlation_id() -> str:
    """Mint a fresh lineage id."""
    return str(uuid.uuid4())


@dataclass
class TradingSignal:
    """Canonical signal shared across modules."""

    symbol: str
    signal: str                       # "STRONG BUY" | "BUY" | "SELL" | "HOLD"
    signal_type: str = "consensus"    # "swing" | "usx" | "consensus" | "pairs" | "forecast"
    score: float = 0.0
    price: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    confidence: float = 0.0

    # Forecast linkage (shadow — does not change votes/decisions)
    forecast_direction: Optional[str] = None      # "up" | "down" | "neutral"
    forecast_confidence: Optional[float] = None
    forecast_agrees: Optional[bool] = None        # does forecast agree with the signal side?

    # Context lineage
    source_module: str = ""
    correlation_id: str = field(default_factory=new_correlation_id)
    regime: Optional[str] = None
    risk_posture: Optional[str] = None
    extra: dict = field(default_factory=dict)
    # Phase 0: scanner component breakdown for future attribution
    breakdown: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    def is_buy(self) -> bool:
        return "BUY" in (self.signal or "").upper()

    def persist(self) -> Optional[int]:
        """Write to signal_history. Returns row id, or None on failure."""
        try:
            from app.db.database import SessionLocal
            from app.db.models import SignalHistory
            db = SessionLocal()
            try:
                _det = {
                    "forecast_direction":  self.forecast_direction,
                    "forecast_confidence": self.forecast_confidence,
                    "forecast_agrees":     self.forecast_agrees,
                    "source_module":       self.source_module,
                    "correlation_id":      self.correlation_id,
                    "regime":              self.regime,
                    "risk_posture":        self.risk_posture,
                    **(self.extra or {}),
                }
                if self.breakdown:
                    _det["breakdown"] = self.breakdown
                row = SignalHistory(
                    symbol=(self.symbol or "").upper(),
                    signal_type=self.signal_type,
                    signal=self.signal,
                    score=self.score,
                    price=self.price,
                    stop_loss=self.stop_loss,
                    take_profit=self.take_profit,
                    confidence=self.confidence,
                    details=_det,
                )
                db.add(row)
                db.commit()
                return row.id
            finally:
                db.close()
        except Exception:
            return None


def quick_forecast_direction(price: float, sma50: float, chg_1m: float) -> str:
    """Fast forecast-direction proxy from trend + 1-month momentum.

    A lightweight stand-in for the DL forecast (which trains and is too slow to
    call per-candidate inside the pipeline): trend up AND positive momentum → up,
    trend down AND negative momentum → down, otherwise neutral.
    """
    try:
        p = float(price or 0)
        s = float(sma50 or 0)
        m = float(chg_1m or 0)
    except (TypeError, ValueError):
        return "neutral"
    if p > 0 and s > 0:
        if p > s and m > 0:
            return "up"
        if p < s and m < 0:
            return "down"
    return "neutral"


def forecast_agrees_with(signal: str, direction: str) -> Optional[bool]:
    """Does a forecast direction agree with a BUY/SELL signal side?"""
    if not direction or direction == "neutral":
        return None
    s = (signal or "").upper()
    if "BUY" in s:
        return direction == "up"
    if "SELL" in s:
        return direction == "down"
    return None


def gather_lineage(correlation_id: str, scan_limit: int = 500) -> dict:
    """Reconstruct the end-to-end lineage for one signal by correlation_id.

    Walks signal_history (details JSON), alerts (column), and trade_history
    (signal_details JSON) so you can answer "why did we act on X?". JSON columns
    are filtered in Python for dialect portability (SQLite + Postgres).
    """
    out: dict = {"correlation_id": correlation_id, "signals": [], "alerts": [], "trades": []}
    if not correlation_id:
        return out
    try:
        from app.db.database import SessionLocal
        from app.db.models import SignalHistory, Alert, TradeHistory
        db = SessionLocal()
        try:
            # signal_history — correlation_id lives in details JSON
            for r in (db.query(SignalHistory)
                        .order_by(SignalHistory.created_at.desc()).limit(scan_limit).all()):
                d = r.details or {}
                if isinstance(d, dict) and d.get("correlation_id") == correlation_id:
                    out["signals"].append({
                        "id": r.id, "symbol": r.symbol, "signal": r.signal,
                        "signal_type": r.signal_type, "score": r.score,
                        "confidence": r.confidence,
                        "forecast_direction": d.get("forecast_direction"),
                        "forecast_agrees": d.get("forecast_agrees"),
                        "source_module": d.get("source_module"),
                        "regime": d.get("regime"), "risk_posture": d.get("risk_posture"),
                        "created_at": r.created_at.isoformat() if r.created_at else None,
                    })
            # alerts — correlation_id is a column
            for a in (db.query(Alert)
                        .filter(Alert.correlation_id == correlation_id)
                        .order_by(Alert.ts.desc()).all()):
                out["alerts"].append({
                    "id": a.id, "symbol": a.symbol, "alert_type": a.alert_type,
                    "signal": a.signal, "guard_passed": a.guard_passed,
                    "guard_reason": a.guard_reason, "sent": a.sent,
                    "ts": a.ts.isoformat() if a.ts else None,
                })
            # trade_history — correlation_id lives in signal_details JSON
            for t in (db.query(TradeHistory)
                        .order_by(TradeHistory.created_at.desc()).limit(scan_limit).all()):
                d = t.signal_details or {}
                if isinstance(d, dict) and d.get("correlation_id") == correlation_id:
                    out["trades"].append({
                        "id": t.id, "symbol": t.symbol, "side": t.side, "qty": t.qty,
                        "entry_price": t.entry_price, "status": t.status,
                        "pnl_pct": t.pnl_pct, "strategy_id": t.strategy_id,
                        "created_at": t.created_at.isoformat() if t.created_at else None,
                    })
            return out
        finally:
            db.close()
    except Exception:
        return out
