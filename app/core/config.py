"""Central typed configuration shared across strategy, risk, and execution code."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings

logger = logging.getLogger("screener")


class UniverseCfg(BaseModel):
    max_symbols: int = 355
    halal_only: bool = True
    min_avg_volume: int = 500_000
    min_price: float = 5.0


class IndicatorCfg(BaseModel):
    ema_fast: int = 20
    ema_slow: int = 50
    ema_trend: int = 200
    rsi_period: int = 14
    atr_period: int = 14
    macd: tuple[int, int, int] = (12, 26, 9)
    bollinger_period: int = 20
    bollinger_std: float = 2.0


class RegimeCfg(BaseModel):
    vix_halt_pct: float = 75.0
    vix_lookback_days: int = 3650
    yield_spread_bear_bps: float = -25.0
    spy_ema_trend: int = 200
    confirmation_scans: int = 2


class RiskCfg(BaseModel):
    risk_per_trade_pct: dict[str, float] = Field(
        default_factory=lambda: {"BULL": 1.0, "NEUTRAL": 0.875, "BEAR": 0.625}
    )
    max_positions: dict[str, int] = Field(
        default_factory=lambda: {"BULL": 6, "NEUTRAL": 4, "BEAR": 2}
    )
    daily_loss_limit_pct: float = 2.5
    portfolio_dd_cap_pct: float = 6.0
    portfolio_dd_window_days: int = 30
    sector_exposure_cap_pct: float = 25.0
    correlation_block_threshold: float = 0.65
    correlation_lookback_days: int = 20
    earnings_blackout_days: int = 5
    kelly_enabled_after_trades: int = 10
    max_position_pct: float = 15.0
    use_modular_guards: bool = True


class ExecutionCfg(BaseModel):
    max_notional_per_order_usd: float = 5_000.0
    bracket_required: bool = True
    trail_percent_default: float = 2.5
    trail_percent_max: float = 10.0
    order_poll_seconds: int = 30
    client_order_id_max_len: int = 48


class ThresholdCfg(BaseModel):
    min_confidence: dict[str, float] = Field(
        default_factory=lambda: {"momentum": 35.0, "reversion": 30.0, "ml": 35.0}
    )
    strong_buy_votes: int = 5
    buy_votes_margin: int = 1
    min_buy_confidence: float = 35.0
    atr_targets: dict[str, dict[str, float]] = Field(
        default_factory=lambda: {
            "base": {"sl": 1.5, "tp1": 1.5, "tp2": 2.5, "tp3": 4.0},
            "momentum": {"sl": 1.5, "tp1": 2.5, "tp2": 4.0, "tp3": 6.0},
            "reversion": {"stop_pct": 2.0, "tp2": 1.5, "tp3": 2.5},
            "ml": {"sl": 1.5, "tp2": 3.0, "tp3": 5.0},
        }
    )
    ml_tools_enabled: dict[str, bool] = Field(
        default_factory=lambda: {"lstm": True, "transformer": True, "ensemble": True}
    )


class NotifyCfg(BaseModel):
    telegram_enabled: bool = True
    critical_channel: str = ""
    dedup_window_seconds: int = 1800


class AppCfg(BaseSettings):
    universe: UniverseCfg = UniverseCfg()
    indicators: IndicatorCfg = IndicatorCfg()
    regime: RegimeCfg = RegimeCfg()
    risk: RiskCfg = RiskCfg()
    execution: ExecutionCfg = ExecutionCfg()
    thresholds: ThresholdCfg = ThresholdCfg()
    notify: NotifyCfg = NotifyCfg()
    killed: bool = False

    model_config = {
        "env_file": ".env",
        "extra": "ignore",
        "env_nested_delimiter": "__",
        "env_prefix": "APP_CFG__",
    }

    def to_redacted_dict(self) -> dict:
        return self.model_dump()


def _load_latest_calibration() -> dict | None:
    calibration_dir = Path("calibration")
    files = sorted(calibration_dir.glob("calibration_*.json"))
    if not files:
        return None
    latest = files[-1]
    try:
        payload = json.loads(latest.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Failed to load calibration file %s: %s", latest, exc)
        return None
    thresholds = payload.get("thresholds")
    if not isinstance(thresholds, dict):
        return None
    return thresholds


app_cfg = AppCfg()

_threshold_overrides = _load_latest_calibration()
if _threshold_overrides:
    try:
        app_cfg.thresholds = ThresholdCfg(**_threshold_overrides)
        logger.info("Loaded calibrated thresholds from latest calibration file.")
    except Exception as exc:
        logger.warning("Ignoring invalid calibration thresholds: %s", exc)
