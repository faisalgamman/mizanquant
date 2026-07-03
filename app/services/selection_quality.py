"""Selection-quality scorecard for the dashboard — the honest, at-a-glance answer to
**"does our stock SELECTION actually add value over just buying SPY?"**

Bundles two READ-ONLY measurements already in the codebase:
  • ``beta_benchmark.alpha_vs_spy`` — each closed trade's return minus SPY over the SAME
    holding window → mean alpha + a t-stat (so a near-zero result on a small sample is not
    mistaken for edge).
  • ``signal_calibration.calibration_report`` — does a higher scanner score actually yield
    a higher forward return (score→return rank correlation)?

…into one plain-language GRADE per scanner. It is deliberately conservative: it never
claims edge on a thin sample or without statistical significance. Pure measurement from
the paper ledgers (no trade-path impact); cached because it does not change intra-session.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

logger = logging.getLogger("screener")

_CACHE: dict = {"at": 0.0, "scanners": None}
_TTL = 1800.0            # 30 min — this is slow-moving measurement, not live data
_MIN_N = 20             # below this the sample is too thin to grade honestly

# dashboard scanner → (ledger strategy id, Arabic label)
_SCANNERS = (("weekly", "PV", "الأسبوعي"), ("monthly", "PVM", "الشهري"))


def _grade(alpha, t, n) -> dict:
    """Numbers → honest verdict + a colour key the UI maps to a token.

    ``good`` = positive alpha that is statistically distinguishable from zero;
    ``warn`` = profitable but that profit is market beta, not proven selection edge;
    ``bad``  = significantly WORSE than SPY; ``muted`` = not enough data / no edge.
    """
    if n < _MIN_N:
        return {"key": "insufficient", "grade": "—",
                "label": "بيانات غير كافية بعد", "color": "muted"}
    if t is not None and t <= -2:
        return {"key": "negative", "grade": "ضعيف",
                "label": "أسوأ من SPY — ألفا سالبة مؤكَّدة", "color": "bad"}
    if t is not None and t >= 2 and (alpha or 0) > 0:
        return {"key": "alpha", "grade": "قوي",
                "label": "يتفوّق على SPY — ألفا موجبة مؤكَّدة", "color": "good"}
    if (alpha or 0) > 0:
        return {"key": "beta", "grade": "بيتا",
                "label": "ربح من السوق لا من الاختيار — لا ألفا مؤكَّدة", "color": "warn"}
    return {"key": "flat", "grade": "محايد",
            "label": "لا قيمة اختيار مقاسة بعد", "color": "muted"}


def _one(scanner: str, strat: str, name_ar: str) -> dict:
    from app.services.beta_benchmark import alpha_vs_spy
    from app.services.signal_calibration import calibration_report

    a = alpha_vs_spy(strategy_ids=(strat,), days=365)
    n = int(a.get("n") or 0)

    rank_corr = None
    try:
        rank_corr = calibration_report(scanner, min_n=_MIN_N).get("score_return_rank_corr")
    except Exception as e:
        logger.debug("selection_quality calibration %s failed: %s", scanner, e)

    g = _grade(a.get("mean_alpha"), a.get("alpha_t"), n)
    return {
        "scanner": scanner,
        "name": name_ar,
        "n": n,
        "alpha": a.get("mean_alpha"),          # mean (trade − SPY) %/trade over its window
        "alpha_t": a.get("alpha_t"),           # |t| ≥ ~2 ⇒ distinguishable from zero
        "pct_beat_spy": a.get("pct_beat_spy"),  # % of trades that beat SPY
        "score_rank_corr": rank_corr,          # higher score → higher return? (+1 best)
        **g,
    }


def _read_estimate():
    """Fast ESTIMATE (no ledger wait): the history gate A/B uplift + the RS Information
    Coefficient. Read FRESH each call from factor_lab's own 6h cache (cheap; warms in the
    background if cold) so it appears as soon as the warm finishes, not on the summary TTL."""
    try:
        from app.services.factor_lab import factor_lab_cached
        rep = factor_lab_cached(warm=True)   # cache-only; never blocks the request path
        if rep is None:
            return None
        gab = rep.get("gate_ab") or {}
        ic_rs = rep.get("ic_rs") or {}
        # if the replay produced no usable numbers (empty universe / thin data), report
        # None so the UI shows the "computing" hint rather than a row full of dashes.
        if gab.get("alpha_uplift_pct") is None and ic_rs.get("mean_ic") is None:
            return None
        return {
            "gate_alpha_uplift_pct": gab.get("alpha_uplift_pct"),
            "gate_t_pass_vs_fail": gab.get("t_pass_vs_fail"),
            "rs_ic": ic_rs.get("mean_ic"),
            "rs_ic_ir": ic_rs.get("ic_ir"),
            "note": "تقدير تاريخي فوري (آمن ضد التطلّع، أسعار فقط) — يوجّه الآن، والدفتر يؤكّد.",
        }
    except Exception as e:
        logger.debug("selection_quality estimate failed: %s", e)
        return None


def _read_gate_reco():
    """④ The self-calibrating gate's OOS recommendation + the current live threshold, read
    FRESH from factor_lab's gate-calibration cache (cheap; the heavy compute is the warm)."""
    try:
        from app.services.factor_lab import gate_calibration_cached
        from app.services.gate_config import gate_config_state
        cal = gate_calibration_cached()
        state = gate_config_state()
        reco = (cal or {}).get("recommendation") if cal else None
        return {"current_min_rs": state.get("min_rs"),
                "source": state.get("source"),
                "recommendation": reco,
                "updated_at": state.get("updated_at")}
    except Exception as e:
        logger.debug("gate reco read failed: %s", e)
        return None


def _read_overlays():
    """Quant-fund overlays for the scorecard: ① capture size, ② meta-model AUC, ④ HMM
    regime, ⑤ gate PBO trust. Cheap reads (capture/meta are DB/JSON; regime + PBO come
    from factor_lab's 6h cache) — never computes on the request path."""
    try:
        from app.services.alpha_capture import capture_status
        from app.services.meta_label import meta_model_status
        from app.services.factor_lab import factor_lab_cached
        rep = factor_lab_cached() or {}
        cap = capture_status()
        meta = meta_model_status()
        regime = rep.get("regime")
        pbo = ((rep.get("gate_calibration") or {}).get("pbo") or {}) if isinstance(rep.get("gate_calibration"), dict) else {}
        enb = rep.get("concentration") or {}
        return {
            "capture_rows": cap.get("rows"), "capture_labelled": cap.get("labelled"),
            "meta_status": meta.get("status"), "meta_auc": meta.get("auc_in_sample"),
            "meta_oos_auc": meta.get("oos_auc"), "meta_trusted": meta.get("trusted"),
            "regime": regime.get("dominant") if isinstance(regime, dict) else None,
            "crisis_prob": regime.get("crisis_prob") if isinstance(regime, dict) else None,
            "pbo": pbo.get("pbo"), "pbo_trust": pbo.get("trust"),
            "enb": enb.get("enb"), "enb_ratio": enb.get("enb_ratio"),
            "avg_corr": enb.get("avg_pairwise_corr"),
            "concentration": enb.get("concentration"), "enb_n": enb.get("n"),
        }
    except Exception as e:
        logger.debug("overlays read failed: %s", e)
        return None


def selection_quality_summary(force: bool = False) -> dict:
    """Per-scanner selection-quality scorecard (weekly + monthly). The per-scanner alpha/
    calibration block is cached ~30 min; the fast history estimate is read fresh each call."""
    now = time.time()
    cached = _CACHE.get("scanners")
    if force or cached is None or (now - _CACHE["at"]) >= _TTL:
        scanners = []
        for scanner, strat, name in _SCANNERS:
            try:
                scanners.append(_one(scanner, strat, name))
            except Exception as e:
                logger.debug("selection_quality %s failed: %s", scanner, e)
                scanners.append({"scanner": scanner, "name": name, "n": 0,
                                 "key": "insufficient", "grade": "—",
                                 "label": "تعذّر القياس", "color": "muted"})
        _CACHE.update(at=now, scanners=scanners)
        cached = scanners

    return {
        "scanners": cached,
        "estimate": _read_estimate(),
        "gate": _read_gate_reco(),
        "overlays": _read_overlays(),
        "as_of": datetime.now(timezone.utc).isoformat(),
        "method": ("مقياس أمين: عائد كل صفقة مُغلقة مطروحاً منه عائد SPY على نفس نافذة "
                   "الاحتفاظ (ألفا)، مع دلالة إحصائية (t)، وارتباط الرتبة بين الدرجة "
                   "والعائد. قياس فقط من الدفتر الورقي — لا يؤثّر في التنفيذ."),
        "caveat": "عيّنات صغيرة (<100 صفقة) إرشادية لا قاطعة — دع الدفتر يتراكم.",
    }


__all__ = ["selection_quality_summary"]
