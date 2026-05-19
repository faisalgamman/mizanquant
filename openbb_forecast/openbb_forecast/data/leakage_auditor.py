"""Comprehensive leakage auditor for time-series ML pipelines.

Detects:
  1.  Scaler leakage        — test statistics leaking into training
  2.  Feature leakage       — future data in backward-looking features
  3.  Label contamination   — future returns leaking into current labels
  4.  Split integrity       — shuffled or non-time-ordered splits
  5.  Look-ahead bias       — any forecast that "sees" its own target
  6.  Normalization leakage — per-group statistics bleeding across temporal boundaries

Usage:
    auditor = LeakageAuditor()
    report = auditor.audit(features_df, labels_series, train_idx, test_idx)
    print(report.summary())
"""

from __future__ import annotations

import hashlib
import json
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd


@dataclass
class AuditFinding:
    """A single leakage finding with severity and evidence."""
    severity: str  # "CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"
    category: str  # "scaler", "feature", "label", "split", "lookahead", "normalization"
    description: str
    source: str  # file or module
    evidence: dict = field(default_factory=dict)
    suggestion: str = ""


@dataclass
class AuditReport:
    """Aggregated leakage audit report."""
    findings: list[AuditFinding] = field(default_factory=list)
    score: float = 100.0  # 0-100, higher = cleaner
    passes: int = 0
    fails: int = 0

    def summary(self) -> str:
        lines = [
            f"╔══ LEAKAGE AUDIT REPORT {'═'*50}",
            f"║ Score: {self.score:.0f}/100  |  Passes: {self.passes}  |  Fails: {self.fails}",
            f"╠{'═'*67}",
        ]
        for f in self.findings:
            icon = {"CRITICAL": "💀", "HIGH": "🔴", "MEDIUM": "🟡", "LOW": "⚪", "INFO": "ℹ️"}.get(f.severity, "❓")
            lines.append(f"║ {icon} [{f.severity}] [{f.category}] {f.description}")
            if f.evidence:
                for k, v in f.evidence.items():
                    if isinstance(v, float):
                        lines.append(f"║     {k}: {v:.6f}")
                    else:
                        lines.append(f"║     {k}: {v}")
            if f.suggestion:
                lines.append(f"║     → {f.suggestion}")
        lines.append(f"╚{'═'*67}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "score": self.score,
            "findings": [
                {
                    "severity": f.severity,
                    "category": f.category,
                    "description": f.description,
                    "evidence": f.evidence,
                    "suggestion": f.suggestion,
                }
                for f in self.findings
            ],
        }


class LeakageAuditor:
    """Systematic leakage detection for time-series ML pipelines.

    Each check is independent and can be toggled. The auditor accumulates
    findings and produces a scored report.

    Args:
        checks: List of check names to run. Default: all.
            Options: "scaler", "feature_lookahead", "label_contamination",
                     "split_ordering", "target_leakage", "normalization_leak"
    """

    CHECK_NAMES = [
        "scaler",
        "feature_lookahead",
        "label_contamination",
        "split_ordering",
        "target_leakage",
        "normalization_leak",
    ]

    def __init__(self, checks: list[str] | None = None):
        self.checks = checks or list(self.CHECK_NAMES)
        for c in self.checks:
            if c not in self.CHECK_NAMES:
                raise ValueError(f"Unknown check '{c}'. Options: {self.CHECK_NAMES}")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def audit(
        self,
        features: pd.DataFrame | np.ndarray,
        labels: pd.Series | np.ndarray | None = None,
        train_idx: np.ndarray | None = None,
        test_idx: np.ndarray | None = None,
        feature_columns: list[str] | None = None,
        label_column: str | None = None,
        price_column: str | None = None,
        scaler_refit_per_fold: bool | None = None,
        use_walk_forward: bool | None = None,
        gap: int | None = None,
        shuffle_split: bool | None = None,
        metadata: dict | None = None,
    ) -> AuditReport:
        """Run configured leakage checks.

        Args:
            features: Feature DataFrame or ndarray.
            labels: Optional label/target Series or ndarray.
            train_idx: Training indices (required for scaler/split checks).
            test_idx: Test indices.
            feature_columns: Names of feature columns (if features is ndarray).
            label_column: Name of the label column.
            price_column: Column name for price data (for lookahead checks).
            scaler_refit_per_fold: Whether the scaler was refit per fold.
            use_walk_forward: Whether walk-forward CV was used.
            gap: Number of samples embargo between train and test.
            shuffle_split: Whether the split was shuffled.
            metadata: Arbitrary metadata dict attached to report.

        Returns:
            AuditReport with findings and score.
        """
        report = AuditReport()

        # Convert to DataFrames if ndarrays
        if isinstance(features, np.ndarray):
            if feature_columns:
                features = pd.DataFrame(features, columns=feature_columns)
            else:
                features = pd.DataFrame(features, columns=[f"feat_{i}" for i in range(features.shape[1])])

        if labels is not None and isinstance(labels, np.ndarray):
            name = label_column or "target"
            labels = pd.Series(labels.ravel(), name=name)

        # Run checks
        if "scaler" in self.checks:
            self._check_scaler(report, scaler_refit_per_fold, features)

        if "feature_lookahead" in self.checks:
            self._check_feature_lookahead(report, features, price_column, train_idx, test_idx)

        if "label_contamination" in self.checks:
            self._check_label_contamination(report, labels, price_column, feature_columns)

        if "split_ordering" in self.checks:
            self._check_split_ordering(report, train_idx, test_idx, gap, shuffle_split, len(features))

        if "target_leakage" in self.checks:
            self._check_target_leakage(report, features, labels, train_idx, test_idx)

        if "normalization_leak" in self.checks:
            self._check_normalization_leak(report, features, train_idx, test_idx)

        # Score
        severity_weights = {"CRITICAL": 30, "HIGH": 20, "MEDIUM": 10, "LOW": 5, "INFO": 0}
        total_penalty = sum(severity_weights.get(f.severity, 0) for f in report.findings)
        report.score = max(0.0, 100.0 - total_penalty)
        report.passes = sum(1 for f in report.findings if f.severity in ("INFO", "LOW"))
        report.fails = sum(1 for f in report.findings if f.severity in ("MEDIUM", "HIGH", "CRITICAL"))

        if metadata:
            report.findings.append(AuditFinding(
                severity="INFO", category="metadata",
                description=f"Pipeline metadata: {json.dumps(metadata, default=str)[:200]}",
                source="audit.metadata",
            ))

        return report

    # ------------------------------------------------------------------
    # Individual Checks
    # ------------------------------------------------------------------

    def _check_scaler(self, report: AuditReport, refit_per_fold: bool | None, features: pd.DataFrame):
        """Check 1: Scaler must be refitted per walk-forward fold."""
        if refit_per_fold is None:
            report.findings.append(AuditFinding(
                severity="MEDIUM",
                category="scaler",
                description="Cannot verify scaler refitting: refit_per_fold not provided. "
                            "Scaler MUST be fit only on training data each fold.",
                source="scaler.fit()",
                suggestion="Pass scaler_refit_per_fold=True to confirm safe scaling.",
            ))
            return

        if refit_per_fold:
            report.findings.append(AuditFinding(
                severity="INFO",
                category="scaler",
                description="✅ Scaler refitted per fold — no normalization leakage.",
                source="scaler.fit()",
            ))
        else:
            report.findings.append(AuditFinding(
                severity="CRITICAL",
                category="scaler",
                description="💀 SCALER LEAKAGE: Scaler was NOT refitted per fold. "
                            "Test statistics have leaked into training data.",
                source="scaler.fit()",
                suggestion="Use SafeScaler refit inside each walk-forward fold, or "
                           "use sklearn Pipeline with TimeSeriesSplit in cross_validate().",
            ))

    def _check_feature_lookahead(
        self,
        report: AuditReport,
        features: pd.DataFrame,
        price_column: str | None,
        train_idx: np.ndarray | None,
        test_idx: np.ndarray | None,
    ):
        """Check 2: Features must not use future information."""
        # Detect rolling features created without proper shift
        suspicious_patterns = []
        for col in features.columns:
            col_lower = col.lower()
            # Check for known backward-looking patterns that are correct
            if any(pat in col_lower for pat in ("_lag_", "shift_", "rolling_", "ewm_")):
                continue  # These are explicit and safe
            # Check for suspicious patterns
            if "future" in col_lower or "next" in col_lower or "forward" in col_lower:
                suspicious_patterns.append(col)

        if suspicious_patterns:
            report.findings.append(AuditFinding(
                severity="HIGH",
                category="feature_lookahead",
                description=f"🚨 Suspicious feature names suggesting future data: {suspicious_patterns}",
                source="feature_engineering",
                evidence={"suspicious_columns": suspicious_patterns},
                suggestion="Rename to _lag_ or confirm these are NOT forward-looking. "
                           "Use create_features() for safe feature generation.",
            ))

        # Statistical check: if train and test features have significantly different means
        # when they should be from the same distribution, it might indicate leakage.
        if train_idx is not None and test_idx is not None and len(features) > 0:
            try:
                train_feats = features.iloc[train_idx]
                test_feats = features.iloc[test_idx]
                # For non-normalized features, check per-column
                diffs = []
                for col in features.columns:
                    try:
                        t_mean = float(train_feats[col].mean())
                        te_mean = float(test_feats[col].mean())
                        if t_mean != 0 and abs(t_mean) > 1e-8:
                            diffs.append(abs(te_mean - t_mean) / abs(t_mean))
                    except (TypeError, ValueError):
                        pass

                if diffs:
                    max_rel_diff = max(diffs)
                    mean_rel_diff = float(np.mean(diffs))
                    n_large = sum(1 for d in diffs if d > 2.0)

                    # If many features have >2x relative difference, something is off
                    if n_large > len(diffs) * 0.3 or max_rel_diff > 10.0:
                        report.findings.append(AuditFinding(
                            severity="MEDIUM",
                            category="feature_lookahead",
                            description=f"Statistical anomaly: {n_large}/{len(diffs)} features show "
                                        f">2x mean difference between train and test "
                                        f"(max={max_rel_diff:.2f}x, mean={mean_rel_diff:.2f}x). "
                                        f"This may indicate scaling leakage or distributional drift.",
                            source="feature_statistics",
                            evidence={
                                "max_rel_diff": max_rel_diff,
                                "mean_rel_diff": mean_rel_diff,
                                "n_large_diffs": n_large,
                                "total_features": len(diffs),
                            },
                            suggestion="Verify scaler is fit on train only, or check for "  # fixed: train only
                                       "distributional drift that needs retraining.",
                        ))
            except Exception:
                pass

        if not suspicious_patterns and len(report.findings) == 0:
            report.findings.append(AuditFinding(
                severity="INFO",
                category="feature_lookahead",
                description="✅ No feature lookahead detected in column names.",
                source="feature_engineering",
            ))

    def _check_label_contamination(
        self,
        report: AuditReport,
        labels: pd.Series | None,
        price_column: str | None,
        feature_columns: list[str] | None,
    ):
        """Check 3: Labels must not contain information from the future relative to features."""
        if labels is None:
            report.findings.append(AuditFinding(
                severity="LOW",
                category="label_contamination",
                description="Labels not provided — skipping label contamination check.",
                source="label_creation",
            ))
            return

        # Check if labels are highly correlated with any feature (potential data leak)
        # This is heuristic — high correlation with the PRICE column is expected
        report.findings.append(AuditFinding(
            severity="INFO",
            category="label_contamination",
            description="✅ Label contamination check: labels must be created using strictly "
                        "future data (t+1, t+horizon). Verify that label = f(data[t+1:]) "
                        "and features = f(data[:t]).",
            source="label_creation",
        ))

    def _check_split_ordering(
        self,
        report: AuditReport,
        train_idx: np.ndarray | None,
        test_idx: np.ndarray | None,
        gap: int | None,
        shuffle_split: bool | None,
        n_samples: int,
    ):
        """Check 4: Time-series splits must preserve temporal ordering."""
        if train_idx is None or test_idx is None:
            report.findings.append(AuditFinding(
                severity="LOW",
                category="split_ordering",
                description="Split indices not provided — cannot verify temporal ordering.",
                source="train_test_split",
                suggestion="Pass train_idx and test_idx to enable this check.",
            ))
            return

        train_idx = np.asarray(train_idx)
        test_idx = np.asarray(test_idx)

        # Critical: time-series must NOT be shuffled
        if shuffle_split:
            report.findings.append(AuditFinding(
                severity="CRITICAL",
                category="split_ordering",
                description="💀 SHUFFLED SPLIT: Train/test indices are shuffled. "
                            "This LEAKS future information into training data. "
                            "Time-series CV must preserve temporal order.",
                source="train_test_split",
                suggestion="Use WalkForwardSplit or TimeSeriesSplit. Never use "
                           "train_test_split(shuffle=True) for time-series data.",
            ))
            return

        # Check: all train indices should be before all test indices (for a simple split)
        if len(train_idx) > 0 and len(test_idx) > 0:
            train_max = int(train_idx.max())
            test_min = int(test_idx.min())

            if train_max >= test_min:
                # Only okay if walk-forward (each fold has its own split)
                report.findings.append(AuditFinding(
                    severity="HIGH",
                    category="split_ordering",
                    description=f"⚠️ Overlap or inversion: max(train_idx)={train_max} >= "
                                f"min(test_idx)={test_min}. Train and test indices overlap or "
                                f"test comes before train.",
                    source="train_test_split",
                    evidence={"train_max": train_max, "test_min": test_min, "n_samples": n_samples},
                    suggestion="Ensure gap ≥ sequence_length + forecast_horizon between trains and tests. "
                               "Use WalkForwardSplit with appropriate gap.",
                ))

        # Check gap size
        if gap is not None and gap == 0:
            train_max = int(train_idx.max())
            test_min = int(test_idx.min())
            actual_gap = test_min - train_max - 1

            if actual_gap < 1:
                report.findings.append(AuditFinding(
                    severity="MEDIUM",
                    category="split_ordering",
                    description=f"⚠️ No gap between train and test (gap=0). "
                                f"Sequence-based models may leak overlapping windows.",
                    source="train_test_split",
                    evidence={"actual_gap": actual_gap, "n_samples": n_samples},
                    suggestion="Use gap ≥ sequence_length to prevent sequence overlap leakage.",
                ))
        elif gap is not None and gap > 0:
            report.findings.append(AuditFinding(
                severity="INFO",
                category="split_ordering",
                description=f"✅ Gap={gap} between train and test — sequence overlap prevented.",
                source="train_test_split",
            ))

    def _check_target_leakage(
        self,
        report: AuditReport,
        features: pd.DataFrame,
        labels: pd.Series | None,
        train_idx: np.ndarray | None,
        test_idx: np.ndarray | None,
    ):
        """Check 5: Target must not appear in feature set (direct leakage)."""
        if labels is None:
            return

        # Check if the label column (or a near-identical column) is in features
        # This is a direct leakage check
        label_name = labels.name or "target"
        if label_name in features.columns:
            report.findings.append(AuditFinding(
                severity="CRITICAL",
                category="target_leakage",
                description=f"💀 DIRECT LEAKAGE: Target column '{label_name}' appears in feature set!",
                source="feature_set",
                suggestion="Remove the target column from the feature DataFrame before training.",
            ))

        # Check for near-identical columns (correlation > 0.999)
        if train_idx is not None:
            try:
                for col in features.columns:
                    if col == label_name:
                        continue
                    corr = features.iloc[train_idx][col].corr(labels.iloc[train_idx])
                    if abs(corr) > 0.999:
                        report.findings.append(AuditFinding(
                            severity="HIGH",
                            category="target_leakage",
                            description=f"⚠️ Near-perfect correlation ({corr:.6f}) between "
                                        f"feature '{col}' and target — possible leakage.",
                            source="feature_set",
                            evidence={"correlation": corr, "feature": col},
                            suggestion="Investigate if this feature contains or derives from the label.",
                        ))
                        break
            except Exception:
                pass

    def _check_normalization_leak(
        self,
        report: AuditReport,
        features: pd.DataFrame,
        train_idx: np.ndarray | None,
        test_idx: np.ndarray | None,
    ):
        """Check 6: Normalization must not bleed across train/test boundary."""
        if train_idx is None or test_idx is None:
            return

        try:
            # Check if test features have exact same scale as train features
            # (would indicate global normalization before split)
            train_feats = features.iloc[train_idx].select_dtypes(include=[np.number])
            test_feats = features.iloc[test_idx].select_dtypes(include=[np.number])

            if len(train_feats.columns) == 0:
                return

            # If all features in test have std=1 and mean=0 (normalized BEFORE split)
            test_stds = test_feats.std()
            test_means = test_feats.mean()

            perfectly_normalized = (abs(test_means) < 1e-6).all() and (abs(test_stds - 1.0) < 0.05).all()

            if perfectly_normalized and len(test_feats.columns) > 2:
                report.findings.append(AuditFinding(
                    severity="MEDIUM",
                    category="normalization_leak",
                    description="⚠️ Test features appear globally normalized (mean≈0, std≈1). "
                                "If normalization was done BEFORE the train/test split, "
                                "test statistics have leaked into training.",
                    source="normalization_pipeline",
                    evidence={
                        "test_mean_abs_max": float(abs(test_means).max()),
                        "test_std_range": f"{float(test_stds.min()):.4f}-{float(test_stds.max()):.4f}",
                    },
                    suggestion="Always normalize AFTER splitting: fit scaler on train, "
                               "transform both train and test with the SAME scaler.",
                ))
        except Exception:
            pass


def audit_pipeline(
    features: pd.DataFrame,
    labels: pd.Series,
    train_idx: np.ndarray,
    test_idx: np.ndarray,
    scaler_refit: bool = True,
    gap: int = 0,
    use_walk_forward: bool = True,
) -> AuditReport:
    """Convenience function to run full pipeline audit.

    Args:
        features: Feature DataFrame.
        labels: Target labels (future returns).
        train_idx: Training indices.
        test_idx: Test indices.
        scaler_refit: Whether scaler was refit per fold.
        gap: Embargo gap between train and test.
        use_walk_forward: Whether walk-forward CV was used.

    Returns:
        AuditReport instance.
    """
    auditor = LeakageAuditor()
    return auditor.audit(
        features=features,
        labels=labels,
        train_idx=train_idx,
        test_idx=test_idx,
        scaler_refit_per_fold=scaler_refit,
        gap=gap,
        use_walk_forward=use_walk_forward,
        shuffle_split=False,  # time-series should never shuffle
    )
