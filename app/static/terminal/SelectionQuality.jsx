// SelectionQuality.jsx — the honest "does our SELECTION actually beat SPY?" scorecard.
// Self-fetching strip on the main screen. Data: GET /api/selection-quality (alpha-vs-SPY
// t-stat + score→return calibration → a plain-language grade per scanner). No deps.
const { useState, useEffect } = React;

const SQ_COLOR = {
  good: "var(--positive)", warn: "var(--warning)",
  bad: "var(--negative)", muted: "var(--text-muted)",
};

function _sqPct(v, dp = 2) {
  return (v == null || isNaN(v)) ? "—" : (v >= 0 ? "+" : "") + Number(v).toFixed(dp) + "%";
}
function _sqNum(v, dp = 2) {
  return (v == null || isNaN(v)) ? "—" : Number(v).toFixed(dp);
}

function SelectionQuality() {
  const [data, setData] = useState(null);   // null = loading, {…} = loaded, {error} = failed

  useEffect(() => {
    let alive = true;
    fetch("/api/selection-quality")
      .then(r => r.json())
      .then(j => { if (alive) setData(j || {}); })
      .catch(e => { if (alive) setData({ error: String(e) }); });
    return () => { alive = false; };
  }, []);

  const scanners = (data && Array.isArray(data.scanners)) ? data.scanners : [];

  return (
    <div className="selq" dir="rtl">
      <div className="selq-head">
        <span className="selq-title">جودة اختيار الأسهم</span>
        <span className="selq-sub" title={(data && data.method) || ""}>
          هل يتفوّق الاختيار على SPY؟ · قياس أمين من الدفتر الورقي
        </span>
      </div>

      <div className="selq-cards">
        {data === null && <div className="selq-empty">…يقيس</div>}
        {data && data.error && <div className="selq-empty">تعذّر تحميل القياس</div>}
        {scanners.map(s => {
          const c = SQ_COLOR[s.color] || SQ_COLOR.muted;
          const aCol = s.alpha > 0 ? "var(--positive)" : (s.alpha < 0 ? "var(--negative)" : "inherit");
          return (
            <div className="selq-card" key={s.scanner}>
              <div className="selq-card-top">
                <span className="selq-name">{s.name}</span>
                <span className="selq-grade" style={{ color: c, borderColor: c }}>{s.grade}</span>
              </div>
              <div className="selq-label" style={{ color: c }}>{s.label}</div>
              <div className="selq-metrics">
                <span title="متوسط (عائد الصفقة − SPY) على نفس نافذة الاحتفاظ">
                  ألفا <b style={{ color: aCol }}>{_sqPct(s.alpha)}</b>/صفقة
                </span>
                <span title="الدلالة الإحصائية — |t| ≥ 2 يعني مميّز عن الصفر">
                  t <b>{_sqNum(s.alpha_t)}</b>
                </span>
                <span title="نسبة الصفقات التي تفوّقت على SPY">
                  تفوّق <b>{s.pct_beat_spy == null ? "—" : s.pct_beat_spy + "%"}</b>
                </span>
                <span title="ارتباط الرتبة بين الدرجة والعائد — أعلى يعني درجة أعلى ⇐ عائد أعلى">
                  معايرة <b>{_sqNum(s.score_rank_corr)}</b>
                </span>
                <span className="selq-n">{s.n} صفقة</span>
              </div>
            </div>
          );
        })}
      </div>

      {data && data.estimate && (() => {
        const e = data.estimate;
        const up = e.gate_alpha_uplift_pct;
        return (
          <div className="selq-estimate" title={e.note || ""}>
            <span className="selq-est-title">تقدير فوري (تاريخي)</span>
            <span title="كم كانت بوّابة الدخول سترفع ألفا الأسبوعي — إعادة تشغيل تاريخية آمنة ضد التطلّع">
              رفع البوّابة <b style={{ color: up > 0 ? "var(--positive)" : (up < 0 ? "var(--negative)" : "inherit") }}>{_sqPct(up)}</b>/صفقة
              <span className="selq-dim"> (t {_sqNum(e.gate_t_pass_vs_fail)})</span>
            </span>
            <span title="معامل المعلومات المقطعي للقوة النسبية — موجب يعني إشارة تنبّؤية">
              IC(RS) <b>{_sqNum(e.rs_ic, 3)}</b><span className="selq-dim"> (IR {_sqNum(e.rs_ic_ir)})</span>
            </span>
          </div>
        );
      })()}
      {data && !data.estimate && !data.error && (
        <div className="selq-estimate selq-dim">…يُحسب التقدير التاريخي الفوري</div>
      )}

      {data && data.caveat && <div className="selq-caveat">{data.caveat}</div>}
    </div>
  );
}
