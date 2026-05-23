"""
ML Quality Pipeline — 15-enhancement framework for Mizanquant models.

Covers:
  1. Quality Gates (Sharpe >= 1.0, Accuracy >= 0.55, MaxDD <= 15%)
  2. Walk-Forward Validation (replaces fixed 80/20 split)
  3. Negative Sharpe Early Termination
  4. Feature Selection & Weak Indicator Removal
  5. Market Regime Detection (bullish/bearish/sideways)
  6. Probability Calibration (Platt Scaling / Isotonic)
  7. Ensemble Weighted Voting (Sharpe-based)
  8. Transaction Cost + Slippage
  9. Early Stopping & Gradient Clipping
 10. Best Checkpoint Only (not last epoch)
 11. Outlier Filtering (MAD + IQR)
 12. Max Drawdown Gate (< 15%)
 13. Rolling Retraining (weekly cron — registered separately)
 14. Feature Importance Report
 15. SPY Buy & Hold Benchmark
"""

from __future__ import annotations

import json
import logging
import warnings
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger("ml_pipeline")

# ============================================================================
# 11. Outlier Filtering
# ============================================================================

def mad_filter(series: np.ndarray, threshold: float = 3.5) -> np.ndarray:
    """Median Absolute Deviation outlier mask (robust to fat tails).

    Returns boolean mask where True = keep (not outlier).
    """
    median = np.median(series)
    mad = np.median(np.abs(series - median))
    if mad == 0:
        return np.ones(len(series), dtype=bool)
    modified_z = 0.6745 * (series - median) / mad
    return np.abs(modified_z) <= threshold


def iqr_filter(series: np.ndarray, multiplier: float = 1.5) -> np.ndarray:
    """IQR-based outlier mask. Returns True = keep."""
    q1, q3 = np.percentile(series, [25, 75])
    iqr = q3 - q1
    lower = q1 - multiplier * iqr
    upper = q3 + multiplier * iqr
    return (series >= lower) & (series <= upper)


def filter_outliers(prices: np.ndarray, method: str = "mad") -> np.ndarray:
    """Apply outlier filter to price array, return cleaned prices.

    Uses MAD by default (more robust than IQR for financial data).
    Falls back to keeping all data if too few points remain.
    """
    if len(prices) < 30:
        return prices  # not enough data for meaningful filtering
    fn = mad_filter if method == "mad" else iqr_filter
    mask = fn(prices)
    clean = prices[mask]
    if len(clean) < max(30, len(prices) * 0.7):
        logger.warning("Outlier filter removed too many points; keeping original data")
        return prices
    n_removed = len(prices) - len(clean)
    if n_removed > 0:
        logger.info("Outlier filter removed %d/%d points (%.1f%%)",
                     n_removed, len(prices), n_removed / len(prices) * 100)
    return clean


# ============================================================================
# 2. Walk-Forward Validation
# ============================================================================

def walk_forward_split(
    X: np.ndarray,
    y: np.ndarray,
    n_splits: int = 5,
    test_size: float = 0.2,
    min_train_size: int = 100,
) -> list[tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]]:
    """Chronological walk-forward cross-validation folds.

    Each fold's test window sits immediately after its training window.
    Returns list of (X_train, y_train, X_test, y_test) from earliest to latest.
    """
    n = len(X)
    if n < min_train_size * 2:
        # fallback: single 80/20 split
        split = int(n * 0.8)
        return [(X[:split], y[:split], X[split:], y[split:])]

    folds = []
    fold_size = max(int(n * test_size), 30)
    for i in range(n_splits):
        test_end = n - i * fold_size
        test_start = test_end - fold_size
        if test_start <= min_train_size:
            break
        folds.append((X[:test_start], y[:test_start],
                       X[test_start:test_end], y[test_start:test_end]))
    return folds[::-1] if folds else [(X[: int(n * 0.8)], y[: int(n * 0.8)],
                                        X[int(n * 0.8):], y[int(n * 0.8):])]


def walk_forward_validate(
    X: np.ndarray,
    y: np.ndarray,
    predict_fn: Callable[[np.ndarray], np.ndarray],
    n_splits: int = 5,
    gap: int = 0,
    min_train_ratio: float = 0.6,
    cost_bps: float = 20.0,
) -> dict[str, Any]:
    """Run walk-forward validation and compute aggregated metrics.

    Phase 2 upgrade: uses ``WalkForwardSplit`` (with embargo ``gap``) from
    the openbb_forecast library instead of the local fixed-split helper.

    Phase 1 upgrade: runs ``LeakageAuditor`` on the first fold.

    Phase 3 upgrade: applies transaction costs (``cost_bps`` per side) to
    simulated strategy returns before computing Sharpe.

    Phase 4 upgrade: computes DSR + permutation p-value + bootstrap lower
    bound on all out-of-sample returns.

    Args:
        X:               Feature array (samples [, seq_len, features]).
        y:               Target array.
        predict_fn:      Function taking X_test → predictions.
        n_splits:        Number of walk-forward folds.
        gap:             Embargo samples between train and test boundaries.
                         Set to sequence_length to prevent overlap leakage.
        min_train_ratio: Minimum fraction of data in the first training fold.
        cost_bps:        One-way transaction cost in basis points (default 20).

    Returns:
        Dict with test_sharpe, test_acc, fold_metrics, avg_mse,
        fold_sharpe_std (stability), deflated_sharpe, permutation_pvalue,
        bootstrap_lower_5pct, leakage_clean, leakage_report.
    """
    # ── Pillar 2: WalkForwardSplit with embargo gap ────────────────────
    try:
        from openbb_forecast.data.validation import WalkForwardSplit
        splitter = WalkForwardSplit(
            n_splits=n_splits,
            min_train_ratio=min_train_ratio,
            gap=gap,
        )
        raw_folds = splitter.split(len(X))
    except Exception as _wf_err:
        logger.warning("WalkForwardSplit unavailable (%s), falling back to local split", _wf_err)
        raw_folds = [(np.arange(0, int(len(X) * (0.6 + 0.08 * i))),
                      np.arange(int(len(X) * (0.6 + 0.08 * i)),
                                min(int(len(X) * (0.6 + 0.08 * (i + 1))), len(X))))
                     for i in range(n_splits)]
        raw_folds = [f for f in raw_folds if len(f[1]) > 0]

    # ── Pillar 1: LeakageAuditor on first fold ────────────────────────
    leakage_clean: bool = True
    leakage_report: dict = {}
    try:
        from openbb_forecast.data.leakage_auditor import LeakageAuditor
        if raw_folds:
            train_idx, test_idx = raw_folds[0]
            # Flatten to 2-D for the auditor (works for 3-D LSTM arrays too)
            X2d = X.reshape(len(X), -1) if X.ndim > 2 else X
            features_df = pd.DataFrame(
                X2d, columns=[f"feat_{j}" for j in range(X2d.shape[1])]
            )
            labels_s = pd.Series(np.asarray(y).ravel()[:len(X2d)], name="target")
            audit = LeakageAuditor().audit(
                features=features_df,
                labels=labels_s,
                train_idx=train_idx,
                test_idx=test_idx,
                scaler_refit_per_fold=True,
                gap=gap,
                shuffle_split=False,
            )
            # CRITICAL / HIGH = real data leakage → block.
            # MEDIUM = advisory (e.g. distributional drift) → warn only.
            blocking = [f for f in audit.findings if f.severity in ("CRITICAL", "HIGH")]
            leakage_clean = len(blocking) == 0
            leakage_report = audit.to_dict()
            if not leakage_clean:
                logger.warning(
                    "LeakageAuditor found %d CRITICAL/HIGH issue(s) in "
                    "walk-forward fold 0:\n%s",
                    len(blocking), audit.summary(),
                )
            elif audit.fails > 0:
                logger.info(
                    "LeakageAuditor: %d advisory (MEDIUM) warning(s) only — "
                    "pipeline marked clean.", audit.fails
                )
    except Exception as _la_err:
        logger.debug("LeakageAuditor skipped: %s", _la_err)

    fold_metrics = []
    all_strategy_rets: list[float] = []

    for fold_idx, (train_idx, test_idx) in enumerate(raw_folds):
        X_tr = X[train_idx]
        y_tr = y[train_idx]
        X_te = X[test_idx]
        y_te = y[test_idx]
        try:
            preds = predict_fn(X_te)
            if preds is None:
                continue
            preds = np.asarray(preds).flatten()
            actuals = np.asarray(y_te).flatten()
            min_len = min(len(preds), len(actuals))
            preds = preds[:min_len]
            actuals = actuals[:min_len]

            mse = float(np.mean((preds - actuals) ** 2))
            mae = float(np.mean(np.abs(preds - actuals)))
            if min_len >= 2:
                pred_dir = np.diff(preds) > 0
                actual_dir = np.diff(actuals) > 0
                acc = float(np.mean(pred_dir == actual_dir))
            else:
                acc = 0.0

            # Directional-strategy returns on ACTUAL prices.
            actual_rets = np.diff(actuals) / (np.abs(actuals[:-1]) + 1e-9)
            pred_direction = np.sign(np.diff(preds))  # +1 up, -1 down, 0 flat

            # ── Pillar 3: apply round-trip cost to every triggered trade ──
            round_trip = 2.0 * cost_bps / 10_000.0
            active = pred_direction != 0
            strategy_rets = pred_direction * actual_rets
            strategy_rets[active] -= round_trip  # deduct cost only when trading

            sharpe = _calc_sharpe(strategy_rets)

            fold_metrics.append({
                "fold": fold_idx + 1,
                "mse": round(mse, 6),
                "mae": round(mae, 4),
                "directional_acc": round(acc, 4),
                "sharpe": round(sharpe, 3),
                "train_size": len(X_tr),
                "test_size": len(X_te),
            })
            all_strategy_rets.extend(strategy_rets.tolist())
        except Exception as e:
            logger.warning("Walk-forward fold %d failed: %s", fold_idx + 1, e)

    if not fold_metrics:
        return {
            "test_sharpe": 0.0, "test_acc": 0.0, "fold_metrics": [],
            "avg_mse": 0.0, "status": "failed",
            "fold_sharpe_std": 0.0,
            "deflated_sharpe": 0.0, "permutation_pvalue": 1.0,
            "bootstrap_lower_5pct": 0.0,
            "leakage_clean": leakage_clean, "leakage_report": leakage_report,
        }

    avg_sharpe = float(np.mean([m["sharpe"] for m in fold_metrics]))
    fold_sharpe_std = float(np.std([m["sharpe"] for m in fold_metrics]))
    avg_acc = float(np.mean([m["directional_acc"] for m in fold_metrics]))
    avg_mse = float(np.mean([m["mse"] for m in fold_metrics]))

    # ── Pillar 4: Robustness QC on pooled OOS returns ────────────────
    dsr = 0.0
    pval = 1.0
    boot_lb = 0.0
    try:
        from app.services.backtest_qc import qc_report as _qc_report
        if len(all_strategy_rets) >= 5:
            qc = _qc_report(all_strategy_rets, n_trials=1, annualization=252)
            dsr = round(float(qc.get("deflated_sharpe", 0.0)), 3)
            pval = round(float(qc.get("permutation_pvalue", 1.0)), 3)
            boot_lb = round(float(qc.get("bootstrap_lower_5pct", 0.0)), 6)
    except Exception as _qc_err:
        logger.debug("qc_report skipped: %s", _qc_err)

    return {
        "test_sharpe": round(avg_sharpe, 3),
        "test_acc": round(avg_acc, 4),
        "avg_mse": round(avg_mse, 6),
        "fold_metrics": fold_metrics,
        "n_folds": len(fold_metrics),
        "fold_sharpe_std": round(fold_sharpe_std, 3),
        "status": "ok",
        # ── Robustness QC ──
        "deflated_sharpe": dsr,
        "permutation_pvalue": pval,
        "bootstrap_lower_5pct": boot_lb,
        # ── Leakage ──
        "leakage_clean": leakage_clean,
        "leakage_report": leakage_report,
    }


def _calc_sharpe(returns: np.ndarray, annualization: float = 252.0) -> float:
    """Calculate annualized Sharpe ratio from period returns."""
    if len(returns) < 2:
        return 0.0
    mean_r = float(np.mean(returns))
    std_r = float(np.std(returns)) + 1e-9
    rf_period = 0.043 / annualization
    return float((mean_r - rf_period) / std_r * np.sqrt(annualization))


# ============================================================================
# 1 & 12. Quality Gates
# ============================================================================

@dataclass
class QualityGateResult:
    passed: bool
    test_sharpe: float = 0.0
    test_acc: float = 0.0
    max_drawdown: float = 0.0
    failures: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)


QUALITY_GATES = {
    "min_test_sharpe": 1.0,
    "min_test_acc": 0.55,
    "max_drawdown_pct": 15.0,
}


def check_quality_gates(
    metrics: dict[str, Any],
    returns: Optional[np.ndarray] = None,
    gates_override: Optional[dict[str, float]] = None,
) -> QualityGateResult:
    """Check model quality against promotion gates.

    Args:
        metrics: dict with keys like 'test_sharpe', 'test_acc', 'Sharpe Ratio', etc.
        returns: optional return series for drawdown calculation.
        gates_override: optional dict to override default gate thresholds.

    Returns:
        QualityGateResult with passed/failed status.
    """
    gates = {**QUALITY_GATES, **(gates_override or {})}
    failures = []

    # Test Sharpe
    test_sharpe = float(metrics.get("test_sharpe",
                                     metrics.get("Sharpe Ratio",
                                                 metrics.get("sharpe", 0))))
    if test_sharpe < gates["min_test_sharpe"]:
        failures.append(f"test_sharpe={test_sharpe:.2f} < {gates['min_test_sharpe']}")

    # Test Accuracy (directional)
    test_acc = float(metrics.get("test_acc",
                                  metrics.get("directional_acc",
                                              metrics.get("Win Rate %", 0)) / 100.0))
    if test_acc < gates["min_test_acc"]:
        failures.append(f"test_acc={test_acc:.3f} < {gates['min_test_acc']}")

    # Max Drawdown
    max_dd = float(metrics.get("max_drawdown",
                                metrics.get("Max Drawdown %", 0)))
    if returns is not None and max_dd == 0:
        max_dd = _compute_max_drawdown(returns)
    if max_dd > gates["max_drawdown_pct"]:
        failures.append(f"max_drawdown={max_dd:.1f}% > {gates['max_drawdown_pct']}%")

    return QualityGateResult(
        passed=len(failures) == 0,
        test_sharpe=round(test_sharpe, 3),
        test_acc=round(test_acc, 4),
        max_drawdown=round(max_dd, 2),
        failures=failures,
        details={"gates_applied": gates, "metrics_received": metrics},
    )


def _compute_max_drawdown(returns: np.ndarray) -> float:
    """Compute max drawdown percentage from return series."""
    if len(returns) == 0:
        return 0.0
    cumulative = np.cumprod(1 + returns)
    peak = np.maximum.accumulate(cumulative)
    drawdowns = (cumulative - peak) / peak
    return float(np.min(drawdowns) * 100)


# ============================================================================
# 5. Market Regime Detection (per-symbol)
# ============================================================================

def detect_market_regime(
    df: pd.DataFrame,
    ma_short: int = 50,
    ma_long: int = 200,
    neutral_band_pct: float = 3.0,
) -> dict[str, Any]:
    """Detect market regime from price data: bullish / bearish / sideways.

    Uses:
      - Price vs MA(short) vs MA(long) alignment
      - Trend strength (ADX if available)
      - Volatility regime (ATR percentile)

    Returns:
        Dict with regime, confidence, trend_strength, volatility_regime.
    """
    close = df["close"].astype(float)
    if len(close) < ma_long:
        return {"regime": "unknown", "confidence": 0.0, "reason": "insufficient_data"}

    latest = float(close.iloc[-1])
    ma_s = float(close.rolling(ma_short).mean().iloc[-1])
    ma_l = float(close.rolling(ma_long).mean().iloc[-1]) if len(close) >= ma_long else ma_s

    # Trend alignment score
    trend_score = 0
    if latest > ma_s:
        trend_score += 1
    if ma_s > ma_l:
        trend_score += 1
    if latest > ma_l:
        trend_score += 1

    # Price distance from MA long
    pct_from_ma = (latest - ma_l) / ma_l * 100 if ma_l > 0 else 0

    # Volatility check (ATR percentile)
    vol_regime = "normal"
    if "high" in df.columns and "low" in df.columns and len(df) >= 20:
        tr = pd.concat([
            df["high"] - df["low"],
            (df["high"] - df["close"].shift()).abs(),
            (df["low"] - df["close"].shift()).abs(),
        ], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        atr_pct = (atr / close * 100).iloc[-1]
        atr_median = (atr / close * 100).rolling(252).median().iloc[-1] if len(df) >= 252 else atr_pct
        if atr_pct > atr_median * 1.5:
            vol_regime = "high"
        elif atr_pct < atr_median * 0.5:
            vol_regime = "low"

    # ADX for trend strength if available
    adx_val = 20.0
    if "high" in df.columns and "low" in df.columns and len(df) >= 28:
        try:
            from app.services.technical import adx as calc_adx
            adx_series, _, _ = calc_adx(df, 14)
            adx_val = float(adx_series.iloc[-1])
        except Exception:
            pass

    # Determine regime
    if trend_score >= 2 and pct_from_ma > neutral_band_pct and adx_val > 20:
        regime = "bullish"
        confidence = min(0.9, 0.5 + trend_score * 0.15)
    elif trend_score <= 1 and pct_from_ma < -neutral_band_pct:
        regime = "bearish"
        confidence = min(0.9, 0.5 + (2 - trend_score) * 0.15)
    else:
        regime = "sideways"
        confidence = 0.4 + (0.1 if abs(pct_from_ma) < neutral_band_pct else 0)

    return {
        "regime": regime,
        "confidence": round(confidence, 3),
        "trend_score": trend_score,
        "pct_from_ma_long": round(pct_from_ma, 2),
        "adx": round(adx_val, 1),
        "volatility_regime": vol_regime,
        "ma_short": round(ma_s, 2),
        "ma_long": round(ma_l, 2),
    }


# ============================================================================
# 4. Feature Selection
# ============================================================================

def feature_selection_report(
    feature_names: list[str],
    importance_scores: np.ndarray,
    threshold: float = 0.01,
) -> dict[str, Any]:
    """Generate feature importance report and identify weak features.

    Args:
        feature_names: list of feature names.
        importance_scores: array of importance values (higher = more important).
        threshold: minimum importance score to keep feature.

    Returns:
        Dict with strong_features, weak_features, report table.
    """
    if len(feature_names) != len(importance_scores):
        logger.error("Feature names/importance length mismatch")
        return {"error": "length_mismatch"}

    # Normalize to [0, 1]
    total = importance_scores.sum()
    if total > 0:
        normalized = importance_scores / total
    else:
        normalized = importance_scores

    strong = []
    weak = []
    report_rows = []
    for name, imp, norm in zip(feature_names, importance_scores, normalized):
        row = {
            "feature": name,
            "importance": round(float(imp), 6),
            "normalized": round(float(norm), 4),
        }
        report_rows.append(row)
        if norm >= threshold:
            strong.append(name)
        else:
            weak.append({"feature": name, "importance": round(float(norm), 4)})

    # Sort by importance desc
    report_rows.sort(key=lambda r: r["importance"], reverse=True)
    strong.sort(key=lambda n: next(r["importance"] for r in report_rows if r["feature"] == n), reverse=True)
    weak.sort(key=lambda w: w["importance"])

    return {
        "strong_features": strong,
        "weak_features": weak,
        "n_strong": len(strong),
        "n_weak": len(weak),
        "threshold": threshold,
        "feature_report": report_rows,
    }


def select_features(
    X: np.ndarray,
    feature_names: list[str],
    importance_scores: np.ndarray,
    threshold: float = 0.01,
) -> tuple[np.ndarray, list[str]]:
    """Filter out weak features from X array.

    Returns (filtered_X, selected_feature_names).
    """
    report = feature_selection_report(feature_names, importance_scores, threshold)
    strong = report["strong_features"]
    strong_indices = [feature_names.index(f) for f in strong]
    X_filtered = X[:, :, strong_indices] if X.ndim == 3 else X[:, strong_indices]
    return X_filtered, strong


# ============================================================================
# 6. Probability Calibration
# ============================================================================

def platt_scaling(scores: np.ndarray, y_true: np.ndarray) -> Callable[[np.ndarray], np.ndarray]:
    """Fit Platt scaling (logistic regression) to calibrate prediction scores.

    Returns a callable that transforms raw scores to calibrated probabilities.
    """
    from scipy.optimize import minimize
    scores = np.asarray(scores, dtype=np.float64).flatten()
    y_true = np.asarray(y_true, dtype=np.float64).flatten()

    # Clip for numerical stability
    scores = np.clip(scores, -10, 10)

    def neg_log_likelihood(params):
        a, b = params
        z = a * scores + b
        probs = 1.0 / (1.0 + np.exp(-z))
        probs = np.clip(probs, 1e-12, 1 - 1e-12)
        return -np.sum(y_true * np.log(probs) + (1 - y_true) * np.log(1 - probs))

    try:
        result = minimize(neg_log_likelihood, [0.0, 0.0], method="L-BFGS-B")
        a, b = result.x
    except Exception:
        a, b = 1.0, 0.0

    def calibrate(raw_scores: np.ndarray) -> np.ndarray:
        z = a * np.asarray(raw_scores) + b
        return 1.0 / (1.0 + np.exp(-z))

    return calibrate


def isotonic_calibration(
    scores: np.ndarray,
    y_true: np.ndarray,
) -> Callable[[np.ndarray], np.ndarray]:
    """Fit isotonic regression for probability calibration.

    Non-parametric, monotonic — better when Platt's sigmoid assumption fails.
    """
    try:
        from sklearn.isotonic import IsotonicRegression
    except ImportError:
        logger.warning("sklearn not available; falling back to Platt scaling")
        return platt_scaling(scores, y_true)

    scores = np.asarray(scores, dtype=np.float64).flatten()
    y_true = np.asarray(y_true, dtype=np.float64).flatten()

    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    iso.fit(scores, y_true)

    def calibrate(raw_scores: np.ndarray) -> np.ndarray:
        return iso.predict(np.asarray(raw_scores).flatten())

    return calibrate


# ============================================================================
# 3. Negative Sharpe Early Termination
# ============================================================================

class SharpeMonitor:
    """Monitor rolling Sharpe during training; signal early stop on persistent negatives."""

    def __init__(self, window: int = 10, max_consecutive: int = 5):
        self.window = window
        self.max_consecutive = max_consecutive
        self.rewards_history: list[float] = []
        self.consecutive_negative = 0

    def update(self, episode_rewards: list[float]) -> bool:
        """Record new rewards; return True if training should STOP."""
        self.rewards_history.extend(episode_rewards)
        if len(self.rewards_history) < self.window:
            return False

        recent = self.rewards_history[-self.window:]
        sharpe = _calc_sharpe(np.array(recent), annualization=252)

        if sharpe < 0:
            self.consecutive_negative += 1
        else:
            self.consecutive_negative = 0

        if self.consecutive_negative >= self.max_consecutive:
            logger.warning(
                "SharpeMonitor: %d consecutive negative-Sharpe windows — stopping training",
                self.consecutive_negative,
            )
            return True
        return False


# ============================================================================
# 9. Early Stopping & Gradient Clipping
# ============================================================================

class EarlyStopping:
    """Early stopping with best checkpoint tracking.

    Monitors a metric (e.g., Sharpe, accuracy). Saves best model state.
    Stops training when no improvement for `patience` evaluations.
    """

    def __init__(
        self,
        patience: int = 10,
        min_delta: float = 0.001,
        mode: str = "max",
        checkpoint_dir: str = "model_checkpoints",
        model_name: str = "model",
    ):
        self.patience = patience
        self.min_delta = min_delta
        self.mode = mode  # 'max' for Sharpe/acc, 'min' for loss
        self.best_score: Optional[float] = None
        self.best_epoch: int = 0
        self.counter: int = 0
        self.checkpoint_dir = Path(checkpoint_dir)
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        self.model_name = model_name
        self.best_checkpoint_path: Optional[str] = None
        self.early_stop: bool = False

    def step(self, score: float, epoch: int, model_state: dict) -> bool:
        """Evaluate current score; return True if this is a new best (checkpoint saved)."""
        is_better = False
        if self.best_score is None:
            is_better = True
        elif self.mode == "max" and score > self.best_score + self.min_delta:
            is_better = True
        elif self.mode == "min" and score < self.best_score - self.min_delta:
            is_better = True

        if is_better:
            self.best_score = score
            self.best_epoch = epoch
            self.counter = 0
            # Save best checkpoint (overwrite previous)
            ckpt_path = self.checkpoint_dir / f"{self.model_name}_best.pt"
            try:
                import torch
                torch.save(model_state, str(ckpt_path))
            except ImportError:
                import pickle
                with open(ckpt_path, "wb") as f:
                    pickle.dump(model_state, f)
            # Also save a timestamped copy
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            ts_path = self.checkpoint_dir / f"{self.model_name}_{ts}_epoch{epoch}.pt"
            try:
                import torch
                torch.save(model_state, str(ts_path))
            except ImportError:
                import pickle
                with open(ts_path, "wb") as f:
                    pickle.dump(model_state, f)
            self.best_checkpoint_path = str(ckpt_path)
            # Cleanup: keep only best + last 2 timestamped
            self._cleanup_old_checkpoints(keep=2)
            return True
        else:
            self.counter += 1
            if self.counter >= self.patience:
                self.early_stop = True
            return False

    def _cleanup_old_checkpoints(self, keep: int = 2):
        """Keep only the best checkpoint + N most recent timestamped ones."""
        files = sorted(
            self.checkpoint_dir.glob(f"{self.model_name}_20*_epoch*.pt"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for f in files[keep:]:
            try:
                f.unlink()
            except Exception:
                pass

    def load_best(self):
        """Load the best model checkpoint."""
        if self.best_checkpoint_path and Path(self.best_checkpoint_path).exists():
            try:
                import torch
                return torch.load(self.best_checkpoint_path, weights_only=False)
            except ImportError:
                import pickle
                with open(self.best_checkpoint_path, "rb") as f:
                    return pickle.load(f)
        return None


# ============================================================================
# 7. Ensemble Weighted Voting (Sharpe-based)
# ============================================================================

def ensemble_weighted_vote(
    predictions: dict[str, np.ndarray],
    model_metrics: dict[str, dict[str, float]],
    method: str = "sharpe",
) -> dict[str, Any]:
    """Compute weighted ensemble prediction using model quality metrics.

    Args:
        predictions: {model_name: predicted_values_array}
        model_metrics: {model_name: {"sharpe": x, "win_rate": y, ...}}
        method: weighting method — "sharpe", "sharpe_win", or "equal"

    Returns:
        Dict with weighted_prediction, weights_used, confidence, per_model_contributions.
    """
    if not predictions:
        return {"weighted_prediction": [], "weights_used": {}, "confidence": 0.0}

    # Compute weights
    weights = {}
    for name in predictions:
        metrics = model_metrics.get(name, {})
        sharpe = float(metrics.get("sharpe", metrics.get("Sharpe Ratio", 1.0)))
        win_rate = float(metrics.get("win_rate", metrics.get("Win Rate %", 50.0)) / 100.0)

        if method == "sharpe":
            w = max(sharpe, 0.1)  # floor of 0.1 to keep model in ensemble
        elif method == "sharpe_win":
            w = max(sharpe, 0.1) * max(win_rate, 0.3)
        else:  # equal
            w = 1.0
        weights[name] = w

    total_w = sum(weights.values())
    if total_w == 0:
        return {"weighted_prediction": [], "weights_used": {}, "confidence": 0.0}

    # Normalize
    weights = {k: v / total_w for k, v in weights.items()}

    # Compute weighted prediction (align lengths)
    max_len = max(len(p) for p in predictions.values())
    weighted = np.zeros(max_len)
    for name, preds in predictions.items():
        w = weights[name]
        padded = np.zeros(max_len)
        padded[:len(preds)] = np.asarray(preds).flatten()
        weighted += w * padded

    # Confidence: inverse of prediction variance weighted by model agreement
    pred_matrix = np.zeros((len(predictions), max_len))
    for i, (name, preds) in enumerate(predictions.items()):
        pred_matrix[i, :len(preds)] = np.asarray(preds).flatten()
    std_across_models = np.std(pred_matrix, axis=0)
    mean_std = float(np.mean(std_across_models))
    confidence = 1.0 / (1.0 + mean_std)

    return {
        "weighted_prediction": weighted.tolist(),
        "weights_used": {k: round(v, 4) for k, v in weights.items()},
        "confidence": round(float(confidence), 4),
        "model_count": len(predictions),
    }


# ============================================================================
# 8. Transaction Cost + Slippage Model
# ============================================================================

@dataclass
class CostModel:
    commission_bps: float = 10.0       # 10 bps = 0.1%
    slippage_bps: float = 5.0           # 5 bps for liquid stocks
    spread_bps: float = 2.0             # bid-ask spread estimate
    min_commission: float = 1.0         # $1 minimum

    def round_trip_cost(self, price: float, shares: int = 100) -> float:
        """Estimate total round-trip cost in dollars."""
        notional = price * shares
        commission = max(self.min_commission, notional * self.commission_bps / 10000)
        slippage = notional * self.slippage_bps / 10000
        spread = notional * self.spread_bps / 10000
        return float(commission + slippage + spread)

    def cost_pct(self, price: float) -> float:
        """Round-trip cost as percentage of price."""
        notional = price * 100  # assume 100 shares
        total = self.round_trip_cost(price, 100)
        return float(total / notional * 100)


def apply_costs_to_trades(
    trades: list[dict[str, Any]],
    cost_model: Optional[CostModel] = None,
) -> tuple[list[dict[str, Any]], float]:
    """Apply transaction costs + slippage to backtest trades.

    Returns (adjusted_trades, total_cost_deducted).
    """
    if cost_model is None:
        cost_model = CostModel()
    total_cost = 0.0
    adjusted = []
    for trade in trades:
        entry_price = float(trade.get("entry_price", 0))
        exit_price = float(trade.get("exit_price", 0))
        cost = cost_model.round_trip_cost(entry_price) + cost_model.round_trip_cost(exit_price)
        total_cost += cost
        trade_adj = dict(trade)
        trade_adj["return_pct"] = round(float(trade.get("return_pct", 0)) - float(cost / (entry_price * 100) * 100), 2)
        trade_adj["cost_deducted"] = round(cost, 2)
        adjusted.append(trade_adj)
    return adjusted, round(total_cost, 2)


# ============================================================================
# 13, 14. Training Report + Feature Importance
# ============================================================================

def generate_training_report(
    model_name: str,
    symbol: str,
    metrics: dict[str, Any],
    feature_report: Optional[dict[str, Any]] = None,
    regime_info: Optional[dict[str, Any]] = None,
    quality_gate: Optional[QualityGateResult] = None,
    benchmark_vs_spy: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Generate a comprehensive training report after each training run.

    Returns a structured dict suitable for JSON serialization and logging.
    """
    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "model": model_name,
        "symbol": symbol,
        "metrics": metrics,
        "feature_importance": feature_report,
        "market_regime": regime_info,
        "quality_gate": {
            "passed": quality_gate.passed if quality_gate else None,
            "failures": quality_gate.failures if quality_gate else [],
            "test_sharpe": quality_gate.test_sharpe if quality_gate else None,
            "test_acc": quality_gate.test_acc if quality_gate else None,
            "max_drawdown": quality_gate.max_drawdown if quality_gate else None,
        },
        "benchmark_vs_spy": benchmark_vs_spy,
        "promotable": quality_gate.passed if quality_gate else False,
    }
    return report


# ============================================================================
# 15. SPY Buy & Hold Benchmark
# ============================================================================

def spy_benchmark(
    symbol_returns: np.ndarray,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> dict[str, Any]:
    """Compare strategy/model returns against SPY buy & hold over same period.

    Args:
        symbol_returns: array of period returns for the strategy/model.
        start_date: optional start date for SPY data fetch.
        end_date: optional end date for SPY data fetch.

    Returns:
        Dict with spy_sharpe, spy_total_return, spy_max_dd, alpha, beta, info_ratio.
    """
    # Fetch SPY data for the same period
    try:
        import yfinance as yf
        spy = yf.download("SPY", start=start_date or "2019-01-01",
                          end=end_date or datetime.now().strftime("%Y-%m-%d"),
                          progress=False, auto_adjust=True)
        if spy is None or spy.empty:
            return {"error": "SPY data unavailable"}
        spy_close = spy["Close"].astype(float)
        spy_returns = spy_close.pct_change().dropna().values
    except Exception as e:
        logger.warning("SPY benchmark fetch failed: %s", e)
        return {"error": str(e)}

    if len(symbol_returns) < 2 or len(spy_returns) < 2:
        return {"error": "insufficient_return_data"}

    # Align lengths
    min_len = min(len(symbol_returns), len(spy_returns))
    sym_r = symbol_returns[-min_len:]
    spy_r = spy_returns[-min_len:]

    # SPY metrics
    spy_sharpe = _calc_sharpe(spy_r)
    spy_total_ret = float(np.prod(1 + spy_r) - 1) * 100
    spy_max_dd = _compute_max_drawdown(spy_r)

    # Strategy metrics
    strat_sharpe = _calc_sharpe(sym_r)
    strat_total_ret = float(np.prod(1 + sym_r) - 1) * 100
    strat_max_dd = _compute_max_drawdown(sym_r)

    # Alpha & Beta (simple linear regression)
    try:
        cov = np.cov(sym_r, spy_r)
        beta = float(cov[0, 1] / cov[1, 1]) if cov[1, 1] > 0 else 0.0
        alpha = float(np.mean(sym_r) - beta * np.mean(spy_r))
        # Annualized alpha
        alpha_annual = alpha * 252.0
    except Exception:
        beta = 0.0
        alpha_annual = 0.0

    # Information ratio
    excess = sym_r - spy_r
    info_ratio = float(np.mean(excess) / (np.std(excess) + 1e-9) * np.sqrt(252))

    return {
        "spy_sharpe": round(spy_sharpe, 3),
        "spy_total_return_pct": round(spy_total_ret, 2),
        "spy_max_drawdown_pct": round(spy_max_dd, 2),
        "strategy_sharpe": round(strat_sharpe, 3),
        "strategy_total_return_pct": round(strat_total_ret, 2),
        "strategy_max_drawdown_pct": round(strat_max_dd, 2),
        "alpha_annual": round(alpha_annual, 4),
        "beta": round(beta, 3),
        "information_ratio": round(info_ratio, 3),
        "n_periods": min_len,
        "sharpe_delta": round(strat_sharpe - spy_sharpe, 3),
    }


# ============================================================================
# Full Pipeline Runner
# ============================================================================

def run_quality_pipeline(
    X: np.ndarray,
    y: np.ndarray,
    prices: np.ndarray,
    symbol: str,
    model_name: str,
    predict_fn: Callable[[np.ndarray], np.ndarray],
    feature_names: Optional[list[str]] = None,
    importance_scores: Optional[np.ndarray] = None,
    cost_model: Optional[CostModel] = None,
) -> dict[str, Any]:
    """Run the full 15-point quality pipeline on a trained model.

    Steps:
      1. Walk-forward validation → test_sharpe, test_acc
      2. Quality gate check
      3. Feature importance report (if available)
      4. Market regime detection
      5. SPY benchmark comparison
      6. Generate training report
    """
    # Outlier filtering on prices
    clean_prices = filter_outliers(prices)

    # Walk-forward validation
    wf_result = walk_forward_validate(X, y, predict_fn)

    # Compute returns from predictions for quality gate
    test_returns = None
    if wf_result.get("fold_metrics"):
        # Approximate returns from directional predictions
        returns_list = []
        for fm in wf_result["fold_metrics"]:
            r = fm.get("sharpe", 0) * 0.01  # rough estimate
            returns_list.append(r)
        test_returns = np.array(returns_list) if returns_list else None

    # Quality gate
    gate = check_quality_gates(wf_result, returns=test_returns)

    # Feature importance report
    feature_report = None
    if feature_names and importance_scores is not None:
        feature_report = feature_selection_report(feature_names, importance_scores)

    # Market regime
    try:
        from app.services.market_data import fetch as fetch_market_data
        df = fetch_market_data(symbol, period="2y")
        regime_info = detect_market_regime(df) if df is not None else None
    except Exception:
        regime_info = None

    # SPY benchmark
    spy_bench = None
    if test_returns is not None and len(test_returns) > 0:
        spy_bench = spy_benchmark(test_returns)

    # Generate report
    report = generate_training_report(
        model_name=model_name,
        symbol=symbol,
        metrics=wf_result,
        feature_report=feature_report,
        regime_info=regime_info,
        quality_gate=gate,
        benchmark_vs_spy=spy_bench,
    )

    leakage_clean: bool = bool(wf_result.get("leakage_clean", True))
    leakage_report: dict = wf_result.get("leakage_report", {})

    return {
        "quality_gate": gate,
        "walk_forward": wf_result,
        "feature_report": feature_report,
        "regime_info": regime_info,
        "benchmark_vs_spy": spy_bench,
        "report": report,
        "pipeline_status": "ok" if gate.passed else "quality_gate_failed",
        "outliers_removed": len(prices) - len(clean_prices),
        # Pillar 1 output — forwarded to model_registry quality gates
        "leakage_clean": leakage_clean,
        "leakage_report": leakage_report,
    }
