"""PSI / KS drift monitors for input feature distributions.

Compares live feature distributions against reference distributions
and alerts via Telegram + Sentry when drift exceeds configured thresholds.
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from pathlib import Path
from typing import Optional, Sequence

import numpy as np

from app.services.notify import send_message

logger = logging.getLogger("screener")

_DRIFT_DIR = Path(os.environ.get("DRIFT_REFERENCE_DIR", "drift_references"))

_PSI_THRESHOLD = float(os.environ.get("DRIFT_PSI_THRESHOLD", "0.2"))
_KS_THRESHOLD = float(os.environ.get("DRIFT_KS_THRESHOLD", "0.15"))
_ALERT_COOLDOWN = float(os.environ.get("DRIFT_ALERT_COOLDOWN", "3600"))

# ── Helpers ──


def _eps() -> float:
    return 1e-15


# ── Reference distributions ──


def _ref_path(name: str) -> Path:
    return _DRIFT_DIR / f"{name}.json"


def _load_ref(name: str) -> Optional[dict]:
    path = _ref_path(name)
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return None


def _save_ref(name: str, ref: dict):
    _DRIFT_DIR.mkdir(parents=True, exist_ok=True)
    _ref_path(name).write_text(json.dumps(ref, indent=2, default=str))


# ── PSI (Population Stability Index) ──


def compute_psi(
    expected: Sequence[float],
    actual: Sequence[float],
    bins: int = 10,
) -> float:
    """Population Stability Index between two distributions.

    PSI = Σ (actual_pct_i - expected_pct_i) * ln(actual_pct_i / expected_pct_i)

    Reference interpretation:
        PSI < 0.1   → no significant change
        0.1 ≤ PSI < 0.25 → moderate change, investigate
        PSI ≥ 0.25 → significant shift, action required
    """
    if len(expected) == 0 or len(actual) == 0:
        return 0.0

    all_vals = np.concatenate([expected, actual])
    if all_vals.std() == 0:
        return 0.0

    bin_edges = np.histogram_bin_edges(all_vals, bins=bins)
    exp_hist, _ = np.histogram(expected, bins=bin_edges)
    act_hist, _ = np.histogram(actual, bins=bin_edges)

    psi = 0.0
    for e, a in zip(exp_hist, act_hist):
        e_pct = e / len(expected) if len(expected) > 0 else 0.0
        a_pct = a / len(actual) if len(actual) > 0 else 0.0
        e_pct = max(e_pct, _eps())
        a_pct = max(a_pct, _eps())
        psi += (a_pct - e_pct) * math.log(a_pct / e_pct)

    return psi


# ── KS (Kolmogorov-Smirnov) ──


def compute_ks(reference: Sequence[float], current: Sequence[float]) -> float:
    """Two-sample Kolmogorov-Smirnov statistic.

    Returns the maximum absolute difference between the empirical CDFs.
    0 = identical distributions, 1 = completely different.
    """
    if len(reference) == 0 or len(current) == 0:
        return 0.0

    ref_sorted = np.sort(reference)
    cur_sorted = np.sort(current)

    ks = 0.0
    i = j = 0
    n1, n2 = len(ref_sorted), len(cur_sorted)

    while i < n1 and j < n2:
        if ref_sorted[i] <= cur_sorted[j]:
            cdf1 = (i + 1) / n1
            cdf2 = j / n2
            i += 1
        else:
            cdf1 = i / n1
            cdf2 = (j + 1) / n2
            j += 1
        ks = max(ks, abs(cdf2 - cdf1))

    return float(ks)


# ── Core monitor ──


class DriftMonitor:
    """Maintains reference distributions and checks live data for drift."""

    def __init__(self, psi_threshold: float = _PSI_THRESHOLD, ks_threshold: float = _KS_THRESHOLD):
        self.psi_threshold = psi_threshold
        self.ks_threshold = ks_threshold
        self._last_alert: dict[str, float] = {}

    # ── Reference management ──

    def set_reference(self, name: str, values: Sequence[float], dist_type: str = "numeric"):
        """Store a reference distribution for *name*."""
        ref = {
            "type": dist_type,
            "values": [float(v) for v in values],
            "recorded_at": time.time(),
        }
        _save_ref(name, ref)

    def get_reference(self, name: str) -> Optional[dict]:
        return _load_ref(name)

    def has_reference(self, name: str) -> bool:
        return _load_ref(name) is not None

    def list_references(self) -> list[str]:
        if not _DRIFT_DIR.exists():
            return []
        return sorted(p.stem for p in _DRIFT_DIR.iterdir() if p.suffix == ".json")

    # ── Checking ──

    def check_numeric(self, name: str, current_values: Sequence[float]) -> dict:
        """Run PSI + KS checks on *name*, return results dict.

        Returns::

            {"drifted": True/False, "psi": 0.12, "ks": 0.08, "alerted": True/False}
        """
        ref = _load_ref(name)
        if ref is None:
            return {"drifted": False, "psi": 0.0, "ks": 0.0, "alerted": False, "error": "no_reference"}

        expected = ref["values"]
        psi = compute_psi(expected, current_values)
        ks = compute_ks(expected, current_values)

        psi_drifted = psi > self.psi_threshold
        ks_drifted = ks > self.ks_threshold
        drifted = psi_drifted or ks_drifted

        alerted = False
        if drifted:
            alerted = self._alert(name, psi, ks, psi_drifted, ks_drifted)

        return {"drifted": drifted, "psi": round(psi, 4), "ks": round(ks, 4), "alerted": alerted}

    def check_categorical(self, name: str, current_label: str, reference_frequencies: Optional[dict[str, float]] = None) -> dict:
        """Check if a categorical feature has drifted using PSI on proportions.

        *reference_frequencies* should map label → expected proportion.
        If None, the stored reference is used.
        """
        ref = reference_frequencies or _load_ref(name)
        if ref is None:
            return {"drifted": False, "psi": 0.0, "alerted": False, "error": "no_reference"}

        expected = ref.get("frequencies", ref)
        total = sum(expected.values())
        if total == 0:
            return {"drifted": False, "psi": 0.0, "alerted": False, "error": "empty_reference"}

        # Build actual frequencies — if current_label is just one observation,
        # treat it as a single sample (for real-time checking).
        actual = {k: 0.0 for k in expected}
        if current_label in actual:
            actual[current_label] = 1.0
        else:
            actual[current_label] = 1.0

        eps = _eps()
        psi = 0.0
        for label in set(list(expected.keys()) + list(actual.keys())):
            e_pct = expected.get(label, 0.0) / total
            a_pct = actual.get(label, 0.0) / max(sum(actual.values()), 1)
            e_pct = max(e_pct, eps)
            a_pct = max(a_pct, eps)
            psi += (a_pct - e_pct) * math.log(a_pct / e_pct)

        drifted = psi > self.psi_threshold
        alerted = False
        if drifted:
            alerted = self._alert(name, psi, 0.0, True, False)

        return {"drifted": drifted, "psi": round(psi, 4), "alerted": alerted}

    # ── Alerting ──

    def _alert(self, name: str, psi: float, ks: float, psi_drifted: bool, ks_drifted: bool) -> bool:
        now = time.time()
        last = self._last_alert.get(name, 0.0)
        if now - last < _ALERT_COOLDOWN:
            return False

        reasons = []
        if psi_drifted:
            reasons.append(f"PSI={psi:.3f} (threshold={self.psi_threshold})")
        if ks_drifted:
            reasons.append(f"KS={ks:.3f} (threshold={self.ks_threshold})")

        msg = f"⚠️ *Drift detected* — {name}\n" + "\n".join(reasons)
        send_message(msg, dedup_key=f"drift_{name}")
        try:
            import sentry_sdk
            sentry_sdk.capture_message(f"Drift: {name} — {', '.join(reasons)}")
        except ImportError:
            pass

        self._last_alert[name] = now
        return True
