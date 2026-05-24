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
                row = SignalHistory(
                    symbol=(self.symbol or "").upper(),
                    signal_type=self.signal_type,
                    signal=self.signal,
                    score=self.score,
                    price=self.price,
                    stop_loss=self.stop_loss,
                    take_profit=self.take_profit,
                    confidence=self.confidence,
                    details={
                        "forecast_direction":  self.forecast_direction,
                        "forecast_confidence": self.forecast_confidence,
                        "forecast_agrees":     self.forecast_agrees,
                        "source_module":       self.source_module,
                        "correlation_id":      self.correlation_id,
                        "regime":              self.regime,
                        "risk_posture":        self.risk_posture,
                        **(self.extra or {}),
                    },
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
