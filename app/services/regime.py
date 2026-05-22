"""Market regime detector with two-scan confirmation and DB persistence."""

from __future__ import annotations

import csv
import io
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

import httpx

from app.core.config import app_cfg

logger = logging.getLogger("screener")


@dataclass(frozen=True)
class RegimeSnapshot:
    state: Literal["BULL", "NEUTRAL", "BEAR"]
    vix: float
    vix_pctile: float
    spy_ema_slope_pct: float
    yield_spread_bps: float
    computed_at: datetime
    changed: bool


_last_confirmed: str = "NEUTRAL"
_last_candidate: str | None = None
_current_snapshot: RegimeSnapshot | None = None
_forced_state: str | None = None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _ema(values: list[float], span: int) -> list[float]:
    alpha = 2.0 / (span + 1.0)
    ema_vals = [float(values[0])]
    for idx in range(1, len(values)):
        ema_vals.append(alpha * float(values[idx]) + (1.0 - alpha) * float(ema_vals[idx - 1]))
    return ema_vals


# ---------------------------------------------------------------------------
# FRED series cache — 4-hour TTL, serves stale on network failure
# ---------------------------------------------------------------------------

_FRED_CACHE: dict[str, tuple[list[float], float]] = {}
_FRED_CACHE_TTL = 14400  # 4 hours


def _fetch_fred_series(series_id: str) -> list[float]:
    now = time.time()
    if series_id in _FRED_CACHE:
        values, expiry = _FRED_CACHE[series_id]
        if now < expiry:
            return values

    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    try:
        with httpx.Client(timeout=20) as client:
            resp = client.get(url)
            resp.raise_for_status()
        reader = csv.DictReader(io.StringIO(resp.text))
        values: list[float] = []
        for row in reader:
            raw = row.get(series_id)
            try:
                if raw and raw != ".":
                    values.append(float(raw))
            except ValueError:
                continue
        if not values:
            raise ValueError(f"No FRED values returned for {series_id}")
        _FRED_CACHE[series_id] = (values, now + _FRED_CACHE_TTL)
        return values
    except Exception as exc:
        if series_id in _FRED_CACHE:
            logger.warning("FRED fetch failed, serving stale cache for %s: %s", series_id, exc)
            return _FRED_CACHE[series_id][0]
        raise


def _fetch_spy_ema_slope_pct() -> float:
    from app.services.market_data import fetch

    df = fetch("SPY", period="5y")
    if df is None or len(df) < app_cfg.regime.spy_ema_trend + 5:
        raise ValueError("Insufficient SPY history for regime computation")
    closes = [float(value) for value in df["close"].astype(float).values]
    ema_series = _ema(closes, app_cfg.regime.spy_ema_trend)
    latest = ema_series[-1]
    prior = ema_series[-5]
    if prior == 0:
        return 0.0
    return float((latest - prior) / prior * 100.0)


def _compute_candidate(vix: float, vix_pctile: float, spy_ema_slope_pct: float, yield_spread_bps: float) -> str:
    score = 0
    if spy_ema_slope_pct > 0:
        score += 1
    if vix_pctile < app_cfg.regime.vix_halt_pct:
        score += 1
    if yield_spread_bps > app_cfg.regime.yield_spread_bear_bps:
        score += 1
    if score >= 2:
        return "BULL"
    if score == 1:
        return "NEUTRAL"
    return "BEAR"


def _persist_snapshot(snapshot: RegimeSnapshot) -> None:
    try:
        from app.db.database import SessionLocal
        from app.db.models import RegimeLog

        db = SessionLocal()
        try:
            db.add(
                RegimeLog(
                    ts_utc=snapshot.computed_at,
                    state=snapshot.state,
                    vix=snapshot.vix,
                    vix_pctile=snapshot.vix_pctile,
                    spy_slope=snapshot.spy_ema_slope_pct,
                    yield_spread=snapshot.yield_spread_bps,
                    changed=snapshot.changed,
                )
            )
            db.commit()
        finally:
            db.close()
    except Exception as exc:
        logger.warning("Failed to persist regime snapshot: %s", exc)


def _emit_change(snapshot: RegimeSnapshot) -> None:
    if not snapshot.changed:
        return
    try:
        from app.services.notify import notify

        notify(
            "regime_change",
            dedup_key=f"regime:{snapshot.state}",
            state=snapshot.state,
            vix_pctile=round(snapshot.vix_pctile, 2),
            spy_ema_slope_pct=round(snapshot.spy_ema_slope_pct, 3),
            yield_spread_bps=round(snapshot.yield_spread_bps, 2),
        )
    except Exception:
        logger.exception("Failed to emit regime change notification")


def refresh_regime() -> RegimeSnapshot:
    global _last_candidate, _last_confirmed, _current_snapshot

    if _forced_state:
        snapshot = RegimeSnapshot(
            state=_forced_state,
            vix=get_regime().vix,
            vix_pctile=get_regime().vix_pctile,
            spy_ema_slope_pct=get_regime().spy_ema_slope_pct,
            yield_spread_bps=get_regime().yield_spread_bps,
            computed_at=_utc_now(),
            changed=False,
        )
        _current_snapshot = snapshot
        return snapshot

    try:
        vix_series = _fetch_fred_series("VIXCLS")
        spread_series = _fetch_fred_series("T10Y2Y")
        vix = float(vix_series[-1])
        lookback = vix_series[-min(len(vix_series), app_cfg.regime.vix_lookback_days) :]
        vix_pctile = float(sum(1 for value in lookback if value <= vix) / max(len(lookback), 1) * 100.0)
        yield_spread_bps = float(spread_series[-1] * 100.0)
        spy_ema_slope_pct = _fetch_spy_ema_slope_pct()
    except Exception as exc:
        logger.error("Regime refresh failed, keeping last snapshot: %s", exc, exc_info=True)
        return get_regime()

    candidate = _compute_candidate(vix, vix_pctile, spy_ema_slope_pct, yield_spread_bps)
    changed = False
    if candidate == _last_confirmed:
        _last_candidate = None
    elif candidate == _last_candidate:
        _last_confirmed = candidate
        _last_candidate = None
        changed = True
    else:
        _last_candidate = candidate

    snapshot = RegimeSnapshot(
        state=_last_confirmed,
        vix=vix,
        vix_pctile=vix_pctile,
        spy_ema_slope_pct=spy_ema_slope_pct,
        yield_spread_bps=yield_spread_bps,
        computed_at=_utc_now(),
        changed=changed,
    )
    _current_snapshot = snapshot
    if snapshot.changed:
        try:
            from app.services.market_data import clear_market_data_cache

            clear_market_data_cache()
        except Exception:
            logger.exception("Failed to clear market-data cache after regime change")
    _persist_snapshot(snapshot)
    _emit_change(snapshot)
    return snapshot


def get_regime() -> RegimeSnapshot:
    global _current_snapshot
    if _current_snapshot is None:
        _current_snapshot = RegimeSnapshot(
            state=_last_confirmed,
            vix=0.0,
            vix_pctile=0.0,
            spy_ema_slope_pct=0.0,
            yield_spread_bps=0.0,
            computed_at=_utc_now(),
            changed=False,
        )
    return _current_snapshot


def force_regime(state: Literal["BULL", "NEUTRAL", "BEAR"] | None) -> RegimeSnapshot:
    """Force the current regime until cleared by the operator."""
    global _forced_state, _last_candidate, _last_confirmed, _current_snapshot

    previous = get_regime()
    if state is None:
        _forced_state = None
        _last_candidate = None
        return get_regime()

    _forced_state = state
    _last_candidate = None
    _last_confirmed = state
    snapshot = RegimeSnapshot(
        state=state,
        vix=previous.vix,
        vix_pctile=previous.vix_pctile,
        spy_ema_slope_pct=previous.spy_ema_slope_pct,
        yield_spread_bps=previous.yield_spread_bps,
        computed_at=_utc_now(),
        changed=previous.state != state,
    )
    _current_snapshot = snapshot
    _persist_snapshot(snapshot)
    _emit_change(snapshot)
    try:
        from app.services.market_data import clear_market_data_cache

        clear_market_data_cache()
    except Exception:
        logger.exception("Failed to clear market-data cache after forced regime change")
    return snapshot
