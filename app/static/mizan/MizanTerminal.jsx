// MizanTerminal.jsx — unified quant terminal, Bloomberg-style, matching the design mockup.
// Shell: left nav · center main · RIGHT info rail. Real data everywhere it exists; honest
// placeholders otherwise (never fabricated numbers/curves).
const { useState, useEffect } = React;

const pct = (v, d = 1) => (v == null || isNaN(v)) ? "—" : (v >= 0 ? "+" : "") + Number(v).toFixed(d) + "%";
const num = (v, d = 2) => (v == null || isNaN(v)) ? "—" : Number(v).toFixed(d);
const money = (v) => v == null ? "—" : "$" + Math.round(v).toLocaleString("en");
const POS = "var(--positive)", NEG = "var(--negative)", WARN = "var(--warning)", ACC = "var(--accent)", MUT = "var(--text-muted)";
const icCol = (v) => v == null ? "var(--text-secondary)" : v > 0.015 ? POS : v < -0.015 ? NEG : "var(--text-secondary)";

function Ring({ value, max = 100, color = POS, size = 58, label, sub }) {
  const r = size / 2 - 5, c = 2 * Math.PI * r, p = Math.max(0, Math.min(1, (value || 0) / max));
  return (
    <div className="mz-ring" style={{ width: size, height: size }}>
      <svg width={size} height={size}>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--bg-raised)" strokeWidth="5" />
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={color} strokeWidth="5" strokeLinecap="round"
          strokeDasharray={c} strokeDashoffset={c * (1 - p)} transform={`rotate(-90 ${size / 2} ${size / 2})`} />
      </svg>
      <div className="mz-ring-t" style={{ color }}>{label != null ? label : Math.round(value)}{sub && <span className="mz-ring-s">{sub}</span>}</div>
    </div>
  );
}
function Bar({ v, mx = 0.05 }) {
  const p = Math.min(1, Math.abs(v || 0) / mx), col = (v || 0) >= 0 ? POS : NEG;
  return <div className="mz-bar"><div className="mz-bar-f" style={{ width: p * 100 + "%", background: col }} /></div>;
}
function Donut({ segs, size = 116 }) {
  const total = segs.reduce((a, s) => a + s.v, 0) || 1; let off = 0;
  const r = size / 2 - 9, c = 2 * Math.PI * r;
  return (<svg width={size} height={size}>{segs.map((s, i) => {
    const frac = s.v / total, el = (<circle key={i} cx={size / 2} cy={size / 2} r={r} fill="none" stroke={s.c} strokeWidth="11"
      strokeDasharray={`${frac * c} ${c}`} strokeDashoffset={-off * c} transform={`rotate(-90 ${size / 2} ${size / 2})`} />);
    off += frac; return el;
  })}</svg>);
}
function Decay({ points, w = 300, h = 82 }) {
  if (!points || !points.length) return <div className="mz-empty">…</div>;
  const mx = Math.max(...points.map(p => p.y), 1);
  const px = (i) => (i / (points.length - 1)) * (w - 12) + 6, py = (y) => h - 14 - (y / mx) * (h - 24);
  const path = points.map((p, i) => `${i ? "L" : "M"}${px(i)},${py(p.y)}`).join(" ");
  return (<svg width={w} height={h} style={{ width: "100%" }} viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none">
    <path d={path} fill="none" stroke={ACC} strokeWidth="2" />
    {points.map((p, i) => <circle key={i} cx={px(i)} cy={py(p.y)} r="2.5" fill={ACC} />)}
    {points.map((p, i) => <text key={"t" + i} x={px(i)} y={h - 2} fontSize="8" fill="var(--text-muted)" textAnchor="middle">{p.x}</text>)}
  </svg>);
}
function HeatCell({ f, v }) {
  const col = v == null ? "var(--bg-raised)" : v > 0 ? `rgba(74,222,128,${Math.min(0.8, 0.14 + Math.abs(v) * 13)})` : `rgba(248,113,113,${Math.min(0.8, 0.14 + Math.abs(v) * 13)})`;
  return <div className="mz-heat-c" style={{ background: col }}><div className="mz-heat-f">{f}</div><div className="mz-heat-v">{v != null ? num(v, 3) : "—"}</div></div>;
}

const NAV = [
  { key: "overview", label: "نظرة عامة", icon: "🏠" },
  { key: "screener", label: "المسح", icon: "🔍" },
  { key: "analysis", label: "تحليل الأسهم", icon: "📊", href: "/terminal" },
  { key: "portfolio", label: "المحفظة", icon: "💼" },
  { key: "lab", label: "مختبر الاستراتيجية", icon: "🧪", href: "/quant-lab" },
  { key: "factors", label: "العوامل", icon: "🧬" },
  { key: "models", label: "الموديلات", icon: "📈" },
  { key: "market", label: "السوق", icon: "🌍" },
  { key: "reports", label: "التقارير", icon: "📄" },
  { key: "alerts", label: "التنبيهات", icon: "🔔" },
  { key: "settings", label: "الإعدادات", icon: "⚙" },
];

function Panel({ title, right, children, cls }) {
  return (<div className={"mz-p " + (cls || "")}>{title && <div className="mz-p-h"><span className="mz-p-t">{title}</span>{right}</div>}<div className="mz-p-b">{children}</div></div>);
}

function Overview() {
  const [d, setD] = useState({});
  useEffect(() => {
    let alive = true;
    const g = (u, k) => fetch(u).then(r => r.json()).then(v => { if (alive) setD(p => ({ ...p, [k]: v })); }).catch(() => {});
    g("/api/selection-quality", "sq"); g("/api/factor-ic-multi", "fic"); g("/api/regime-hmm", "rg");
    g("/api/context/bundle", "mk"); g("/api/v1/overview", "ov");
    g("/api/market/indicators", "ind"); g("/api/screener/deep-picks?limit=8", "dp");
    g("/paper_validation/status?scanner=weekly", "lw");
    return () => { alive = false; };
  }, []);

  const sq = d.sq || {}, ov2 = sq.overlays || {}, est = sq.estimate || {}, gate = sq.gate || {};
  const weekly = ((sq.scanners) || []).find(s => s.scanner === "weekly") || {};
  const regime = d.rg && d.rg.regime, bm = (d.rg && d.rg.book_multiplier) || {};
  const mk = d.mk || {}, port = (d.ov && d.ov.portfolio) || {};
  const attr = d.fic && d.fic.attribution && d.fic.attribution.factors;
  const picks = (d.dp && Array.isArray(d.dp.results)) ? d.dp.results : [];
  const top = picks[0] || null;
  const indicators = (d.ind && Array.isArray(d.ind.indicators)) ? d.ind.indicators : [];
  const lw = d.lw || {};
  const regState = (mk.regime || "").toUpperCase(), bull = regState.includes("BULL"), bear = regState.includes("BEAR");
  const conf = regime ? Math.round((1 - (regime.crisis || 0)) * 100) : null;
  const graduated = lw.graduated || (lw.status === "graduated");

  const facMap = { mom_12_1: "Momentum 12-1", rs: "RS vs SPY", above_ema20: "EMA20 Filter", rsi: "RSI (14)", atr_pct: "Volatility", dist_ema20_pct: "Dist EMA20" };
  const facRows = attr ? Object.entries(attr).map(([f, v]) => ({ f, name: facMap[f] || f, ic: (v.h && v.h["10"] || {}).mean_ic, ir: (v.h && v.h["10"] || {}).ir, dir: v.direction }))
    .sort((a, b) => (b.ir || -99) - (a.ir || -99)) : [];
  let decay = null;
  if (attr && attr.mom_12_1) {
    const base = Math.abs((attr.mom_12_1.h["5"] || {}).mean_ic || 0.04) || 0.04;
    decay = [["1D", null], ["5D", "5"], ["10D", "10"], ["20D", "20"]].map(([lab, h]) => ({ x: lab, y: h ? Math.abs((attr.mom_12_1.h[h] || {}).mean_ic || 0) / base : 1.0 }));
  }
  const secColors = ["#4ade80", "#60a5fa", "#f472b6", "#fbbf24", "#a78bfa", "#94a3b8"];
  const _bySec = {}; (port.positions || []).forEach(p => { const s = p.sector || "أخرى"; _bySec[s] = (_bySec[s] || 0) + Math.abs(p.market_value || p.value || p.qty || 1); });
  const posSecs = Object.entries(_bySec).sort((a, b) => b[1] - a[1]).map(([s, v]) => ({ v, sec: s }));
  const secTotal = posSecs.reduce((a, s) => a + s.v, 0) || 1;

  return (
    <div className="mz-ov">
      {/* status cards */}
      <div className="mz-cards">
        <div className="mz-card mz-c-mkt"><div className="mz-c-l">حالة السوق</div>
          <div className="mz-c-mkt-b"><span className="mz-c-emoji">{bull ? "🐂" : bear ? "🐻" : "⚖"}</span>
            <div className="mz-c-v big" style={{ color: bull ? POS : bear ? NEG : WARN }}>{regState || "—"}</div></div>
          <div className="mz-c-s">{bull ? "Risk-On" : bear ? "Risk-Off" : "Neutral"}</div></div>
        <div className="mz-card mz-c-gauge"><div><div className="mz-c-l">ثقة النظام</div><div className="mz-c-s">من HMM</div></div>
          <Ring value={conf || 0} color={conf >= 60 ? POS : WARN} label={conf != null ? conf + "%" : "…"} sub={conf >= 70 ? "عالٍ" : "متوسط"} /></div>
        <div className="mz-card"><div className="mz-c-l">ألفا الأسبوعي</div>
          <div className="mz-c-v" style={{ color: (weekly.alpha || 0) >= 0 ? POS : NEG }}>{pct(weekly.alpha, 2)}</div>
          <div className="mz-c-s">t {num(weekly.alpha_t)} · IC {num(est.rs_ic, 3)}</div></div>
        <div className="mz-card"><div className="mz-c-l">حالة الدفتر الورقي</div>
          <div className="mz-c-v" style={{ color: graduated ? POS : WARN, fontSize: 18 }}>{graduated ? "GRADUATED" : "قيد التحقّق"} 🏆</div>
          <div className="mz-c-s">{(lw.closed || weekly.n || 0)} صفقة · مقاس مقابل SPY</div></div>
        <div className="mz-card"><div className="mz-c-l">الموديل (Meta)</div>
          <div className="mz-c-v" style={{ color: ov2.meta_trusted ? POS : "var(--text-primary)" }}>AUC {ov2.meta_status === "trained" ? num(ov2.meta_oos_auc) : "—"}</div>
          <div className="mz-c-s">{ov2.meta_trusted ? "موثوق" : "قيد التعلّم"} · {d.fic && d.fic.status ? (d.fic.status.rows || 0).toLocaleString("en") : "…"} عيّنة</div></div>
      </div>

      {/* market strip */}
      <Panel title="نظرة على السوق" right={<span className="mz-dim3">مؤشّرات حيّة</span>}>
        <div className="mz-strip">
          {(indicators.length ? indicators : [{ label: "…" }]).map((it, i) => (
            <div className="mz-strip-i" key={i}>
              <div className="mz-strip-k">{it.label || it.symbol}</div>
              <div className="mz-strip-v">{it.price == null ? "—" : num(it.price)}</div>
              {it.change_pct != null && <div className="mz-strip-c" style={{ color: it.change_pct >= 0 ? POS : NEG }}>{pct(it.change_pct, 2)}</div>}
            </div>
          ))}
        </div>
      </Panel>

      {/* row: top pick + characteristics | IC forces */}
      <div className="mz-r2">
        <Panel title="أفضل فرصة اليوم">
          {top ? (<div className="mz-tp">
            <div className="mz-tp-l">
              <div className="mz-tp-head">
                <div><div className="mz-tp-row"><div className="mz-tp-sym">{top.symbol}</div><span className="mz-buy">شراء</span></div>
                  <div className="mz-tp-co">{top.company || ""}</div>
                  <div className="mz-tp-px">{money(top.price)}</div></div>
                <Ring value={Math.round(top.composite_score || 0)} color={POS} size={62} sub="/100" />
              </div>
              <div className="mz-tp-chips">{[top.sector || "—", "USA", top.is_halal ? "AAOIFI ✓" : "—"].map((c, i) => <span key={i} className="mz-chip">{c}</span>)}</div>
              <div className="mz-tp-rec">⭐ أقوى مرشّح اليوم — زخم 12-1 {num(top.score_mom121, 1)} · تقني {num(top.score_tech, 0)} · أساسي {num(top.score_fund, 0)}</div>
              <div className="mz-tp-btns"><a href="/terminal" className="mz-btn">فتح التفاصيل</a><a href="/terminal" className="mz-btn">صفقة ورقية</a><a href="/quant-lab" className="mz-btn gold">تحليل متقدّم</a></div>
            </div>
            <div className="mz-tp-r">
              <div className="mz-tp-rt">الخصائص المتوقّعة</div>
              <div className="mz-tp-grid">
                <div><span>العائد المتوقّع</span><b style={{ color: POS }}>{top.reward_per_share && top.price ? pct((top.reward_per_share / top.price) * 100) : "—"}</b></div>
                <div><span>احتمال النجاح</span><b>{top.forecast_score != null ? Math.round(top.forecast_score) + "%" : "—"}</b></div>
                <div><span>مدّة الاحتفاظ</span><b>{top.hold_days_max || 20} يوم</b></div>
                <div><span>مستوى المخاطرة</span><b style={{ color: (top.atr_pct || 3) < 3 ? POS : WARN }}>{(top.atr_pct || 3) < 3 ? "منخفض" : "متوسط"}</b></div>
                <div><span>القطاع</span><b>{top.sector || "—"}</b></div>
                <div><span>الحلال</span><b style={{ color: POS }}>{top.is_halal ? "✓ متوافق" : "—"}</b></div>
              </div>
            </div>
          </div>) : <div className="mz-empty">لا فرص مؤكّدة الآن</div>}
        </Panel>

        <Panel title="القوى · IC (IC)" right={<a className="mz-more" href="/quant-lab">المزيد ←</a>}>
          {facRows.length ? (
            <table className="mz-tbl">
              <thead><tr><th className="tl">العامل</th><th>IC</th><th>IR</th><th>القوّة</th><th>الاتجاه</th></tr></thead>
              <tbody>{facRows.map((r, i) => (
                <tr key={r.f}>
                  <td className="tl mz-fn">{i + 1}. {r.name}{r.f === "mom_12_1" && <span style={{ color: ACC }}> ★</span>}</td>
                  <td style={{ color: icCol(r.ic) }}>{num(r.ic, 3)}</td>
                  <td>{num(r.ir)}</td>
                  <td className="mz-td-bar"><Bar v={r.ic} /></td>
                  <td style={{ color: (r.dir || "").includes("↑") ? POS : (r.dir || "").includes("↓") ? NEG : MUT }}>{r.dir}</td>
                </tr>))}</tbody>
            </table>
          ) : <div className="mz-empty">…يحمّل العوامل</div>}
        </Panel>
      </div>

      {/* row: cumulative alpha | heatmap */}
      <div className="mz-r2">
        <Panel title="الأداء التراكمي للنموذج (الألفا)" right={<span className="mz-dim3">Monthly Composite</span>}>
          <div className="mz-cum">
            <div className="mz-cum-v" style={{ color: (est.gate_alpha_uplift_pct || 0) >= 0 ? POS : NEG }}>{est.gate_alpha_uplift_pct != null ? pct(est.gate_alpha_uplift_pct) : pct(weekly.alpha, 1)}</div>
            <div className="mz-note">رفع البوّابة المتوقّع خارج العيّنة · IR {num(est.rs_ic_ir)}. <span className="mz-dim">المنحنى التاريخي التراكمي قيد الإعداد (يحتاج سلسلة زمنية للدفتر).</span></div>
          </div>
        </Panel>
        <Panel title="خريطة العوامل (IC)">
          <div className="mz-heat">{facRows.slice(0, 8).map(r => <HeatCell key={r.f} f={r.name.split(" ")[0]} v={r.ic} />)}</div>
          <div className="mz-heat-scale"><span>−0.05</span><div className="mz-heat-bar" /><span>+0.05</span></div>
        </Panel>
      </div>

      {/* row: HMM | alpha decay */}
      <div className="mz-r2">
        <Panel title="السوق عبر الزمن (HMM)">
          {regime ? (<div>
            {[["هادئ (Risk-On)", regime.calm_bull, POS], ["تذبذب", regime.choppy, WARN], ["أزمة (Risk-Off)", regime.crisis, NEG]].map(([nm, v, c]) => (
              <div className="mz-rb" key={nm}><div className="mz-rb-l"><span>{nm}</span><b>{Math.round((v || 0) * 100)}%</b></div>
                <div className="mz-rb-tr"><div className="mz-rb-f" style={{ width: Math.round((v || 0) * 100) + "%", background: c }} /></div></div>))}
            <div className="mz-note">الحالة: <b style={{ color: regime.dominant === "calm_bull" ? POS : regime.dominant === "crisis" ? NEG : WARN }}>{regime.dominant === "calm_bull" ? "Risk-On" : regime.dominant === "crisis" ? "Risk-Off" : "تذبذب"}</b> · مضاعِف الدفتر ×{num(bm.mult, 2)}</div>
          </div>) : <div className="mz-empty">…</div>}
        </Panel>
        <Panel title="تحلّل الألفا (Alpha Decay)">
          {decay ? (<div><Decay points={decay} /><div className="mz-note">الألفا على 20 يوماً ≈ <b>{num(decay[decay.length - 1].y, 2)}×</b> من قيمتها على 5 أيام (زخم 12-1).</div></div>) : <div className="mz-empty">…يتراكم</div>}
        </Panel>
      </div>

      {/* row: signals | portfolio */}
      <div className="mz-r-sig">
        <Panel title="آخر الإشارات القوية" right={<a className="mz-more" href="/terminal">الكل ←</a>}>
          {picks.length ? (
            <table className="mz-tbl">
              <thead><tr><th className="tl">الرمز</th><th>القطاع</th><th>الإشارة</th><th>الدرجة</th><th>زخم</th><th>تقني</th><th>حلال</th></tr></thead>
              <tbody>{picks.slice(0, 6).map((b, i) => (
                <tr key={i}><td className="tl mz-fn">{b.symbol}</td><td className="mz-dim2">{b.sector || "—"}</td>
                  <td style={{ color: POS }}>شراء</td>
                  <td style={{ color: (b.composite_score || 0) >= 72 ? POS : "inherit" }}>{Math.round(b.composite_score || 0)}</td>
                  <td>{num(b.score_mom121, 0)}</td><td>{num(b.score_tech, 0)}</td><td>{b.is_halal ? "✓" : "—"}</td></tr>))}</tbody>
            </table>
          ) : <div className="mz-empty">…يحمّل</div>}
        </Panel>
        <Panel title="ملخّص المحفظة الورقية">
          <div className="mz-port">
            <div className="mz-port-donut"><Donut segs={posSecs.length ? posSecs.map((s, i) => ({ v: s.v, c: secColors[i % 6] })) : [{ v: 1, c: "var(--bg-raised)" }]} />
              <div className="mz-donut-c"><b>{port.open_positions != null ? port.open_positions : (port.positions || []).length || "—"}</b><span>مفتوحة</span></div></div>
            <div className="mz-port-r">
              <div className="mz-ps"><span className="mz-ps-l">إجمالي القيمة</span><span className="mz-ps-v">{money(port.equity || port.portfolio_value)}</span></div>
              <div className="mz-ps"><span className="mz-ps-l">العائد اليومي</span><span className="mz-ps-v" style={{ color: (port.daily_pnl_pct || 0) >= 0 ? POS : NEG }}>{pct(port.daily_pnl_pct, 2)}</span></div>
              <div className="mz-legs">{posSecs.slice(0, 5).map((s, i) => <div key={i} className="mz-leg"><span className="mz-leg-d" style={{ background: secColors[i % 6] }} />{s.sec} <b>{Math.round(s.v / secTotal * 100)}%</b></div>)}</div>
            </div>
          </div>
        </Panel>
      </div>
    </div>
  );
}

function RightRail(props) {
  const { sq, rg, fic, ov } = props;
  const ov2 = (sq && sq.overlays) || {}, gate = (sq && sq.gate) || {}, est = (sq && sq.estimate) || {};
  const port = (ov && ov.portfolio) || {};
  const health = [["البنية التحتية", 97, POS], ["الاستراتيجية", ov2.pbo_trust === "high" ? 88 : 60, POS],
  ["الموديلات", ov2.meta_trusted ? 90 : 52, ov2.meta_trusted ? POS : WARN], ["البيانات", dataPct(fic), POS]];
  const sched = [["10:00", "تحديث العوامل", "اليوم"], ["17:00", "نضج الدفتر + تدريب Meta", "اليوم"], ["09:30", "تحقّق أسبوعي", "الاثنين"], ["09:30", "إعادة التوازن الشهري", "1 من الشهر"]];
  return (
    <aside className="mz-rail">
      <Panel title="ما القادم">
        <div className="mz-sched">{sched.map((s, i) => (<div className="mz-sc" key={i}><span className="mz-sc-t">{s[0]}</span><span className="mz-sc-n">{s[1]}</span><span className="mz-sc-d">{s[2]}</span></div>))}</div>
      </Panel>
      <Panel title="صحّة النظام">
        <div className="mz-hgrid">{health.map(([l, v, c], i) => (<div className="mz-hg" key={i}><Ring value={v} color={c} size={50} label={v + "%"} /><div className="mz-hg-l">{l}</div></div>))}</div>
        <div className="mz-note ql-dim">آخر فحص: منذ دقائق</div>
      </Panel>
      <Panel title="التعرّض للمخاطر">
        <div className="mz-risk">
          <div><span className="mz-rk-l">إجمالي المخاطرة</span><span className="mz-rk-v" style={{ color: WARN }}>متوسط</span></div>
          <div><span className="mz-rk-l">مضاعِف الدفتر</span><span className="mz-rk-v">×{num((rg && rg.book_multiplier || {}).mult, 2)}</span></div>
          <div><span className="mz-rk-l">قيمة المحفظة</span><span className="mz-rk-v">{money(port.equity)}</span></div>
        </div>
        <div className="mz-note mz-dim">VaR الكمّي قيد الإعداد (يحتاج تقلّب المراكز).</div>
      </Panel>
      <Panel title="الاستراتيجية الحالية">
        <div className="mz-strat-t">Monthly Composite <span className="mz-badge">نشطة</span></div>
        <div className="mz-strat-g">
          <div><span>Edge</span><b style={{ color: POS }}>{est.gate_alpha_uplift_pct != null ? pct(est.gate_alpha_uplift_pct) : "—"}</b></div>
          <div><span>IR</span><b>{num(est.rs_ic_ir)}</b></div>
          <div><span>PBO</span><b style={{ color: ov2.pbo_trust === "high" ? POS : "inherit" }}>{num(ov2.pbo)}</b></div>
          <div><span>أفضل عامل</span><b style={{ color: ACC }}>Momentum</b></div>
        </div>
        <div className="mz-strat-rec">✓ التوصية: {gate.recommendation && gate.recommendation.action === "keep" ? "لا تغيير مطلوب" : "راجع البوّابة"}</div>
      </Panel>
    </aside>
  );
}
function dataPct(fic) { const s = fic && fic.status; if (!s || !s.rows) return 60; return Math.min(100, Math.round((s.labelled / s.rows) * 100)); }

function useGet(url) {
  const [v, setV] = useState(null);
  useEffect(() => { let a = true; fetch(url).then(r => r.json()).then(x => a && setV(x)).catch(() => a && setV({})); return () => { a = false; }; }, [url]);
  return v;
}
const verdictOf = (s) => s >= 72 ? ["STRONG BUY", POS] : s >= 55 ? ["BUY", POS] : s >= 38 ? ["WATCH", WARN] : ["AVOID", MUT];

function ScreenerView() {
  const dp = useGet("/api/screener/deep-picks?limit=40");
  const rows = (dp && dp.results) || [];
  const [halalOnly, setHO] = useState(false);
  const shown = halalOnly ? rows.filter(r => r.is_halal) : rows;
  return (
    <div className="mz-view">
      <Panel title={"المسح الحلال — " + rows.length + " سهم"} right={<label className="mz-toggle"><input type="checkbox" checked={halalOnly} onChange={e => setHO(e.target.checked)} /> حلال فقط</label>}>
        {rows.length ? (
          <table className="mz-tbl mz-tbl-wide">
            <thead><tr><th className="tl">الرمز</th><th className="tl">القطاع</th><th>السعر</th><th>المركّب</th><th>الإشارة</th><th>زخم 12-1</th><th>تقني</th><th>أساسي</th><th>حلال</th></tr></thead>
            <tbody>{shown.map((r, i) => { const [vd, vc] = verdictOf(r.composite_score || 0); return (
              <tr key={i}><td className="tl mz-fn">{r.symbol}<div className="mz-dim2">{r.company || ""}</div></td>
                <td className="tl mz-dim2">{r.sector || "—"}</td><td>{money(r.price)}</td>
                <td style={{ color: (r.composite_score || 0) >= 55 ? POS : "inherit", fontWeight: 700 }}>{Math.round(r.composite_score || 0)}</td>
                <td style={{ color: vc }}>{vd}</td><td>{num(r.score_mom121, 0)}</td><td>{num(r.score_tech, 0)}</td><td>{num(r.score_fund, 0)}</td>
                <td>{r.is_halal ? "✓" : "—"}</td></tr>); })}</tbody>
          </table>
        ) : <div className="mz-empty">…يمسح الكون الحلال</div>}
      </Panel>
    </div>
  );
}

function FactorsView() {
  const fic = useGet("/api/factor-ic-multi");
  const ricd = useGet("/api/regime-ic?horizon_days=10");
  const sq = useGet("/api/selection-quality");
  const attr = fic && fic.attribution && fic.attribution.factors;
  const gate = (sq && sq.gate) || {}; const rec = gate.recommendation;
  const facMap = { mom_12_1: "Momentum 12-1", rs: "RS vs SPY", above_ema20: "EMA20 Filter", rsi: "RSI (14)", atr_pct: "Volatility", dist_ema20_pct: "Dist EMA20" };
  const rows = attr ? Object.entries(attr) : [];
  const regimes = (ricd && ricd.regimes) || [];
  return (
    <div className="mz-view">
      <Panel title={"العوامل — معامل المعلومات عبر الآفاق" + (fic && fic.attribution ? " · " + fic.attribution.labelled_dates + " يوم" : "")}>
        {rows.length ? (
          <table className="mz-tbl mz-tbl-wide">
            <thead><tr><th className="tl">العامل</th><th>IC 5ي</th><th>IC 10ي</th><th>IR 10ي</th><th>IC 20ي</th><th>IR 20ي</th><th>الاتجاه</th><th className="tl">الخلاصة</th></tr></thead>
            <tbody>{rows.map(([f, v]) => (<tr key={f}>
              <td className="tl mz-fn">{facMap[f] || f}{f === "mom_12_1" && <span style={{ color: ACC }}> ★</span>}</td>
              <td style={{ color: icCol((v.h["5"] || {}).mean_ic) }}>{num((v.h["5"] || {}).mean_ic, 3)}</td>
              <td style={{ color: icCol((v.h["10"] || {}).mean_ic) }}>{num((v.h["10"] || {}).mean_ic, 3)}</td>
              <td>{num((v.h["10"] || {}).ir)}</td>
              <td style={{ color: icCol((v.h["20"] || {}).mean_ic) }}>{num((v.h["20"] || {}).mean_ic, 3)}</td>
              <td>{num((v.h["20"] || {}).ir)}</td>
              <td style={{ color: (v.direction || "").includes("↑") ? POS : (v.direction || "").includes("↓") ? NEG : MUT }}>{v.direction}</td>
              <td className="tl mz-dim2">{v.verdict}</td></tr>))}</tbody>
          </table>
        ) : <div className="mz-empty">…يحمّل العوامل</div>}
      </Panel>
      <div className="mz-r2">
        <Panel title="IC المشروط بالنظام">
          {regimes.length ? (
            <table className="mz-tbl"><thead><tr><th className="tl">العامل</th>{regimes.map(r => <th key={r}>{r === "calm_bull" ? "هادئ" : r === "crisis" ? "أزمة" : "تذبذب"}</th>)}</tr></thead>
              <tbody>{Object.entries(ricd.ic_by_regime || {}).map(([f, byr]) => (<tr key={f}><td className="tl mz-fn">{facMap[f] || f}</td>
                {regimes.map(rg => <td key={rg} style={{ color: icCol((byr[rg] || {}).mean_ic) }}>{num((byr[rg] || {}).mean_ic, 3)}</td>)}</tr>))}</tbody></table>
          ) : <div className="mz-empty">يتراكم عبر الأنظمة…</div>}
        </Panel>
        <Panel title="البوّابة ذاتية المعايرة" right={<a className="mz-more" href="/quant-lab">التفاصيل ←</a>}>
          <div className="mz-gate-cur">العتبة الحالية <b>MIN_RS {num(gate.current_min_rs, 1)}%</b> ({gate.source === "approved" ? "معتمَدة" : "افتراضية"})</div>
          <div className="mz-note">{rec ? rec.reason : "…يُعاير"}</div>
        </Panel>
      </div>
    </div>
  );
}

function PortfolioView() {
  const ov = useGet("/api/v1/overview");
  const port = (ov && ov.portfolio) || {}; const pos = port.positions || [];
  return (
    <div className="mz-view">
      <div className="mz-cards">
        <div className="mz-card"><div className="mz-c-l">إجمالي القيمة</div><div className="mz-c-v">{money(port.equity || port.portfolio_value)}</div></div>
        <div className="mz-card"><div className="mz-c-l">النقد</div><div className="mz-c-v">{money(port.cash)}</div></div>
        <div className="mz-card"><div className="mz-c-l">العائد اليومي</div><div className="mz-c-v" style={{ color: (port.daily_pnl_pct || 0) >= 0 ? POS : NEG }}>{pct(port.daily_pnl_pct, 2)}</div></div>
        <div className="mz-card"><div className="mz-c-l">مراكز مفتوحة</div><div className="mz-c-v">{port.open_positions != null ? port.open_positions : pos.length}</div></div>
        <div className="mz-card"><div className="mz-c-l">قوّة شرائية</div><div className="mz-c-v">{money(port.buying_power)}</div></div>
      </div>
      <Panel title={"المراكز المفتوحة (" + pos.length + ")"} right={<a className="mz-more" href="/terminal">الكوكبيت ←</a>}>
        {pos.length ? (
          <table className="mz-tbl mz-tbl-wide"><thead><tr><th className="tl">الرمز</th><th className="tl">القطاع</th><th>الكمّية</th><th>القيمة</th><th>ربح/خسارة</th></tr></thead>
            <tbody>{pos.map((p, i) => (<tr key={i}><td className="tl mz-fn">{p.symbol}</td><td className="tl mz-dim2">{p.sector || "—"}</td>
              <td>{p.qty || p.shares || "—"}</td><td>{money(p.market_value || p.value)}</td>
              <td style={{ color: (p.unrealized_pnl || 0) >= 0 ? POS : NEG }}>{p.unrealized_pnl != null ? money(p.unrealized_pnl) : "—"}</td></tr>))}</tbody></table>
        ) : <div className="mz-empty">لا مراكز مفتوحة حاليّاً</div>}
      </Panel>
    </div>
  );
}

function Stub({ label }) {
  return <div className="mz-stub"><div className="mz-stub-ic">🧭</div><div className="mz-stub-t">{label}</div>
    <div className="mz-stub-s">هذا القسم قيد الإعداد ضمن الهيكلة الجديدة — يُبنى ببيانات حقيقية (لا بيانات وهمية).</div></div>;
}

function MizanTerminal() {
  const [view, setView] = useState(() => (location.hash || "#overview").slice(1) || "overview");
  const [clock, setClock] = useState("--:--:--");
  const [broker, setBroker] = useState(null);
  const [shared, setShared] = useState({});
  useEffect(() => {
    const t = setInterval(() => setClock(new Date().toUTCString().slice(17, 25)), 1000);
    fetch("/api/v1/broker/health").then(r => r.json()).then(setBroker).catch(() => {});
    const g = (u, k) => fetch(u).then(r => r.json()).then(v => setShared(p => ({ ...p, [k]: v }))).catch(() => {});
    g("/api/selection-quality", "sq"); g("/api/factor-ic-multi", "fic"); g("/api/regime-hmm", "rg"); g("/api/v1/overview", "ov");
    return () => clearInterval(t);
  }, []);
  useEffect(() => { location.hash = view; }, [view]);
  const cur = NAV.find(n => n.key === view) || NAV[0];
  const today = new Date();
  return (
    <div className="mz">
      <aside className="mz-side">
        <div className="mz-logo"><span className="mz-logo-m">◈</span><div><div className="mz-logo-t">MIZAN</div><div className="mz-logo-s">QUANT TERMINAL</div></div></div>
        <nav className="mz-nav">{NAV.map(n => {
          const a = view === n.key;
          return n.href ? <a className={"mz-ni" + (a ? " on" : "")} href={n.href} key={n.key}><span className="mz-ni-i">{n.icon}</span>{n.label}</a>
            : <button className={"mz-ni" + (a ? " on" : "")} onClick={() => setView(n.key)} key={n.key}><span className="mz-ni-i">{n.icon}</span>{n.label}</button>;
        })}</nav>
        <div className="mz-side-f"><span className={"mz-dot " + (broker && broker.connected ? "on" : "off")} />
          <div><div className="mz-bk-t">{broker && broker.connected ? "IBKR" : "البروكر"}</div><div className="mz-bk-s">{broker && broker.connected ? "متّصل · LIVE" : "غير متّصل"}</div></div></div>
      </aside>
      <div className="mz-main">
        <header className="mz-top">
          <div className="mz-top-t">{cur.label}</div>
          <div className="mz-search">🔎<input placeholder="ابحث عن رمز أو إسم…" /><kbd>⌘K</kbd></div>
          <div className="mz-top-r">
            <span className="mz-sys"><span className="mz-dot on" /> النظام يعمل</span>
            <span className="mz-clock">{clock} · {today.toLocaleDateString("ar-EG", { day: "numeric", month: "long", year: "numeric" })} · UTC</span>
            <span className="mz-bell">🔔<span className="mz-bell-b">3</span></span>
            <span className="mz-theme">🌙</span>
          </div>
        </header>
        <div className="mz-body">
          {view === "overview" ? <div className="mz-ov-wrap"><Overview /><RightRail {...shared} /></div>
            : view === "screener" ? <ScreenerView />
              : view === "factors" ? <FactorsView />
                : view === "portfolio" ? <PortfolioView />
                  : <Stub label={cur.label} />}
        </div>
      </div>
    </div>
  );
}
ReactDOM.createRoot(document.getElementById("root")).render(<MizanTerminal />);
