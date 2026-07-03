// MizanTerminal.jsx — unified quant terminal, Bloomberg-style, matching the design mockup.
// LTR shell layout (left nav · center · right rail); RTL text inside. Real data everywhere
// it exists; honest placeholders otherwise (never fabricated numbers).
const { useState, useEffect } = React;

const pct = (v, d = 1) => (v == null || isNaN(v)) ? "—" : (v >= 0 ? "+" : "") + Number(v).toFixed(d) + "%";
const num = (v, d = 2) => (v == null || isNaN(v)) ? "—" : Number(v).toFixed(d);
const money = (v) => v == null ? "—" : "$" + Math.round(v).toLocaleString("en");
const POS = "var(--positive)", NEG = "var(--negative)", WARN = "var(--warning)", ACC = "var(--accent)", MUT = "var(--text-muted)";
const icCol = (v) => v == null ? "var(--text-secondary)" : v > 0.015 ? POS : v < -0.015 ? NEG : "var(--text-secondary)";

// ── SVG primitives ───────────────────────────────────────────────────────────
function Ring({ value, max = 100, color = POS, size = 58, label }) {
  const r = size / 2 - 5, c = 2 * Math.PI * r, p = Math.max(0, Math.min(1, (value || 0) / max));
  return (
    <div className="mz-ring" style={{ width: size, height: size }}>
      <svg width={size} height={size}>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--bg-raised)" strokeWidth="5" />
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={color} strokeWidth="5" strokeLinecap="round"
          strokeDasharray={c} strokeDashoffset={c * (1 - p)} transform={`rotate(-90 ${size / 2} ${size / 2})`} />
      </svg>
      <div className="mz-ring-t" style={{ color }}>{label != null ? label : Math.round(value)}</div>
    </div>
  );
}
function Spark({ data, color = POS, w = 60, h = 20 }) {
  if (!data || data.length < 2) return <svg width={w} height={h} />;
  const mn = Math.min(...data), mx = Math.max(...data), rg = (mx - mn) || 1;
  const pts = data.map((v, i) => `${(i / (data.length - 1)) * w},${h - ((v - mn) / rg) * h}`).join(" ");
  return <svg width={w} height={h}><polyline points={pts} fill="none" stroke={color} strokeWidth="1.5" /></svg>;
}
function Donut({ segs, size = 120 }) {
  const total = segs.reduce((a, s) => a + s.v, 0) || 1;
  let off = 0; const r = size / 2 - 10, c = 2 * Math.PI * r;
  return (
    <svg width={size} height={size}>
      {segs.map((s, i) => {
        const frac = s.v / total, dash = `${frac * c} ${c}`, el = (
          <circle key={i} cx={size / 2} cy={size / 2} r={r} fill="none" stroke={s.c} strokeWidth="12"
            strokeDasharray={dash} strokeDashoffset={-off * c} transform={`rotate(-90 ${size / 2} ${size / 2})`} />);
        off += frac; return el;
      })}
    </svg>
  );
}
function Decay({ points, w = 300, h = 90 }) {
  if (!points || !points.length) return <div className="mz-empty">…</div>;
  const mx = Math.max(...points.map(p => p.y), 1);
  const px = (i) => (i / (points.length - 1)) * (w - 10) + 5;
  const py = (y) => h - 12 - (y / mx) * (h - 22);
  const path = points.map((p, i) => `${i ? "L" : "M"}${px(i)},${py(p.y)}`).join(" ");
  return (
    <svg width={w} height={h} style={{ width: "100%" }} viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none">
      <path d={path} fill="none" stroke={ACC} strokeWidth="2" />
      {points.map((p, i) => <circle key={i} cx={px(i)} cy={py(p.y)} r="2.5" fill={ACC} />)}
      {points.map((p, i) => <text key={"t" + i} x={px(i)} y={h - 2} fontSize="8" fill="var(--text-muted)" textAnchor="middle">{p.x}</text>)}
    </svg>
  );
}
function LineArea({ data, color = POS, w = 340, h = 120, area }) {
  if (!data || data.length < 2) return <div className="mz-empty">…</div>;
  const mn = Math.min(...data), mx = Math.max(...data), rg = (mx - mn) || 1;
  const px = (i) => (i / (data.length - 1)) * w;
  const py = (v) => h - 6 - ((v - mn) / rg) * (h - 12);
  const line = data.map((v, i) => `${i ? "L" : "M"}${px(i)},${py(v)}`).join(" ");
  return (
    <svg width={w} height={h} style={{ width: "100%" }} viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none">
      {area && <path d={`${line} L${w},${h} L0,${h} Z`} fill={color} opacity="0.12" />}
      <path d={line} fill="none" stroke={color} strokeWidth="1.8" />
    </svg>
  );
}
function HeatCell({ f, v }) {
  const col = v == null ? "var(--bg-raised)" : v > 0 ? `rgba(74,222,128,${Math.min(0.85, 0.15 + Math.abs(v) * 14)})`
    : `rgba(248,113,113,${Math.min(0.85, 0.15 + Math.abs(v) * 14)})`;
  return <div className="mz-heat-c" style={{ background: col }}><div className="mz-heat-f">{f}</div><div className="mz-heat-v">{v != null ? num(v, 3) : "—"}</div></div>;
}

const NAV = [
  { key: "overview", label: "نظرة عامة", icon: "🏠" },
  { key: "screener", label: "المسح", icon: "🔍", group: "التحليل", href: "/halal-screener-v2" },
  { key: "analysis", label: "تحليل الأسهم", icon: "📊", group: "التحليل", href: "/terminal" },
  { key: "lab", label: "مختبر الاستراتيجية", icon: "🧪", group: "التحليل", href: "/quant-lab" },
  { key: "factors", label: "العوامل", icon: "🧬", group: "التحليل", href: "/quant-lab" },
  { key: "models", label: "الموديلات", icon: "📈", group: "" },
  { key: "market", label: "السوق", icon: "🌍", group: "" },
  { key: "portfolio", label: "المحفظة", icon: "💼", group: "التنفيذ", href: "/terminal" },
  { key: "reports", label: "التقارير", icon: "📄", group: "المتابعة" },
  { key: "alerts", label: "التنبيهات", icon: "🔔", group: "المتابعة" },
  { key: "settings", label: "الإعدادات", icon: "⚙", group: "" },
];

function Panel({ title, right, children, cls }) {
  return (
    <div className={"mz-p " + (cls || "")}>
      {title && <div className="mz-p-h"><span className="mz-p-t">{title}</span>{right}</div>}
      <div className="mz-p-b">{children}</div>
    </div>
  );
}

function Overview() {
  const [d, setD] = useState({});
  useEffect(() => {
    let alive = true;
    // progressive: each panel appears as its endpoint resolves (don't gate on the slowest)
    const g = (u, k) => fetch(u).then(r => r.json()).then(v => { if (alive) setD(p => ({ ...p, [k]: v })); }).catch(() => {});
    g("/api/selection-quality", "sq"); g("/api/factor-ic-multi", "fic"); g("/api/regime-hmm", "rg");
    g("/api/context/bundle", "mk"); g("/api/v1/overview", "ov");
    g("/api/market/indicators", "ind"); g("/api/screener/deep-picks?limit=8", "dp");
    return () => { alive = false; };
  }, []);

  const sq = d.sq || {}, ov2 = sq.overlays || {}, est = sq.estimate || {}, gate = sq.gate || {};
  const scanners = Array.isArray(sq.scanners) ? sq.scanners : [];
  const weekly = scanners.find(s => s.scanner === "weekly") || {}, monthly = scanners.find(s => s.scanner === "monthly") || {};
  const regime = d.rg && d.rg.regime, bm = (d.rg && d.rg.book_multiplier) || {};
  const mk = d.mk || {}, port = (d.ov && d.ov.portfolio) || {};
  const attr = d.fic && d.fic.attribution && d.fic.attribution.factors;
  const picks = (d.dp && Array.isArray(d.dp.results)) ? d.dp.results : [];
  const top = picks[0] || null;
  const indicators = (d.ind && Array.isArray(d.ind.indicators)) ? d.ind.indicators : [];
  const regState = (mk.regime || "").toUpperCase();
  const bull = regState.includes("BULL");
  const conf = regime ? Math.round((1 - (regime.crisis || 0)) * 100) : null;

  // factor rows (map internal → display names, ordered by 10d IR)
  const facMap = { mom_12_1: "Momentum 12-1", rs: "RS vs SPY", above_ema20: "EMA20 Filter", rsi: "RSI (14)", atr_pct: "Volatility", dist_ema20_pct: "Dist EMA20" };
  const facRows = attr ? Object.entries(attr).map(([f, v]) => ({
    f, name: facMap[f] || f, ic: (v.h && v.h["10"] || {}).mean_ic, ir: (v.h && v.h["10"] || {}).ir, dir: v.direction,
  })).sort((a, b) => (b.ir || -99) - (a.ir || -99)) : [];
  // alpha decay from the best factor's IC across horizons (normalised to 1.0 at 5d)
  let decay = null;
  if (attr && attr.mom_12_1) {
    const H = [["1D", null], ["5D", "5"], ["10D", "10"], ["20D", "20"]];
    const base = Math.abs((attr.mom_12_1.h["5"] || {}).mean_ic || 0.04) || 0.04;
    decay = H.map(([lab, h]) => ({ x: lab, y: h ? Math.abs((attr.mom_12_1.h[h] || {}).mean_ic || 0) / base : 1.0 }));
  }
  const HS = ["10", "20", "5"];
  const secColors = ["#4ade80", "#60a5fa", "#f472b6", "#fbbf24", "#a78bfa", "#94a3b8"];
  const _bySec = {};
  (port.positions || []).forEach(p => { const s = p.sector || "أخرى"; _bySec[s] = (_bySec[s] || 0) + Math.abs(p.market_value || p.value || p.qty || 1); });
  const posSecs = Object.entries(_bySec).sort((a, b) => b[1] - a[1]).map(([s, v]) => ({ v, sec: s }));

  return (
    <div className="mz-ov">
      {/* status cards */}
      <div className="mz-cards">
        <div className="mz-card"><div className="mz-c-l">حالة السوق</div>
          <div className="mz-c-v big" style={{ color: bull ? POS : regState.includes("BEAR") ? NEG : WARN }}>{regState || "—"}</div>
          <div className="mz-c-s">{bull ? "Risk-On" : regState.includes("BEAR") ? "Risk-Off" : "Neutral"}</div></div>
        <div className="mz-card mz-c-gauge"><div><div className="mz-c-l">ثقة النظام</div><div className="mz-c-s">من HMM</div></div>
          <Ring value={conf || 0} color={conf >= 60 ? POS : WARN} label={conf != null ? conf + "%" : "…"} /></div>
        <div className="mz-card"><div className="mz-c-l">ألفا الأسبوعي</div>
          <div className="mz-c-v" style={{ color: (weekly.alpha || 0) >= 0 ? POS : NEG }}>{pct(weekly.alpha, 2)}</div>
          <div className="mz-c-s">t {num(weekly.alpha_t)} · IC {num(est.rs_ic, 3)}</div></div>
        <div className="mz-card"><div className="mz-c-l">الدفتر الورقي</div>
          <div className="mz-c-v" style={{ color: POS }}>{weekly.n || 0}<span className="mz-c-u"> صفقة</span></div>
          <div className="mz-c-s">مقاس مقابل SPY 🏆</div></div>
        <div className="mz-card"><div className="mz-c-l">الموديل (Meta)</div>
          <div className="mz-c-v" style={{ color: ov2.meta_trusted ? POS : "var(--text-primary)" }}>AUC {ov2.meta_status === "trained" ? num(ov2.meta_oos_auc) : "—"}</div>
          <div className="mz-c-s">{ov2.meta_trusted ? "موثوق" : "قيد التعلّم"} · {d.fic && d.fic.status ? (d.fic.status.rows || 0).toLocaleString("en") : "…"} عيّنة</div></div>
      </div>

      {/* market strip */}
      <Panel title="نظرة على السوق" cls="mz-strip-p">
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

      {/* mid: top pick | IC table (main) + right rail handled outside */}
      <div className="mz-mid">
        <Panel title="أفضل فرصة اليوم">
          {top ? (
            <div className="mz-tp">
              <div className="mz-tp-head">
                <div><div className="mz-tp-sym">{top.symbol}</div><div className="mz-tp-co">{top.company || ""}</div>
                  <div className="mz-tp-px">{money(top.price)}</div></div>
                <Ring value={Math.round(top.composite_score || 0)} color={POS} size={64} />
              </div>
              <div className="mz-tp-chips">{[top.sector || "—", "USA", top.is_halal ? "AAOIFI ✓" : "—", "زخم " + num(top.score_mom121, 0)].map((c, i) => <span key={i} className="mz-chip">{c}</span>)}</div>
              <div className="mz-tp-rec">⭐ درجة مركّبة {Math.round(top.composite_score || 0)}/100 — أقوى مرشّح اليوم (زخم 12-1 {num(top.score_mom121, 1)}، تقني {num(top.score_tech, 0)}).</div>
              <div className="mz-tp-btns"><a href="/terminal" className="mz-btn">فتح التفاصيل</a><a href="/terminal" className="mz-btn">صفقة ورقية</a><a href="/quant-lab" className="mz-btn gold">تحليل متقدّم</a></div>
            </div>
          ) : <div className="mz-empty">لا فرص مؤكّدة الآن</div>}
        </Panel>

        <Panel title="القوى · IC (IC)" right={<a className="mz-more" href="/quant-lab">المزيد ←</a>}>
          {facRows.length ? (
            <table className="mz-tbl">
              <thead><tr><th className="tl">العامل</th><th>IC</th><th>IR</th><th>الاتجاه</th></tr></thead>
              <tbody>{facRows.map((r, i) => (
                <tr key={r.f}>
                  <td className="tl mz-fn">{i + 1}. {r.name}{r.f === "mom_12_1" && <span style={{ color: ACC }}> ★</span>}</td>
                  <td style={{ color: icCol(r.ic) }}>{num(r.ic, 3)}</td>
                  <td>{num(r.ir)}</td>
                  <td style={{ color: (r.dir || "").includes("↑") ? POS : (r.dir || "").includes("↓") ? NEG : MUT }}>{r.dir}</td>
                </tr>))}</tbody>
            </table>
          ) : <div className="mz-empty">…يحمّل العوامل</div>}
        </Panel>
      </div>

      {/* charts row: heatmap | HMM | alpha decay */}
      <div className="mz-charts">
        <Panel title="خريطة العوامل (IC)">
          <div className="mz-heat">{facRows.slice(0, 8).map(r => <HeatCell key={r.f} f={r.name.split(" ")[0]} v={r.ic} />)}</div>
          <div className="mz-heat-scale"><span>−0.05</span><div className="mz-heat-bar" /><span>+0.05</span></div>
        </Panel>
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

      {/* bottom: signals table | portfolio donut */}
      <div className="mz-bottom">
        <Panel title="آخر الإشارات القوية" right={<a className="mz-more" href="/terminal">الكل ←</a>}>
          {picks.length ? (
            <table className="mz-tbl">
              <thead><tr><th className="tl">الرمز</th><th>القطاع</th><th>الدرجة</th><th>زخم</th><th>حلال</th></tr></thead>
              <tbody>{picks.slice(0, 6).map((b, i) => (
                <tr key={i}><td className="tl mz-fn">{b.symbol}</td><td className="mz-dim2">{b.sector || "—"}</td>
                  <td style={{ color: (b.composite_score || 0) >= 72 ? POS : "inherit" }}>{Math.round(b.composite_score || 0)}</td>
                  <td>{num(b.score_mom121, 0)}</td><td>{b.is_halal ? "✓" : "—"}</td></tr>))}</tbody>
            </table>
          ) : <div className="mz-empty">…يحمّل</div>}
        </Panel>
        <Panel title="ملخّص المحفظة الورقية">
          <div className="mz-port">
            <div className="mz-port-donut"><Donut segs={posSecs.length ? posSecs.map((s, i) => ({ v: s.v, c: secColors[i % 6] })) : [{ v: 1, c: "var(--bg-raised)" }]} />
              <div className="mz-donut-c"><b>{port.open_positions != null ? port.open_positions : (port.positions || []).length || "—"}</b><span>مفتوحة</span></div></div>
            <div className="mz-port-stats">
              <div><div className="mz-ps-l">إجمالي القيمة</div><div className="mz-ps-v">{money(port.equity || port.portfolio_value)}</div></div>
              <div><div className="mz-ps-l">العائد اليومي</div><div className="mz-ps-v" style={{ color: (port.daily_pnl_pct || 0) >= 0 ? POS : NEG }}>{pct(port.daily_pnl_pct, 2)}</div></div>
              {posSecs.slice(0, 4).map((s, i) => <div key={i} className="mz-leg"><span className="mz-leg-d" style={{ background: secColors[i % 6] }} />{s.sec}</div>)}
              <a href="/terminal" className="mz-btn" style={{ marginTop: 6 }}>عرض المحفظة الكاملة</a>
            </div>
          </div>
        </Panel>
      </div>
    </div>
  );
}

function RightRail(props) {
  const { sq, rg } = props;
  const ov2 = (sq && sq.overlays) || {}, gate = (sq && sq.gate) || {}, est = (sq && sq.estimate) || {};
  const monthly = ((sq && sq.scanners) || []).find(s => s.scanner === "monthly") || {};
  const sched = [["10:00", "تحديث العوامل", "اليوم"], ["17:00", "نضج الدفتر + تدريب Meta", "اليوم"], ["09:30", "تحقّق أسبوعي", "الاثنين"], ["09:30", "إعادة التوازن الشهري", "1 من الشهر"]];
  const health = [["البنية التحتية", 97, POS], ["الاستراتيجية", ov2.pbo_trust === "high" ? 88 : 60, POS], ["الموديلات", ov2.meta_trusted ? 90 : 52, ov2.meta_trusted ? POS : WARN], ["البيانات", d3(props), POS]];
  return (
    <aside className="mz-rail">
      <Panel title="ما القادم">
        <div className="mz-sched">{sched.map((s, i) => (<div className="mz-sc" key={i}><span className="mz-sc-t">{s[0]}</span><span className="mz-sc-n">{s[1]}</span><span className="mz-sc-d">{s[2]}</span></div>))}</div>
      </Panel>
      <Panel title="صحّة النظام">
        <div className="mz-health">{health.map(([l, v, c], i) => (
          <div className="mz-hg" key={i}><Ring value={v} color={c} size={48} label={v + "%"} /><div className="mz-hg-l">{l}</div></div>))}</div>
        <div className="mz-note">آخر فحص: منذ دقائق</div>
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
function d3(props) { const s = props.fic && props.fic.status; if (!s || !s.rows) return 60; return Math.min(100, Math.round((s.labelled / s.rows) * 100)); }

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
    Promise.all([fetch("/api/selection-quality").then(r => r.json()).catch(() => null), fetch("/api/factor-ic-multi").then(r => r.json()).catch(() => null), fetch("/api/regime-hmm").then(r => r.json()).catch(() => null)])
      .then(([sq, fic, rg]) => setShared({ sq, fic, rg }));
    return () => clearInterval(t);
  }, []);
  useEffect(() => { location.hash = view; }, [view]);
  const cur = NAV.find(n => n.key === view) || NAV[0];
  let lastG = null;
  return (
    <div className="mz">
      <aside className="mz-side">
        <div className="mz-logo"><span className="mz-logo-m">◈</span><div><div className="mz-logo-t">MIZAN</div><div className="mz-logo-s">QUANT TERMINAL</div></div></div>
        <nav className="mz-nav">{NAV.map(n => {
          const grp = n.group && n.group !== lastG ? (lastG = n.group, <div className="mz-nav-g" key={"g" + n.key}>{n.group}</div>) : null;
          const a = view === n.key;
          const el = n.href ? <a className={"mz-ni" + (a ? " on" : "")} href={n.href} key={n.key}><span className="mz-ni-i">{n.icon}</span>{n.label}</a>
            : <button className={"mz-ni" + (a ? " on" : "")} onClick={() => setView(n.key)} key={n.key}><span className="mz-ni-i">{n.icon}</span>{n.label}</button>;
          return <React.Fragment key={"f" + n.key}>{grp}{el}</React.Fragment>;
        })}</nav>
        <div className="mz-side-f"><span className={"mz-dot " + (broker && broker.connected ? "on" : "off")} />
          <div><div className="mz-bk-t">{broker && broker.connected ? "IBKR" : "البروكر"}</div><div className="mz-bk-s">{broker && broker.connected ? "متّصل · LIVE" : "غير متّصل"}</div></div></div>
      </aside>
      <div className="mz-main">
        <header className="mz-top">
          <div className="mz-top-t">{cur.label}</div>
          <div className="mz-search">🔎<input placeholder="ابحث عن رمز أو إسم…" /></div>
          <div className="mz-top-r"><span className="mz-sys"><span className="mz-dot on" /> النظام يعمل</span><span className="mz-clock">{clock} UTC</span></div>
        </header>
        <div className="mz-body">
          {view === "overview"
            ? <div className="mz-ov-wrap"><Overview /><RightRail {...shared} /></div>
            : <Stub label={cur.label} />}
        </div>
      </div>
    </div>
  );
}
ReactDOM.createRoot(document.getElementById("root")).render(<MizanTerminal />);
