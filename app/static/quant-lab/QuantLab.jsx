// QuantLab.jsx — "مختبر الاستراتيجية": the whole learning loop on one screen.
// Aggregates the endpoints we built (selection-quality, alpha-capture, regime-hmm) into a
// professional daily-monitoring dashboard. Self-contained; auto-refreshes every 60s.
const { useState, useEffect } = React;

const pct = (v, d = 2) => (v == null || isNaN(v)) ? "—" : (v >= 0 ? "+" : "") + Number(v).toFixed(d) + "%";
const num = (v, d = 2) => (v == null || isNaN(v)) ? "—" : Number(v).toFixed(d);
const COLW = { good: "var(--positive)", bad: "var(--negative)", warn: "var(--warning)", muted: "var(--text-muted)" };
const gradeColor = (k) => k === "good" || k === "alpha" ? "var(--positive)"
  : k === "warn" || k === "beta" ? "var(--warning)"
  : k === "bad" || k === "negative" ? "var(--negative)" : "var(--text-muted)";

function Card({ title, tag, children }) {
  return (
    <div className="ql-card">
      <div className="ql-card-h"><span className="ql-card-title">{title}</span>{tag && <span className="ql-tag">{tag}</span>}</div>
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
  const [ac, setAc] = useState(null);
  const [rg, setRg] = useState(null);
  const [ts, setTs] = useState(null);
  const [busy, setBusy] = useState(false);
  const [applying, setApplying] = useState(false);

  const load = async () => {
    setBusy(true);
    const g = (u) => fetch(u).then(r => r.json()).catch(() => null);
    const [a, b, c] = await Promise.all([
      g("/api/selection-quality"), g("/api/alpha-capture"), g("/api/regime-hmm"),
    ]);
    setSq(a); setAc(b); setRg(c); setTs(new Date()); setBusy(false);
  };
  useEffect(() => { load(); const t = setInterval(load, 60000); return () => clearInterval(t); }, []);

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

  const scanners = (sq && Array.isArray(sq.scanners)) ? sq.scanners : [];
  const ov = (sq && sq.overlays) || {};
  const est = (sq && sq.estimate) || {};
  const gate = (sq && sq.gate) || {};
  const rec = gate.recommendation;
  const regime = rg && rg.regime;
  const bm = (rg && rg.book_multiplier) || {};
  const attr = ac && ac.attribution && ac.attribution.factors;
  const capRows = ov.capture_rows, capLab = ov.capture_labelled;
  const weekly = scanners.find(s => s.scanner === "weekly") || {};
  const monthly = scanners.find(s => s.scanner === "monthly") || {};

  return (
    <div className="ql">
      <header className="ql-top">
        <div>
          <div className="ql-brand">مختبر الاستراتيجية</div>
          <div className="ql-sub">الحلقة التعلّمية · الطبقات الكمّية · البوّابة ذاتية المعايرة — متابعة يومية</div>
        </div>
        <div className="ql-top-r">
          <span className="ql-updated">{ts ? "آخر تحديث " + ts.toLocaleTimeString("ar-EG") : "…يحمّل"}</span>
          <button className="ql-refresh" disabled={busy} onClick={load}>{busy ? "…" : "⟳ تحديث"}</button>
          <a className="ql-refresh" href="/terminal">← الترمينال</a>
        </div>
      </header>

      {/* ── the learning loop ── */}
      <div className="ql-loop">
        <Card title="① جمع المعلومات" tag="COLLECT">
          <div className="ql-row">
            <Stat label="صفقات أسبوعية مُغلقة" value={weekly.n ?? "—"} help="دفتر التحقّق الورقي (PV)" />
            <Stat label="لقطات الكون" value={capRows ?? "…"} sub={capLab != null ? capLab + " مُسمّاة" : null} help="قاعدة التقاط الألفا — لقطة يومية لكل الكون" color={capRows ? "var(--positive)" : "var(--text-muted)"} />
          </div>
          <div className="ql-note">تُجمَع يومياً تلقائياً — كل يوم يضيف عيّنات ويقرّب لحظة الدلالة.</div>
        </Card>

        <Card title="② التحليل" tag="ANALYZE">
          <div className="ql-row">
            <Stat label="ألفا الأسبوعي" value={pct(weekly.alpha)} sub={"t " + num(weekly.alpha_t)} color={gradeColor(weekly.color)} help="عائد الصفقة − SPY على نفس النافذة" />
            <Stat label="IC القوّة النسبية" value={num(est.rs_ic, 3)} sub={est.rs_ic_ir != null ? "IR " + num(est.rs_ic_ir) : "…"} help="معامل معلومات RS (تاريخي)" />
            <Stat label="ثقة البوّابة (PBO)" value={ov.pbo != null ? num(ov.pbo) : "…"} sub={ov.pbo_trust || null} color={ov.pbo_trust === "high" ? "var(--positive)" : ov.pbo_trust === "low" ? "var(--negative)" : null} help="احتمال فرط التخصيص — أقل = أوثق" />
          </div>
        </Card>

        <Card title="③ تصحيح الاستراتيجية" tag="CORRECT">
          <div className="ql-row">
            <Stat label="عتبة الدخول النشطة" value={"MIN_RS " + num(gate.current_min_rs, 1) + "%"} sub={gate.source === "approved" ? "معتمَدة" : "افتراضية"} help="تحكم ما يدخل الدفتر — نشطة الآن" />
            <Stat label="مضاعِف الدفتر" value={"×" + num(bm.mult, 2)} sub={"سوق " + num(bm.regime, 2) + " · HMM " + num(bm.hmm, 2) + " · تقلّب " + num(bm.vol_target, 2)} color={bm.mult < 0.7 ? "var(--warning)" : "inherit"} help="تحجيم حيّ للدفتر (regime × HMM × استهداف التقلّب)" />
            <Stat label="نموذج Meta" value={ov.meta_status === "trained" ? num(ov.meta_oos_auc) : "…يتراكم"} sub={ov.meta_status === "trained" ? (ov.meta_trusted ? "موثوق ✓" : "دون العتبة") : null} color={ov.meta_trusted ? "var(--positive)" : "var(--text-muted)"} help="AUC خارج العيّنة (مُطهَّر) — يحجّم فقط إن تجاوز 0.53" />
          </div>
        </Card>
      </div>

      {/* ── selection quality ── */}
      <h2 className="ql-h2">جودة الاختيار — هل يتفوّق الاختيار على SPY؟</h2>
      <div className="ql-grid">
        {scanners.map(s => (
          <div className="ql-scan" key={s.scanner}>
            <div className="ql-scan-h">
              <span className="ql-scan-name">{s.name}</span>
              <span className="ql-grade" style={{ color: gradeColor(s.color), borderColor: gradeColor(s.color) }}>{s.grade}</span>
            </div>
            <div className="ql-scan-label" style={{ color: gradeColor(s.color) }}>{s.label}</div>
            <div className="ql-scan-metrics">
              <span>ألفا <b style={{ color: s.alpha > 0 ? "var(--positive)" : s.alpha < 0 ? "var(--negative)" : "inherit" }}>{pct(s.alpha)}</b></span>
              <span>t <b>{num(s.alpha_t)}</b></span>
              <span>تفوّق <b>{s.pct_beat_spy != null ? s.pct_beat_spy + "%" : "—"}</b></span>
              <span>معايرة <b>{num(s.score_rank_corr)}</b></span>
              <span className="ql-dim">{s.n} صفقة</span>
            </div>
          </div>
        ))}
        {!scanners.length && <div className="ql-empty">…يحمّل جودة الاختيار</div>}
      </div>

      {/* ── regime + gate ── */}
      <div className="ql-two">
        <Card title="نظام السوق (HMM)" tag="④">
          {regime ? (
            <div>
              <div className="ql-regime-bars">
                {[["هدوء صاعد", regime.calm_bull, "var(--positive)"], ["تذبذب", regime.choppy, "var(--warning)"], ["أزمة", regime.crisis, "var(--negative)"]].map(([nm, v, c]) => (
                  <div className="ql-rb" key={nm}>
                    <div className="ql-rb-l"><span>{nm}</span><b>{Math.round((v || 0) * 100)}%</b></div>
                    <div className="ql-rb-track"><div className="ql-rb-fill" style={{ width: Math.round((v || 0) * 100) + "%", background: c }} /></div>
                  </div>
                ))}
              </div>
              <div className="ql-note">الحالة المهيمنة: <b style={{ color: regime.dominant === "calm_bull" ? "var(--positive)" : regime.dominant === "crisis" ? "var(--negative)" : "var(--warning)" }}>{regime.dominant === "calm_bull" ? "هدوء صاعد" : regime.dominant === "crisis" ? "أزمة" : "تذبذب"}</b> — يُقلّص الدفتر قبل أن تنكسر المتوسّطات.</div>
            </div>
          ) : <div className="ql-empty">…يحسب نظام السوق</div>}
        </Card>

        <Card title="البوّابة ذاتية المعايرة" tag="⑤ OOS+PBO">
          <div className="ql-gate-cur">العتبة الحالية <b>MIN_RS {num(gate.current_min_rs, 1)}%</b> <span className="ql-dim">({gate.source === "approved" ? "معتمَدة بالدليل" : "افتراضية"})</span></div>
          {rec && (rec.action === "raise" || rec.action === "lower") ? (
            <div className="ql-reco">
              <div className="ql-reco-msg">⬆ {rec.reason}</div>
              <button className="ql-approve" disabled={applying} onClick={() => approve(rec)}>{applying ? "…يُعتمد" : `اعتمد ${rec.min_rs}%`}</button>
            </div>
          ) : (
            <div className="ql-note">{rec ? rec.reason : "…يُعاير من التاريخ"}</div>
          )}
          <div className="ql-note ql-dim">القرار بيدك — النظام يجمع الدليل خارج العيّنة ويقترح؛ لا يُطبّق ذاتياً. ورقي فقط · قابل للعكس.</div>
        </Card>
      </div>

      {/* ── factor IC from the capture panel ── */}
      <h2 className="ql-h2">العوامل — أيّها يتنبّأ فعلاً؟ (من قاعدة الالتقاط)</h2>
      <Card title="معامل المعلومات لكل عامل" tag={ac && ac.attribution ? (ac.attribution.labelled_dates || 0) + " يوم" : ""}>
        {attr && Object.keys(attr).length ? (
          <table className="ql-tbl">
            <thead><tr><th>العامل</th><th>IC متوسط</th><th>IR (≈t)</th><th>أيام</th></tr></thead>
            <tbody>
              {Object.entries(attr).map(([f, v]) => (
                <tr key={f}>
                  <td>{f}</td>
                  <td style={{ color: v.mean_ic > 0.03 ? "var(--positive)" : v.mean_ic < -0.03 ? "var(--negative)" : "inherit" }}>{v.mean_ic != null ? num(v.mean_ic, 3) : "…"}</td>
                  <td>{num(v.ic_ir)}</td>
                  <td className="ql-dim">{v.n_dates ?? 0}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : <div className="ql-empty">لم تنضج بعد — تبدأ حين تُسمَّى اللقطات (~10 أيام تداول). كل يوم يقرّبها.</div>}
        <div className="ql-note ql-dim">قوّة إحصائية تتراكم لكل <b>يوم</b> (عشرات الأسهم) لا لكل صفقة — لهذا بنينا قاعدة الالتقاط.</div>
      </Card>

      <footer className="ql-foot">
        قياس أمين فقط · لا صفقات حقيقية · كل التغييرات مفاتيحها env قابلة للعكس · {sq && sq.caveat}
      </footer>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<QuantLab />);
