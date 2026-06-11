"""Shared technical-feature library — single computation for training AND serving.

Kills the train/serve drift that would occur if the fitter and the runtime
scorer each had their own feature code. Signal attribution imports this and
wraps it with signal metadata + excess_vs_spy.

Exported:
  FEATURE_ORDER : list[str]   — fixed-order 16-float feature names
  compute_features(df, spy_df, asof) -> dict | None
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# Fixed-order feature list for the fitter and scorer.
# Order MUST match the column order of compute_features output.
FEATURE_ORDER = [
    "rsi14", "macd_hist", "macd_hist_rising",
    "adx14", "di_bullish",
    "bb_bandwidth_pctile",
    "vol_ratio_20", "vol_dryup",
    "prox_52w",
    "trend_above_ema50", "ema50_above_200",
    "mom_1m", "mom_3m",
    "rs_spy_20", "rs_spy_63",
]


def _ewm_ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def _cut(frame, when):
    """Tz-normalized slice: frame[frame.index <= when], handling aware vs naive."""
    ts = pd.Timestamp(when)
    idx_tz = getattr(frame.index, "tz", None)
    if ts.tzinfo is not None and idx_tz is None:
        ts = ts.tz_localize(None)
    elif ts.tzinfo is None and idx_tz is not None:
        ts = ts.tz_localize(idx_tz)
    return frame[frame.index <= ts]


def compute_features(df: pd.DataFrame, spy_df: pd.DataFrame | None, asof) -> dict | None:
    """Reconstruct the 16 technical features at *asof* (a datetime or Timestamp).

    Returns a dict with keys matching FEATURE_ORDER, or None when the symbol
    has fewer than 60 bars at the cut point.
    """
    try:
        sym_df = _cut(df, asof)
        if len(sym_df) < 60:
            return None
        close = sym_df["close"].astype(float)
        high = sym_df["high"].astype(float)
        low = sym_df["low"].astype(float)
        vol = sym_df["volume"].astype(float)
        price = float(close.iloc[-1])

        # RSI 14
        delta = close.diff()
        gain = delta.clip(lower=0).ewm(alpha=1 / 14, min_periods=14).mean()
        loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, min_periods=14).mean()
        rs = gain / (loss + 1e-9)
        rsi14 = float(100 - (100 / (1 + rs)).iloc[-1]) if len(gain) >= 14 else 50.0

        # MACD
        ema12 = _ewm_ema(close, 12)
        ema26 = _ewm_ema(close, 26)
        macd_line = ema12 - ema26
        macd_signal_line = _ewm_ema(macd_line, 9)
        macd_hist = macd_line - macd_signal_line
        macd_hist_val = float(macd_hist.iloc[-1]) if len(macd_hist) >= 2 else 0.0
        macd_hist_prev = float(macd_hist.iloc[-2]) if len(macd_hist) >= 2 else 0.0
        macd_hist_rising = macd_hist_val > macd_hist_prev

        # ADX
        adx_val, di_bullish = 20.0, False
        try:
            up_move = high.diff()
            down_move = -low.diff()
            plus_dm = pd.Series(
                np.where((up_move > down_move) & (up_move > 0), up_move, 0),
                index=high.index,
            )
            minus_dm = pd.Series(
                np.where((down_move > up_move) & (down_move > 0), down_move, 0),
                index=high.index,
            )
            tr = pd.concat(
                [high - low, (high - close.shift()).abs(), (low - close.shift()).abs()],
                axis=1,
            ).max(axis=1)
            atr_s = tr.ewm(alpha=1 / 14, min_periods=14).mean()
            pdm_s = plus_dm.ewm(alpha=1 / 14, min_periods=14).mean()
            mdm_s = minus_dm.ewm(alpha=1 / 14, min_periods=14).mean()
            pdi = (pdm_s / (atr_s + 1e-9)) * 100
            mdi = (mdm_s / (atr_s + 1e-9)) * 100
            dx = (abs(pdi - mdi) / (pdi + mdi + 1e-9)) * 100
            adx_val = float(dx.ewm(alpha=1 / 14, min_periods=14).mean().iloc[-1]) if len(dx) >= 14 else 20.0
            di_bullish = float(pdi.iloc[-1]) > float(mdi.iloc[-1])
        except Exception:
            pass

        # BB bandwidth percentile
        n_close = len(close)
        bb_widths = []
        for i in range(20, n_close + 1):
            w = close.iloc[i - 20 : i]
            if len(w) == 20:
                mid = w.mean()
                std = w.std()
                bw = (2 * std * 2) / (mid + 1e-9) * 100
                bb_widths.append(float(bw))
        if bb_widths:
            curr_bb = bb_widths[-1]
            bb_pctile = sum(1 for v in bb_widths if v <= curr_bb) / len(bb_widths) * 100
        else:
            bb_pctile = 50.0

        # Volume
        mean_vol_20 = float(vol.iloc[-20:].mean()) if len(vol) >= 20 else 1.0
        vol_ratio_20 = float(vol.iloc[-1] / mean_vol_20) if mean_vol_20 > 0 else 1.0
        mean_vol_5 = float(vol.iloc[-5:].mean()) if len(vol) >= 5 else 0
        vol_dryup = mean_vol_5 / mean_vol_20 if mean_vol_20 > 0 else 1.0

        # 52w prox
        high_52w = float(high.iloc[-252:].max()) if len(high) >= 252 else float(high.max())
        prox_52w = price / high_52w if high_52w > 0 else 1.0

        # EMAs
        ema50 = float(_ewm_ema(close, 50).iloc[-1]) if len(close) >= 50 else price
        ema200 = float(_ewm_ema(close, 200).iloc[-1]) if len(close) >= 200 else price
        trend_above_ema50 = price > ema50
        ema50_above_200_ok = ema50 > ema200

        # Momentum
        mom_1m = float(close.iloc[-1] / close.iloc[-21] - 1) if len(close) >= 21 else 0.0
        mom_3m = float(close.iloc[-1] / close.iloc[-63] - 1) if len(close) >= 63 else 0.0

        # RS vs SPY
        rs_spy_20 = 1.0
        rs_spy_63 = 1.0
        if spy_df is not None and len(spy_df) >= 63:
            spy_sliced = _cut(spy_df, asof)
            if len(spy_sliced) >= 63 and len(sym_df) >= 63:
                sym_ret_20 = float(close.iloc[-1] / close.iloc[-20]) if close.iloc[-20] != 0 else 1.0
                spy_ret_20 = float(spy_sliced["close"].iloc[-1] / spy_sliced["close"].iloc[-20]) if spy_sliced["close"].iloc[-20] != 0 else 1.0
                rs_spy_20 = round(sym_ret_20 / spy_ret_20, 4) if spy_ret_20 != 0 else 1.0
                sym_ret_63 = float(close.iloc[-1] / close.iloc[-63]) if close.iloc[-63] != 0 else 1.0
                spy_ret_63 = float(spy_sliced["close"].iloc[-1] / spy_sliced["close"].iloc[-63]) if spy_sliced["close"].iloc[-63] != 0 else 1.0
                rs_spy_63 = round(sym_ret_63 / spy_ret_63, 4) if spy_ret_63 != 0 else 1.0

        return {
            "rsi14": round(rsi14, 1),
            "macd_hist": round(macd_hist_val, 6),
            "macd_hist_rising": macd_hist_rising,
            "adx14": round(adx_val, 1),
            "di_bullish": di_bullish,
            "bb_bandwidth_pctile": round(bb_pctile, 1),
            "vol_ratio_20": round(vol_ratio_20, 2),
            "vol_dryup": round(vol_dryup, 2),
            "prox_52w": round(prox_52w, 3),
            "trend_above_ema50": trend_above_ema50,
            "ema50_above_200": ema50_above_200_ok,
            "mom_1m": round(mom_1m, 4),
            "mom_3m": round(mom_3m, 4),
            "rs_spy_20": rs_spy_20,
            "rs_spy_63": rs_spy_63,
        }
    except Exception:
        return None
