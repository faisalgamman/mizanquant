"""Sector-neutral ranking — strip the hidden sector BET out of a cross-sectional factor.

A raw RS/momentum rank rewards a stock partly because its whole sector is flying; that is
a sector timing bet, not stock selection. Quant funds rank WITHIN the peer group: each
name's factor is z-scored against its own sector, so what survives is idiosyncratic
strength. Pure NumPy; degrades gracefully for tiny sectors.
"""

from __future__ import annotations

import numpy as np


def sector_neutral_zscores(values: dict, sectors: dict, min_peers: int = 3) -> dict:
    """Map ``{symbol: factor_value}`` → ``{symbol: within-sector z-score}``.

    For each sector with ≥ ``min_peers`` names, subtract the sector mean and divide by the
    sector std. Symbols with an unknown/too-small sector fall back to a GLOBAL z-score so
    they are still comparable (never dropped). NaNs/None inputs are skipped.
    """
    clean = {s: float(v) for s, v in values.items()
             if v is not None and np.isfinite(_safe(v))}
    if not clean:
        return {}

    # group by sector
    groups: dict = {}
    for s, v in clean.items():
        sec = sectors.get(s) or "_UNKNOWN"
        groups.setdefault(sec, []).append(s)

    all_vals = np.array(list(clean.values()), dtype=float)
    g_mean = float(all_vals.mean())
    g_std = float(all_vals.std()) or 1.0

    out: dict = {}
    for sec, syms in groups.items():
        vals = np.array([clean[s] for s in syms], dtype=float)
        if sec != "_UNKNOWN" and len(syms) >= min_peers and vals.std() > 0:
            mu, sd = float(vals.mean()), float(vals.std())
        else:                                    # tiny/unknown sector → global reference
            mu, sd = g_mean, g_std
        sd = sd or 1.0
        for s in syms:
            out[s] = round(float((clean[s] - mu) / sd), 4)
    return out


def _safe(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return float("nan")


__all__ = ["sector_neutral_zscores"]
