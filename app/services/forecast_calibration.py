"""Evidence for the Monte-Carlo forecast — is prob_profit calibrated, and is the
drift-shrink / the 55-45 "why buy" gate earned by history rather than hand-picked?

Look-ahead-safe backtest: at many historical as-of points, run the forecast on
prices UP TO that point and compare the predicted prob_profit to the ACTUAL
horizon-day-forward outcome (terminal > start). Feed the (predicted, realized)
pairs to app.services.calibration. Because it can sweep ``drift_shrink``, it also
tells us whether shrinking the drift actually improved calibration.
"""

from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger("screener")

# A small, liquid, sector-spread default universe — enough as-of points for a
# few-thousand-sample calibration without hammering the data source.
_DEFAULT_SYMBOLS = [
    "AAPL", "MSFT", "JPM", "XOM", "JNJ", "PG", "KO", "WMT", "HD", "CAT",
    "NEM", "GLD", "CVX", "MRK", "PEP", "MCD", "UNH", "LMT", "DUK", "SO",
]


def mc_calibration_backtest(
    symbols: list[str] | None = None,
    horizon: int = 20,
    step: int = 10,
    sims: int = 400,
    drift_shrink: float | None = None,
    min_history: int = 60,
) -> dict:
    """Run the calibration backtest and return the calibration report + coverage.

    For each symbol, walk history in ``step``-bar strides; at each point with
    >= min_history bars behind and >= horizon bars ahead, forecast from the past
    only and score against the realized forward move. ``drift_shrink`` overrides
    the env default so callers can compare shrink levels.
    """
    from app.services.market_data import fetch
    from app.services.price_forecast import monte_carlo_forecast
    from app.services.calibration import calibration_report

    syms = symbols or _DEFAULT_SYMBOLS
    probs: list[float] = []
    outs: list[float] = []
    n_symbols = 0

    for sym in syms:
        try:
            df = fetch(sym, period="2y")
            if df is None or len(df) < (min_history + horizon + step):
                continue
            closes = df["close"].astype(float).values
            n = len(closes)
            used = False
            for i in range(min_history, n - horizon, step):
                fc = monte_carlo_forecast(closes[: i + 1], horizon=horizon,
                                          sims=sims, drift_shrink=drift_shrink)
                if "error" in fc or fc.get("prob_profit_pct") is None:
                    continue
                probs.append(float(fc["prob_profit_pct"]) / 100.0)
                outs.append(1.0 if closes[i + horizon] > closes[i] else 0.0)
                used = True
            n_symbols += 1 if used else 0
        except Exception as exc:
            logger.debug("mc_calibration_backtest %s failed: %s", sym, exc)

    rep = calibration_report(probs, outs)
    rep["symbols_used"] = n_symbols
    rep["horizon"] = horizon
    rep["drift_shrink"] = drift_shrink
    return rep


def compare_drift_shrink(shrinks=(0.0, 0.3, 1.0), **kwargs) -> list[dict]:
    """Calibration report at several drift-shrink levels — the direct evidence for
    whether shrinking the drift improved the forecast (lower Brier/ECE, mean_pred
    closer to the base rate)."""
    out = []
    for s in shrinks:
        rep = mc_calibration_backtest(drift_shrink=s, **kwargs)
        out.append({"drift_shrink": s, "n": rep["n"], "base_rate": rep["base_rate"],
                    "mean_pred": rep["mean_pred"], "brier": rep["brier"], "ece": rep["ece"]})
    return out


__all__ = ["mc_calibration_backtest", "compare_drift_shrink"]
