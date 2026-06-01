"""GARCH(1,1) volatility model with shadow-mode sizing integration.

σ²_t = ω + α·ε²_{t-1} + β·σ²_{t-1}

References:
  - Jansen Ch.9: Time-Series Models for Volatility Forecasts
  - Chan Ch.6: Money and Risk Management (Kelly + vol regime)
  - Bollerslev (1986): "Generalized Autoregressive Conditional Heteroskedasticity"
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import numpy as np

logger = logging.getLogger("screener")

# ── Feature flag ────────────────────────────────────────────────────────
GARCH_ENABLED: bool = os.environ.get("GARCH_ENABLED", "true").strip().lower() in (
    "1", "true", "yes", "on",
)

# ── Constants ────────────────────────────────────────────────────────────
GARCH_LOOKBACK: int = 252          # training window (trading days)
GARCH_FORECAST_HORIZON: int = 5    # forecast horizon (trading days)
VOL_REGIME_THRESHOLDS: dict[str, float] = {
    "LOW": 0.15,      # annualised vol < 15% → LOW
    "HIGH": 0.35,     # annualised vol > 35% → HIGH
}
# Sizing multipliers per vol regime (additive to the shadow pattern)
VOL_REGIME_MULTIPLIERS: dict[str, float] = {
    "LOW": 1.15,      # calm markets → increase exposure modestly
    "MEDIUM": 1.0,    # normal → no adjustment
    "HIGH": 0.75,     # turbulent → reduce exposure for safety
}


# ═══════════════════════════════════════════════════════════════════════════
# GARCH(1,1) core
# ═══════════════════════════════════════════════════════════════════════════

class GARCHVolatility:
    """Fit a GARCH(1,1) model and forecast conditional volatility.

    Caches fitted parameters per symbol so repeated calls inside the same
    trading session are cheap.
    """

    __slots__ = ("lookback", "_cache")

    def __init__(self, lookback: int = GARCH_LOOKBACK) -> None:
        self.lookback = lookback
        # {symbol: (omega, alpha, beta, last_sigma2, last_return)}
        self._cache: dict[str, tuple[float, float, float, float, float]] = {}

    # ── data helpers ──────────────────────────────────────────────────

    def _get_returns(self, symbol: str) -> np.ndarray:
        """Pull prices from market_data and compute demeaned log-returns."""
        from app.services.market_data import fetch as fetch_market_data

        df = fetch_market_data(symbol, period=f"{self.lookback + 5}d")
        if df is None or df.empty:
            raise ValueError(f"No market data for {symbol}")

        col_map = {c.lower(): c for c in df.columns}
        close_col = col_map.get("close") or col_map.get("adjclose")
        if close_col is None:
            raise KeyError(f"No close/adjclose column for {symbol}")

        prices = df[close_col].dropna().values[-self.lookback:]
        if len(prices) < 50:
            raise ValueError(f"Only {len(prices)} obs for {symbol}; need ≥50")

        log_ret = np.diff(np.log(prices))
        return log_ret - log_ret.mean()  # demeaned

    # ── MLE via scipy ─────────────────────────────────────────────────

    @staticmethod
    def _nll(params: np.ndarray, returns: np.ndarray) -> float:
        """Negative log-likelihood for GARCH(1,1)."""
        omega, alpha, beta = params
        if omega <= 0 or alpha <= 0 or beta <= 0 or alpha + beta >= 1:
            return 1e10

        n = len(returns)
        sigma2 = np.empty(n, dtype=np.float64)
        sigma2[0] = np.var(returns).item()  # initial variance

        for t in range(1, n):
            sigma2[t] = omega + alpha * returns[t - 1] ** 2 + beta * sigma2[t - 1]

        mask = sigma2 > 0
        if not mask.all():
            return 1e10

        log_lik = -0.5 * np.sum(
            np.log(2 * np.pi * sigma2[mask]) + returns[mask] ** 2 / sigma2[mask]
        )
        return -log_lik

    def fit(self, symbol: str) -> tuple[float, float, float]:
        """Estimate (omega, alpha, beta) via MLE.

        Raises
        ------
        ValueError
            If market data is unavailable or insufficient.
        RuntimeError
            If the optimiser fails to converge.
        """
        from scipy.optimize import minimize

        returns = self._get_returns(symbol)

        result = minimize(
            self._nll,
            x0=np.array([1e-6, 0.1, 0.8]),
            args=(returns,),
            method="L-BFGS-B",
            bounds=[(1e-8, 1.0), (1e-8, 0.5), (1e-8, 0.99)],
            options={"maxiter": 500},
        )

        if not result.success:
            raise RuntimeError(
                f"GARCH fit failed for {symbol}: {result.message}"
            )

        omega, alpha, beta = result.x

        # Walk forward to get the last conditional variance
        n = len(returns)
        sigma2 = np.var(returns).item()
        for t in range(1, n):
            sigma2 = omega + alpha * returns[t - 1] ** 2 + beta * sigma2

        self._cache[symbol] = (
            float(omega),
            float(alpha),
            float(beta),
            float(sigma2),
            float(returns[-1]),
        )
        return self._cache[symbol][:3]

    # ── forecasting ───────────────────────────────────────────────────

    def forecast(self, symbol: str, horizon: int = GARCH_FORECAST_HORIZON) -> float:
        """Forecast annualised volatility *horizon* days ahead."""
        if symbol not in self._cache:
            self.fit(symbol)

        omega, alpha, beta, sigma2_last, _last_ret = self._cache[symbol]
        persistence = alpha + beta
        unconditional = (
            omega / (1.0 - persistence) if persistence < 1.0 else sigma2_last
        )

        sigma2_fwd = unconditional + (persistence ** horizon) * (
            sigma2_last - unconditional
        )
        daily_vol = float(np.sqrt(max(sigma2_fwd, 1e-12)))
        return daily_vol * np.sqrt(252)

    def get_current(self, symbol: str) -> float:
        """Current GARCH conditional volatility (annualised)."""
        return self.forecast(symbol, horizon=1)

    def vol_regime(self, symbol: str) -> str:
        """Classify current volatility regime: LOW / MEDIUM / HIGH."""
        vol = self.get_current(symbol)
        if vol < VOL_REGIME_THRESHOLDS["LOW"]:
            return "LOW"
        if vol > VOL_REGIME_THRESHOLDS["HIGH"]:
            return "HIGH"
        return "MEDIUM"


# ── Module-level singleton (lazy initialised) ────────────────────────────
_garch: Optional[GARCHVolatility] = None


def _ensure_garch() -> GARCHVolatility:
    global _garch
    if _garch is None:
        _garch = GARCHVolatility(lookback=GARCH_LOOKBACK)
    return _garch


# ═══════════════════════════════════════════════════════════════════════════
# Public API — consumed by risk_manager.py (shadow-mode pattern)
# ═══════════════════════════════════════════════════════════════════════════

def garch_vol_multiplier(symbol: str) -> float:
    """Return the position-size multiplier for *symbol*'s current vol regime.

    Returns 1.0 on any failure (silent degradation).
    """
    if not GARCH_ENABLED:
        return 1.0
    try:
        regime = _ensure_garch().vol_regime(symbol)
        mult = VOL_REGIME_MULTIPLIERS.get(regime, 1.0)
        logger.debug(
            "garch_vol: %s regime=%s multiplier=%.2f", symbol, regime, mult
        )
        return mult
    except Exception as exc:
        logger.debug("garch_vol: skipped for %s — %s", symbol, exc)
        return 1.0


def get_garch_volatility(symbol: str) -> float:
    """Quick-access: current GARCH annualised volatility for *symbol*."""
    try:
        return _ensure_garch().get_current(symbol)
    except Exception as exc:
        logger.debug("garch_vol: get failed for %s — %s", symbol, exc)
        return 0.0
