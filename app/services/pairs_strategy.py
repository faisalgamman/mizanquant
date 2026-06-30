"""Long-only halal pairs-trading strategy (Phase 3B).

The system is strictly long-only (no short selling — both a code constraint
and a halal requirement), so a classic dollar-neutral long/short pairs trade
is impossible.  Instead this implements a **relative-value long-only** rule:
at a spread extreme we BUY whichever leg is *relatively undervalued* and ride
the mean reversion, exiting when the spread returns to its mean.

For a cointegrated pair (y, x) with spread = y − β·x − α and z-score z:
  • z ≤ −entry_z  → y is cheap relative to x   → BUY y
  • z ≥ +entry_z  → y is rich relative to x    → BUY x  (x is the cheap leg)
  • |z| ≤ exit_z  → reversion complete         → EXIT the open leg
  • otherwise     → hold

Every entry routes through trading_engine.execute_buy, which already enforces
the kill-switch, halal screen, risk sizing, portfolio-stop, and (Phase 2)
execution-cost gate + smart routing.  Exits route through execute_sell, which
only closes a position we actually hold.  No new execution code is introduced.

Environment
-----------
PAIRS_TRADING_ENABLED   Execute live trades (default "true"). When false the
                        cycle still computes/returns signals but does NOT
                        place orders (useful for the API + dry monitoring).
PAIRS_ENTRY_Z           |z| at/above which we open (default 2.0).
PAIRS_EXIT_Z            |z| at/below which we exit (default 0.5).
PAIRS_ATR_SL            Stop-loss = entry − k·ATR on the long leg (default 2.0).
PAIRS_ATR_TP            Take-profit = entry + k·ATR on the long leg (default 3.0).
PAIRS_BREAKDOWN_Z       |z| at/above which the spread is treated as BLOWN OUT —
                        the relationship is diverging, not reverting, so we CUT
                        the position instead of holding/adding (default 4.0 —
                        a full 2σ of room past the 2.0 entry before cutting).
PAIRS_BREAKDOWN_PVAL    Re-test Engle–Granger on a recent window; p above this
                        means cointegration has decayed → cut (default 0.10).
PAIRS_BREAKDOWN_LOOKBACK  Bars used for that recent re-test (default 90).
"""

from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass

logger = logging.getLogger("screener")

PAIRS_STRATEGY_ID = "PAIRS"  # fits TradeHistory.strategy_id String(5)

# ── Config (env-driven) ──────────────────────────────────────────────────────

PAIRS_TRADING_ENABLED: bool = (
    os.environ.get("PAIRS_TRADING_ENABLED", "true").lower() not in ("false", "0", "no")
)
PAIRS_ENTRY_Z: float = float(os.environ.get("PAIRS_ENTRY_Z", "2.0"))
PAIRS_EXIT_Z: float = float(os.environ.get("PAIRS_EXIT_Z", "0.5"))
PAIRS_ATR_SL: float = float(os.environ.get("PAIRS_ATR_SL", "2.0"))
PAIRS_ATR_TP: float = float(os.environ.get("PAIRS_ATR_TP", "3.0"))
# Breakdown stop — the #1 risk in pairs is the relationship BREAKING (the spread
# diverges and never reverts). These two independent triggers cut such a position.
PAIRS_BREAKDOWN_Z: float = float(os.environ.get("PAIRS_BREAKDOWN_Z", "4.0"))
PAIRS_BREAKDOWN_PVAL: float = float(os.environ.get("PAIRS_BREAKDOWN_PVAL", "0.10"))
PAIRS_BREAKDOWN_LOOKBACK: int = int(os.environ.get("PAIRS_BREAKDOWN_LOOKBACK", "90"))
# Kalman time-varying hedge ratio — adapts β to relationship drift instead of a
# stale static OLS β. Drives the trading spread/z only; pair *identification*
# (Engle-Granger) stays static. Fail-open to OLS, so it can never break a signal.
PAIRS_KALMAN: bool = (
    os.environ.get("PAIRS_KALMAN", "true").lower() not in ("false", "0", "no")
)
PAIRS_KALMAN_DELTA: float = float(os.environ.get("PAIRS_KALMAN_DELTA", "0.0001"))


# ── Data object ───────────────────────────────────────────────────────────────

@dataclass
class PairSignal:
    """Desired state for a pair at the current bar.

    action ∈ {"long_y", "long_x", "exit", "hold"}.  long_symbol is the leg to
    BUY for entries (None for exit/hold).
    """
    pair: str
    action: str
    long_symbol: str | None
    entry: float
    stop_loss: float
    take_profit: float
    zscore: float
    hedge_ratio: float
    half_life: float
    confidence: float
    reason: str

    def as_dict(self) -> dict:
        return {
            "pair": self.pair,
            "action": self.action,
            "long_symbol": self.long_symbol,
            "entry": round(self.entry, 2),
            "stop_loss": round(self.stop_loss, 2),
            "take_profit": round(self.take_profit, 2),
            "zscore": round(self.zscore, 3),
            "hedge_ratio": round(self.hedge_ratio, 4),
            "half_life_bars": round(self.half_life, 2),
            "confidence": round(self.confidence, 1),
            "reason": self.reason,
        }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _atr_or_pct(df, price: float) -> float:
    """ATR of the long leg (technical.atr), or 1% of price as a fallback."""
    try:
        if df is not None and all(c in df.columns for c in ("high", "low", "close")) and len(df) >= 15:
            from app.services.technical import atr as _atr
            a = float(_atr(df).iloc[-1])
            if math.isfinite(a) and a > 0:
                return a
    except Exception:
        pass
    return max(price * 0.01, 0.01)


def _confidence_from_z(z: float) -> float:
    return float(min(95.0, 55.0 + abs(z) * 12.0))


def pair_breakdown(z: float, y_close=None, x_close=None) -> tuple[bool, str | None]:
    """Has this pair's cointegration BROKEN? (the #1 way pairs trades lose).

    Two independent triggers — either one cuts the position:
      • spread blowout: |z| ≥ PAIRS_BREAKDOWN_Z — the spread has diverged far
        past the entry extreme and is NOT reverting (the thesis is failing). This
        also catches the long-only blind spot where our held leg is flat but the
        *other* leg ran away, so the ATR stop never fires.
      • cointegration decay: re-running Engle–Granger on the last
        PAIRS_BREAKDOWN_LOOKBACK bars gives p > PAIRS_BREAKDOWN_PVAL — the
        statistical relationship that justified the trade has weakened.

    The price-series test is fail-open: any error → no breakdown signalled, so a
    data hiccup can never force-close a position. Returns (cut?, reason).
    """
    # 1) spread blowout — primary, always available from z alone
    if math.isfinite(z) and abs(z) >= PAIRS_BREAKDOWN_Z:
        return True, (f"spread blowout |z|={abs(z):.2f} ≥ {PAIRS_BREAKDOWN_Z} "
                      f"— diverging, not reverting")
    # 2) cointegration decay — secondary, needs the two price series
    if y_close is not None and x_close is not None:
        try:
            from app.services.cointegration import engle_granger_pvalue
            n = PAIRS_BREAKDOWN_LOOKBACK
            yc = list(y_close)[-n:]
            xc = list(x_close)[-n:]
            if len(yc) >= 60 and len(xc) >= 60 and len(yc) == len(xc):
                p = engle_granger_pvalue(yc, xc)
                if math.isfinite(p) and p > PAIRS_BREAKDOWN_PVAL:
                    return True, (f"cointegration decay p={p:.3f} > "
                                  f"{PAIRS_BREAKDOWN_PVAL} on last {n} bars")
        except Exception:
            pass
    return False, None


def pairs_spread(y_close, x_close):
    """Spread series that drives the trading z-score.

    Kalman time-varying hedge ratio when PAIRS_KALMAN is on (adapts to drift),
    else the static OLS spread. Fail-open: any Kalman error or a too-short result
    falls back to the OLS spread, so the signal path can never break.
    """
    from app.services.cointegration import spread_series

    if PAIRS_KALMAN:
        try:
            from app.services.kalman_hedge import kalman_spread_series
            s = kalman_spread_series(y_close, x_close, delta=PAIRS_KALMAN_DELTA)
            if s is not None and len(s) >= 60:
                return s
        except Exception:
            pass
    return spread_series(y_close, x_close)


# ── Core evaluation ─────────────────────────────────────────────────────────

def evaluate_pair(report, y_df, x_df) -> PairSignal | None:
    """Compute the desired long-only state for one cointegrated pair.

    Parameters
    ----------
    report : pairs_scanner.PairReport
    y_df, x_df : aligned OHLC DataFrames (same DatetimeIndex) for the two legs.
                 Must contain a "close" column; "high"/"low" enable ATR stops.

    Returns a PairSignal, or None if data is insufficient.
    """
    from app.services.cointegration import spread_zscore

    try:
        y_close = y_df["close"].astype(float).values
        x_close = x_df["close"].astype(float).values
    except Exception:
        return None

    if len(y_close) < 60 or len(x_close) < 60 or len(y_close) != len(x_close):
        return None

    spread = pairs_spread(y_close, x_close)
    z = spread_zscore(spread)
    if not math.isfinite(z):
        return None

    pair_key = report.pair_key
    y_sym, x_sym = report.y_symbol, report.x_symbol

    # ── Exit zone ──
    if abs(z) <= PAIRS_EXIT_Z:
        return PairSignal(
            pair=pair_key, action="exit", long_symbol=None,
            entry=0.0, stop_loss=0.0, take_profit=0.0,
            zscore=z, hedge_ratio=report.hedge_ratio, half_life=report.half_life,
            confidence=_confidence_from_z(z),
            reason=f"|z|={abs(z):.2f} ≤ exit_z={PAIRS_EXIT_Z} — reversion complete",
        )

    # ── Breakdown zone (checked before entry) ──
    # A spread this far out is failing, not setting up: cut a held leg, and never
    # OPEN here (this branch returns "exit", so compute_signals won't enter it).
    broke, why = pair_breakdown(z, y_close, x_close)
    if broke:
        return PairSignal(
            pair=pair_key, action="exit", long_symbol=None,
            entry=0.0, stop_loss=0.0, take_profit=0.0,
            zscore=z, hedge_ratio=report.hedge_ratio, half_life=report.half_life,
            confidence=_confidence_from_z(z),
            reason=f"breakdown — {why}",
        )

    # ── Entry zone ──
    if abs(z) >= PAIRS_ENTRY_Z:
        if z <= 0:
            # y cheap relative to x → buy y
            long_sym, long_df = y_sym, y_df
            action = "long_y"
        else:
            # y rich relative to x → x is the cheap leg → buy x
            long_sym, long_df = x_sym, x_df
            action = "long_x"

        entry = float(long_df["close"].astype(float).values[-1])
        atr_val = _atr_or_pct(long_df, entry)
        stop_loss = round(entry - PAIRS_ATR_SL * atr_val, 2)
        take_profit = round(entry + PAIRS_ATR_TP * atr_val, 2)

        return PairSignal(
            pair=pair_key, action=action, long_symbol=long_sym,
            entry=entry, stop_loss=stop_loss, take_profit=take_profit,
            zscore=z, hedge_ratio=report.hedge_ratio, half_life=report.half_life,
            confidence=_confidence_from_z(z),
            reason=(
                f"z={z:.2f} ⇒ buy relatively-undervalued leg {long_sym} "
                f"(half-life={report.half_life:.1f} bars)"
            ),
        )

    # ── Dead zone ──
    return PairSignal(
        pair=pair_key, action="hold", long_symbol=None,
        entry=0.0, stop_loss=0.0, take_profit=0.0,
        zscore=z, hedge_ratio=report.hedge_ratio, half_life=report.half_life,
        confidence=0.0,
        reason=f"exit_z<|z|={abs(z):.2f}<entry_z — hold",
    )


# ── Data access ───────────────────────────────────────────────────────────────

def _aligned_ohlc(y_sym: str, x_sym: str, lookback: str = "1y"):
    """Fetch both legs and align them on their common DatetimeIndex.

    Returns (y_df, x_df) reindexed to shared dates, or (None, None).
    """
    try:
        import pandas as pd

        from app.services.market_data import fetch as fetch_market_data

        y_raw = fetch_market_data(y_sym, period=lookback)
        x_raw = fetch_market_data(x_sym, period=lookback)
        if y_raw is None or x_raw is None or y_raw.empty or x_raw.empty:
            return None, None

        # Normalise column names to lowercase
        y_raw = y_raw.rename(columns={c: c.lower() for c in y_raw.columns})
        x_raw = x_raw.rename(columns={c: c.lower() for c in x_raw.columns})

        common = y_raw.index.intersection(x_raw.index)
        if len(common) < 60:
            return None, None
        return y_raw.loc[common], x_raw.loc[common]
    except Exception as exc:
        logger.debug("pairs_strategy: alignment failed for %s/%s: %s", y_sym, x_sym, exc)
        return None, None


def _pairs_held_symbols() -> set[str]:
    """Symbols the PAIRS strategy bought that the broker still holds.

    Intersection of (current broker positions) ∩ (PAIRS buy history) — the
    broker is the source of truth for "still open", TradeHistory tells us the
    position belongs to the pairs strategy.
    """
    held: set[str] = set()
    try:
        from app.services.broker.factory import get_broker

        broker = get_broker(strategy_id=PAIRS_STRATEGY_ID)
        positions = broker.get_positions(strategy_id=PAIRS_STRATEGY_ID) if broker else []
        held = {p.get("symbol", "").upper() for p in (positions or []) if p.get("symbol")}
    except Exception as exc:
        logger.debug("pairs_strategy: broker positions lookup failed: %s", exc)
        return set()

    try:
        from app.db.database import SessionLocal
        from app.db.models import TradeHistory

        db = SessionLocal()
        try:
            rows = (
                db.query(TradeHistory.symbol)
                .filter(TradeHistory.strategy_id == PAIRS_STRATEGY_ID)
                .filter(TradeHistory.side == "buy")
                .all()
            )
            pairs_bought = {r[0].upper() for r in rows if r[0]}
        finally:
            db.close()
    except Exception as exc:
        logger.debug("pairs_strategy: PAIRS buy history lookup failed: %s", exc)
        return set()

    return held & pairs_bought


# ── Signal computation (execution-free; used by API) ─────────────────────────

def compute_signals(max_pairs: int | None = None) -> list[PairSignal]:
    """Scan cointegrated pairs and compute current long-only signals.

    No orders are placed. Used by the API and by run_pairs_cycle.
    """
    from app.services.pairs_scanner import find_cointegrated_pairs

    signals: list[PairSignal] = []
    for report in find_cointegrated_pairs(max_pairs=max_pairs):
        y_df, x_df = _aligned_ohlc(report.y_symbol, report.x_symbol)
        if y_df is None or x_df is None:
            continue
        sig = evaluate_pair(report, y_df, x_df)
        if sig is not None:
            signals.append(sig)
    return signals


# ── Live cycle (entry/exit reconciliation) ───────────────────────────────────

def run_pairs_cycle(dry_run: bool = False) -> dict:
    """Compute signals and reconcile against open positions.

    Exits first (free capital), then entries. Execution is skipped when
    PAIRS_TRADING_ENABLED is false or dry_run is true — signals are still
    returned so the run is observable.
    """
    from app.services.trading_engine import execute_buy, execute_sell

    signals = compute_signals()
    held = _pairs_held_symbols()
    execute = PAIRS_TRADING_ENABLED and not dry_run

    entries = 0
    exits = 0
    actions: list[dict] = []

    # ── Exits first ──
    for sig in signals:
        if sig.action != "exit":
            continue
        # Exit whichever leg of this pair is currently held
        y_sym, x_sym = sig.pair.split("/", 1)
        for leg in (y_sym, x_sym):
            if leg.upper() in held:
                rec = {"pair": sig.pair, "action": "exit", "symbol": leg, "z": round(sig.zscore, 3)}
                if execute:
                    try:
                        res = execute_sell(leg, sig.entry or 0.0, sig.confidence, strategy_id=PAIRS_STRATEGY_ID)
                        rec["executed"] = bool(res and res.get("executed"))
                        rec["reason"] = res.get("reason") if res else None
                        if rec["executed"]:
                            exits += 1
                    except Exception as exc:
                        rec["executed"] = False
                        rec["reason"] = str(exc)
                        logger.warning("pairs exit failed for %s: %s", leg, exc)
                else:
                    rec["executed"] = False
                    rec["reason"] = "execution disabled"
                actions.append(rec)

    # ── Entries ──
    for sig in signals:
        if sig.action not in ("long_y", "long_x") or not sig.long_symbol:
            continue
        y_sym, x_sym = sig.pair.split("/", 1)
        # Skip if either leg is already held (avoid stacking / double exposure)
        if y_sym.upper() in held or x_sym.upper() in held:
            continue

        rec = {
            "pair": sig.pair, "action": sig.action, "symbol": sig.long_symbol,
            "z": round(sig.zscore, 3), "entry": sig.entry,
            "stop_loss": sig.stop_loss, "take_profit": sig.take_profit,
        }
        if execute:
            try:
                res = execute_buy(
                    symbol=sig.long_symbol,
                    price=sig.entry,
                    stop_loss=sig.stop_loss,
                    take_profit=sig.take_profit,
                    confidence=sig.confidence,
                    signal_details={
                        "strategy": "pairs",
                        "pair": sig.pair,
                        "long_symbol": sig.long_symbol,
                        "zscore": sig.zscore,
                        "hedge_ratio": sig.hedge_ratio,
                        "half_life": sig.half_life,
                        "reason": sig.reason,
                    },
                    strategy_id=PAIRS_STRATEGY_ID,
                )
                rec["executed"] = bool(res and res.get("executed"))
                rec["reason"] = res.get("reason") if res else None
                if rec["executed"]:
                    entries += 1
                    held.add(sig.long_symbol.upper())  # prevent same-cycle re-entry
            except Exception as exc:
                rec["executed"] = False
                rec["reason"] = str(exc)
                logger.warning("pairs entry failed for %s: %s", sig.long_symbol, exc)
        else:
            rec["executed"] = False
            rec["reason"] = "execution disabled"
        actions.append(rec)

    summary = {
        "enabled": PAIRS_TRADING_ENABLED,
        "dry_run": dry_run,
        "pairs_scanned": len(signals),
        "entries": entries,
        "exits": exits,
        "actions": actions,
        "signals": [s.as_dict() for s in signals],
    }
    logger.info(
        "pairs cycle: %d pairs, %d entries, %d exits (execute=%s)",
        len(signals), entries, exits, execute,
    )
    return summary


__all__ = [
    "PairSignal",
    "PAIRS_STRATEGY_ID",
    "PAIRS_TRADING_ENABLED",
    "pair_breakdown",
    "pairs_spread",
    "evaluate_pair",
    "compute_signals",
    "run_pairs_cycle",
]
