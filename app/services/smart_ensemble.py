"""Smart Ensemble with Governance — multi-model consensus with accountability.

V4 Upgrade — adds:
  1.  Regime-conditional weighting (higher weight to regime-appropriate models)
  2.  Confidence calibration (isotonic regression on OOS predictions)
  3.  Per-model accountability tracking (Sharpe, hit rate, regime breakdown)
  4.  Dynamic recalibration via recency-weighted performance decay

Weights: base_weight × regime_multiplier × recency_decay
Models failing quality gates (sharpe<0.5, hit_rate<40%, drawdown>25%) are excluded.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

from openbb_forecast.models.regime_engine import MarketRegime, RegimeEngine

logger = logging.getLogger("screener")

_MODEL_REGISTRY_DIR = Path("model_registry")
_TOP_K = 5
_STRONG_THRESHOLD = 0.65

# Quality gate thresholds
_MIN_SHARPE = 0.5
_MIN_HIT_RATE = 40.0   # %
_MAX_DRAWDOWN = 25.0    # %

# Recency decay factor (per trade)
_RECENCY_DECAY = 0.97

# Regime-strategy mapping
REGIME_STRATEGY_MAP = {
    "low_vol_bull":    "trend_following",
    "high_vol_bull":   "trend_following",
    "low_vol_bear":    "short_trend",
    "high_vol_bear":   "mean_reversion",
    "sideways":        "mean_reversion",
}

# Model strategy affinity (which strategy family each model belongs to)
_MODEL_STRATEGY = {
    "turtle":                   "trend_following",
    "moving_average":            "trend_following",
    "evolution_strategy":        "trend_following",
    "neuro_evolution_novelty":   "mean_reversion",
    "dqn":                       "mean_reversion",
    "ppo":                       "mean_reversion",
    "sac":                       "mean_reversion",
    "ensemble":                  "trend_following",
}


# ------------------------------------------------------------------
# Confidence Calibration
# ------------------------------------------------------------------

class ConfidenceCalibrator:
    """Calibrates prediction confidence to empirical accuracy.

    Uses a simple binning approach:
      Predictions are binned by confidence; within each bin,
      accuracy = fraction of correct predictions.
      Output "calibrated confidence" = empirical accuracy for that bin.

    This corrects overconfident models and aligns confidence with reality.
    """

    def __init__(self, n_bins: int = 10):
        self.n_bins = n_bins
        self._bin_edges: np.ndarray | None = None
        self._bin_accuracies: np.ndarray | None = None
        self._fitted = False

    def fit(
        self,
        predictions: np.ndarray,
        confidences: np.ndarray,
        actuals: np.ndarray,
    ):
        """Fit calibrator: compute empirical accuracy per confidence bin.

        Args:
            predictions: Model predictions (BUY/SELL direction or regression values).
            confidences: Raw model confidence scores in [0, 1].
            actuals: Ground truth outcomes.
        """
        if len(predictions) < self.n_bins * 5:
            logger.warning("Too few samples (%d) for calibration; skipping", len(predictions))
            self._fitted = False
            return self

        predictions = np.asarray(predictions)
        confidences = np.asarray(confidences)
        actuals = np.asarray(actuals)

        # Binning of confidences
        self._bin_edges = np.linspace(0, 1, self.n_bins + 1)
        self._bin_accuracies = np.zeros(self.n_bins)

        for i in range(self.n_bins):
            mask = (confidences >= self._bin_edges[i]) & (confidences < self._bin_edges[i + 1])
            if mask.sum() > 0:
                # Accuracy: fraction where prediction direction matches actual
                correct = (np.sign(predictions[mask]) == np.sign(actuals[mask]))
                self._bin_accuracies[i] = float(correct.mean())
            else:
                self._bin_accuracies[i] = float(self._bin_edges[i] + 0.05)

        self._fitted = True
        logger.info("Calibrator fitted: %d bins, acc range [%.2f, %.2f]",
                    self.n_bins, self._bin_accuracies.min(), self._bin_accuracies.max())
        return self

    def calibrate(self, confidence: float) -> float:
        """Return calibrated confidence for a raw confidence score.

        Args:
            confidence: Raw confidence in [0, 1].

        Returns:
            Calibrated confidence in [0, 1].
        """
        if not self._fitted or self._bin_edges is None or self._bin_accuracies is None:
            return confidence

        # Find bin
        bin_idx = min(self.n_bins - 1, max(0, int(confidence * self.n_bins)))
        return float(self._bin_accuracies[bin_idx])

    def calibrate_array(self, confidences: np.ndarray) -> np.ndarray:
        """Calibrate an array of confidence values."""
        if not self._fitted:
            return confidences
        return np.array([self.calibrate(c) for c in confidences])


# ------------------------------------------------------------------
# Model Governance
# ------------------------------------------------------------------

class ModelGovernance:
    """Per-model accountability, regime-aware weights, and dynamic recalibration."""

    def __init__(self):
        self._regime_engine: RegimeEngine | None = None
        self._calibrator: ConfidenceCalibrator = ConfidenceCalibrator()
        self._trade_history: dict[str, list[dict]] = defaultdict(list)

    @property
    def regime_engine(self) -> RegimeEngine:
        if self._regime_engine is None:
            self._regime_engine = RegimeEngine()
        return self._regime_engine

    def get_regime(self, prices: np.ndarray | None = None) -> MarketRegime:
        """Get current market regime.

        Args:
            prices: Optional price data array. Uses cached regime if None.

        Returns:
            MarketRegime enum.
        """
        if prices is not None and len(prices) > 50:
            import pandas as pd
            self.regime_engine.fit(pd.Series(prices.ravel()))
            return self.regime_engine.predict_latest(pd.Series(prices.ravel()))
        return MarketRegime.LOW_VOL_BULL

    def regime_multiplier(self, model_name: str, regime: MarketRegime) -> float:
        """How much to weight a model in the current regime.

        Models get higher weight when the regime matches their strategy affinity.

        Args:
            model_name: Name of the model.
            regime: Current market regime.

        Returns:
            Multiplier in [0.3, 2.0].
        """
        model_strategy = _MODEL_STRATEGY.get(model_name, "trend_following")
        preferred = regime.preferred_strategy

        if model_strategy == preferred:
            return 1.5  # Bonus for regime alignment
        elif regime.is_high_vol and model_strategy == "mean_reversion":
            return 1.3  # Mean reversion shines in high vol
        elif regime == MarketRegime.SIDEWAYS and model_strategy == "mean_reversion":
            return 1.5
        elif regime.is_bull and model_strategy == "trend_following":
            return 1.2
        return 0.8  # Mild penalty for mismatch

    def recency_weight(self, trade_count: int, recency_decay: float = _RECENCY_DECAY) -> float:
        """Apply recency decay to base weight.

        More recent trades → higher weight.
        For models with no trades, use neutral weight.

        Args:
            trade_count: Number of recent trades for this model.
            recency_decay: Decay factor per trade age step.

        Returns:
            Weight multiplier in [0.3, 1.0].
        """
        if trade_count <= 0:
            return 1.0  # No trade data → no penalty
        # Sharp decay for stale models, mild for active
        return max(0.3, recency_decay ** max(0, 10 - trade_count))

    def record_trade(self, model_name: str, pnl: float, regime: MarketRegime | None = None):
        """Record a trade outcome for accountability.

        Args:
            model_name: Model that generated the signal.
            pnl: Realized PnL (positive = win).
            regime: Market regime at trade time.
        """
        self._trade_history[model_name].append({
            "pnl": pnl,
            "win": pnl > 0,
            "regime": regime.label if regime else "unknown",
        })
        # Keep last 100 trades
        if len(self._trade_history[model_name]) > 100:
            self._trade_history[model_name] = self._trade_history[model_name][-100:]

    def model_diagnostics(self, model_name: str) -> dict[str, Any]:
        """Compute per-model accountability metrics.

        Args:
            model_name: Model name.

        Returns:
            Dict with sharpe, hit_rate, win_rate, avg_return, n_trades, regime_breakdown.
        """
        trades = self._trade_history.get(model_name, [])
        if len(trades) < 5:
            wins = sum(1 for t in trades if t["win"])
            return {
                "model": model_name,
                "sharpe": 0.0,
                "hit_rate": wins / max(len(trades), 1) * 100 if trades else 0,
                "win_rate": wins / max(len(trades), 1) * 100 if trades else 0,
                "avg_return": float(np.mean([t["pnl"] for t in trades])) if trades else 0.0,
                "n_trades": len(trades),
                "regime_breakdown": {},
                "active": True,
            }

        pnls = [t["pnl"] for t in trades]
        wins = sum(1 for t in trades if t["win"])
        avg_return = float(np.mean(pnls))
        std_return = float(np.std(pnls)) if len(pnls) > 1 else 1e-6
        sharpe = avg_return / max(std_return, 1e-6)

        # Regime breakdown
        regime_trades = defaultdict(list)
        for t in trades:
            regime_trades[t.get("regime", "unknown")].append(t["pnl"])

        regime_breakdown = {}
        for reg, r_pnls in regime_trades.items():
            regime_breakdown[reg] = {
                "n": len(r_pnls),
                "avg_return": float(np.mean(r_pnls)),
                "win_rate": float(sum(1 for p in r_pnls if p > 0) / len(r_pnls)),
            }

        return {
            "model": model_name,
            "sharpe": round(sharpe, 4),
            "hit_rate": round(wins / len(trades) * 100, 1),
            "win_rate": round(wins / len(trades) * 100, 1),
            "avg_return": round(avg_return, 6),
            "n_trades": len(trades),
            "regime_breakdown": regime_breakdown,
            "active": True,
        }


# ------------------------------------------------------------------
# Core loading & weighting
# ------------------------------------------------------------------

_governance = ModelGovernance()


def _load_all_models(regime: MarketRegime | None = None) -> dict[str, dict[str, Any]]:
    """Load all production models from registry with governance-aware weights.

    Args:
        regime: Current market regime (optional). If provided, weights are
                adjusted for regime alignment.

    Returns:
        Dict of {model_name: {weight, sharpe, win_rate, ...}}.
    """
    models = {}
    reg_dir = _MODEL_REGISTRY_DIR
    if not reg_dir.exists():
        return models

    for model_dir in reg_dir.iterdir():
        if not model_dir.is_dir():
            continue
        prod_file = model_dir / "production.json"
        if not prod_file.exists():
            continue
        try:
            data = json.loads(prod_file.read_text())
            metrics = data.get("metrics", {})

            sharpe = float(metrics.get("sharpe", metrics.get("Sharpe Ratio", 0)))
            win_rate = float(metrics.get("win_rate", metrics.get("Win Rate %", 50.0)))
            max_dd = float(metrics.get("max_drawdown", metrics.get("Max Drawdown %", 15.0)))

            # Quality gates
            if sharpe < _MIN_SHARPE:
                logger.debug("Excluding %s: Sharpe %.2f < %.2f", model_dir.name, sharpe, _MIN_SHARPE)
                continue
            if win_rate < _MIN_HIT_RATE:
                logger.debug("Excluding %s: HitRate %.0f%% < %.0f%%", model_dir.name, win_rate, _MIN_HIT_RATE)
                continue
            if max_dd > _MAX_DRAWDOWN:
                logger.debug("Excluding %s: MaxDD %.1f%% > %.1f%%", model_dir.name, max_dd, _MAX_DRAWDOWN)
                continue

            # Base weight
            dd_penalty = max(0.1, 1.0 - abs(max_dd) / 100.0)
            win_rate_norm = max(0.3, win_rate / 100.0)
            base_weight = round(sharpe * win_rate_norm * dd_penalty, 4)

            # Apply governance adjustments
            name = model_dir.name

            # Regime multiplier
            if regime is not None:
                regime_mult = _governance.regime_multiplier(name, regime)
            else:
                regime_mult = 1.0

            # Recency decay
            diag = _governance.model_diagnostics(name)
            recency = _governance.recency_weight(diag["n_trades"])

            # Combined weight
            weight = base_weight * regime_mult * recency

            data["name"] = name
            data["sharpe"] = sharpe
            data["win_rate"] = win_rate
            data["max_drawdown"] = max_dd
            data["weight"] = max(weight, 0.001)
            data["base_weight"] = base_weight
            data["regime_multiplier"] = regime_mult
            data["recency_multiplier"] = recency
            data["quality_passed"] = True
            data["diagnostics"] = diag
            models[name] = data
        except Exception as exc:
            logger.debug("Failed to load model %s: %s", model_dir.name, exc)
    return models


def get_model_weights(regime: MarketRegime | None = None) -> dict[str, float]:
    """Return {model_name: weight} for all registered production models.

    Args:
        regime: Optional market regime for regime-conditional weighting.
    """
    models = _load_all_models(regime=regime)
    return {name: m["weight"] for name, m in models.items()}


def get_top_models(n: int = _TOP_K, regime: MarketRegime | None = None) -> list[dict[str, Any]]:
    """Return top n models sorted by weight descending.

    Args:
        n: Number of top models to return.
        regime: Optional market regime for regime-conditional weighting.
    """
    models = list(_load_all_models(regime=regime).values())
    models.sort(key=lambda m: m["weight"], reverse=True)
    return models[:n]


def get_governance_report() -> list[dict[str, Any]]:
    """Return governance diagnostics for all models."""
    models = _load_all_models()
    report = []
    for name, m in models.items():
        diag = _governance.model_diagnostics(name)
        report.append({
            "model": name,
            "base_weight": m["weight"],
            "sharpe": m["sharpe"],
            "win_rate": m["win_rate"],
            "live_sharpe": diag["sharpe"],
            "live_hit_rate": diag["hit_rate"],
            "n_trades": diag["n_trades"],
            "regime_breakdown": diag["regime_breakdown"],
        })
    return sorted(report, key=lambda x: x["base_weight"], reverse=True)


# ------------------------------------------------------------------
# Consensus computation
# ------------------------------------------------------------------

_TOOL_TO_MODEL = {
    "Halal Screener": "turtle",
    "USX Pro": "moving_average",
    "Backtest 2Y": "evolution_strategy",
    "Monte Carlo": "neuro_evolution_novelty",
    "Bollinger Bands": "moving_average",
    "EMA Alignment": "turtle",
    "XGBoost": "evolution_strategy",
    "Momentum": "turtle",
    "Volume-Price": "moving_average",
    "LSTM": "neuro_evolution_novelty",
    "Transformer": "neuro_evolution_novelty",
    "Ensemble": "evolution_strategy",
    "DQN": "neuro_evolution_novelty",
    "PolicyGrad": "evolution_strategy",
}


def weighted_consensus(
    tool_votes: list[dict[str, Any]],
    model_weights: dict[str, float] | None = None,
    calibrate: bool = True,
) -> dict[str, Any]:
    """Compute weighted consensus from tool votes with calibration.

    Args:
        tool_votes: List of dicts with keys 'Tool', 'Vote' ('BUY'/'SELL'/'HOLD'/'SKIP').
        model_weights: Optional override weights. Auto-loaded if None.
        calibrate: Whether to apply confidence calibration.

    Returns:
        Dict with verdict, confidence, buy_weight, total_weight, calibrated_confidence, top_models.
    """
    if model_weights is None:
        top = get_top_models(_TOP_K)
        model_weights = {m["name"]: m["weight"] for m in top}

    buy_weight = 0.0
    sell_weight = 0.0
    total_weight = 0.0
    used_tools = []
    raw_confidences = []

    for vote in tool_votes:
        tool = vote.get("Tool", "")
        vote_str = vote.get("Vote", "HOLD")
        if vote_str in ("SKIP", "-", "ERROR"):
            continue

        model_name = _TOOL_TO_MODEL.get(tool)
        weight = model_weights.get(model_name, 1.0) if model_name else 1.0
        total_weight += weight

        if vote_str == "BUY":
            buy_weight += weight
            raw_confidences.append(1.0)
            used_tools.append({"tool": tool, "vote": vote_str, "weight": weight})
        elif vote_str == "SELL":
            sell_weight += weight
            raw_confidences.append(1.0)
            used_tools.append({"tool": tool, "vote": vote_str, "weight": weight})
        else:
            raw_confidences.append(0.5)
            used_tools.append({"tool": tool, "vote": vote_str, "weight": weight})

    if total_weight == 0:
        return {
            "verdict": "NEUTRAL",
            "confidence": 0.0,
            "calibrated_confidence": 0.0,
            "buy_weight": 0.0,
            "sell_weight": 0.0,
            "total_weight": 0.0,
            "buy_ratio": 0.0,
            "top_models": list(model_weights.keys()),
            "used_tools": [],
        }

    buy_ratio = buy_weight / total_weight
    sell_ratio = sell_weight / total_weight

    # Raw confidence
    raw_confidence = max(buy_ratio, sell_ratio)

    # Calibrate confidence
    calibrated = raw_confidence
    if calibrate and raw_confidences:
        calibrated = _governance._calibrator.calibrate(raw_confidence)

    # Verdict
    if buy_ratio >= _STRONG_THRESHOLD:
        verdict = "STRONG BUY"
    elif buy_ratio >= 0.50:
        verdict = "BUY"
    elif sell_ratio >= 0.50:
        verdict = "SELL"
    else:
        verdict = "NEUTRAL"

    return {
        "verdict": verdict,
        "confidence": round(raw_confidence * 100, 1),
        "calibrated_confidence": round(calibrated * 100, 1),
        "buy_weight": round(buy_weight, 4),
        "sell_weight": round(sell_weight, 4),
        "total_weight": round(total_weight, 4),
        "buy_ratio": round(buy_ratio, 4),
        "sell_ratio": round(sell_ratio, 4),
        "top_models": list(model_weights.keys()),
        "used_tools": used_tools,
    }


# ------------------------------------------------------------------
# Dynamic weight updates
# ------------------------------------------------------------------

def update_weights_from_trades(trade_results: list[dict]) -> None:
    """Update model weights based on live/paper trade results.

    Uses governance's recency-weighted performance tracking.
    Updates both the in-memory trade history and persisted production.json.

    Args:
        trade_results: List of dicts with 'model_used' and 'pnl_pct'.
    """
    if not trade_results:
        return

    # Record trades to governance
    for trade in trade_results[-30:]:
        model = trade.get("model_used", "")
        pnl = trade.get("pnl_pct", 0)
        if model:
            _governance.record_trade(model, pnl)

    # Update persisted weights
    models = _load_all_models()
    for name, m in models.items():
        diag = _governance.model_diagnostics(name)
        trades = _governance._trade_history.get(name, [])

        if len(trades) < 5:
            continue

        # Compute live metrics
        wins = sum(1 for t in trades if t["win"])
        win_rate = wins / len(trades) if trades else 0
        pnls = [t["pnl"] for t in trades]
        avg_return = float(np.mean(pnls)) if pnls else 0
        std_returns = float(np.std(pnls)) if len(pnls) > 1 else 1e-6
        live_sharpe = avg_return / max(std_returns, 1e-6)

        new_weight = round(live_sharpe * win_rate, 4)

        # Persist
        prod_file = _MODEL_REGISTRY_DIR / name / "production.json"
        if prod_file.exists():
            try:
                data = json.loads(prod_file.read_text())
                data["metrics"]["live_sharpe"] = round(live_sharpe, 4)
                data["metrics"]["live_win_rate"] = round(win_rate * 100, 1)
                data["metrics"]["live_avg_return"] = round(avg_return, 4)
                data["metrics"]["live_weight"] = new_weight
                prod_file.write_text(json.dumps(data, indent=2, default=str))
                logger.info(
                    "Updated %s: weight %.4f (live WR %.0f%%, Sharpe %.2f, %d trades)",
                    name, new_weight, win_rate * 100, live_sharpe, len(trades),
                )
            except Exception as exc:
                logger.debug("Failed to update %s: %s", name, exc)
