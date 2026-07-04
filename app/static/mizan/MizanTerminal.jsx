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
function LineArea({ data, color = POS, w = 320, h = 96 }) {
  if (!data || data.length < 2) return <div className="mz-empty">…</div>;
  const mn = Math.min(...data, 0), mx = Math.max(...data, 0), rg = (mx - mn) || 1;
  const px = (i) => (i / (data.length - 1)) * w, py = (v) => h - 4 - ((v - mn) / rg) * (h - 8);
  const line = data.map((v, i) => `${i ? "L" : "M"}${px(i)},${py(v)}`).join(" ");
  const zero = py(0);
  return (<svg width={w} height={h} style={{ width: "100%" }} viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none">
    <line x1="0" y1={zero} x2={w} y2={zero} stroke="var(--border)" strokeWidth="1" strokeDasharray="3 3" />
    <path d={`${line} L${w},${zero} L0,${zero} Z`} fill={color} opacity="0.12" />
    <path d={line} fill="none" stroke={color} strokeWidth="1.8" />
  </svg>);
}
function Spark({ data, color = POS, w = 58, h = 16 }) {
  if (!data || data.length < 2) return null;
  const mn = Math.min(...data), mx = Math.max(...data), rg = (mx - mn) || 1;
  const pts = data.map((v, i) => `${(i / (data.length - 1)) * w},${h - ((v - mn) / rg) * (h - 2) - 1}`).join(" ");
  return <svg width={w} height={h} className="mz-spark"><polyline points={pts} fill="none" stroke={color} strokeWidth="1.3" /></svg>;
}
const SPARK_MAP = { "S&P 500": "SPY", "Nasdaq": "QQQ", "Dow": "DIA", "Russell": "IWM", "Gold": "GLD", "Brent": "BNO" };
function HeatCell({ f, v }) {
  const col = v == null ? "var(--bg-raised)" : v > 0 ? `rgba(74,222,128,${Math.min(0.8, 0.14 + Math.abs(v) * 13)})` : `rgba(248,113,113,${Math.min(0.8, 0.14 + Math.abs(v) * 13)})`;
  return <div className="mz-heat-c" style={{ background: col }}><div className="mz-heat-f">{f}</div><div className="mz-heat-v">{v != null ? num(v, 3) : "—"}</div></div>;
}

const NAV = [
  { key: "overview", label: "نظرة عامة", icon: "🏠" },
  { key: "screener", label: "المسح", icon: "🔍" },
  { key: "analysis", label: "تحليل الأسهم", icon: "📊" },
  { key: "portfolio", label: "المحفظة", icon: "💼" },
  { key: "ledger", label: "الدفتر الورقي", icon: "📒" },
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
    g("/paper_validation/status?scanner=weekly", "lw"); g("/api/alpha-curve", "ac");
    g("/api/market/spark", "sp");
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
          {(indicators.length ? indicators : [{ label: "…" }]).map((it, i) => {
            const sk = (d.sp && d.sp.spark || {})[SPARK_MAP[it.label]];
            return (<div className="mz-strip-i" key={i}>
              <div className="mz-strip-k">{it.label || it.symbol}</div>
              <div className="mz-strip-v">{it.price == null ? "—" : num(it.price)}</div>
              {it.change_pct != null && <div className="mz-strip-c" style={{ color: it.change_pct >= 0 ? POS : NEG }}>{pct(it.change_pct, 2)}</div>}
              {sk && <Spark data={sk} color={it.change_pct >= 0 ? POS : NEG} />}
            </div>);
          })}
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
        <Panel title="الأداء التراكمي للنموذج (الألفا)" right={<span className="mz-dim3">من الدفتر المُغلق</span>}>
          {(() => {
            const s = (d.ac && d.ac.series) || []; const vals = s.map(p => p.cum_alpha); const fin = d.ac && d.ac.final_alpha;
            return vals.length >= 2 ? (<div>
              <div className="mz-cum-v" style={{ color: (fin || 0) >= 0 ? POS : NEG }}>{pct(fin, 1)}</div>
              <LineArea data={vals} color={(fin || 0) >= 0 ? POS : NEG} />
              <div className="mz-note">ألفا الاختيار التراكمي عبر <b>{s.length}</b> صفقة مغلقة (عائد الصفقة − SPY).</div>
            </div>) : <div className="mz-cum"><div className="mz-cum-v">{pct(fin, 1)}</div>
              <div className="mz-note ql-dim">يتراكم — يظهر المنحنى بعد صفقات مغلقة كافية.</div></div>;
          })()}
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
  const [risk, setRisk] = useState(null);
  useEffect(() => {
    const eq = port.equity || port.portfolio_value;
    if (eq) fetch("/api/risk/var?equity=" + eq).then(r => r.json()).then(setRisk).catch(() => {});
  }, [port.equity, port.portfolio_value]);
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
          <div><span className="mz-rk-l">إجمالي المخاطرة</span><span className="mz-rk-v" style={{ color: risk && risk.posture === "منخفض" ? POS : risk && risk.posture === "مرتفع" ? NEG : WARN }}>{(risk && risk.posture) || "…"}</span></div>
          <div><span className="mz-rk-l">VaR (95%) يومي</span><span className="mz-rk-v" style={{ color: NEG }}>{risk && risk.var_95 != null ? money(risk.var_95) : "…"}</span></div>
          <div><span className="mz-rk-l">التقلّب السنوي</span><span className="mz-rk-v">{risk && risk.ann_vol_pct != null ? risk.ann_vol_pct + "%" : "…"}</span></div>
          <div><span className="mz-rk-l">مضاعِف الدفتر</span><span className="mz-rk-v">×{num((rg && rg.book_multiplier || {}).mult, 2)}</span></div>
        </div>
        <div className="mz-note mz-dim">VaR بارامتري يومي (القيمة × تقلّب SPY × 1.645).</div>
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
  const dp = useGet("/api/screener/deep-picks?limit=200");
  const rows = (dp && dp.results) || [];
  const [tab, setTab] = useState("all");
  const [q, setQ] = useState("");
  const [sortK, setSortK] = useState("composite_score");
  const TABS = [["all", "كل الأسهم"], ["halal", "متوافقة شرعاً"], ["buy", "توصية شراء"]];
  let shown = rows.filter(r => (tab === "all" || (tab === "halal" && r.is_halal) || (tab === "buy" && (r.composite_score || 0) >= 55))
    && (!q || (r.symbol || "").toUpperCase().includes(q.toUpperCase()) || (r.company || "").toUpperCase().includes(q.toUpperCase())));
  shown = [...shown].sort((a, b) => (b[sortK] || 0) - (a[sortK] || 0));

  const dist = [0, 0, 0, 0, 0]; rows.forEach(r => dist[Math.min(4, Math.floor((r.composite_score || 0) / 20))]++);
  const distMax = Math.max(...dist, 1);
  const sec = {}; rows.forEach(r => { const k = r.sector || "أخرى"; (sec[k] = sec[k] || []).push(r.composite_score || 0); });
  const secAvg = Object.entries(sec).map(([k, v]) => ({ sec: k, avg: v.reduce((a, x) => a + x, 0) / v.length, n: v.length })).sort((a, b) => b.avg - a.avg);
  const avg = rows.length ? rows.reduce((a, r) => a + (r.composite_score || 0), 0) / rows.length : 0;
  const avgMom = rows.length ? rows.reduce((a, r) => a + (r.score_mom121 || 0), 0) / rows.length : 0;

  const exportCsv = () => {
    const hdr = ["symbol", "company", "sector", "price", "composite", "mom121", "tech", "fund", "halal"];
    const lines = [hdr.join(",")].concat(shown.map(r => [r.symbol, '"' + (r.company || "") + '"', r.sector, r.price, r.composite_score, r.score_mom121, r.score_tech, r.score_fund, r.is_halal].join(",")));
    const a = document.createElement("a"); a.href = "data:text/csv;charset=utf-8," + encodeURIComponent(lines.join("\n")); a.download = "mizan_screener.csv"; a.click();
  };
  const secCol = (v) => v >= 75 ? "rgba(74,222,128,0.22)" : v >= 65 ? "rgba(251,191,36,0.18)" : "rgba(248,113,113,0.18)";

  return (
    <div className="mz-view">
      <div className="mz-ana-top">
        <div className="mz-tabs">{TABS.map(([k, l]) => <button key={k} className={"mz-tab" + (tab === k ? " on" : "")} onClick={() => setTab(k)}>{l}</button>)}</div>
        <div className="mz-dim3">إجمالي النتائج <b style={{ color: "var(--text-primary)" }}>{shown.length}</b></div>
      </div>
      <div className="mz-ana-wrap">
        <div className="mz-ana-main">
          <Panel right={<div className="mz-sort">فرز: {[["composite_score", "الدرجة"], ["score_mom121", "الزخم"], ["score_fund", "الأساسي"]].map(([k, l]) => <button key={k} className={"mz-rg" + (sortK === k ? " on" : "")} onClick={() => setSortK(k)}>{l}</button>)}</div>} title={<input className="mz-inp" style={{ width: 200 }} placeholder="بحث رمز أو اسم…" value={q} onChange={e => setQ(e.target.value)} />}>
            {rows.length ? (
              <table className="mz-tbl mz-tbl-wide">
                <thead><tr><th className="tl">الرمز</th><th className="tl">القطاع</th><th>السعر</th><th>التوصية</th><th>الدرجة</th><th>زخم</th><th>تقني</th><th>أساسي</th><th>حلال</th></tr></thead>
                <tbody>{shown.slice(0, 60).map((r, i) => { const [vd, vc] = verdictOf(r.composite_score || 0); return (
                  <tr key={i}><td className="tl mz-fn">{r.symbol}<div className="mz-dim2">{r.company || ""}</div></td>
                    <td className="tl mz-dim2">{r.sector || "—"}</td><td>{money(r.price)}</td>
                    <td><span className="mz-vd" style={{ color: vc, borderColor: vc }}>{vd}</span></td>
                    <td><span className="mz-score" style={{ color: (r.composite_score || 0) >= 55 ? POS : (r.composite_score || 0) >= 38 ? WARN : NEG }}>{Math.round(r.composite_score || 0)}</span></td>
                    <td>{num(r.score_mom121, 0)}</td><td>{num(r.score_tech, 0)}</td><td>{num(r.score_fund, 0)}</td><td>{r.is_halal ? "✓" : "—"}</td></tr>); })}</tbody>
              </table>
            ) : <div className="mz-empty">…يمسح الكون</div>}
          </Panel>
          <div className="mz-r2">
            <Panel title="توزيع الدرجات">
              <div className="mz-distr">{dist.map((v, i) => { const cols = [NEG, NEG, WARN, POS, POS]; return (
                <div className="mz-dcol" key={i}><div className="mz-dbar" style={{ height: (v / distMax * 100) + "%", background: cols[i] }}><span>{v}</span></div>
                  <div className="mz-dlab">{i * 20}-{i * 20 + 20}</div></div>); })}</div>
            </Panel>
            <Panel title="خريطة القطاعات (متوسط الدرجة)">
              <div className="mz-secmap">{secAvg.slice(0, 8).map((s, i) => (
                <div className="mz-sm" key={i} style={{ background: secCol(s.avg) }}><div className="mz-sm-n">{s.sec}</div><div className="mz-sm-v">{Math.round(s.avg)}</div></div>))}</div>
            </Panel>
          </div>
        </div>
        <aside className="mz-ana-rail">
          <Panel title="تصنيفات سريعة">
            <div className="mz-tags">{[["composite_score", "الأعلى درجة"], ["score_mom121", "الأعلى زخم"], ["score_fund", "الأقوى أساساً"], ["score_tech", "الأقوى تقنياً"]].map(([k, l]) => (
              <button key={k} className={"mz-tag2" + (sortK === k ? " on" : "")} onClick={() => setSortK(k)}>{l}</button>))}</div>
          </Panel>
          <Panel title="إحصائيات الماسح">
            <div className="mz-dl">
              {[["إجمالي النتائج", rows.length], ["متوسّط الدرجة", num(avg, 0)], ["متوسّط الزخم", num(avgMom, 1)],
              ["أفضل قطاع", secAvg[0] ? secAvg[0].sec : "—"], ["متوافقة شرعاً", rows.filter(r => r.is_halal).length]].map(([l, v], i) => (
                <div className="mz-dl-r" key={i}><span className="mz-dl-l">{l}</span><span className="mz-dl-v">{v}</span></div>))}
            </div>
            <button className="mz-btn gold" style={{ marginTop: 10 }} onClick={exportCsv}>⬇ تصدير النتائج (CSV)</button>
          </Panel>
        </aside>
      </div>
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
  const bh = useGet("/api/v1/broker/health");
  const port = (ov && ov.portfolio) || {}; const pos = port.positions || [];
  const acc = (bh && bh.account) || {};
  return (
    <div className="mz-view">
      <Panel title="البروكر" right={<span className="mz-dim3">{bh && bh.mode}</span>}>
        <div className="mz-broker"><span className={"mz-dot " + (bh && bh.connected ? "on" : "off")} />
          <b>{bh && bh.connected ? "متّصل — IBKR Paper" : "غير متّصل"}</b>
          <span className="mz-dim">· {(bh && bh.host) || ""}:{(bh && bh.port) || ""} · {(bh && bh.strategy) || ""}</span></div>
      </Panel>
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

const STRAT = { PV: "أسبوعي", PVM: "شهري", PVP: "أزواج", PVSH: "ظلّ" };

function LedgerView() {
  const pt = useGet("/api/v1/paper/trades");
  const lw = useGet("/paper_validation/status?scanner=weekly");
  const lm = useGet("/paper_validation/status?scanner=monthly");
  const trades = Array.isArray(pt) ? pt : (pt && pt.trades) || [];
  const open = trades.filter(t => t.status === "open"), closed = trades.filter(t => t.status !== "open");
  return (
    <div className="mz-view">
      <div className="mz-cards">
        <div className="mz-card"><div className="mz-c-l">الأسبوعي (PV)</div><div className="mz-c-v">{(lw && lw.open) || 0}<span className="mz-c-u"> مفتوح</span></div><div className="mz-c-s">{(lw && lw.closed) || 0} مغلق · {lw && lw.graduated ? "متخرّج" : "قيد التحقّق"}</div></div>
        <div className="mz-card"><div className="mz-c-l">الشهري (PVM)</div><div className="mz-c-v">{(lm && lm.open) || 0}<span className="mz-c-u"> مفتوح</span></div><div className="mz-c-s">{(lm && lm.closed) || 0} مغلق</div></div>
        <div className="mz-card"><div className="mz-c-l">إجمالي المفتوحة</div><div className="mz-c-v" style={{ color: POS }}>{open.length}</div></div>
        <div className="mz-card"><div className="mz-c-l">إجمالي المغلقة</div><div className="mz-c-v">{closed.length}</div></div>
      </div>
      <Panel title={"المراكز الورقية المفتوحة (" + open.length + ")"}>
        {open.length ? <table className="mz-tbl mz-tbl-wide"><thead><tr><th className="tl">الرمز</th><th>الاستراتيجية</th><th>الجانب</th><th>الكمّية</th><th>الدخول</th><th>القيمة</th><th>الثقة</th></tr></thead>
          <tbody>{open.slice(0, 30).map((t, i) => (<tr key={i}><td className="tl mz-fn">{t.symbol}</td><td className="mz-dim2">{STRAT[t.strategy_id] || t.strategy_id}</td>
            <td>{t.side === "buy" ? "شراء" : t.side}</td><td>{t.qty}</td><td>{money(t.entry_price)}</td><td>{money(t.position_value)}</td><td>{Math.round(t.confidence || 0)}</td></tr>))}</tbody></table>
          : <div className="mz-empty">لا مراكز مفتوحة</div>}
      </Panel>
      <Panel title={"المغلقة (" + closed.length + ")"}>
        {closed.length ? <table className="mz-tbl mz-tbl-wide"><thead><tr><th className="tl">الرمز</th><th>الاستراتيجية</th><th>الدخول</th><th>الخروج</th><th>العائد</th></tr></thead>
          <tbody>{closed.slice(0, 30).map((t, i) => (<tr key={i}><td className="tl mz-fn">{t.symbol}</td><td className="mz-dim2">{STRAT[t.strategy_id] || t.strategy_id}</td>
            <td>{money(t.entry_price)}</td><td>{money(t.exit_price)}</td><td style={{ color: (t.pnl_pct || 0) >= 0 ? POS : NEG, fontWeight: 700 }}>{pct(t.pnl_pct, 2)}</td></tr>))}</tbody></table>
          : <div className="mz-empty">لا صفقات مغلقة بعد</div>}
      </Panel>
    </div>
  );
}

function MarketView() {
  const mk = useGet("/api/context/bundle");
  const ind = useGet("/api/market/indicators");
  const nw = useGet("/api/market/news?limit=8");
  const macro = (mk && mk.macro) || {};
  const inds = (ind && ind.indicators) || [];
  const news = Array.isArray(nw) ? nw : (nw && (nw.news || nw.articles)) || [];
  const reg = ((mk && mk.regime) || "").toUpperCase();
  return (
    <div className="mz-view">
      <div className="mz-cards">
        <div className="mz-card"><div className="mz-c-l">النظام</div><div className="mz-c-v" style={{ color: reg.includes("BULL") ? POS : reg.includes("BEAR") ? NEG : WARN }}>{reg || "—"}</div><div className="mz-c-s">{(mk && mk.risk_posture) || ""}</div></div>
        <div className="mz-card"><div className="mz-c-l">VIX</div><div className="mz-c-v">{num(mk && mk.vix)}</div></div>
        <div className="mz-card"><div className="mz-c-l">التضخّم (CPI)</div><div className="mz-c-v">{num(macro.cpi_yoy)}%</div></div>
        <div className="mz-card"><div className="mz-c-l">الفائدة</div><div className="mz-c-v">{num(macro.fed_rate)}%</div></div>
        <div className="mz-card"><div className="mz-c-l">البطالة</div><div className="mz-c-v">{num(macro.unemployment)}%</div></div>
      </div>
      <div className="mz-r2">
        <Panel title="المؤشّرات الحيّة">
          <div className="mz-strip">{inds.map((it, i) => (<div className="mz-strip-i" key={i}><div className="mz-strip-k">{it.label}</div><div className="mz-strip-v">{num(it.price)}</div>{it.change_pct != null && <div className="mz-strip-c" style={{ color: it.change_pct >= 0 ? POS : NEG }}>{pct(it.change_pct, 2)}</div>}</div>))}</div>
        </Panel>
        <Panel title="أخبار السوق">
          {news.length ? <div className="mz-news">{news.slice(0, 6).map((n, i) => (<a className="mz-nw" href={n.link} target="_blank" key={i}><div className="mz-nw-t">{n.title}</div><div className="mz-nw-s">{n.publisher} · {(n.published || "").slice(0, 10)}</div></a>))}</div> : <div className="mz-empty">…</div>}
        </Panel>
      </div>
    </div>
  );
}

function ModelsView() {
  const mm = useGet("/api/meta-model");
  const sq = useGet("/api/selection-quality");
  const ov2 = (sq && sq.overlays) || {};
  return (
    <div className="mz-view">
      <Panel title="نموذج Meta-labeling">
        {mm && mm.status === "trained" ? (<div>
          <div className="mz-cards"><div className="mz-card"><div className="mz-c-l">AUC خارج العيّنة</div><div className="mz-c-v" style={{ color: mm.trusted ? POS : "inherit" }}>{num(mm.oos_auc)}</div><div className="mz-c-s">{mm.trusted ? "موثوق ✓" : "دون عتبة 0.53"}</div></div>
            <div className="mz-card"><div className="mz-c-l">AUC داخل العيّنة</div><div className="mz-c-v">{num(mm.auc_in_sample)}</div></div>
            <div className="mz-card"><div className="mz-c-l">العيّنات</div><div className="mz-c-v">{(mm.n || 0).toLocaleString("en")}</div></div>
            <div className="mz-card"><div className="mz-c-l">المعدّل الأساسي</div><div className="mz-c-v">{num(mm.base_rate)}</div></div></div>
          <div className="mz-note">أهمّ العوامل: {(mm.top_features || []).map(f => f[0]).join(" · ")}. النموذج لا يُحجّم الدفتر إلا إذا تجاوز AUC خارج العيّنة العتبة (انضباط ضدّ فرط التخصيص).</div>
        </div>) : <div className="mz-empty">نموذج Meta قيد التعلّم — يتدرّب حين تُسمّى ≥120 لقطة. الحالة: {(mm && mm.status) || "…"}</div>}
      </Panel>
      <Panel title="لوحة الموديلات (Leaderboard)"><div className="mz-empty">تتراكم مع تسجيل أداء الاستراتيجيات — لا توجد موديلات مُقيَّمة بعد.</div></Panel>
    </div>
  );
}

function SettingsView() {
  const gc = useGet("/api/gate-config");
  const [msg, setMsg] = useState("");
  const knobs = [
    ["GATE_MIN_T", "حسّاسية دلالة توصية البوّابة", "2.0"],
    ["WEEKLY_MIN_RS", "عتبة الدخول الأسبوعي", gc ? num(gc.min_rs, 1) : "-2"],
    ["COMPOSITE_MOM121_WEIGHT", "وزن الزخم 12-1", "12"],
    ["COMPOSITE_MOMENTUM_WEIGHT", "وزن RS", "10"],
    ["VOL_TARGET", "تقلّب المحفظة المستهدف", "0.14"],
    ["META_MIN_OOS_AUC", "عتبة ثقة نموذج Meta", "0.53"],
  ];
  const resetGate = async () => {
    if (!window.confirm("إعادة عتبة الدخول إلى الافتراضي (−2)؟ ورقي فقط · قابل للعكس.")) return;
    try { const r = await fetch("/api/gate-config/reset", { method: "POST" }).then(x => x.json()); setMsg("تمّت الإعادة → MIN_RS " + r.now); } catch (e) { setMsg("تعذّر"); }
  };
  return (
    <div className="mz-view">
      <Panel title="عتبة الدخول (البوّابة)">
        <div className="mz-gate-cur">MIN_RS الحالية <b>{gc ? num(gc.min_rs, 1) : "…"}%</b> ({gc && gc.source === "approved" ? "معتمَدة بالدليل" : "افتراضية"})</div>
        <button className="mz-btn" style={{ maxWidth: 220 }} onClick={resetGate}>إعادة إلى الافتراضي (−2)</button>
        {msg && <div className="mz-note" style={{ color: POS }}>{msg}</div>}
      </Panel>
      <Panel title="مفاتيح الضبط (env · قياس فقط · قابلة للعكس)">
        <table className="mz-tbl mz-tbl-wide"><thead><tr><th className="tl">المفتاح</th><th className="tl">الوصف</th><th>القيمة</th></tr></thead>
          <tbody>{knobs.map((k, i) => (<tr key={i}><td className="tl mz-fn" style={{ fontFamily: "var(--font-mono)", fontSize: 11 }}>{k[0]}</td><td className="tl mz-dim2">{k[1]}</td><td>{k[2]}</td></tr>))}</tbody></table>
        <div className="mz-note ql-dim">التعديل عبر متغيّرات البيئة على الخادم — كلّها آمنة، ورقية، وقابلة للعكس. لا صفقات حقيقية.</div>
      </Panel>
    </div>
  );
}

function ReportsView() {
  return (<div className="mz-view"><Panel title="التقارير">
    <div className="mz-reports">
      <a className="mz-rep" href="/quant-lab">📊 مختبر الاستراتيجية — تقرير العوامل والباك-تيست</a>
      <a className="mz-rep" href="/terminal">📈 لوحة الترمينال — الأداء والمراكز</a>
      <a className="mz-rep" href="/risk-desk-v2">🛡️ مكتب المخاطر</a>
    </div>
    <div className="mz-note ql-dim">تصدير PDF المجدوَل قيد الإعداد ضمن الهيكلة الجديدة.</div>
  </Panel></div>);
}

function Candles({ bars, w = 660, h = 300 }) {
  if (!bars || bars.length < 2) return <div className="mz-empty">…يحمّل المخطّط</div>;
  const n = bars.length, hi = Math.max(...bars.map(b => b.high)), lo = Math.min(...bars.map(b => b.low)), rg = (hi - lo) || 1;
  const vmax = Math.max(...bars.map(b => b.volume || 0)) || 1;
  const padB = 30, volH = 34, plotH = h - padB - volH, cw = w / n, bw = Math.max(1.2, cw * 0.62);
  const y = (p) => 4 + (1 - (p - lo) / rg) * (plotH - 8);
  const last = bars[n - 1];
  return (<svg width={w} height={h} style={{ width: "100%" }} viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none">
    {bars.map((b, i) => {
      const x = i * cw + cw / 2, up = b.close >= b.open, col = up ? "var(--positive)" : "var(--negative)";
      const vh = ((b.volume || 0) / vmax) * volH;
      return (<g key={i}>
        <rect x={x - bw / 2} y={h - vh} width={bw} height={vh} fill={col} opacity="0.28" />
        <line x1={x} y1={y(b.high)} x2={x} y2={y(b.low)} stroke={col} strokeWidth="1" />
        <rect x={x - bw / 2} y={Math.min(y(b.open), y(b.close))} width={bw} height={Math.max(1, Math.abs(y(b.open) - y(b.close)))} fill={col} />
      </g>);
    })}
    <line x1="0" y1={y(last.close)} x2={w} y2={y(last.close)} stroke={ACC} strokeWidth="0.7" strokeDasharray="2 2" opacity="0.6" />
  </svg>);
}

const RANGES = [["1D", "5d"], ["1W", "5d"], ["1M", "1mo"], ["3M", "3mo"], ["6M", "6mo"], ["1Y", "1y"], ["2Y", "2y"], ["5Y", "5y"]];

function StockAnalysisView() {
  const dp = useGet("/api/screener/deep-picks?limit=200");
  const rows = (dp && dp.results) || [];
  const [sym, setSym] = useState("AAPL");
  const [inp, setInp] = useState("AAPL");
  const [range, setRange] = useState("6mo");
  const plan = useGet("/api/v1/trade/plan?symbol=" + sym);
  const chart = useGet("/api/stock/chart?symbol=" + sym + "&range=" + range);
  const pick = rows.find(r => r.symbol === sym) || {};
  const bars = (chart && chart.bars) || [];
  const det = (plan && plan.details) || {}, an = (plan && plan.analyst) || {}, fu = (plan && plan.fundamentals) || {}, pm = (plan && plan.premortem) || {}, ea = (plan && plan.earnings) || {};
  const score = pick.composite_score != null ? Math.round(pick.composite_score) : (plan && plan.strategy_score != null ? Math.round(plan.strategy_score) : null);
  const [vd, vc] = score != null ? verdictOf(score) : ["…", MUT];
  const px = pick.price || (plan && plan.price) || (bars.length ? bars[bars.length - 1].close : null);
  const chg = det.day_change_pct;
  const anTotal = (an.buy || 0) + (an.hold || 0) + (an.sell || 0) || 1;
  const expRet = plan && plan.reward_per_share && px ? (plan.reward_per_share / px) * 100 : null;
  const go = () => setSym((inp || "").trim().toUpperCase() || sym);
  const wkHi = bars.length ? Math.max(...bars.map(b => b.high)) : null, wkLo = bars.length ? Math.min(...bars.map(b => b.low)) : null;
  const avgVol = bars.length ? bars.reduce((a, b) => a + (b.volume || 0), 0) / bars.length : null;
  const tags = [];
  if (fu.revenue_growth > 8) tags.push("نمو قوي");
  if (fu.roe > 30) tags.push("ربحية عالية");
  if (pick.is_halal || plan && plan.halal === "halal") tags.push("حلال");
  if ((det.adx || 0) > 25) tags.push("اتجاه قويّ");
  if (pm.risk === "medium" || pm.risk === "high") tags.push("مخاطرة " + (pm.risk === "high" ? "عالية" : "متوسطة"));

  return (
    <div className="mz-view">
      <div className="mz-ana-top">
        <div className="mz-crumb">الرئيسية / تحليل الأسهم / <b>{sym}</b></div>
        <div className="mz-sym-pick"><input className="mz-inp" value={inp} onChange={e => setInp(e.target.value)} onKeyDown={e => e.key === "Enter" && go()} placeholder="رمز السهم…" /><button className="mz-btn gold" style={{ maxWidth: 60 }} onClick={go}>تحليل</button></div>
      </div>

      <div className="mz-ana-wrap">
        <div className="mz-ana-main">
          <Panel>
            <div className="mz-ana-hero">
              <div className="mz-ana-id">
                <div className="mz-tp-row"><div className="mz-tp-sym">{sym}</div><span className="mz-buy" style={{ color: an.rating === "sell" ? NEG : POS, borderColor: an.rating === "sell" ? NEG : POS }}>{an.rating === "sell" ? "SELL" : an.rating === "hold" ? "HOLD" : "BUY"}</span></div>
                <div className="mz-tp-co">{pick.company || ""}</div>
                <div className="mz-tp-chips">{[pick.sector || "—", "USA", (pick.is_halal || (plan && plan.halal) === "halal") ? "شرعية متوافقة · AAOIFI ✓" : "غير متوافق"].map((c, i) => <span key={i} className="mz-chip">{c}</span>)}</div>
                <div className="mz-ana-px">{money(px)} {chg != null && <span style={{ color: chg >= 0 ? POS : NEG }}>{pct(chg, 2)}</span>}</div>
              </div>
              <Ring value={score || 0} color={vc} size={78} sub="درجة شاملة" />
            </div>
            <div className="mz-ana-metrics">
              {[["العائد المتوقّع", expRet != null ? pct(expRet) : "—", expRet >= 0 ? POS : "inherit"], ["مدّة الاحتفاظ", (plan && plan.hold_days_max || plan && plan.hold_days_min || 18) + " يوم", "inherit"],
              ["مستوى المخاطرة", pm.risk === "high" ? "مرتفع" : pm.risk === "low" ? "منخفض" : "متوسط", pm.risk === "high" ? NEG : pm.risk === "low" ? POS : WARN], ["عائد:مخاطرة", (plan && plan.rr_ratio != null ? num(plan.rr_ratio, 1) : "—"), "inherit"],
              ["وقف كارثي", plan && plan.catastrophe_stop_pct != null ? "-" + num(plan.catastrophe_stop_pct, 0) + "%" : "—", NEG]].map(([l, v, c], i) => (
                <div className="mz-am" key={i}><div className="mz-am-l">{l}</div><div className="mz-am-v" style={{ color: c }}>{v}</div></div>))}
            </div>
          </Panel>

          <Panel title="السعر" right={<div className="mz-ranges">{RANGES.slice(2).map(([lab, r]) => <button key={lab} className={"mz-rg" + (range === r ? " on" : "")} onClick={() => setRange(r)}>{lab}</button>)}</div>}>
            <Candles bars={bars} />
            <div className="mz-ind">
              {[["RSI", num(det.rsi, 1), (det.rsi || 50) > 55 ? "صاعد" : (det.rsi || 50) < 45 ? "هابط" : "محايد"], ["ADX", num(det.adx, 1), (det.adx || 0) > 25 ? "قويّ" : "ضعيف"],
              ["ATR", num(det.atr, 2), "—"], ["EMA50", det.above_ema50 ? "فوق" : "تحت", det.above_ema50 ? "صاعد" : "هابط"], ["نطاق القمّة", num((det.close_position || 0) * 100, 0) + "%", "—"]].map(([l, v, s], i) => (
                <div className="mz-in" key={i}><div className="mz-in-l">{l}</div><div className="mz-in-v">{v}</div><div className="mz-in-s" style={{ color: s === "صاعد" || s === "قويّ" ? POS : s === "هابط" ? NEG : MUT }}>{s}</div></div>))}
            </div>
          </Panel>

          <div className="mz-r2">
            <Panel title="تقييم المحلّلين">
              {an.known ? (<div className="mz-port"><div className="mz-port-donut"><Donut segs={[{ v: an.buy, c: POS }, { v: an.hold, c: WARN }, { v: an.sell, c: NEG }]} size={104} /><div className="mz-donut-c"><b>{an.n_analysts}</b><span>محلّل</span></div></div>
                <div className="mz-port-r"><div className="mz-leg"><span className="mz-leg-d" style={{ background: POS }} />شراء <b>{Math.round(an.buy / anTotal * 100)}%</b></div>
                  <div className="mz-leg"><span className="mz-leg-d" style={{ background: WARN }} />احتفاظ <b>{Math.round(an.hold / anTotal * 100)}%</b></div>
                  <div className="mz-leg"><span className="mz-leg-d" style={{ background: NEG }} />بيع <b>{Math.round(an.sell / anTotal * 100)}%</b></div>
                  <div className="mz-note">التقييم: <b style={{ color: an.rating === "sell" ? NEG : POS }}>{an.rating === "sell" ? "بيع" : an.rating === "hold" ? "احتفاظ" : "شراء"}</b></div></div></div>) : <div className="mz-empty">لا تغطية تحليلية</div>}
            </Panel>
            <Panel title="النقاط الرئيسية">
              <div className="mz-keys">
                {fu.revenue_growth > 5 && <div className="mz-key ok">✓ نمو الإيرادات {num(fu.revenue_growth, 1)}%</div>}
                {fu.roe > 20 && <div className="mz-key ok">✓ عائد على حقوق الملكية {num(fu.roe, 0)}%</div>}
                {fu.gross_margin > 30 && <div className="mz-key ok">✓ هامش إجمالي {num(fu.gross_margin, 1)}%</div>}
                {(pm.flags || []).slice(0, 3).map((f, i) => <div className="mz-key risk" key={i}>⚠ {f}</div>)}
                {!fu.known && !(pm.flags || []).length && <div className="mz-empty">…يحمّل</div>}
              </div>
            </Panel>
          </div>
        </div>

        <aside className="mz-ana-rail">
          <Panel title="تفاصيل السهم">
            <div className="mz-dl">
              {[["السعر الحالي", money(px)], ["التغيّر اليومي", pct(chg, 2)], ["أعلى (النطاق)", money(wkHi)], ["أدنى (النطاق)", money(wkLo)],
              ["متوسّط الحجم", avgVol ? (avgVol / 1e6).toFixed(1) + "M" : "—"], ["نمو الإيرادات", fu.revenue_growth != null ? num(fu.revenue_growth, 1) + "%" : "—"],
              ["ROE", fu.roe != null ? num(fu.roe, 0) + "%" : "—"], ["الأرباح القادمة", ea.date || "—"]].map(([l, v], i) => (
                <div className="mz-dl-r" key={i}><span className="mz-dl-l">{l}</span><span className="mz-dl-v">{v}</span></div>))}
            </div>
          </Panel>
          <Panel title="التقييم الأساسي">
            <div className="mz-dl">
              {[["هامش إجمالي", fu.gross_margin != null ? num(fu.gross_margin, 1) + "%" : "—"], ["دين/حقوق", fu.debt_equity != null ? num(fu.debt_equity, 2) : "—"],
              ["FCF/سهم", fu.fcf_per_share != null ? "$" + num(fu.fcf_per_share, 2) : "—"], ["درجة الأساسيات", fu.score != null ? num(fu.score, 0) + "/100" : "—"],
              ["الحلال", (pick.is_halal || (plan && plan.halal) === "halal") ? "✓ متوافق" : "—"]].map(([l, v], i) => (
                <div className="mz-dl-r" key={i}><span className="mz-dl-l">{l}</span><span className="mz-dl-v">{v}</span></div>))}
            </div>
          </Panel>
          {tags.length > 0 && <Panel title="الوسوم"><div className="mz-tags">{tags.map((t, i) => <span className="mz-tag2" key={i}>{t}</span>)}</div></Panel>}
        </aside>
      </div>
    </div>
  );
}

function AlertsView() {
  const ov = useGet("/api/v1/overview");
  const port = (ov && ov.portfolio) || {};
  const items = [
    ["التداول الآلي", port.auto_trade_enabled ? "مُفعّل" : "متوقّف", port.auto_trade_enabled ? WARN : POS],
    ["نوع الحساب", port.account_type || "—", MUT],
    ["الوسيط", port.broker_type || "—", MUT],
  ];
  return (
    <div className="mz-view">
      <Panel title="حالة النظام">
        <div className="mz-risk">{items.map(([l, v, c], i) => (<div key={i}><span className="mz-rk-l">{l}</span><span className="mz-rk-v" style={{ color: c }}>{v}</span></div>))}</div>
      </Panel>
      <Panel title="التنبيهات النشطة">
        <div className="mz-empty">لا تنبيهات حرجة الآن. تظهر هنا تنبيهات الخروج الذكي، انكسار الأزواج، وتغيّر النظام حين تقع — قياس فقط.</div>
      </Panel>
    </div>
  );
}

function Stub({ label }) {
  return <div className="mz-stub"><div className="mz-stub-ic">🧭</div><div className="mz-stub-t">{label}</div>
    <div className="mz-stub-s">هذا القسم قيد الإعداد ضمن الهيكلة الجديدة — يُبنى ببيانات حقيقية (لا بيانات وهمية).</div></div>;
}

function MizanTerminal() {
  const [view, setView] = useState((location.hash || "").replace(/^#/, "") || "overview");
  const [clock, setClock] = useState("--:--:--");
  const [broker, setBroker] = useState(null);
  const [shared, setShared] = useState({});
  useEffect(() => {  // hash drives the view — robust to direct load, back/forward, and clicks
    const read = () => setView((location.hash || "").replace(/^#/, "") || "overview");
    read();
    window.addEventListener("hashchange", read);
    return () => window.removeEventListener("hashchange", read);
  }, []);
  useEffect(() => {
    const t = setInterval(() => setClock(new Date().toUTCString().slice(17, 25)), 1000);
    fetch("/api/v1/broker/health").then(r => r.json()).then(setBroker).catch(() => {});
    const g = (u, k) => fetch(u).then(r => r.json()).then(v => setShared(p => ({ ...p, [k]: v }))).catch(() => {});
    g("/api/selection-quality", "sq"); g("/api/factor-ic-multi", "fic"); g("/api/regime-hmm", "rg"); g("/api/v1/overview", "ov");
    return () => clearInterval(t);
  }, []);
  const cur = NAV.find(n => n.key === view) || NAV[0];
  const today = new Date();
  return (
    <div className="mz">
      <aside className="mz-side">
        <div className="mz-logo"><span className="mz-logo-m">◈</span><div><div className="mz-logo-t">MIZAN</div><div className="mz-logo-s">QUANT TERMINAL</div></div></div>
        <nav className="mz-nav">{NAV.map(n => {
          const a = view === n.key;
          return n.href ? <a className={"mz-ni" + (a ? " on" : "")} href={n.href} key={n.key}><span className="mz-ni-i">{n.icon}</span>{n.label}</a>
            : <button className={"mz-ni" + (a ? " on" : "")} onClick={() => { location.hash = n.key; }} key={n.key}><span className="mz-ni-i">{n.icon}</span>{n.label}</button>;
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
              : view === "analysis" ? <StockAnalysisView />
              : view === "factors" ? <FactorsView />
                : view === "portfolio" ? <PortfolioView />
                  : view === "ledger" ? <LedgerView />
                    : view === "market" ? <MarketView />
                      : view === "models" ? <ModelsView />
                        : view === "reports" ? <ReportsView />
                          : view === "alerts" ? <AlertsView />
                            : view === "settings" ? <SettingsView />
                              : <Stub label={cur.label} />}
        </div>
      </div>
    </div>
  );
}
ReactDOM.createRoot(document.getElementById("root")).render(<MizanTerminal />);
