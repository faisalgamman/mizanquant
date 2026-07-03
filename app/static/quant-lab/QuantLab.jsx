// QuantLab.jsx — "مختبر الاستراتيجية": the whole learning loop on one screen.
// Aggregates the endpoints we built into a professional daily-monitoring dashboard.
const { useState, useEffect } = React;

const pct = (v, d = 2) => (v == null || isNaN(v)) ? "—" : (v >= 0 ? "+" : "") + Number(v).toFixed(d) + "%";
const num = (v, d = 2) => (v == null || isNaN(v)) ? "—" : Number(v).toFixed(d);
const gradeColor = (k) => (k === "good" || k === "alpha") ? "var(--positive)"
  : (k === "warn" || k === "beta") ? "var(--warning)"
  : (k === "bad" || k === "negative") ? "var(--negative)" : "var(--text-muted)";
const icColor = (v) => v == null ? "inherit" : v > 0.02 ? "var(--positive)" : v < -0.02 ? "var(--negative)" : "var(--text-secondary)";

function Card({ title, tag, icon, children }) {
  return (
    <div className="ql-card">
      <div className="ql-card-h">
        <span className="ql-card-title">{title}{icon && <span className="ql-ci">{icon}</span>}</span>
        {tag && <span className="ql-tag">{tag}</span>}
      </div>
      <div className="ql-card-b">{children}</div>
    </div>
  );
}
function Stat({ label, value, sub, color, help }) {
  return (
    <div className="ql-stat" title={help || ""}>
      <div className="ql-stat-l">{label}</div>
      <div className="ql-stat-v" style={color ? { color } : null}>{value}</div>
      {sub != null && <div className="ql-stat-s">{sub}</div>}
    </div>
  );
}

function QuantLab() {
  const [sq, setSq] = useState(null);
  const [fic, setFic] = useState(null);
  const [rg, setRg] = useState(null);
  const [ts, setTs] = useState(null);
  const [busy, setBusy] = useState(false);
  const [applying, setApplying] = useState(false);
  const [autoOn, setAutoOn] = useState(true);

  const load = async () => {
    setBusy(true);
    const g = (u) => fetch(u).then(r => r.json()).catch(() => null);
    const [a, b, c] = await Promise.all([
      g("/api/selection-quality"), g("/api/factor-ic-multi"), g("/api/regime-hmm"),
    ]);
    setSq(a); setFic(b); setRg(c); setTs(new Date()); setBusy(false);
  };
  useEffect(() => {
    load();
    if (!autoOn) return;
    const t = setInterval(load, 60000);
    return () => clearInterval(t);
  }, [autoOn]);

  const approve = async (rec) => {
    if (!rec || applying) return;
    if (!window.confirm(`اعتماد MIN_RS = ${rec.min_rs}%؟ (الدفتر الورقي فقط · قابل للعكس)`)) return;
    setApplying(true);
    try {
      const qs = `min_rs=${rec.min_rs}` + (rec.test_t != null ? `&test_t=${rec.test_t}` : "") + (rec.train_t != null ? `&train_t=${rec.train_t}` : "");
      await fetch("/api/gate-config/apply?" + qs, { method: "POST" });
      await load();
    } catch (e) { /* keep visible */ }
    setApplying(false);
  };
  const settings = () => window.alert(
    "الإعدادات (مفاتيح env — قابلة للضبط، قياس فقط):\n\n" +
    "• GATE_MIN_T — حسّاسية دلالة توصية البوّابة (2.0)\n" +
    "• COMPOSITE_MOM121_WEIGHT — وزن الزخم 12-1 (12)\n" +
    "• COMPOSITE_MOMENTUM_WEIGHT — وزن RS (10)\n" +
    "• VOL_TARGET — تقلّب المحفظة المستهدف (0.14)\n" +
    "• WEEKLY_MIN_RS — عتبة الدخول الأسبوعي (-2)\n" +
    "• META_MIN_OOS_AUC — عتبة ثقة نموذج Meta (0.53)\n\n" +
    "لا صفقات حقيقية · كل التغييرات قابلة للعكس.");

  const scanners = (sq && Array.isArray(sq.scanners)) ? sq.scanners : [];
  const ov = (sq && sq.overlays) || {};
  const est = (sq && sq.estimate) || {};
  const gate = (sq && sq.gate) || {};
  const rec = gate.recommendation;
  const regime = rg && rg.regime;
  const bm = (rg && rg.book_multiplier) || {};
  const attr = fic && fic.attribution && fic.attribution.factors;
  const capRows = ov.capture_rows, capLab = ov.capture_labelled;
  const weekly = scanners.find(s => s.scanner === "weekly") || {};

  const HS = ["10", "20", "5"];   // primary first

  return (
    <div className="ql">
      <header className="ql-top">
        <div className="ql-top-l">
          <button className={"ql-btn" + (autoOn ? " on" : "")} onClick={() => setAutoOn(!autoOn)}>
            {autoOn ? "● " : "○ "}تحديث تلقائي 60 ث
          </button>
          <button className="ql-btn" disabled={busy} onClick={load}>{busy ? "…" : "⟳ تحديث الآن"}</button>
          <button className="ql-btn" onClick={settings}>⚙ الإعدادات</button>
        </div>
        <div className="ql-brand-wrap">
          <div className="ql-brand">🧪 مختبر الاستراتيجية</div>
          <div className="ql-sub">الحلقة التعلّمية · البوّابة ذاتية المعايرة · متابعة يومية
            {ts && <span className="ql-updated"> · آخر تحديث {ts.toLocaleTimeString("ar-EG")}</span>}</div>
        </div>
      </header>

      {/* ── the learning loop ── */}
      <div className="ql-loop">
        <Card title="تصحيح الاستراتيجية" tag="CORRECT" icon="✓">
          <div className="ql-row">
            <Stat label="عتبة الدخول النشطة" value={"MIN_RS " + num(gate.current_min_rs, 1) + "%"} sub={gate.source === "approved" ? "معتمَدة" : "مستقرّة"} />
            <Stat label="مضاعِف الدفتر" value={"×" + num(bm.mult, 2)} sub={"سوق " + num(bm.regime, 2) + " · HMM " + num(bm.hmm, 2) + " · تقلّب " + num(bm.vol_target, 2)} color={bm.mult < 0.7 ? "var(--warning)" : "inherit"} />
            <Stat label="نموذج Meta" value={ov.meta_status === "trained" ? num(ov.meta_oos_auc) : "…"} sub={ov.meta_status === "trained" ? (ov.meta_trusted ? "موثوق ✓" : "دون عتبة") : "يتراكم"} color={ov.meta_trusted ? "var(--positive)" : "var(--text-muted)"} />
          </div>
        </Card>
        <Card title="التحليل" tag="ANALYZE" icon="②">
          <div className="ql-row">
            <Stat label="ألفا الأسبوعي" value={pct(weekly.alpha)} sub={"t " + num(weekly.alpha_t)} color={gradeColor(weekly.color)} />
            <Stat label="IC القوّة النسبية" value={num(est.rs_ic, 3)} sub={est.rs_ic_ir != null ? "IR " + num(est.rs_ic_ir) : "…"} />
            <Stat label="ثقة البوّابة (PBO)" value={ov.pbo != null ? num(ov.pbo) : "…"} sub={ov.pbo_trust || null} color={ov.pbo_trust === "high" ? "var(--positive)" : ov.pbo_trust === "low" ? "var(--negative)" : null} />
          </div>
        </Card>
        <Card title="جمع المعلومات" tag="COLLECT" icon="ⓘ">
          <div className="ql-row">
            <Stat label="لقطات الكون" value={capRows ? capRows.toLocaleString("en") : "…"} sub={capLab != null ? capLab.toLocaleString("en") + " مُسمّاة" : null} color={capRows ? "var(--positive)" : "var(--text-muted)"} />
            <Stat label="صفقات أسبوعية مُغلقة" value={weekly.n ?? "—"} />
          </div>
          <div className="ql-note">تُجمَع يوميّاً تلقائيّاً — كل يوم يضيف عيّنات ويقوّي لحظة الدلالة.</div>
        </Card>
      </div>

      {/* ── selection quality + regime ── */}
      <div className="ql-mid">
        <div>
          <h2 className="ql-h2">جودة الاختيار — هل يتفوّق على SPY؟</h2>
          <div className="ql-scan-grid">
            {scanners.map(s => (
              <div className={"ql-scan " + (s.color === "bad" ? "bad" : s.color === "warn" ? "warn" : "")} key={s.scanner}>
                <div className="ql-scan-h">
                  <span className="ql-scan-name">{s.name}</span>
                  <span className="ql-grade" style={{ color: gradeColor(s.color), borderColor: gradeColor(s.color) }}>{s.grade}</span>
                </div>
                <div className="ql-scan-label" style={{ color: gradeColor(s.color) }}>{s.label}</div>
                <div className="ql-scan-big" style={{ color: s.alpha > 0 ? "var(--positive)" : "var(--negative)" }}>{pct(s.alpha)}</div>
                <div className="ql-scan-sub">ألفا من SPY · t {num(s.alpha_t)}</div>
                <div className="ql-scan-metrics">
                  <span>تفوّق <b>{s.pct_beat_spy != null ? s.pct_beat_spy + "%" : "—"}</b></span>
                  <span>معايرة <b>{num(s.score_rank_corr)}</b></span>
                  <span className="ql-dim">{s.n} صفقة</span>
                </div>
              </div>
            ))}
            {!scanners.length && <div className="ql-empty">…يحمّل</div>}
          </div>
          <Card title="البوّابة ذاتية المعايرة" tag="⑤ OOS+PBO">
            <div className="ql-gate-cur">العتبة الحالية <b>MIN_RS {num(gate.current_min_rs, 1)}%</b> <span className="ql-dim">({gate.source === "approved" ? "معتمَدة بالدليل" : "افتراضية"})</span></div>
            {rec && (rec.action === "raise" || rec.action === "lower") ? (
              <div className="ql-reco">
                <span className="ql-reco-msg">⬆ {rec.reason}</span>
                <button className="ql-approve" disabled={applying} onClick={() => approve(rec)}>{applying ? "…" : `اعتمد ${rec.min_rs}%`}</button>
              </div>
            ) : <div className="ql-note">{rec ? rec.reason : "…يُعاير من التاريخ"}</div>}
            <div className="ql-note ql-dim">القرار بيدك — النظام يجمع الدليل خارج العيّنة ويقترح؛ لا يُطبّق ذاتيّاً. ورقي فقط · قابل للعكس.</div>
          </Card>
        </div>

        <Card title="نظام السوق (HMM)" tag="④">
          {regime ? (
            <div>
              <div className="ql-regime-bars">
                {[["هادئ", regime.calm_bull, "var(--positive)"], ["تذبذب", regime.choppy, "var(--warning)"], ["أزمة", regime.crisis, "var(--negative)"]].map(([nm, v, c]) => (
                  <div className="ql-rb" key={nm}>
                    <div className="ql-rb-l"><span>{nm}</span><b>{Math.round((v || 0) * 100)}%</b></div>
                    <div className="ql-rb-track"><div className="ql-rb-fill" style={{ width: Math.round((v || 0) * 100) + "%", background: c }} /></div>
                  </div>
                ))}
              </div>
              <div className="ql-note">الحالة المهيمنة: <b style={{ color: regime.dominant === "calm_bull" ? "var(--positive)" : regime.dominant === "crisis" ? "var(--negative)" : "var(--warning)" }}>{regime.dominant === "calm_bull" ? "هادئ" : regime.dominant === "crisis" ? "أزمة" : "تذبذب"}</b> — يُقلّص الدفتر قبل أن تنكسر المتوسّطات.</div>
            </div>
          ) : <div className="ql-empty">…يحسب نظام السوق</div>}
        </Card>
      </div>

      {/* ── multi-horizon factor IC table ── */}
      <div className="ql-fac-head">
        <h2 className="ql-h2">العوامل — أيّها يتنبّأ فعلاً؟ (من قاعدة الالتقاط)</h2>
        <span className="ql-fac-hz">آفاق العوائد: 10 أيام (أساسي) · 20 يوم · 5 يوم</span>
      </div>
      <div className="ql-card">
        <div className="ql-card-b" style={{ padding: 0, overflowX: "auto" }}>
          {attr ? (
            <table className="ql-tbl">
              <thead>
                <tr>
                  <th>العامل</th>
                  {HS.map(h => [<th key={"ic" + h}>IC ({h}ي)</th>, <th key={"ir" + h}>IR</th>])}
                  <th>الاتجاه</th><th className="ql-th-sum">الخلاصة</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(attr).map(([f, v]) => (
                  <tr key={f}>
                    <td className="ql-fac-name">{f}{f === "mom_12_1" && <span className="ql-star"> ★</span>}</td>
                    {HS.map(h => {
                      const d = (v.h && v.h[h]) || {};
                      return [
                        <td key={"ic" + h} style={{ color: icColor(d.mean_ic) }}>{d.mean_ic != null ? num(d.mean_ic, 3) : "—"}</td>,
                        <td key={"ir" + h} className="ql-dim">{num(d.ir)}</td>,
                      ];
                    })}
                    <td className="ql-dir" style={{ color: v.direction && v.direction.includes("↑") ? "var(--positive)" : v.direction && v.direction.includes("↓") ? "var(--negative)" : "var(--text-muted)" }}>{v.direction}</td>
                    <td className="ql-verdict">{v.verdict}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : <div className="ql-empty" style={{ padding: 14 }}>لم تنضج بعد — تبدأ حين تُسمَّى اللقطات. كل يوم يقرّبها.</div>}
        </div>
      </div>

      <footer className="ql-foot">
        <span className="ql-dim">الألوان: <b style={{ color: "var(--positive)" }}>أخضر</b> دالّ إحصائيّاً تقريباً · <b style={{ color: "var(--warning)" }}>أصفر</b> إرشادي · <b style={{ color: "var(--negative)" }}>أحمر</b> سالب.</span>
        {" "}قياس أمين فقط · لا صفقات حقيقية · {sq && sq.caveat}
      </footer>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<QuantLab />);
