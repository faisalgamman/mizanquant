"""
Tests for scripts/signal_attribution.py - Phase 0 signal attribution.
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

# Ensure the project root is on sys.path so app.* imports work
_PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ not in sys.path:
    sys.path.insert(0, _PROJ)

# Import the attribution functions (importable module)
from scripts.signal_attribution import (  # noqa: E402
    reconstruct_features,
    compute_attribution,
    format_markdown_report,
)


def _utcnow():
    return datetime.now(timezone.utc)


def _make_price_df(n_days=300, trend=0.0005, noise=0.015, seed=42):
    """Generate synthetic daily OHLCV data."""
    rng = np.random.default_rng(seed)
    close = 100.0
    dates = []
    opens, highs, lows, closes, volumes = [], [], [], [], []
    for i in range(n_days):
        ret = trend + rng.normal(0, noise)
        close = close * (1 + ret)
        dates.append(_utcnow() - timedelta(days=n_days - i))
        o = close * (1 + rng.normal(0, 0.003))
        h = max(o, close) * (1 + abs(rng.normal(0, 0.005)))
        l_val = min(o, close) * (1 - abs(rng.normal(0, 0.005)))
        opens.append(o)
        highs.append(h)
        lows.append(l_val)
        closes.append(close)
        volumes.append(rng.integers(500_000, 5_000_000))
    df = pd.DataFrame({
        "open": opens, "high": highs, "low": lows,
        "close": closes, "volume": volumes,
    }, index=pd.DatetimeIndex(dates))
    return df


def _temp_sqlite_db(monkeypatch):
    """Create a temp sqlite DB with SignalHistory table and monkeypatch SessionLocal."""
    import app.db.models  # noqa: F401
    from app.db.database import Base
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    test_engine = create_engine(f"sqlite:///{db_path}", echo=False)
    Base.metadata.create_all(bind=test_engine)
    TestSessionLocal = sessionmaker(bind=test_engine)
    monkeypatch.setattr("app.db.database.SessionLocal", TestSessionLocal)
    monkeypatch.setattr("app.background.cache_manager.SessionLocal", TestSessionLocal)
    import app.db.database as _adb
    monkeypatch.setattr(_adb, "SessionLocal", TestSessionLocal)
    return TestSessionLocal, db_path


def _insert_signals(db_session, signals_data):
    from app.db.models import SignalHistory
    for s in signals_data:
        row = SignalHistory(**s)
        db_session.add(row)
    db_session.commit()


# -------------------------------------------------------
# Tests
# -------------------------------------------------------

class TestPerfectPredictiveFeature:

    def test_perfect_rank_ic(self):
        """Engineered feature that linearly predicts outcome has elevated |IC|."""
        rng = np.random.default_rng(42)
        rows = []
        for i in range(200):
            feat_val = (i - 100) * 0.1
            outcome = feat_val * 0.5 + rng.normal(0, 0.3)
            rows.append({
                "signal_id": i, "symbol": f"P{i:03d}",
                "signal_type": "swing", "signal": "BUY",
                "score": 75.0,
                "outcome_return_pct": round(float(outcome), 2),
                "regime": "NEUTRAL", "forecast_agrees": None,
                "rsi14": 50.0, "macd_hist": 0.0, "macd_hist_rising": False,
                "adx14": 25.0, "di_bullish": True,
                "bb_bandwidth_pctile": 50.0, "vol_ratio_20": 1.0,
                "vol_dryup": 1.0, "prox_52w": 0.9,
                "trend_above_ema50": True, "ema50_above_200": True,
                "mom_1m": round(float(feat_val / 100), 4),
                "mom_3m": 0.0, "rs_spy_20": 1.0, "rs_spy_63": 1.0,
            })

        features_df = pd.DataFrame(rows)
        sigs = [{"id": i, "details": {}} for i in range(200)]
        summary = compute_attribution(features_df, sigs, days=365, skipped=0)
        ic_rows = {r["feature"]: r["ic"] for r in summary["rank_ic"]}

        mom_ic = abs(ic_rows.get("mom_1m", 0))
        assert mom_ic > 0.7, f"mom_1m IC={mom_ic:.4f} expected >0.7"
        # Constant features produce NaN IC (zero variance) — verify they're excluded or NaN
        rsi_ic = ic_rows.get("rsi14", 0)
        assert np.isnan(rsi_ic) or abs(rsi_ic) < 0.1, \
            f"rsi14 IC={rsi_ic} — constant feature should have NaN or near-zero IC"


class TestNoiseFeature:

    def test_noise_rank_ic(self, monkeypatch):
        """Random outcomes should not produce spuriously high IC."""
        now = _utcnow()
        rng = np.random.default_rng(42)
        signals_data = []
        for i in range(100):
            sym = f"N{i:03d}"
            outcome = rng.normal(0, 5)
            signals_data.append({
                "symbol": sym, "signal_type": "swing", "signal": "BUY",
                "score": float(rng.integers(40, 90)), "price": 100.0,
                "created_at": now - timedelta(days=60),
                "outcome_return_pct": round(float(outcome), 2),
                "details": {"regime": "NEUTRAL"}, "id": i + 1,
            })

        TestSL, db_path = _temp_sqlite_db(monkeypatch)
        db = TestSL()
        try:
            _insert_signals(db, signals_data)
        finally:
            db.close()

        import app.services.market_data as _md

        def fake_fetch(sym, period="2y", start=None, end=None):
            if sym == "SPY":
                return _make_price_df(n_days=300, trend=0.0003, noise=0.012, seed=999)
            if sym.startswith("N"):
                return _make_price_df(n_days=150, trend=0.0, noise=0.03, seed=hash(sym) % 1000)
            return None

        monkeypatch.setattr(_md, "fetch", fake_fetch)

        sigs = []
        for sd in signals_data:
            sigs.append({
                "id": sd["id"], "symbol": sd["symbol"],
                "signal_type": sd["signal_type"], "signal": sd["signal"],
                "score": sd["score"], "price": sd["price"],
                "created_at": sd["created_at"].replace(tzinfo=timezone.utc)
                if sd["created_at"].tzinfo is None else sd["created_at"],
                "outcome_return_pct": sd["outcome_return_pct"],
                "details": sd["details"],
            })

        price_data = {}
        for sym in set(s["symbol"] for s in sigs):
            df = fake_fetch(sym)
            if df is not None:
                price_data[sym] = df
        price_data["SPY"] = fake_fetch("SPY")

        features_df, _ = reconstruct_features(sigs, price_data)
        summary = compute_attribution(features_df, sigs, days=365, skipped=0)
        ic_rows = {r["feature"]: r["ic"] for r in summary["rank_ic"]}

        rsi_ic = abs(ic_rows.get("rsi14", 0))
        assert rsi_ic < 0.50, f"rsi14 |IC|={rsi_ic:.4f} too high for random outcomes"

        try:
            from app.db.database import engine as _eng
            _eng.dispose()
        except Exception:
            pass
        try:
            os.unlink(db_path)
        except OSError:
            pass


class TestPerBandStats:

    def test_per_band_exact(self, monkeypatch):
        """Per-band: 10 wins + 10 losses with known magnitudes."""
        now = _utcnow()
        signals_data = []
        for i in range(10):
            signals_data.append({
                "symbol": f"B{i:03d}", "signal_type": "swing",
                "signal": "STRONG BUY", "score": 80.0, "price": 100.0,
                "created_at": now - timedelta(days=60),
                "outcome_return_pct": float(i + 1),
                "details": {"regime": "BULL"}, "id": i + 1,
            })
        for i in range(10):
            signals_data.append({
                "symbol": f"B{i+10:03d}", "signal_type": "swing",
                "signal": "STRONG BUY", "score": 80.0, "price": 100.0,
                "created_at": now - timedelta(days=60),
                "outcome_return_pct": float(-(i + 1)),
                "details": {"regime": "BULL"}, "id": i + 11,
            })

        features_rows = []
        for sd in signals_data:
            features_rows.append({
                "signal_id": sd["id"], "symbol": sd["symbol"],
                "signal_type": sd["signal_type"], "signal": sd["signal"],
                "score": sd["score"],
                "outcome_return_pct": sd["outcome_return_pct"],
                "regime": sd["details"]["regime"],
                "forecast_agrees": None,
                "rsi14": 50.0, "macd_hist": 0.0, "macd_hist_rising": False,
                "adx14": 25.0, "di_bullish": True,
                "bb_bandwidth_pctile": 50.0, "vol_ratio_20": 1.0,
                "vol_dryup": 1.0, "prox_52w": 0.9,
                "trend_above_ema50": True, "ema50_above_200": True,
                "mom_1m": 0.0, "mom_3m": 0.0,
                "rs_spy_20": 1.0, "rs_spy_63": 1.0,
            })

        features_df = pd.DataFrame(features_rows)
        sigs = [{"id": sd["id"], "details": sd["details"]} for sd in signals_data]
        summary = compute_attribution(features_df, sigs, days=365, skipped=0)

        per_band = summary["per_band"]
        strong_buy = next(b for b in per_band if b["signal"] == "STRONG BUY")
        assert strong_buy["n"] == 20
        assert strong_buy["win_rate"] == 50.0
        wins_sum = sum(range(1, 11))
        losses_sum = abs(sum(-(i+1) for i in range(10)))
        expected_pf = round(wins_sum / losses_sum, 2)
        assert strong_buy["profit_factor"] == expected_pf


class TestDecileMonotonicity:

    def test_decile_monotonic(self):
        """Score deciles: top decile avg > bottom decile avg."""
        rng = np.random.default_rng(42)
        rows = []
        for i in range(100):
            score = i
            outcome = score * 0.1 + rng.normal(0, 2)
            rows.append({
                "signal_id": i, "symbol": f"D{i:03d}",
                "signal_type": "swing", "signal": "BUY",
                "score": float(score),
                "outcome_return_pct": round(float(outcome), 2),
                "regime": "NEUTRAL", "forecast_agrees": None,
                "rsi14": 50.0, "macd_hist": 0.0, "macd_hist_rising": False,
                "adx14": 25.0, "di_bullish": True,
                "bb_bandwidth_pctile": 50.0, "vol_ratio_20": 1.0,
                "vol_dryup": 1.0, "prox_52w": 0.9,
                "trend_above_ema50": True, "ema50_above_200": True,
                "mom_1m": 0.0, "mom_3m": 0.0,
                "rs_spy_20": 1.0, "rs_spy_63": 1.0,
            })

        features_df = pd.DataFrame(rows)
        sigs = [{"id": i, "details": {}} for i in range(100)]
        summary = compute_attribution(features_df, sigs, days=365, skipped=0)

        per_decile = summary["per_decile"]
        assert len(per_decile) >= 3
        top_decile = per_decile[-1]
        bot_decile = per_decile[0]
        assert top_decile["avg_outcome"] > bot_decile["avg_outcome"]


class TestTzNaivePrices:

    def test_naive_price_index_with_aware_created_at(self):
        """PRODUCTION shape: market_data.fetch dates are tz-NAIVE while
        SignalHistory created_at is tz-aware UTC. The feature reconstruction
        must NOT silently skip such signals (regression for the aware-vs-naive
        TypeError that the broad except used to swallow)."""
        n = 300
        naive_idx = pd.DatetimeIndex(pd.date_range(end=datetime.now(), periods=n, freq="D"))
        rng = np.random.default_rng(7)
        closes = 100 * np.cumprod(1 + rng.normal(0.0005, 0.01, n))
        df = pd.DataFrame({
            "open": closes, "high": closes * 1.01, "low": closes * 0.99,
            "close": closes, "volume": np.full(n, 1_000_000.0),
        }, index=naive_idx)

        sig = {
            "id": 1, "symbol": "TZX", "signal_type": "swing", "signal": "BUY",
            "score": 70.0, "price": float(closes[-1]),
            "created_at": _utcnow() - timedelta(days=30),   # tz-AWARE
            "outcome_return_pct": 2.5, "details": {"regime": "BULL"},
        }
        features_df, skipped = reconstruct_features([sig], {"TZX": df, "SPY": df})
        assert skipped == 0, "aware created_at vs naive price index must not skip the signal"
        assert len(features_df) == 1
        assert "rsi14" in features_df.columns


class TestMarkdownReport:

    def test_report_generates(self):
        summary = {
            "header": {
                "as_of": "2026-06-11T00:00:00", "days": 365, "n": 100,
                "overall_wr": 48.6, "overall_pf": 0.92, "skipped_symbols": 5,
            },
            "rank_ic": [
                {"feature": "mom_1m", "ic": 0.045, "n": 2500},
                {"feature": "rsi14", "ic": -0.012, "n": 2500},
            ],
            "per_band": [
                {"signal_type": "swing", "signal": "STRONG BUY", "n": 30,
                 "win_rate": 53.3, "avg_ret": 1.2, "median_ret": 0.8,
                 "profit_factor": 1.15},
            ],
            "per_decile": [
                {"decile": 0, "n": 10, "score_min": 10.0, "score_max": 25.0,
                 "avg_outcome": -2.5},
                {"decile": 9, "n": 10, "score_min": 88.0, "score_max": 99.0,
                 "avg_outcome": 4.8},
            ],
            "per_regime": [
                {"regime": "BULL", "n": 40, "win_rate": 55.0, "avg_ret": 2.1,
                 "profit_factor": 1.3},
            ],
            "per_forecast": [
                {"forecast_agrees": "True", "n": 30, "win_rate": 60.0,
                 "avg_ret": 2.5, "profit_factor": 1.5},
            ],
            "verdict": {
                "candidate_features": ["mom_1m"],
                "noise_features": ["rsi14"],
                "caveats": ["Caveat 1", "Caveat 2", "Caveat 3"],
            },
        }
        md = format_markdown_report(summary)
        assert "# Signal Attribution Report" in md
        assert "mom_1m" in md
        assert "CANDIDATE" in md
        assert "noise" in md
        assert "Caveat 1" in md
        assert "carries positive rank information" in md
