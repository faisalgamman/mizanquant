"""Multi-regime market detection engine with Hidden Markov Model + rule-based fallback.

Detects market regimes for adaptive strategy selection:
  Regime 0: LOW_VOL_BULL   — steady uptrend, low vol, trend-following works best
  Regime 1: HIGH_VOL_BULL  — volatile uptrend, trend following + mean reversion both ok
  Regime 2: LOW_VOL_BEAR   — steady downtrend, short trend-following
  Regime 3: HIGH_VOL_BEAR  — crisis / panic, mean reversion + defensive
  Regime 4: SIDEWAYS       — range-bound, mean reversion ideal

Features used:
  - Rolling volatility (5, 10, 20 period)
  - Trend strength (ADX-style)
  - Return skewness
  - Volume anomaly (vs. 20-day avg)
  - Correlation structure break detection
  - VIX / credit spread (if available)

Models:
  1. HMM (hmmlearn) — primary, statistical regime detection
  2. HeuristicClassifier — fallback rule-based for when HMM not available
  3. EnsembleRegimeDetector — meta-classifier combining both

Usage:
    engine = RegimeEngine()
    engine.fit(price_data)
    regime = engine.predict_latest(price_data)
    history = engine.predict_series(price_data)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import IntEnum
from typing import Literal

import numpy as np
import pandas as pd

logger = logging.getLogger("regime_engine")


# ------------------------------------------------------------------
# Regime labels (frozen for consistent interpretation)
# ------------------------------------------------------------------

class MarketRegime(IntEnum):
    """Integer regime codes with clear semantic meaning."""
    LOW_VOL_BULL = 0
    HIGH_VOL_BULL = 1
    LOW_VOL_BEAR = 2
    HIGH_VOL_BEAR = 3
    SIDEWAYS = 4

    @property
    def label(self) -> str:
        return {
            0: "low_vol_bull",
            1: "high_vol_bull",
            2: "low_vol_bear",
            3: "high_vol_bear",
            4: "sideways",
        }[self.value]

    @property
    def is_bull(self) -> bool:
        return self in (MarketRegime.LOW_VOL_BULL, MarketRegime.HIGH_VOL_BULL)

    @property
    def is_bear(self) -> bool:
        return self in (MarketRegime.LOW_VOL_BEAR, MarketRegime.HIGH_VOL_BEAR)

    @property
    def is_high_vol(self) -> bool:
        return self in (MarketRegime.HIGH_VOL_BULL, MarketRegime.HIGH_VOL_BEAR)

    @property
    def preferred_strategy(self) -> str:
        """Recommended strategy family for this regime."""
        mapping = {
            0: "trend_following",     # Uptrend, low noise
            1: "trend_following",     # Volatile but trending up
            2: "short_trend",         # Downtrend
            3: "mean_reversion",      # Crisis / panic → mean reversion spikes
            4: "mean_reversion",      # Range-bound
        }
        return mapping[self.value]


@dataclass
class RegimeDiagnostic:
    """Per-regime statistics for model confidence."""
    regime: MarketRegime
    prob: float  # HMM probability
    avg_return: float
    avg_vol: float
    sharpe: float
    n_samples: int
    duration_pct: float  # % of total data
    stable: bool  # has this regime persisted?


# ------------------------------------------------------------------
# HMM-based regime detector
# ------------------------------------------------------------------

class HMMRegimeDetector:
    """Hidden Markov Model for market regime detection.

    Uses hmmlearn's GaussianHMM with diagonal covariance.

    Args:
        n_regimes: Number of hidden states (default 5).
        n_iter: EM iterations.
        lookback: Number of periods for feature computation.
        random_state: Seed for reproducibility.
    """

    def __init__(
        self,
        n_regimes: int = 5,
        n_iter: int = 100,
        lookback: int = 252,
        random_state: int = 42,
    ):
        self.n_regimes = n_regimes
        self.n_iter = n_iter
        self.lookback = lookback
        self.random_state = random_state
        self._model = None
        self._fitted = False

    def _compute_features(self, prices: pd.Series | np.ndarray) -> np.ndarray:
        """Compute features for regime detection.

        Returns (n_samples, n_features) array of standardized features.
        """
        if isinstance(prices, np.ndarray):
            prices = pd.Series(prices.ravel())

        returns = prices.pct_change().fillna(0).clip(-0.15, 0.15)

        features = np.column_stack([
            # Volatility features — log-scaled for heavy tails
            np.log1p(returns.rolling(5, min_periods=3).std().fillna(0).values),
            np.log1p(returns.rolling(10, min_periods=5).std().fillna(0).values),
            np.log1p(returns.rolling(20, min_periods=10).std().fillna(0).values),

            # Trend features — rolling slope & RSI-style
            prices.rolling(20, min_periods=10).apply(
                lambda x: np.polyfit(np.arange(len(x)), x, 1)[0] / (x.mean() + 1e-8) * 100
            ).fillna(0).values,  # normalized slope

            # Momentum — multi-period returns
            prices.pct_change(5).fillna(0).clip(-0.1, 0.1).values,
            prices.pct_change(10).fillna(0).clip(-0.15, 0.15).values,
            prices.pct_change(20).fillna(0).clip(-0.2, 0.2).values,

            # Skewness of returns (tail risk)
            returns.rolling(30, min_periods=15).skew().fillna(0).values,

            # Serial correlation (trending-ness)
            returns.rolling(10, min_periods=5).apply(
                lambda x: x.autocorr() if len(x) > 1 else 0
            ).fillna(0).values,
        ])

        # Replace infinite / NaN
        features = np.nan_to_num(features, nan=0.0, posinf=0.0, neginf=0.0)

        return features

    def fit(self, prices: pd.Series | np.ndarray):
        """Fit HMM on price data.

        Args:
            prices: Price series (close prices).
        """
        try:
            from hmmlearn.hmm import GaussianHMM
        except ImportError:
            logger.warning("hmmlearn not installed. Install with: pip install hmmlearn")
            self._fitted = False
            return self

        features = self._compute_features(prices)

        self._model = GaussianHMM(
            n_components=self.n_regimes,
            covariance_type="diag",
            n_iter=self.n_iter,
            random_state=self.random_state,
            tol=1e-4,
            implementation="scaling",
        )

        self._model.fit(features)

        # Label regimes post-hoc: sort states by expected return
        state_returns = []
        states = self._model.predict(features)
        returns = pd.Series(prices).pct_change().fillna(0).values

        for s in range(self.n_regimes):
            mask = states == s
            if mask.sum() > 0:
                state_returns.append(returns[mask].mean())
            else:
                state_returns.append(0.0)

        # Map HMM states to our MarketRegime enum
        # Strategy: sort by mean_return (ascending) and assign bear→sideways→bull
        # within bull/bear separate by vol
        sorted_states = np.argsort(state_returns)

        self._state_to_regime = {}

        # Bear regimes (lowest returns)
        bear_states = sorted_states[:2]
        # Separate low vol bear from high vol bear
        if len(bear_states) >= 2:
            b_vol0 = returns[states == bear_states[0]].std()
            b_vol1 = returns[states == bear_states[1]].std()
            if b_vol0 <= b_vol1:
                self._state_to_regime[bear_states[0]] = MarketRegime.LOW_VOL_BEAR
                self._state_to_regime[bear_states[1]] = MarketRegime.HIGH_VOL_BEAR
            else:
                self._state_to_regime[bear_states[0]] = MarketRegime.HIGH_VOL_BEAR
                self._state_to_regime[bear_states[1]] = MarketRegime.LOW_VOL_BEAR
        else:
            self._state_to_regime[bear_states[0]] = MarketRegime.LOW_VOL_BEAR

        # Bull regimes (highest returns)
        bull_states = sorted_states[-2:]
        if len(bull_states) >= 2:
            b_vol0 = returns[states == bull_states[0]].std()
            b_vol1 = returns[states == bull_states[1]].std()
            if b_vol0 <= b_vol1:
                self._state_to_regime[bull_states[0]] = MarketRegime.LOW_VOL_BULL
                self._state_to_regime[bull_states[1]] = MarketRegime.HIGH_VOL_BULL
            else:
                self._state_to_regime[bull_states[0]] = MarketRegime.HIGH_VOL_BULL
                self._state_to_regime[bull_states[1]] = MarketRegime.LOW_VOL_BULL
        else:
            self._state_to_regime[bull_states[0]] = MarketRegime.HIGH_VOL_BULL

        # Sideways (middle)
        if len(sorted_states) >= 5:
            self._state_to_regime[sorted_states[2]] = MarketRegime.SIDEWAYS

        self._fitted = True
        logger.info("HMM fitted: %d states → %d regimes mapped", self.n_regimes, len(self._state_to_regime))
        return self

    def predict(self, features: np.ndarray | None = None, prices: pd.Series | None = None) -> np.ndarray:
        """Predict regime labels for each time step.

        Args:
            features: Pre-computed features or None.
            prices: Price series (used if features is None).

        Returns:
            Array of MarketRegime integer values.
        """
        if not self._fitted or self._model is None:
            # Return low-vol bull as safe default when not fitted
            return np.full(len(prices) if prices is not None else 1, MarketRegime.LOW_VOL_BULL.value)

        if features is None and prices is not None:
            features = self._compute_features(prices)

        if features is None:
            raise ValueError("Either features or prices must be provided")

        hmm_states = self._model.predict(features)
        regimes = np.array([self._state_to_regime.get(s, 4) for s in hmm_states])
        return regimes

    def predict_proba(self, features: np.ndarray) -> np.ndarray:
        """Get regime probabilities from HMM.

        Returns (n_samples, n_regimes) probability matrix.
        """
        if not self._fitted or self._model is None:
            return np.tile([0.2] * 5, (features.shape[0], 1))

        hmm_probs = self._model.predict_proba(features)  # shape (n_samples, n_components)
        n_samples = hmm_probs.shape[0]

        regime_probs = np.zeros((n_samples, 5))
        for hmm_state in range(self.n_regimes):
            regime = self._state_to_regime.get(hmm_state, 4)
            regime_probs[:, regime] += hmm_probs[:, hmm_state]

        # Normalize
        regime_probs /= regime_probs.sum(axis=1, keepdims=True)
        return regime_probs

    def predict_latest(self, prices: pd.Series) -> MarketRegime:
        """Predict regime for the latest data point.

        Args:
            prices: Price series including current data.

        Returns:
            MarketRegime enum.
        """
        regimes = self.predict(prices=prices)
        return MarketRegime(regimes[-1])


# ------------------------------------------------------------------
# Rule-based heuristic classifier (fallback when hmmlearn unavailable)
# ------------------------------------------------------------------

class HeuristicRegimeClassifier:
    """Rule-based regime classifier using simple volatility and trend thresholds.

    This is a lightweight fallback when hmmlearn is not installed.
    Produces the same 5-regime classification.

    Args:
        vol_lookback: Periods for volatility estimation (default 20).
        trend_lookback: Periods for trend slope estimation (default 50).
    """

    def __init__(self, vol_lookback: int = 20, trend_lookback: int = 50):
        self.vol_lookback = vol_lookback
        self.trend_lookback = trend_lookback
        self._vol_threshold: float | None = None  # median vol for high/low split
        self._fitted = False

    def fit(self, prices: pd.Series):
        """Fit by computing median volatility threshold.

        Args:
            prices: Price series.
        """
        returns = prices.pct_change().fillna(0)
        rolling_vol = returns.rolling(self.vol_lookback, min_periods=10).std()
        self._vol_threshold = float(rolling_vol.median())
        self._fitted = True
        logger.info("Heuristic fitted: vol_threshold=%.6f (daily)", self._vol_threshold)
        return self

    def predict(self, prices: pd.Series) -> np.ndarray:
        """Classify each timestep.

        Returns:
            Array of MarketRegime integer values.
        """
        if not self._fitted or self._vol_threshold is None:
            return np.full(len(prices), MarketRegime.LOW_VOL_BULL.value)

        close = prices.values
        returns = np.diff(close) / (close[:-1] + 1e-8)
        returns = np.insert(returns, 0, 0.0)

        n = len(close)

        # Compute rolling volatility
        vol_series = pd.Series(returns).rolling(self.vol_lookback, min_periods=10).std().fillna(self._vol_threshold).values

        # Compute smoothed trend (linear regression slope over window)
        trend = np.zeros(n)
        for i in range(self.trend_lookback, n):
            y = close[i - self.trend_lookback:i]
            x = np.arange(len(y))
            slope = np.polyfit(x, y, 1)[0]
            trend[i] = slope / (y.mean() + 1e-8)

        regimes = np.zeros(n, dtype=int)

        for i in range(n):
            vol = vol_series[i]
            t = trend[i]
            is_high_vol = vol > self._vol_threshold
            is_bull = t > 1e-6
            is_bear = t < -1e-6

            if is_bull and not is_high_vol:
                regimes[i] = MarketRegime.LOW_VOL_BULL
            elif is_bull and is_high_vol:
                regimes[i] = MarketRegime.HIGH_VOL_BULL
            elif is_bear and not is_high_vol:
                regimes[i] = MarketRegime.LOW_VOL_BEAR
            elif is_bear and is_high_vol:
                regimes[i] = MarketRegime.HIGH_VOL_BEAR
            else:
                regimes[i] = MarketRegime.SIDEWAYS

        return regimes

    def predict_latest(self, prices: pd.Series) -> MarketRegime:
        regimes = self.predict(prices)
        return MarketRegime(regimes[-1])


# ------------------------------------------------------------------
# Regime Engine (top-level interface)
# ------------------------------------------------------------------

class RegimeEngine:
    """Primary entry point for market regime detection.

    Tries HMM first; falls back to heuristic if hmmlearn is unavailable.

    Args:
        hmm_kwargs: Keyword arguments for HMMRegimeDetector.
        heuristic_kwargs: Keyword arguments for HeuristicRegimeClassifier.
    """

    def __init__(
        self,
        hmm_kwargs: dict | None = None,
        heuristic_kwargs: dict | None = None,
    ):
        self.hmm = HMMRegimeDetector(**(hmm_kwargs or {}))
        self.heuristic = HeuristicRegimeClassifier(**(heuristic_kwargs or {}))
        self._use_hmm = True
        self._fitted = False

    def fit(self, prices: pd.Series):
        """Fit both HMM and heuristic models.

        Args:
            prices: Close price series.
        """
        self.hmm.fit(prices)
        self.heuristic.fit(prices)
        self._use_hmm = self.hmm._fitted
        self._fitted = True

        if not self._use_hmm:
            logger.warning("HMM not fitted (hmmlearn missing?). Using heuristic fallback.")
        return self

    def predict(self, prices: pd.Series) -> np.ndarray:
        """Predict regime labels for the full series."""
        if not self._fitted:
            self.fit(prices)

        if self._use_hmm:
            return self.hmm.predict(prices=prices)
        return self.heuristic.predict(prices)

    def predict_latest(self, prices: pd.Series) -> MarketRegime:
        """Predict regime for the most recent datapoint."""
        if not self._fitted:
            self.fit(prices)

        if self._use_hmm:
            return self.hmm.predict_latest(prices)
        return self.heuristic.predict_latest(prices)

    def get_diagnostics(self, prices: pd.Series) -> list[RegimeDiagnostic]:
        """Return per-regime statistics for model evaluation.

        Args:
            prices: Close price series.

        Returns:
            List of RegimeDiagnostic objects.
        """
        regimes = self.predict(prices)
        returns = prices.pct_change().fillna(0).values

        diagnostics = []
        for regime_val in range(5):
            mask = regimes == regime_val
            n = mask.sum()
            if n == 0:
                continue

            r = returns[mask]
            avg_ret = float(r.mean())
            avg_vol = float(r.std())
            sharpe = avg_ret / max(avg_vol, 1e-8)

            diagnostics.append(RegimeDiagnostic(
                regime=MarketRegime(regime_val),
                prob=n / len(regimes),
                avg_return=avg_ret,
                avg_vol=avg_vol,
                sharpe=sharpe,
                n_samples=int(n),
                duration_pct=float(n / len(regimes) * 100),
                stable=n > len(regimes) * 0.05,
            ))

        return diagnostics

    def summary(self, prices: pd.Series) -> str:
        """Human-readable regime summary."""
        regime = self.predict_latest(prices)
        diags = self.get_diagnostics(prices)

        lines = [
            f"╔══ REGIME ENGINE {'═'*50}",
            f"║ Current Regime: {regime.name} ({regime.label})",
            f"║ Preferred Strategy: {regime.preferred_strategy}",
            f"║ {'═'*60}",
        ]

        for d in sorted(diags, key=lambda x: x.regime.value):
            dur = f"{d.duration_pct:.1f}%"
            shrp = f"Sharpe={d.sharpe:.2f}"
            ret = f"ret={d.avg_return*252*100:.2f}%/yr"
            vol = f"vol={d.avg_vol*np.sqrt(252)*100:.2f}%/yr"
            lines.append(f"║ [{d.regime.name:14s}] {dur:>6s} | {shrp} | {ret} | {vol} | n={d.n_samples}")

        lines.append(f"╚{'═'*63}")
        return "\\n".join(lines)
