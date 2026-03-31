"""Time-series cross-validation with expanding windows.

Critical fix: the original repo uses a single fixed train/test split (last 30 days).
WalkForwardSplit implements proper expanding-window validation with an embargo gap.
"""

from __future__ import annotations

import numpy as np


class WalkForwardSplit:
    """Expanding-window time-series cross-validation.

    Unlike a simple train/test split, this creates multiple folds where
    the training window expands over time — mimicking how a model would
    be retrained in production as more data arrives.

    Example with 252 samples, n_splits=5, min_train_ratio=0.6:
        Fold 1: train=[0..151],  test=[152..171]
        Fold 2: train=[0..171],  test=[172..191]
        Fold 3: train=[0..191],  test=[192..211]
        Fold 4: train=[0..211],  test=[212..231]
        Fold 5: train=[0..231],  test=[232..251]

    The optional `gap` parameter creates an embargo zone between train
    and test to prevent information leakage from overlapping sequences.

    Args:
        n_splits: Number of walk-forward folds.
        min_train_ratio: Minimum fraction of data used for first training set.
        gap: Number of samples to skip between train and test boundaries.
    """

    def __init__(
        self,
        n_splits: int = 5,
        min_train_ratio: float = 0.6,
        gap: int = 0,
    ):
        if n_splits < 1:
            raise ValueError(f"n_splits must be >= 1, got {n_splits}")
        if not 0.1 <= min_train_ratio <= 0.95:
            raise ValueError(f"min_train_ratio must be in [0.1, 0.95], got {min_train_ratio}")
        if gap < 0:
            raise ValueError(f"gap must be >= 0, got {gap}")
        self.n_splits = n_splits
        self.min_train_ratio = min_train_ratio
        self.gap = gap

    def split(self, n_samples: int) -> list[tuple[np.ndarray, np.ndarray]]:
        """Generate train/test index arrays for each fold.

        Args:
            n_samples: Total number of samples in the dataset.

        Returns:
            List of (train_indices, test_indices) tuples.
        """
        min_train = int(np.ceil(n_samples * self.min_train_ratio))
        remaining = n_samples - min_train
        test_size = max(1, remaining // self.n_splits)

        folds = []
        for i in range(self.n_splits):
            train_end = min_train + i * test_size
            test_start = train_end + self.gap
            test_end = min(test_start + test_size, n_samples)

            if test_start >= n_samples or test_start >= test_end:
                break

            train_idx = np.arange(0, train_end)
            test_idx = np.arange(test_start, test_end)
            folds.append((train_idx, test_idx))

        if not folds:
            raise ValueError(
                f"Cannot create any folds with n_samples={n_samples}, "
                f"n_splits={self.n_splits}, min_train_ratio={self.min_train_ratio}, gap={self.gap}"
            )

        return folds

    def summary(self, n_samples: int) -> str:
        """Human-readable summary of the split."""
        folds = self.split(n_samples)
        lines = [f"WalkForwardSplit: {len(folds)} folds from {n_samples} samples (gap={self.gap})"]
        for i, (train_idx, test_idx) in enumerate(folds):
            lines.append(
                f"  Fold {i + 1}: train=[0..{train_idx[-1]}] ({len(train_idx)} samples), "
                f"test=[{test_idx[0]}..{test_idx[-1]}] ({len(test_idx)} samples)"
            )
        return "\n".join(lines)
