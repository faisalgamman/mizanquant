"""Tests for leakage_auditor.py"""
import pytest
import numpy as np
import pandas as pd
from openbb_forecast.data.leakage_auditor import LeakageAuditor, AuditReport, audit_pipeline

class TestLeakageAuditor:
    def test_no_leakage_clean_pipeline(self):
        """Clean pipeline with proper scaler refit should score 100."""
        np.random.seed(42)
        n = 500
        feats = pd.DataFrame({
            'feat_1': np.random.randn(n).cumsum(),
            'feat_2': np.random.randn(n).cumsum(),
        })
        labels = pd.Series(np.random.randn(n), name='target')
        train_idx = np.arange(0, 300)
        test_idx = np.arange(300, n)

        auditor = LeakageAuditor()
        report = auditor.audit(
            features=feats, labels=labels,
            train_idx=train_idx, test_idx=test_idx,
            scaler_refit_per_fold=True, gap=10,
            shuffle_split=False
        )
        assert report.score == 100.0
        assert report.fails == 0

    def test_scaler_leakage_detected(self):
        """Not refitting scaler should produce CRITICAL finding."""
        feats = pd.DataFrame({'f': [1,2,3,4,5]})
        labels = pd.Series([0,0,0,0,0], name='t')
        auditor = LeakageAuditor()
        report = auditor.audit(
            features=feats, labels=labels,
            train_idx=np.array([0,1,2]), test_idx=np.array([3,4]),
            scaler_refit_per_fold=False, shuffle_split=False
        )
        assert report.score < 80
        assert any(f.severity == 'CRITICAL' for f in report.findings)

    def test_shuffled_split_critical(self):
        """Shuffled split should be flagged as CRITICAL leakage."""
        feats = pd.DataFrame({'f': range(100)})
        labels = pd.Series(range(100), name='t')
        auditor = LeakageAuditor()
        report = auditor.audit(
            features=feats, labels=labels,
            train_idx=np.arange(50), test_idx=np.arange(50, 100),
            shuffle_split=True
        )
        assert any(f.severity == 'CRITICAL' and 'SHUFFLED' in f.description for f in report.findings)

    def test_target_in_features_detected(self):
        """Having target column in features should be flagged."""
        feats = pd.DataFrame({'target': range(100), 'other': range(100)})
        labels = pd.Series(range(100), name='target')
        auditor = LeakageAuditor()
        report = auditor.audit(
            features=feats, labels=labels,
            train_idx=np.arange(50), test_idx=np.arange(50, 100),
            scaler_refit_per_fold=True, shuffle_split=False
        )
        assert any(f.severity == 'CRITICAL' and 'target' in f.description.lower() for f in report.findings)

    def test_overlap_inversion_warning(self):
        """Overlapping train/test should be flagged."""
        feats = pd.DataFrame({'f': range(100)})
        labels = pd.Series(range(100), name='t')
        auditor = LeakageAuditor()
        report = auditor.audit(
            features=feats, labels=labels,
            train_idx=np.arange(30, 70),
            test_idx=np.arange(60, 100),
            shuffle_split=False
        )
        assert any(f.severity in ('HIGH', 'MEDIUM') and 'Overlap' in f.description for f in report.findings)

    def test_report_summary_format(self):
        """Report summary should contain score and passes/fails."""
        feats = pd.DataFrame({'f': range(10)})
        labels = pd.Series(range(10), name='t')
        auditor = LeakageAuditor()
        report = auditor.audit(
            features=feats, labels=labels,
            train_idx=np.arange(5), test_idx=np.arange(5, 10),
            scaler_refit_per_fold=True, shuffle_split=False
        )
        summary = report.summary()
        assert 'Score:' in summary or 'LEAKAGE' in summary

    def test_convenience_function(self):
        """audit_pipeline() convenience function should work."""
        n = 200
        feats = pd.DataFrame({'f': np.arange(n, dtype=float)})
        labels = pd.Series(np.arange(n), name='target')
        report = audit_pipeline(feats, labels, np.arange(100), np.arange(100, n))
        assert isinstance(report, AuditReport)
        assert report.score > 50
