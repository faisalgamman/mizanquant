// MizanTerminal.jsx — unified quant terminal, Bloomberg-style, matching the design mockup.
// Shell: left nav · center main · RIGHT info rail. Real data everywhere it exists; honest
// placeholders otherwise (never fabricated numbers/curves).
const { useState, useEffect, useRef } = React;

const pct = (v, d = 1) => (v == null || isNaN(v)) ? "—" : (v >= 0 ? "+" : "") + Number(v).toFixed(d) + "%";
const num = (v, d = 2) => (v == null || isNaN(v)) ? "—" : Number(v).toFixed(d);
const money = (v) => v == null ? "—" : "$" + Math.round(v).toLocaleString("en");
const POS = "var(--positive)", NEG = "var(--negative)", WARN = "var(--warning)", ACC = "var(--accent)", MUT = "var(--text-muted)";
const icCol = (v) => v == null ? "var(--text-secondary)" : v > 0.015 ? POS : v < -0.015 ? NEG : "var(--text-secondary)";
// jump to the in-shell Stock Analysis view for a symbol (carries symbol + intent via sessionStorage)
function goAnalyze(sym, intent) {
  try { sessionStorage.setItem("mz_analyze_sym", (sym || "").toUpperCase()); sessionStorage.setItem("mz_analyze_intent", intent || ""); } catch (e) { }
  if (((location.hash || "").replace(/^#/, "")) === "analysis") window.dispatchEvent(new HashChangeEvent("hashchange"));
  else location.hash = "analysis";
}

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
  { key: "core", label: "محفظة النواة", icon: "🎯" },
  { key: "screener", label: "المسح", icon: "🔍" },
  { key: "daytrade", label: "التداول اليومي", icon: "⚡" },
  { key: "analysis", label: "تحليل الأسهم", icon: "📊" },
  { key: "portfolio", label: "المحفظة", icon: "💼" },
  { key: "ledger", label: "الدفتر الورقي", icon: "📒" },
  { key: "lab", label: "مختبر الاستراتيجية", icon: "🧪" },
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

  const facMap = { mom_12_1: "زخم 12-1", rs: "قوّة نسبيّة (RS)", above_ema20: "شرط فوق EMA20", rsi: "RSI (14)", atr_pct: "التقلّب (ATR)", dist_ema20_pct: "الامتداد عن EMA20",
    hi52_prox: "قرب قمّة 52 أسبوعاً", resid_mom: "زخم البواقي", sharpe_mom: "زخم معدّل بالمخاطر", mom_consistency: "ثبات الزخم", downside_vol: "التقلّب الهابط", beta: "بيتا (مقابل SPY)", maxdd_6m: "أقصى تراجع 6ش", range_pos_20: "الموقع بالنطاق 20ي", rev_5d: "انعكاس 5 أيام", pead: "انجراف الأرباح (PEAD)" };
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
            const sk = (d.sp && d.sp.spark || {})[it.symbol] || (d.sp && d.sp.spark || {})[SPARK_MAP[it.label]];
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
              <div className="mz-tp-btns"><button className="mz-btn" onClick={() => goAnalyze(top.symbol, "")}>فتح التفاصيل</button><button className="mz-btn" onClick={() => goAnalyze(top.symbol, "trade")}>صفقة ورقية</button><button className="mz-btn gold" onClick={() => goAnalyze(top.symbol, "forecast")}>تحليل متقدّم</button></div>
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
  useEffect(() => { if (!url) { setV(null); return; } let a = true; fetch(url).then(r => r.json()).then(x => a && setV(x)).catch(() => a && setV({})); return () => { a = false; }; }, [url]);
  return v;
}
const verdictOf = (s) => s >= 72 ? ["STRONG BUY", POS] : s >= 55 ? ["BUY", POS] : s >= 38 ? ["WATCH", WARN] : ["AVOID", MUT];

const SCR_PRESETS = [
  { name: "درجة عالية", desc: "الدرجة ≥ 70 · فرز بالدرجة", cfg: { tab: "all", sortK: "composite_score", secF: "all", pxMin: 0, scMin: 70 } },
  { name: "زخم قويّ", desc: "الدرجة ≥ 55 · فرز بالزخم", cfg: { tab: "all", sortK: "score_mom121", secF: "all", pxMin: 0, scMin: 55 } },
  { name: "قوّة أساسية", desc: "فرز بالأساسي", cfg: { tab: "all", sortK: "score_fund", secF: "all", pxMin: 0, scMin: 0 } },
  { name: "متوافقة شرعاً", desc: "حلال فقط · درجة ≥ 55", cfg: { tab: "halal", sortK: "composite_score", secF: "all", pxMin: 0, scMin: 55 } },
];

// Squarified treemap (Bruls et al.) — lays each item along the shorter side of the free
// rectangle for good aspect ratios. Returns [{...item, x, y, w, h}] that exactly tile w×h.
function squarify(data, W, H) {
  const items = (data || []).filter(d => d.value > 0).sort((a, b) => b.value - a.value);
  const totalVal = items.reduce((s, d) => s + d.value, 0) || 1;
  const scaled = items.map(d => ({ d, area: d.value / totalVal * (W * H) }));
  const out = [];
  let X = 0, Y = 0, w = W, h = H, i = 0;
  const worst = (row, len) => {
    if (!row.length) return Infinity;
    const s = row.reduce((a, r) => a + r.area, 0);
    const rmax = Math.max(...row.map(r => r.area)), rmin = Math.min(...row.map(r => r.area));
    return Math.max((len * len * rmax) / (s * s), (s * s) / (len * len * rmin));
  };
  while (i < scaled.length) {
    const len = Math.min(w, h);
    const row = [scaled[i]]; let j = i + 1;
    while (j < scaled.length && worst([...row, scaled[j]], len) <= worst(row, len)) { row.push(scaled[j]); j++; }
    const rowArea = row.reduce((a, r) => a + r.area, 0), thickness = rowArea / len;
    const vertical = (w >= h); let pos = 0;
    for (const r of row) {
      const tileLen = r.area / (thickness || 1);
      if (vertical) out.push({ ...r.d, x: X, y: Y + pos, w: thickness, h: tileLen });
      else out.push({ ...r.d, x: X + pos, y: Y, w: tileLen, h: thickness });
      pos += tileLen;
    }
    if (vertical) { X += thickness; w -= thickness; } else { Y += thickness; h -= thickness; }
    i = j;
  }
  return out;
}
function Treemap({ items, w = 680, h = 300 }) {
  if (!items || !items.length) return <div className="mz-empty">…لا بيانات للخريطة</div>;
  const cells = squarify(items, w, h);
  const col = (s) => s >= 72 ? "rgba(74,222,128,0.9)" : s >= 55 ? "rgba(74,222,128,0.55)" : s >= 38 ? "rgba(251,191,36,0.6)" : "rgba(248,113,113,0.55)";
  return (<svg width="100%" viewBox={`0 0 ${w} ${h}`} style={{ display: "block", borderRadius: 8 }}>
    {cells.map((c, i) => (<g key={i} style={{ cursor: "pointer" }} onClick={() => goAnalyze(c.symbol, "")}>
      <rect x={c.x + 1} y={c.y + 1} width={Math.max(0, c.w - 2)} height={Math.max(0, c.h - 2)} fill={col(c.score)} rx="2" />
      {c.w > 42 && c.h > 22 && <text x={c.x + 6} y={c.y + 16} fontSize="11" fontWeight="800" fill="#0b1220">{c.symbol}</text>}
      {c.w > 42 && c.h > 36 && <text x={c.x + 6} y={c.y + 29} fontSize="10" fill="#0b1220" opacity="0.75">{Math.round(c.score)}</text>}
    </g>))}
  </svg>);
}

function ScreenerView() {
  const [tick, setTick] = useState(0);
  const [lastUp, setLastUp] = useState(null);
  const dp = useGet("/api/screener/deep-picks?limit=200&_t=" + tick);
  const rows = (dp && dp.results) || [];
  useEffect(() => { if (dp && dp.results) setLastUp(new Date()); }, [dp]);
  useEffect(() => { const iv = setInterval(() => setTick(t => t + 1), 300000); return () => clearInterval(iv); }, []);  // auto-refresh every 5 min
  const [tab, setTab] = useState("all");
  const [q, setQ] = useState("");
  const [sortK, setSortK] = useState("composite_score");
  const [secF, setSecF] = useState("all");
  const [pxMin, setPxMin] = useState(0);
  const [scMin, setScMin] = useState(0);
  const [saved, setSaved] = useState(() => { try { return JSON.parse(localStorage.getItem("mz_screens") || "[]"); } catch (e) { return []; } });
  const [perfRange, setPerfRange] = useState("6mo");
  const perf = useGet("/api/screener/results-performance?range=" + perfRange + "&limit=20");
  const [sortDir, setSortDir] = useState("desc");
  const [exp, setExp] = useState(null);   // expanded row symbol
  const [pop, setPop] = useState(null);   // score-breakdown popover symbol
  const rg = useGet("/api/regime-hmm");
  const RGMAP = { crisis: "أزمة", choppy: "تقليدي", calm_bull: "هادئ صاعد", calm: "هادئ", bull: "صاعد", neutral: "محايد", risk_on: "مُخاطِر", risk_off: "دفاعي" };
  const _rgo = rg && rg.regime;
  const _rgKey = (_rgo && typeof _rgo === "object") ? _rgo.dominant : (typeof _rgo === "string" ? _rgo : (rg && (rg.state || rg.label)));
  const regimeTxt = _rgKey ? (RGMAP[_rgKey] || _rgKey) : null;
  const bookMult = rg && (rg.book_multiplier != null ? rg.book_multiplier : (rg.book_mult != null ? rg.book_mult : (_rgo && typeof _rgo === "object" ? _rgo.book_mult : null)));
  const [cmp, setCmp] = useState([]);      // symbols picked for side-by-side compare (max 4)
  const [cmpOpen, setCmpOpen] = useState(false);
  const toggleCmp = (sym) => setCmp(c => c.includes(sym) ? c.filter(s => s !== sym) : (c.length >= 4 ? c : [...c, sym]));
  const TABS = [["all", "كل الأسهم"], ["halal", "متوافقة شرعاً"], ["buy", "توصية شراء"], ["fav", "⭐ المفضلة"], ["explosion", "⚡ انفجار يومي"], ["pairs", "🔗 الأزواج"]];
  const isExpl = tab === "explosion";
  const isPairs = tab === "pairs";
  const dtRes = useGet(isExpl ? "/api/screener/daytrade?limit=40" : null);
  let exl = ((dtRes && dtRes.results) || []).filter(r => !q || (r.symbol || "").toUpperCase().includes(q.toUpperCase()));
  const exlKey = ["explosion_score", "rvol", "momentum_pct", "change_pct", "gap_pct", "price", "vol_expansion"].includes(sortK) ? sortK : "explosion_score";
  exl = [...exl].sort((a, b) => { const d = (b[exlKey] || 0) - (a[exlKey] || 0); return sortDir === "asc" ? -d : d; });
  const prRes = useGet(isPairs ? "/api/screener/pairs?limit=30" : null);
  let prs = ((prRes && prRes.results) || []).filter(r => !q || (r.pair || "").toUpperCase().includes(q.toUpperCase()));
  const prKey = ["zscore", "pvalue", "half_life_bars", "hedge_ratio"].includes(sortK) ? sortK : "zscore";
  prs = [...prs].sort((a, b) => { const va = prKey === "zscore" ? Math.abs(a[prKey] || 0) : (a[prKey] || 0); const vb = prKey === "zscore" ? Math.abs(b[prKey] || 0) : (b[prKey] || 0); return sortDir === "asc" ? va - vb : vb - va; });
  const cmpRows = cmp.map(s => rows.find(r => r.symbol === s)).filter(Boolean);
  const CMP_METRICS = [
    ["الدرجة الشاملة", r => Math.round(r.composite_score || 0), "hi"],
    ["التوصية", r => verdictOf(r.composite_score || 0)[0], "vd"],
    ["السعر", r => money(r.price)],
    ["التغيّر اليومي", r => r.change_pct == null ? "—" : pct(r.change_pct, 2), "chg"],
    ["القيمة السوقية", r => fmtCap(r.market_cap)],
    ["السيولة ($م)", r => r.adv_dollar_m != null ? "$" + num(r.adv_dollar_m, 0) : "—"],
    ["تقني /30", r => num(r.score_tech, 0), "hi"],
    ["أساسي /25", r => num(r.score_fund, 0), "hi"],
    ["ذكاء AI /15", r => num(r.score_ai, 0), "hi"],
    ["مشاعر /20", r => num(r.score_sentiment, 0), "hi"],
    ["حلال /12", r => num(r.score_halal, 0), "hi"],
    ["زخم 12-1", r => num(r.score_mom121, 1), "hi"],
    ["ATR % (مخاطرة)", r => num(r.atr_pct, 1) + "%", "lo"],
    ["محلّلون", r => r.analyst_rating || "—"],
    ["نمو الإيرادات", r => r.revenue_growth_yoy != null ? pct(r.revenue_growth_yoy, 0) : "—"],
    ["عائد:مخاطرة", r => (r.trade_plan && r.trade_plan.rr_ratio != null) ? num(r.trade_plan.rr_ratio, 1) : "—", "hi"],
    ["حلال", r => r.is_halal ? "✓" : "—"],
  ];
  const sectors = Array.from(new Set(rows.map(r => r.sector).filter(Boolean))).sort();

  const applyCfg = (c) => { if (c.tab != null) setTab(c.tab); if (c.sortK) setSortK(c.sortK); if (c.secF != null) setSecF(c.secF); if (c.pxMin != null) setPxMin(c.pxMin); if (c.scMin != null) setScMin(c.scMin); };
  const saveCurrent = () => { const name = (prompt("اسم الماسح المحفوظ:") || "").trim(); if (!name) return; const s = saved.filter(x => x.name !== name).concat([{ name, cfg: { tab, sortK, secF, pxMin, scMin } }]); setSaved(s); localStorage.setItem("mz_screens", JSON.stringify(s)); };
  const delSaved = (name) => { const s = saved.filter(x => x.name !== name); setSaved(s); localStorage.setItem("mz_screens", JSON.stringify(s)); };
  const resetF = () => { setTab("all"); setSecF("all"); setPxMin(0); setScMin(0); setQ(""); };

  let shown = rows.filter(r => (tab === "all" || (tab === "halal" && r.is_halal) || (tab === "buy" && (r.composite_score || 0) >= 55) || (tab === "fav" && r.in_watchlist))
    && (secF === "all" || r.sector === secF)
    && (r.price || 0) >= pxMin
    && (r.composite_score || 0) >= scMin
    && (!q || (r.symbol || "").toUpperCase().includes(q.toUpperCase()) || (r.company || "").toUpperCase().includes(q.toUpperCase())));
  shown = [...shown].sort((a, b) => { const d = (b[sortK] || 0) - (a[sortK] || 0); return sortDir === "asc" ? -d : d; });
  const sortByCol = (k) => { if (sortK === k) setSortDir(d => d === "asc" ? "desc" : "asc"); else { setSortK(k); setSortDir("desc"); } };
  const arrow = (k) => sortK === k ? (sortDir === "asc" ? " ▲" : " ▼") : "";
  const activeF = (secF !== "all" ? 1 : 0) + (pxMin > 0 ? 1 : 0) + (scMin > 0 ? 1 : 0) + (tab !== "all" ? 1 : 0);
  const cnt = (cfg) => rows.filter(r => (cfg.tab === "all" || (cfg.tab === "halal" && r.is_halal) || (cfg.tab === "buy" && (r.composite_score || 0) >= 55)) && (cfg.secF === "all" || r.sector === cfg.secF) && (r.price || 0) >= (cfg.pxMin || 0) && (r.composite_score || 0) >= (cfg.scMin || 0)).length;

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
  const fmtCap = (v) => v == null ? "—" : v >= 1e12 ? "$" + (v / 1e12).toFixed(2) + "T" : v >= 1e9 ? "$" + (v / 1e9).toFixed(1) + "B" : v >= 1e6 ? "$" + (v / 1e6).toFixed(0) + "M" : "$" + Math.round(v);
  const WHYF = [["score_tech", "تقني", 30], ["score_fund", "أساسي", 25], ["score_ai", "ذكاء", 15], ["score_sentiment", "مشاعر", 8], ["score_mom121", "زخم", 12], ["score_halal", "حلال", 10]];
  const whyOf = (r) => { let bl = "—", bf = -1; for (const [k, l, mx] of WHYF) { const f = (r[k] || 0) / mx; if (f > bf) { bf = f; bl = l; } } return bl; };

  return (
    <div className="mz-view">
      <div className="mz-ana-top">
        <div className="mz-tabs">{TABS.map(([k, l]) => <button key={k} className={"mz-tab" + (tab === k ? " on" : "")} onClick={() => setTab(k)}>{l}</button>)}</div>
        <div className="mz-tbl-actions">
          <button className="mz-rg" onClick={() => setTick(t => t + 1)} title="إعادة جلب أحدث نتائج المسح">↻ تحديث</button>
          {lastUp && <span className="mz-dim3">آخر تحديث {lastUp.toLocaleTimeString("ar-EG", { hour: "2-digit", minute: "2-digit" })}</span>}
          <span className="mz-dim3">النتائج <b style={{ color: "var(--text-primary)" }}>{isExpl ? exl.length : shown.length}</b></span>
        </div>
      </div>
      {!isExpl && <div className="mz-filters">
        <label className="mz-fl">القطاع<select className="mz-sel" value={secF} onChange={e => setSecF(e.target.value)}><option value="all">كل القطاعات</option>{sectors.map(s => <option key={s} value={s}>{s}</option>)}</select></label>
        <label className="mz-fl">السعر ≥<select className="mz-sel" value={pxMin} onChange={e => setPxMin(+e.target.value)}>{[0, 5, 10, 50, 100, 200].map(v => <option key={v} value={v}>{v ? "$" + v : "الكل"}</option>)}</select></label>
        <label className="mz-fl">الدرجة ≥<select className="mz-sel" value={scMin} onChange={e => setScMin(+e.target.value)}>{[0, 40, 55, 70, 80].map(v => <option key={v} value={v}>{v || "الكل"}</option>)}</select></label>
        <label className="mz-fl">فرز<select className="mz-sel" value={sortK} onChange={e => setSortK(e.target.value)}><option value="composite_score">الدرجة</option><option value="score_mom121">الزخم</option><option value="score_fund">الأساسي</option><option value="score_tech">التقني</option><option value="price">السعر</option></select></label>
        {activeF > 0 && <button className="mz-rg" onClick={resetF}>مسح الفلاتر ({activeF})</button>}
        <button className="mz-rg on" onClick={saveCurrent}>★ حفظ الماسح</button>
      </div>}
      <div className={"mz-ana-wrap" + ((isExpl || isPairs) ? " mz-wrap-full" : "")}>
        <div className="mz-ana-main">
          {isPairs ? (
          <Panel title={<input className="mz-inp" style={{ width: 220 }} placeholder="بحث زوج…" value={q} onChange={e => setQ(e.target.value)} />} right={<span className="mz-dim3">🔗 أزواج متكاملة (تكامل مشترك داخل القطاع) · بحثيّ · طويل فقط</span>}>
            {!prRes ? <div className="mz-empty">…يقرأ الأزواج المُخزّنة</div> : (prRes.status === "scanning") ? <div className="mz-empty">…يمسح الأزواج في الخلفية (قد يستغرق ~دقيقتين — عُد بعد قليل)</div> : prs.length ? (
              <table className="mz-tbl mz-tbl-wide mz-tbl-pro">
                <thead><tr>
                  <th className="tl">الزوج (Y / X)</th>
                  <th className="tl">القطاع</th>
                  <th className="mz-sh" onClick={() => sortByCol("zscore")}>|z| الانحراف{arrow("zscore")}</th>
                  <th>الإشارة</th>
                  <th className="mz-sh" onClick={() => sortByCol("pvalue")}>p-value{arrow("pvalue")}</th>
                  <th className="mz-sh" onClick={() => sortByCol("half_life_bars")}>نصف العمر{arrow("half_life_bars")}</th>
                  <th className="mz-sh" onClick={() => sortByCol("hedge_ratio")}>نسبة التحوّط{arrow("hedge_ratio")}</th>
                  <th>OOS</th>
                </tr></thead>
                <tbody>{prs.slice(0, 40).map((r) => { const z = r.zscore || 0; const az = Math.abs(z); const hot = az >= 2;
                  const sig = az < 1 ? ["محايد", MUT] : z >= 2 ? ["اشترِ X", POS] : z <= -2 ? ["اشترِ Y", POS] : ["راقب", WARN];
                  return (<tr key={r.pair}>
                    <td className="tl mz-fn">{r.y_symbol} / {r.x_symbol}</td>
                    <td className="tl mz-dim2">{r.sector || "—"}</td>
                    <td><span className="mz-score" style={{ color: hot ? POS : az >= 1 ? WARN : MUT }}>{num(z, 2)}</span></td>
                    <td><span className="mz-vd" style={{ color: sig[1], borderColor: sig[1] }}>{sig[0]}</span></td>
                    <td className="mz-dim2">{num(r.pvalue, 3)}</td>
                    <td className="mz-dim2">{num(r.half_life_bars, 0)} شمعة</td>
                    <td className="mz-dim2">{num(r.hedge_ratio, 2)}</td>
                    <td>{r.oos_pvalue != null ? (r.oos_pvalue <= 0.05 ? "✓" : "—") : "·"}</td>
                  </tr>); })}</tbody>
              </table>
            ) : (() => { const dg = prRes.diagnostics || {}; return <div className="mz-empty" style={{ lineHeight: 1.7 }}>
              لا أزواج متكاملة تجتاز البوّابات الآن.{dg.statsmodels_available === false ? " ⚠ محرّك الإحصاء (statsmodels) غير متاح." : ""}
              {dg.candidate_pairs != null && <div className="mz-dim2" style={{ marginTop: 6, fontSize: 11 }}>
                فُحِص {Number(dg.candidate_pairs).toLocaleString("en")} مرشّح · اجتاز الارتباط {dg.passed_correlation} · رُفض خارج العيّنة {dg.rejected_oos} (حارس التكامل الزائف) · أفضل p = {num(dg.best_pvalue, 3)}
              </div>}
              <div className="mz-dim2" style={{ marginTop: 4, fontSize: 11 }}>النتيجة صارمة بحقّ — الماسح لا يُظهر إلا أزواجاً تصمد خارج العيّنة.</div>
            </div>; })()}
          </Panel>
          ) : isExpl ? (
          <Panel title={<input className="mz-inp" style={{ width: 220 }} placeholder="بحث رمز…" value={q} onChange={e => setQ(e.target.value)} />} right={<span className="mz-dim3">⚡ ماسح انفجار لحظي · بحثيّ فقط · مستقلّ عن الحلال</span>}>
            {!dtRes ? <div className="mz-empty">…يمسح الانفجارات</div> : exl.length ? (
              <table className="mz-tbl mz-tbl-wide mz-tbl-pro">
                <thead><tr>
                  <th></th>
                  <th className="tl mz-sh" onClick={() => sortByCol("symbol")}>الرمز</th>
                  <th className="mz-sh" onClick={() => sortByCol("price")}>السعر{arrow("price")}</th>
                  <th className="mz-sh" onClick={() => sortByCol("change_pct")}>التغيّر{arrow("change_pct")}</th>
                  <th className="mz-sh" onClick={() => sortByCol("explosion_score")}>💥 الانفجار{arrow("explosion_score")}</th>
                  <th className="mz-sh" onClick={() => sortByCol("rvol")}>حجم نسبي{arrow("rvol")}</th>
                  <th className="mz-sh" onClick={() => sortByCol("momentum_pct")}>زخم{arrow("momentum_pct")}</th>
                  <th className="mz-sh" onClick={() => sortByCol("gap_pct")}>فجوة{arrow("gap_pct")}</th>
                  <th>حلال</th>
                </tr></thead>
                <tbody>{exl.slice(0, 60).map((r) => { const isE = exp === r.symbol; const c = r.components || {}; const cp = r.change_pct;
                  return (<React.Fragment key={r.symbol}>
                    <tr className="mz-prow" onClick={() => setExp(isE ? null : r.symbol)}>
                      <td className="mz-chev">{isE ? "▾" : "▸"}</td>
                      <td className="tl mz-fn">{r.symbol}</td>
                      <td>{money(r.price)}</td>
                      <td style={{ color: cp == null ? MUT : cp >= 0 ? POS : NEG }}>{cp == null ? "—" : pct(cp, 2)}</td>
                      <td><span className="mz-score" style={{ color: (r.explosion_score || 0) >= 70 ? POS : (r.explosion_score || 0) >= 50 ? WARN : MUT }}>{Math.round(r.explosion_score || 0)}</span></td>
                      <td>{num(r.rvol, 1)}×</td>
                      <td style={{ color: (r.momentum_pct || 0) >= 0 ? POS : NEG }}>{pct(r.momentum_pct, 1)}</td>
                      <td>{pct(r.gap_pct, 1)}</td>
                      <td>{r.halal_verdict === "halal" ? "✓" : r.halal_verdict === "uncertain" ? "؟" : "—"}</td>
                    </tr>
                    {isE && <tr className="mz-exp"><td colSpan="9"><div className="mz-exp-in">
                      <div className="mz-exp-plan">
                        <div className="mz-exp-h">مكوّنات درجة الانفجار</div>
                        <div className="mz-exp-grid">
                          <div><span>حجم نسبي</span><b>{num(c.rvol, 0)}</b></div>
                          <div><span>زخم</span><b>{num(c.momentum, 0)}</b></div>
                          <div><span>تذبذب</span><b>{num(c.volatility, 0)}</b></div>
                          <div><span>فجوة</span><b>{num(c.gap, 0)}</b></div>
                          <div><span>توسّع الحجم</span><b>{num(r.vol_expansion, 1)}×</b></div>
                        </div>
                      </div>
                      <div className="mz-exp-side">
                        <div className="mz-dim2" style={{ fontSize: 11, lineHeight: 1.5 }}>ماسح تقنيّ للانفجار اللحظي (بحثيّ فقط) — ليس توصية ولا دخول دفتر ورقي.</div>
                        <div className="mz-tp-btns" style={{ marginTop: 6 }}>
                          <button className="mz-btn" onClick={e => { e.stopPropagation(); goAnalyze(r.symbol, ""); }}>فتح التفاصيل</button>
                          <button className="mz-btn gold" onClick={e => { e.stopPropagation(); goAnalyze(r.symbol, "forecast"); }}>تحليل متقدّم</button>
                        </div>
                      </div>
                    </div></td></tr>}
                  </React.Fragment>); })}</tbody>
              </table>
            ) : <div className="mz-empty">لا انفجارات مؤكّدة الآن</div>}
          </Panel>
          ) : (<React.Fragment>
          <Panel title={<input className="mz-inp" style={{ width: 220 }} placeholder="بحث رمز أو اسم…" value={q} onChange={e => setQ(e.target.value)} />} right={<div className="mz-tbl-actions">{cmp.length >= 2 && <button className="mz-btn gold" style={{ maxWidth: 130 }} onClick={() => setCmpOpen(true)}>⚖ قارن ({cmp.length})</button>}{cmp.length > 0 && <button className="mz-rg" onClick={() => setCmp([])}>مسح المقارنة</button>}<span className="mz-dim3">☑ للمقارنة · الصفّ للتوسيع</span></div>}>
            {regimeTxt && <div className="mz-regime-bar">🧭 النظام الآن: <b>{regimeTxt}</b>{Number.isFinite(bookMult) ? " · مضاعف الدفتر " + num(bookMult, 2) + "×" : ""} — الترتيب يتكيّف تلقائياً مع النظام.</div>}
            {rows.length ? (
              <table className="mz-tbl mz-tbl-wide mz-tbl-pro">
                <thead><tr>
                  <th></th>
                  <th className="mz-cmp-h" title="اختر 2–4 للمقارنة">⚖</th>
                  <th className="tl mz-sh" onClick={() => sortByCol("symbol")}>الرمز</th>
                  <th className="tl">القطاع</th>
                  <th className="mz-sh" onClick={() => sortByCol("price")}>السعر{arrow("price")}</th>
                  <th className="mz-sh" onClick={() => sortByCol("change_pct")}>التغيّر{arrow("change_pct")}</th>
                  <th>التوصية</th>
                  <th className="mz-sh" onClick={() => sortByCol("composite_score")}>الدرجة{arrow("composite_score")}</th>
                  <th className="mz-sh" onClick={() => sortByCol("market_cap")}>القيمة{arrow("market_cap")}</th>
                  <th className="mz-sh" onClick={() => sortByCol("adv_dollar_m")}>السيولة{arrow("adv_dollar_m")}</th>
                  <th className="mz-sh" onClick={() => sortByCol("score_mom121")}>زخم{arrow("score_mom121")}</th>
                  <th>لماذا؟</th>
                  <th>⭐</th>
                </tr></thead>
                <tbody>{shown.slice(0, 80).map((r) => { const [vd, vc] = verdictOf(r.composite_score || 0); const isExp = exp === r.symbol; const cp = r.change_pct; const tp = r.trade_plan || {};
                  return (<React.Fragment key={r.symbol}>
                    <tr className={"mz-prow" + (cmp.includes(r.symbol) ? " mz-picked" : "")} onClick={() => setExp(isExp ? null : r.symbol)}>
                      <td className="mz-chev">{isExp ? "▾" : "▸"}</td>
                      <td className="mz-cmp-c" onClick={e => { e.stopPropagation(); toggleCmp(r.symbol); }}>{cmp.includes(r.symbol) ? "☑" : "☐"}</td>
                      <td className="tl mz-fn">{r.symbol}<div className="mz-dim2">{(r.company || "").slice(0, 20)}</div></td>
                      <td className="tl mz-dim2">{r.sector || "—"}</td>
                      <td>{money(r.price)}</td>
                      <td style={{ color: cp == null ? MUT : cp >= 0 ? POS : NEG }}>{cp == null ? "—" : pct(cp, 2)}</td>
                      <td><span className="mz-vd" style={{ color: vc, borderColor: vc }}>{vd}</span></td>
                      <td className="mz-score-cell" onMouseEnter={() => setPop(r.symbol)} onMouseLeave={() => setPop(null)}>
                        <span className="mz-score" style={{ color: (r.composite_score || 0) >= 55 ? POS : (r.composite_score || 0) >= 38 ? WARN : NEG }}>{Math.round(r.composite_score || 0)}</span>
                        {pop === r.symbol && <div className="mz-pop" onClick={e => e.stopPropagation()}>
                          <div className="mz-pop-t">تفصيل الدرجة — {r.symbol}</div>
                          {[["تقني", r.score_tech, 30], ["أساسي", r.score_fund, 25], ["ذكاء AI", r.score_ai, 15], ["مشاعر", r.score_sentiment, 20], ["حلال", r.score_halal, 12], ["زخم 12-1", r.score_mom121, 12]].map(([l, v, mx], j) => (
                            <div className="mz-pop-r" key={j}><span>{l}</span><div className="mz-pop-bar"><div style={{ width: Math.min(100, (v || 0) / mx * 100) + "%" }} /></div><b>{num(v, 0)}</b></div>))}
                        </div>}
                      </td>
                      <td className="mz-dim2">{fmtCap(r.market_cap)}</td>
                      <td className="mz-dim2">{r.adv_dollar_m != null ? "$" + num(r.adv_dollar_m, 0) + "م" : "—"}</td>
                      <td>{num(r.score_mom121, 1)}</td>
                      <td><span className="mz-why">{whyOf(r)}</span></td>
                      <td className="mz-star" onClick={e => e.stopPropagation()}>{r.in_watchlist ? "★" : "☆"}</td>
                    </tr>
                    {isExp && <tr className="mz-exp"><td colSpan="13"><div className="mz-exp-in">
                      <div className="mz-exp-plan">
                        <div className="mz-exp-h">خطة الصفقة</div>
                        <div className="mz-exp-grid">
                          <div><span>دخول تقديري</span><b>{money(r.price)}</b></div>
                          <div><span>وقف</span><b style={{ color: NEG }}>{money(tp.stop_price)}</b></div>
                          <div><span>هدف</span><b style={{ color: POS }}>{money(tp.tp_price)}</b></div>
                          <div><span>عائد:مخاطرة</span><b>{tp.rr_ratio != null ? num(tp.rr_ratio, 1) : "—"}</b></div>
                          <div><span>مدّة الاحتفاظ</span><b>{tp.hold_days != null ? tp.hold_days + " يوم" : "—"}</b></div>
                          <div><span>وقف كارثي</span><b>{tp.stop_pct != null ? "-" + num(tp.stop_pct, 0) + "%" : "—"}</b></div>
                        </div>
                      </div>
                      <div className="mz-exp-side">
                        {r.analyst_rating && <div className="mz-exp-chip">محلّلون: <b>{r.analyst_rating}</b>{r.analyst_upside != null ? " · صعود " + pct(r.analyst_upside, 0) : ""}</div>}
                        {r.insider_flag && <div className="mz-exp-chip">مطّلعون: <b>{r.insider_flag}</b></div>}
                        {r.revenue_growth_yoy != null && <div className="mz-exp-chip">نمو الإيرادات {pct(r.revenue_growth_yoy, 0)}</div>}
                        {r.strategy_reason && <div className="mz-dim2" style={{ fontSize: 11, lineHeight: 1.5 }}>{r.strategy_reason}</div>}
                        <div className="mz-tp-btns" style={{ marginTop: 6 }}>
                          <button className="mz-btn" onClick={e => { e.stopPropagation(); goAnalyze(r.symbol, ""); }}>فتح التفاصيل</button>
                          <button className="mz-btn gold" onClick={e => { e.stopPropagation(); goAnalyze(r.symbol, "forecast"); }}>تحليل متقدّم</button>
                          <button className="mz-btn" onClick={e => { e.stopPropagation(); goAnalyze(r.symbol, "trade"); }}>صفقة ورقيّة</button>
                        </div>
                      </div>
                    </div></td></tr>}
                  </React.Fragment>); })}</tbody>
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
                <div className="mz-sm" key={i} style={{ background: secCol(s.avg), cursor: "pointer" }} onClick={() => setSecF(s.sec)}><div className="mz-sm-n">{s.sec}</div><div className="mz-sm-v">{Math.round(s.avg)}</div></div>))}</div>
            </Panel>
          </div>
          <Panel title="خريطة الكون — المساحة = القيمة السوقيّة · اللون = الدرجة">
            <Treemap items={shown.filter(r => r.market_cap > 0).slice(0, 40).map(r => ({ symbol: r.symbol, value: r.market_cap, score: r.composite_score || 0 }))} />
            <div className="mz-note ql-dim">كل مستطيل سهم — مساحته ∝ قيمته السوقيّة، لونه = درجته المركّبة (أخضر عالٍ · أصفر متوسّط · أحمر منخفض). انقر أيّ مستطيل للتحليل.</div>
          </Panel>
          <Panel title="الماسحات المحفوظة">
            <table className="mz-tbl">
              <thead><tr><th className="tl">الاسم</th><th className="tl">الوصف</th><th>النتائج</th><th></th></tr></thead>
              <tbody>
                {SCR_PRESETS.map((p, i) => (
                  <tr key={"p" + i}><td className="tl mz-fn">{p.name}</td><td className="tl mz-dim2">{p.desc}</td><td>{cnt(p.cfg)}</td>
                    <td><button className="mz-rg" onClick={() => applyCfg(p.cfg)}>▷ تطبيق</button></td></tr>))}
                {saved.map((p, i) => (
                  <tr key={"s" + i}><td className="tl mz-fn">★ {p.name}</td><td className="tl mz-dim2">ماسح محفوظ</td><td>{cnt(p.cfg)}</td>
                    <td><button className="mz-rg" onClick={() => applyCfg(p.cfg)}>▷ تطبيق</button> <button className="mz-rg" onClick={() => delSaved(p.name)}>✕</button></td></tr>))}
              </tbody>
            </table>
          </Panel>
          </React.Fragment>)}
        </div>
        {!isExpl && !isPairs && <aside className="mz-ana-rail">
          <Panel title="الفلاتر النشطة">
            <div className="mz-tags">
              <span className="mz-tag2">{tab === "all" ? "كل الأسهم" : tab === "halal" ? "متوافقة شرعاً" : "توصية شراء"}</span>
              {secF !== "all" && <span className="mz-tag2 on" onClick={() => setSecF("all")}>{secF} ✕</span>}
              {pxMin > 0 && <span className="mz-tag2 on" onClick={() => setPxMin(0)}>السعر ≥ ${pxMin} ✕</span>}
              {scMin > 0 && <span className="mz-tag2 on" onClick={() => setScMin(0)}>الدرجة ≥ {scMin} ✕</span>}
              {activeF === 0 && <span className="mz-dim2">لا فلاتر — الكون كامل</span>}
            </div>
          </Panel>
          <Panel title="تصنيفات سريعة">
            <div className="mz-tags">{[["composite_score", "الأعلى درجة"], ["score_mom121", "الأعلى زخم"], ["score_fund", "الأقوى أساساً"], ["score_tech", "الأقوى تقنياً"]].map(([k, l]) => (
              <button key={k} className={"mz-tag2" + (sortK === k ? " on" : "")} onClick={() => setSortK(k)}>{l}</button>))}</div>
          </Panel>
          <Panel title="أداء النتائج" right={<div className="mz-ranges">{[["1M", "1mo"], ["3M", "3mo"], ["6M", "6mo"], ["1Y", "1y"]].map(([l, r]) => <button key={r} className={"mz-rg" + (perfRange === r ? " on" : "")} onClick={() => setPerfRange(r)}>{l}</button>)}</div>}>
            {perf && perf.series && perf.series.length > 1 ? (<div>
              <div className="mz-perf-head">
                <span className="mz-perf-v" style={{ color: (perf.total_return_pct || 0) >= 0 ? POS : NEG }}>{pct(perf.total_return_pct, 2)}</span>
                {perf.spy_return_pct != null && <span className="mz-perf-bench">مقابل SPY <b style={{ color: (perf.spy_return_pct || 0) >= 0 ? POS : NEG }}>{pct(perf.spy_return_pct, 2)}</b></span>}
              </div>
              <LineArea data={perf.series.map(p => p.value - 100)} color={(perf.total_return_pct || 0) >= 0 ? POS : NEG} />
              <div className="mz-note">سلّة أعلى {perf.n} أسهم بالتساوي · تاريخي — تصوّر لجودة الاختيار لا سجلّ تداول.</div>
              {(perf.symbols || []).length ? (<div className="mz-basket">
                <div className="mz-basket-h">مكوّنات السلّة ({(perf.symbols || []).length} سهم)</div>
                <div className="mz-basket-tags">{(perf.symbols || []).map(s => <span key={s} className="mz-basket-t" onClick={() => goAnalyze(s, "")} title="افتح التحليل">{s}</span>)}</div>
              </div>) : null}
            </div>) : <div className="mz-empty">{perf && perf.status && perf.status !== "ok" ? "لا نتائج بعد للحساب" : "…يحسب أداء السلّة"}</div>}
          </Panel>
          <Panel title="إحصائيات الماسح">
            <div className="mz-dl">
              {[["إجمالي النتائج", rows.length], ["الظاهرة بعد الفلترة", shown.length], ["متوسّط الدرجة", num(avg, 0)], ["متوسّط الزخم", num(avgMom, 1)],
              ["أفضل قطاع", secAvg[0] ? secAvg[0].sec : "—"], ["متوافقة شرعاً", rows.filter(r => r.is_halal).length]].map(([l, v], i) => (
                <div className="mz-dl-r" key={i}><span className="mz-dl-l">{l}</span><span className="mz-dl-v">{v}</span></div>))}
            </div>
            <button className="mz-btn gold" style={{ marginTop: 10 }} onClick={exportCsv}>⬇ تصدير النتائج (CSV)</button>
          </Panel>
        </aside>}
      </div>
      {cmpOpen && cmpRows.length >= 2 && <div className="mz-cmp-ov" onClick={() => setCmpOpen(false)}>
        <div className="mz-cmp-box" onClick={e => e.stopPropagation()}>
          <div className="mz-cmp-top"><b>⚖ مقارنة {cmpRows.length} أسهم</b><button className="mz-rg" onClick={() => setCmpOpen(false)}>✕ إغلاق</button></div>
          <table className="mz-tbl mz-cmp-tbl">
            <thead><tr><th className="tl">المقياس</th>{cmpRows.map(r => <th key={r.symbol}>{r.symbol}</th>)}</tr></thead>
            <tbody>{CMP_METRICS.map(([label, fn, kind], i) => (
              <tr key={i}><td className="tl mz-dim2">{label}</td>
              {cmpRows.map(r => {
                if (kind === "vd") { const [vd, vc] = verdictOf(r.composite_score || 0); return <td key={r.symbol}><span className="mz-vd" style={{ color: vc, borderColor: vc }}>{vd}</span></td>; }
                if (kind === "chg") { const c = r.change_pct; return <td key={r.symbol} style={{ color: c == null ? MUT : c >= 0 ? POS : NEG }}>{fn(r)}</td>; }
                return <td key={r.symbol}>{fn(r)}</td>;
              })}</tr>))}</tbody>
          </table>
          <div className="mz-note" style={{ marginTop: 8 }}>بيانات حيّة من الماسح — للمقارنة والبحث، لا توصية.</div>
        </div>
      </div>}
    </div>
  );
}

function FactorsView() {
  const fic = useGet("/api/factor-ic-multi");
  const ricd = useGet("/api/regime-ic?horizon_days=10");
  const sq = useGet("/api/selection-quality");
  const cc = useGet("/api/candidate-composites");
  const ccands = cc && cc.candidates ? Object.entries(cc.candidates) : [];
  const attr = fic && fic.attribution && fic.attribution.factors;
  const gate = (sq && sq.gate) || {}; const rec = gate.recommendation;
  const facMap = { mom_12_1: "زخم 12-1", rs: "قوّة نسبيّة (RS)", above_ema20: "شرط فوق EMA20", rsi: "RSI (14)", atr_pct: "التقلّب (ATR)", dist_ema20_pct: "الامتداد عن EMA20",
    hi52_prox: "قرب قمّة 52 أسبوعاً", resid_mom: "زخم البواقي", sharpe_mom: "زخم معدّل بالمخاطر", mom_consistency: "ثبات الزخم", downside_vol: "التقلّب الهابط", beta: "بيتا (مقابل SPY)", maxdd_6m: "أقصى تراجع 6ش", range_pos_20: "الموقع بالنطاق 20ي", rev_5d: "انعكاس 5 أيام", pead: "انجراف الأرباح (PEAD)" };
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
      <Panel title={"سباق المركّبات المرشّحة — ظلّي/بحثيّ" + (cc && cc.labelled_dates ? " · " + cc.labelled_dates + " يوم" : "")} right={<span className="mz-dim3">فائض السلّة العليا (المهمّ للطويل) + IC · لا يمسّ التسجيل الحيّ</span>}>
        {ccands.length ? (<div>
          <table className="mz-tbl mz-tbl-wide">
            <thead><tr><th className="tl">المركّب المرشّح</th><th>فائض القمّة 5ي</th><th>فائض 10ي</th><th>فوز٪</th><th>IC 5ي</th><th>IC 20ي</th></tr></thead>
            <tbody>{ccands.map(([k, v]) => { const base = k === "mom"; const e5 = (v.h["5"] || {}).top_excess, e10 = (v.h["10"] || {}).top_excess; return (
              <tr key={k} style={base ? { background: "var(--accent-dim)" } : null}>
                <td className="tl mz-fn">{v.label}{base && <span style={{ color: ACC }}> ★</span>}</td>
                <td style={{ color: e5 == null ? MUT : e5 >= 0 ? POS : NEG, fontWeight: 700 }}>{e5 == null ? "—" : (e5 >= 0 ? "+" : "") + num(e5, 2) + "%"}</td>
                <td style={{ color: e10 == null ? MUT : e10 >= 0 ? POS : NEG }}>{e10 == null ? "—" : (e10 >= 0 ? "+" : "") + num(e10, 2) + "%"}</td>
                <td className="mz-dim2">{(v.h["10"] || {}).top_win != null ? (v.h["10"]).top_win + "%" : "—"}</td>
                <td style={{ color: icCol((v.h["5"] || {}).mean_ic) }}>{num((v.h["5"] || {}).mean_ic, 3)}</td>
                <td style={{ color: icCol((v.h["20"] || {}).mean_ic) }}>{num((v.h["20"] || {}).mean_ic, 3)}</td></tr>); })}</tbody>
          </table>
          <div className="mz-note ql-dim">⚠ درس مقاس: النظام طويل فقط — <b>يشتري القمّة</b>، فالمقياس الصحيح هو <b>فائض السلّة العليا</b> لا IP. المرشّح الأفضل بالـIC (زخم−EMA20) هو الأسوأ في القمّة. حاليّاً الزخم الخام و«زخم−امتداد» أفضل السلّة العليا — <b>لكن لا شيء دالّ إحصائيّاً بعد</b> (t&lt;1.3، n={cc && cc.labelled_dates}). لا ترقية للتسجيل الحيّ إلا بقرارك بعد ثبات الدلالة — قياس أوّلاً دائماً.</div>
        </div>) : <div className="mz-empty">…يحسب سباق المركّبات الظلّي</div>}
      </Panel>
    </div>
  );
}

// Real IBKR-paper executed-fills history + an in-app "sync now" (runs in-process, so it reuses
// the app's warm gateway connection instead of colliding on the client-id like an SSH run would).
function IbkrExecPanel() {
  const [data, setData] = useState(null);
  const [syncing, setSyncing] = useState(false);
  const [msg, setMsg] = useState("");
  useEffect(() => { fetch("/api/ibkr-executions").then(r => r.json()).then(setData).catch(() => {}); }, []);
  const sync = async () => {
    setSyncing(true); setMsg("");
    try {
      const r = await fetch("/api/ibkr-exec-sync", { method: "POST" }).then(x => x.json());
      if (r.summary) setData(r.summary);
      const s = r.sync || {};
      setMsg(s.status === "ok" ? ("تمّت المزامنة — تنفيذات جديدة: " + (s.inserted || 0) + " (من " + (s.seen || 0) + ")")
        : s.status === "gateway_offline" ? "البوّابة غير متّصلة الآن — أعِد المحاولة بعد قليل."
        : "تعذّرت المزامنة: " + (s.message || s.status || "?"));
    } catch (e) { setMsg("خطأ في الشبكة."); }
    finally { setSyncing(false); }
  };
  const d = data || {};
  const empty = !d.count || d.status === "empty" || d.status === "error";
  return (
    <Panel title="تنفيذات IBKR الورقيّة الفعليّة" right={
      <button className="mz-btn gold" disabled={syncing} onClick={sync}>{syncing ? "…يزامن" : "مزامنة الآن"}</button>}>
      <div className="mz-note ql-dim">تُقرأ من البوّابة (قراءة فقط، لا أوامر). الربح المحقّق يظهر على صفقات الإغلاق فقط. حساب ورقيّ — ومصدر المراكز غير مؤكّد (قد يشمل محفظة IBKR التجريبيّة).</div>
      {msg ? <div className="mz-note" style={{ color: WARN, marginTop: 6 }}>{msg}</div> : null}
      {empty ? <div className="mz-empty">{d.message || "لا تنفيذات مخزّنة بعد — تتراكم بعد إغلاق أوّل يوم تداول (~4:46م نيويورك). اضغط «مزامنة الآن» لجلبها فور توفّرها."}</div> : (
        <>
          <div className="mz-cards" style={{ marginTop: 10 }}>
            <div className="mz-card"><div className="mz-c-l">تنفيذات</div><div className="mz-c-v">{d.count}</div><div className="mz-c-s">{d.buys}ش · {d.sells}ب</div></div>
            <div className="mz-card"><div className="mz-c-l">ربح محقّق</div><div className="mz-c-v" style={{ color: (d.total_realized_pnl || 0) >= 0 ? POS : NEG }}>{d.total_realized_pnl != null ? money(d.total_realized_pnl) : "—"}</div><div className="mz-c-s">{d.closed_trades_with_pnl} صفقة مُغلقة</div></div>
            <div className="mz-card"><div className="mz-c-l">نسبة الفوز</div><div className="mz-c-v">{d.realized_win_rate_pct != null ? d.realized_win_rate_pct + "%" : "—"}</div></div>
            <div className="mz-card"><div className="mz-c-l">متوسّط رابح/خاسر</div><div className="mz-c-v" style={{ fontSize: 15 }}><span style={{ color: POS }}>{d.avg_win != null ? money(d.avg_win) : "—"}</span> / <span style={{ color: NEG }}>{d.avg_loss != null ? money(d.avg_loss) : "—"}</span></div></div>
            <div className="mz-card"><div className="mz-c-l">العمولات</div><div className="mz-c-v">{d.total_commission != null ? money(d.total_commission) : "—"}</div></div>
          </div>
          {(d.recent_fills || []).length ? (
            <table className="mz-tbl mz-tbl-wide" style={{ marginTop: 10 }}><thead><tr><th className="tl">الرمز</th><th>الجانب</th><th>الكمّية</th><th>السعر</th><th>ربح محقّق</th><th>الوقت</th></tr></thead>
              <tbody>{d.recent_fills.slice(0, 12).map((f, i) => (<tr key={i}><td className="tl mz-fn">{f.symbol}</td>
                <td>{f.side === "BOT" ? "شراء" : f.side === "SLD" ? "بيع" : f.side}</td><td>{f.qty}</td><td>{money(f.price)}</td>
                <td style={{ color: (f.realized_pnl || 0) >= 0 ? POS : NEG }}>{f.realized_pnl != null ? money(f.realized_pnl) : "—"}</td>
                <td className="mz-dim2">{f.time ? String(f.time).slice(0, 16).replace("T", " ") : "—"}</td></tr>))}</tbody></table>
          ) : null}
        </>
      )}
    </Panel>
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
      <IbkrExecPanel />
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

const WEIGHT_LABELS = {
  COMPOSITE_MOM121_WEIGHT: "وزن الزخم 12-1 (الأقوى تاريخياً)",
  COMPOSITE_MOMENTUM_WEIGHT: "وزن القوّة النسبية RS",
  COMPOSITE_SENT_WEIGHT: "وزن المشاعر/التحليلات",
};

function SettingsView() {
  const gc = useGet("/api/gate-config");
  const [msg, setMsg] = useState("");
  const [fw, setFw] = useState(null);          // factor-weights state
  const [draft, setDraft] = useState(null);    // slider working copy
  const [wmsg, setWmsg] = useState("");
  const loadFw = () => fetch("/api/factor-weights").then(r => r.json()).then(s => { setFw(s); setDraft({ ...s.weights }); }).catch(() => {});
  useEffect(() => { loadFw(); }, []);
  const dirty = fw && draft && Object.keys(draft).some(k => draft[k] !== fw.weights[k]);

  const knobs = [
    ["GATE_MIN_T", "حسّاسية دلالة توصية البوّابة", "2.0"],
    ["WEEKLY_MIN_RS", "عتبة الدخول الأسبوعي", gc ? num(gc.min_rs, 1) : "-2"],
    ["VOL_TARGET", "تقلّب المحفظة المستهدف", "0.14"],
    ["META_MIN_OOS_AUC", "عتبة ثقة نموذج Meta", "0.53"],
  ];
  const resetGate = async () => {
    if (!window.confirm("إعادة عتبة الدخول إلى الافتراضي (−2)؟ ورقي فقط · قابل للعكس.")) return;
    try { const r = await fetch("/api/gate-config/reset", { method: "POST" }).then(x => x.json()); setMsg("تمّت الإعادة → MIN_RS " + r.now); } catch (e) { setMsg("تعذّر"); }
  };
  const applyWeights = async () => {
    if (!window.confirm("تطبيق أوزان العوامل الجديدة على تسجيل الماسح؟ يؤثّر في ترتيب الأسهم — ورقي فقط · قابل للعكس بالكامل.")) return;
    setWmsg("…يطبّق");
    try { const r = await fetch("/api/factor-weights/apply", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ weights: draft }) }).then(x => x.json());
      if (r.error) { setWmsg("خطأ: " + r.error); return; }
      setFw(r); setDraft({ ...r.weights }); setWmsg("تمّ التطبيق ✓ — يظهر الأثر في المسح التالي."); } catch (e) { setWmsg("تعذّر"); }
  };
  const resetWeights = async () => {
    if (!window.confirm("إعادة كل الأوزان إلى الافتراضي؟")) return;
    setWmsg("…يعيد");
    try { const r = await fetch("/api/factor-weights/reset", { method: "POST" }).then(x => x.json());
      const s = r.state || r; setFw(s); setDraft({ ...s.weights }); setWmsg("أُعيدت إلى الافتراضي ✓"); } catch (e) { setWmsg("تعذّر"); }
  };
  return (
    <div className="mz-view">
      <Panel title="عتبة الدخول (البوّابة)">
        <div className="mz-gate-cur">MIN_RS الحالية <b>{gc ? num(gc.min_rs, 1) : "…"}%</b> ({gc && gc.source === "approved" ? "معتمَدة بالدليل" : "افتراضية"})</div>
        <button className="mz-btn" style={{ maxWidth: 220 }} onClick={resetGate}>إعادة إلى الافتراضي (−2)</button>
        {msg && <div className="mz-note" style={{ color: POS }}>{msg}</div>}
      </Panel>

      <Panel title="أوزان العوامل — المركّب (ورقي · قابل للعكس)" right={fw && <span className="mz-dim3">{fw.source === "approved" ? "معدّلة" : "افتراضية"}</span>}>
        {fw && draft ? (<div>
          {Object.keys(fw.weights).map(k => { const b = (fw.bounds || {})[k] || { min: 0, max: 40 }; const def = (fw.defaults || {})[k];
            return (<div className="mz-wt" key={k}>
              <div className="mz-wt-h"><span className="mz-wt-l">{WEIGHT_LABELS[k] || k}</span><span className="mz-wt-v">{draft[k]}{def != null && draft[k] !== def && <span className="mz-dim3"> (افتراضي {def})</span>}</span></div>
              <input type="range" className="mz-range" min={b.min} max={b.max} step="1" value={draft[k]} onChange={e => setDraft(d => ({ ...d, [k]: +e.target.value }))} />
            </div>); })}
          <div className="mz-wt-act">
            <button className="mz-btn gold" style={{ maxWidth: 150, opacity: dirty ? 1 : 0.5 }} disabled={!dirty} onClick={applyWeights}>تطبيق</button>
            <button className="mz-btn" style={{ maxWidth: 150 }} onClick={resetWeights}>إعادة للافتراضي</button>
          </div>
          {wmsg && <div className="mz-note" style={{ color: wmsg.startsWith("خطأ") || wmsg === "تعذّر" ? NEG : POS }}>{wmsg}</div>}
          <div className="mz-note ql-dim">تُغيّر هذه الأوزان ترتيب المركّب في الماسح فقط (لا صفقات حقيقية). محفوظة على القرص وتُعاد بالكامل بزرّ الإعادة.</div>
        </div>) : <div className="mz-empty">…يحمّل الأوزان</div>}
      </Panel>

      <Panel title="مفاتيح الضبط (env · قياس فقط · قابلة للعكس)">
        <table className="mz-tbl mz-tbl-wide"><thead><tr><th className="tl">المفتاح</th><th className="tl">الوصف</th><th>القيمة</th></tr></thead>
          <tbody>{knobs.map((k, i) => (<tr key={i}><td className="tl mz-fn" style={{ fontFamily: "var(--font-mono)", fontSize: 11 }}>{k[0]}</td><td className="tl mz-dim2">{k[1]}</td><td>{k[2]}</td></tr>))}</tbody></table>
        <div className="mz-note ql-dim">التعديل عبر متغيّرات البيئة على الخادم — كلّها آمنة، ورقية، وقابلة للعكس. لا صفقات حقيقية.</div>
      </Panel>
    </div>
  );
}

function RaceChart({ rows }) {
  const pts = (rows || []).filter(r => r && (r.core_upl != null || r.sat_upl != null || r.exp_upl != null));
  if (pts.length < 2) return <div className="mz-empty">يبدأ رسم السباق بعد يومين من التسجيل (لقطة تلقائيّة كلّ يوم عند الإغلاق).</div>;
  const W = 620, H = 170, PAD = 26;
  const vals = [];
  pts.forEach(r => ["core_upl", "sat_upl", "exp_upl"].forEach(k => { if (r[k] != null) vals.push(r[k]); }));
  let lo = Math.min(0, ...vals), hi = Math.max(0, ...vals);
  if (hi - lo < 1) { hi += 0.5; lo -= 0.5; }
  const x = i => PAD + (i / (pts.length - 1)) * (W - 2 * PAD);
  const y = v => PAD + (1 - (v - lo) / (hi - lo)) * (H - 2 * PAD);
  const line = key => pts.map((r, i) => r[key] == null ? null : x(i) + "," + y(r[key])).filter(Boolean).join(" ");
  const zeroY = y(0);
  return (<svg viewBox={"0 0 " + W + " " + H} className="mz-race-svg">
    <line x1={PAD} y1={zeroY} x2={W - PAD} y2={zeroY} stroke="var(--border-subtle)" strokeDasharray="3 3" />
    <polyline points={line("core_upl")} fill="none" stroke={ACC} strokeWidth="2" vectorEffect="non-scaling-stroke" />
    <polyline points={line("sat_upl")} fill="none" stroke={POS} strokeWidth="2" vectorEffect="non-scaling-stroke" />
    <polyline points={line("exp_upl")} fill="none" stroke={WARN} strokeWidth="2" vectorEffect="non-scaling-stroke" />
  </svg>);
}

function CorePortfolioView() {
  const dp = useGet("/api/screener/deep-picks?limit=200");
  const co = useGet("/api/core-overlay-sim");
  const nav = useGet("/api/ledger-nav");
  const [capital, setCapital] = useState(10000);
  const [maxLoss, setMaxLoss] = useState(15);
  const [cb, setCb] = useState(null);         // circuit-breaker card state
  const [cbD, setCbD] = useState({ max_cumulative_loss_pct: 20, max_deviation_pct: 10, capital_experimental: 0 });
  const [cbMsg, setCbMsg] = useState("");
  const loadCb = () => fetch("/api/circuit-breaker").then(r => r.json()).then(s => { setCb(s); setCbD({ ...s.values }); }).catch(() => {});
  useEffect(() => { loadCb(); }, []);
  const cbDirty = cb && Object.keys(cbD).some(k => +cbD[k] !== +((cb.values || {})[k]));
  const approveCb = async () => {
    if (!window.confirm("إقرار بطاقة القواطع؟ هذا بروتوكولك المكتوب — لا يُرسِل ولا يمنع أمراً، يُسجَّل بتاريخه فقط.")) return;
    setCbMsg("…يحفظ");
    try { const r = await fetch("/api/circuit-breaker/approve", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ values: cbD }) }).then(x => x.json());
      if (r.error) { setCbMsg("خطأ: " + r.error); return; }
      setCb(r); setCbD({ ...r.values }); setCbMsg("أُقرّت ✓"); } catch (e) { setCbMsg("تعذّر"); }
  };
  const resetCb = async () => {
    if (!window.confirm("إلغاء إقرار البطاقة؟ ستعود إلى «لم تُقرّ بعد» — تذكيرٌ بإعادة الإقرار قبل أوّل أمر حقيقيّ.")) return;
    setCbMsg("…يعيد");
    try { const r = await fetch("/api/circuit-breaker/reset", { method: "POST" }).then(x => x.json());
      const s = r.state || r; setCb(s); setCbD({ ...s.values }); setCbMsg("أُلغي الإقرار."); } catch (e) { setCbMsg("تعذّر"); }
  };
  const cbCap = +(cbD.capital_experimental || 0);
  const cbStop = Math.round(cbCap * (+cbD.max_cumulative_loss_pct || 0) / 100);
  const [cl, setCl] = useState(null);         // paper core ledger (PVC) summary
  const [clMsg, setClMsg] = useState("");
  const loadCl = () => fetch("/api/core-ledger").then(r => r.json()).then(setCl).catch(() => {});
  useEffect(() => { loadCl(); }, []);
  const startCore = async () => {
    if (!window.confirm("فتح/تحديث سلّة النواة الورقيّة الآن؟ دفتر محاكاة فقط — لا يرسل أيّ أمر حقيقيّ.")) return;
    setClMsg("…يبدأ (قد يستغرق دقيقة)");
    try { const r = await fetch("/api/core-ledger/rebalance", { method: "POST" }).then(x => x.json());
      setClMsg(r.message || "بدأ"); setTimeout(loadCl, 9000); setTimeout(loadCl, 26000); } catch (e) { setClMsg("تعذّر"); }
  };
  const [sat, setSat] = useState(null);       // momentum satellite ledger (PVSA) — forward OOS
  const [satMsg, setSatMsg] = useState("");
  const loadSat = () => fetch("/api/satellite-ledger").then(r => r.json()).then(setSat).catch(() => {});
  useEffect(() => { loadSat(); }, []);
  const startSat = async () => {
    if (!window.confirm("فتح/تحديث سلّة القمر (الزخم الشهريّ) الورقيّة؟ تتبّع أماميّ ظلّيّ فقط — لا يُنشَر حيّاً ولا يرسل أمراً حقيقيّاً.")) return;
    setSatMsg("…يبدأ (قد يستغرق دقيقة)");
    try { const r = await fetch("/api/satellite-ledger/rebalance", { method: "POST" }).then(x => x.json());
      setSatMsg(r.message || "بدأ"); setTimeout(loadSat, 9000); setTimeout(loadSat, 26000); } catch (e) { setSatMsg("تعذّر"); }
  };
  const [exp, setExp] = useState(null);       // explorer ledger (PVEX) — rocket-signature forward OOS
  const [expMsg, setExpMsg] = useState("");
  const loadExp = () => fetch("/api/explorer-ledger").then(r => r.json()).then(setExp).catch(() => {});
  useEffect(() => { loadExp(); }, []);
  const startExp = async () => {
    if (!window.confirm("فتح/تحديث سلّة المستكشف (التقلّب العالي + البعد عن القمّة)؟ رهان يانصيب صغير، تتبّع أماميّ ظلّيّ فقط — لا يُنشَر حيّاً ولا يرسل أمراً.")) return;
    setExpMsg("…يبدأ (قد يستغرق دقيقة)");
    try { const r = await fetch("/api/explorer-ledger/rebalance", { method: "POST" }).then(x => x.json());
      setExpMsg(r.message || "بدأ"); setTimeout(loadExp, 9000); setTimeout(loadExp, 26000); } catch (e) { setExpMsg("تعذّر"); }
  };
  const [gc, setGc] = useState(null);         // pre-registered graduation criteria
  const [gcD, setGcD] = useState({ min_rebalances: 4, min_alpha_pct: 3, max_worse_dd_pct: 15 });
  const [gcEval, setGcEval] = useState(null);
  const [gcMsg, setGcMsg] = useState("");
  const loadGc = () => {
    fetch("/api/graduation-criteria").then(r => r.json()).then(s => { setGc(s); setGcD({ ...s.values }); }).catch(() => {});
    fetch("/api/graduation-eval").then(r => r.json()).then(setGcEval).catch(() => {});
  };
  useEffect(() => { loadGc(); }, []);
  const approveGc = async () => {
    if (!window.confirm("قفل معايير التخرّج الآن؟ تُثبَّت قبل تراكم البيانات وتُحفَظ بتاريخها — لا تتداول، تحدّد المسطرة فقط.")) return;
    setGcMsg("…يقفل");
    try { const r = await fetch("/api/graduation-criteria/approve", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ values: gcD }) }).then(x => x.json());
      if (r.error) { setGcMsg("خطأ: " + r.error); return; }
      setGc(r); setGcD({ ...r.values }); setGcMsg("قُفِلت ✓"); loadGc(); } catch (e) { setGcMsg("تعذّر"); }
  };
  const VERDICT = { watching: ["قيد المراقبة", WARN], graduated: ["تخرّج ✓", POS], archive: ["يُوصى بالأرشفة", NEG], criteria_not_locked: ["اقفل المعايير أوّلاً", MUT], no_data: ["لا بيانات بعد", MUT] };
  const hb = useGet("/api/screener/halal-basket");
  const rows = (dp && dp.results) || [];
  const basketRows = ((hb && hb.results) || []).filter(r => r.price > 0);
  const halal = basketRows.length >= 30 ? basketRows
    : rows.filter(r => (r.is_halal || r.halal_verdict === "halal") && r.price > 0);
  const core = co && co.strategies && co.strategies.core;
  const maxDD = core ? Math.abs(core.max_drawdown) : 25;
  const exposure = Math.max(0, Math.min(100, Math.round((maxLoss / maxDD) * 100)));
  const deploy = capital * exposure / 100;
  const N = halal.length || 1;
  const perName = deploy / N;
  const orders = halal.map(r => {
    // whole shares when the per-name budget affords them; else a fractional qty (IBKR supports
    // fractional US shares) so a wide basket stays buyable with modest capital
    const sh = perName >= r.price ? Math.floor(perName / r.price) : +((perName / r.price).toFixed(3));
    return { sym: r.symbol, co: r.company, sector: r.sector, price: r.price, shares: sh, value: sh * r.price };
  }).filter(o => o.shares > 0);
  const invested = orders.reduce((a, o) => a + o.value, 0);
  const worst = Math.round(deploy * maxDD / 100);
  const exportCsv = () => {
    const lines = [["symbol", "shares", "price", "value"].join(",")].concat(orders.map(o => [o.sym, o.shares, o.price, o.value.toFixed(2)].join(",")));
    const a = document.createElement("a"); a.href = "data:text/csv;charset=utf-8," + encodeURIComponent(lines.join("\n")); a.download = "core_basket_orders.csv"; a.click();
  };
  return (
    <div className="mz-view">
      <div className="mz-crumb" style={{ marginBottom: 12 }}>🎯 <b>محفظة النواة</b> — الشيء الوحيد الذي نجا من كلّ اختباراتنا: امتلاك الكون الحلال بالتساوي</div>
      <div className="mz-verdict" style={{ borderColor: WARN, color: "var(--text-secondary)", marginBottom: 16 }}>
        ⚠️ هذه <b>بيتا حلال</b> (عائد السوق) لا مهارة انتقاء — ومع ذلك هزمت كلّ وصفاتنا الذكيّة بعد التكاليف. الأرقام تاريخيّة (انحياز البقاء يضخّمها) ولا تضمن المستقبل. <b>لا أنفّذ أوامر — أُولّد قائمةً تنفّذها أنت من IBKR.</b>
      </div>

      <div className="mz-loop" style={{ marginBottom: 20 }}>
        {[["العائد السنويّ (CAGR)", core ? pct(core.cagr) : "…", POS], ["أقصى تراجع تاريخيّ", core ? pct(core.max_drawdown) : "…", NEG],
        ["نسبة العائد/التراجع", core ? num(core.cagr_dd, 2) : "…", ACC], ["أداء هبوط 2022", core ? pct(core.ret_2022) : "…", (core && core.ret_2022 >= 0) ? POS : NEG]].map(([l, v, c], i) => (
          <div className="mz-loop-c" key={i}><div className="mz-loop-big" style={{ color: c }}>{v}</div><div className="mz-loop-u">{l}</div></div>))}
      </div>

      <div className="mz-r2">
        <Panel title="🩹 حاسبة ميزانيّة الألم">
          <div className="mz-pain">
            <label className="mz-fl">رأس المال ($)<input type="number" className="mz-inp" style={{ width: 120 }} value={capital} onChange={e => setCapital(Math.max(0, +e.target.value))} /></label>
            <label className="mz-fl">أقصى خسارة تتحمّلها (%)<input type="number" className="mz-inp" style={{ width: 80 }} min="1" max={Math.round(maxDD)} value={maxLoss} onChange={e => setMaxLoss(Math.max(1, +e.target.value))} /></label>
          </div>
          <div className="mz-pain-out">
            <div><span>التعرّض المقترح</span><b style={{ color: ACC }}>{exposure}%</b></div>
            <div><span>المبلغ المستثمَر</span><b>{money(deploy)}</b></div>
            <div><span>يبقى نقداً</span><b>{money(capital - deploy)}</b></div>
            <div><span>أسوأ خسارة متوقّعة تاريخيّاً</span><b style={{ color: NEG }}>−{money(worst)}</b></div>
          </div>
          <div className="mz-note ql-dim">ميكانيكيّ من التراجع المقاس ({num(maxDD, 0)}% عند 100%): تعرّض = خسارتك المقبولة ÷ التراجع. أداة أرقام لا نصيحة. ابدأ بمبلغ تستطيع خسارته كاملاً دون ألم.</div>
        </Panel>
        <Panel title="📋 مولّد أوامر السلّة" right={orders.length ? <button className="mz-btn gold" style={{ maxWidth: 130 }} onClick={exportCsv}>⬇ تصدير CSV</button> : null}>
          <div className="mz-pain-out" style={{ marginBottom: 8 }}>
            <div><span>عدد الأسهم</span><b>{N}</b></div>
            <div><span>لكل سهم (~)</span><b>{money(perName)}</b></div>
            <div><span>إجمالي المستثمَر فعليّاً</span><b>{money(invested)}</b></div>
          </div>
          {orders.length ? (
            <table className="mz-tbl mz-tbl-wide"><thead><tr><th className="tl">الرمز</th><th>السعر</th><th>عدد الأسهم</th><th>القيمة</th></tr></thead>
              <tbody>{orders.slice(0, 60).map((o, i) => (<tr key={i}><td className="tl mz-fn">{o.sym}<div className="mz-dim2">{(o.co || "").slice(0, 18)}</div></td><td>{money(o.price)}</td><td className="mz-fn">{o.shares}</td><td>{money(o.value)}</td></tr>))}</tbody></table>
          ) : <div className="mz-empty">…يحمّل السلّة الحلال (أدخِل رأس مالاً كافياً)</div>}
          <div className="mz-note ql-dim">قائمة تنفّذها <b>يدويّاً من IBKR</b> بالتساوي — أنا لا أرسل أوامر. السلّة الآن هي <b>الكون الحلال الكامل</b> ({N} اسماً)؛ الكميّات الكسريّة مدعومة في IBKR للأسهم الأمريكيّة. أعِد التوازن فصليّاً (٤ مرّات/سنة) لإبقاء التكاليف منخفضة.</div>
        </Panel>
      </div>

      <Panel title="🛑 بطاقة القواطع المكتوبة — تُقرّها قبل أوّل أمر حقيقيّ" cls="mz-cb-panel" right={cb && <span className="mz-dim3" style={{ color: cb.approved ? POS : WARN }}>{cb.approved ? "مُقرّة ✓" : "لم تُقرّ بعد"}</span>}>
        {cb ? (<div>
          <div className="mz-cb-banner">قواعدك الشخصيّة المكتوبة — تلتزم بها <b>أنت</b>. النظام لا يفرضها على حسابك الحقيقيّ ولا يُرسِل/يمنع أمراً؛ يسجّلها فقط لتراها مكتوبةً بتاريخها قبل الدخول.</div>
          <div className="mz-cb-rows">
            <div className="mz-cb-row">
              <div className="mz-cb-h">① خسارة تراكميّة قصوى ← توقّف ومراجعة</div>
              <label className="mz-fl">النسبة %<input type="number" className="mz-inp" style={{ width: 80 }} min="1" max="50" value={cbD.max_cumulative_loss_pct} onChange={e => setCbD(d => ({ ...d, max_cumulative_loss_pct: +e.target.value }))} /></label>
              <div className="mz-cb-eff">خط التوقّف ≈ <b style={{ color: NEG }}>−{money(cbStop)}</b> {cbCap ? "من رأس مالك التجريبيّ" : "(أدخِل رأس المال التجريبيّ في ③)"}</div>
            </div>
            <div className="mz-cb-row">
              <div className="mz-cb-h">② انحرافك عن السلّة ← تنبيه انضباط</div>
              <label className="mz-fl">الحدّ %<input type="number" className="mz-inp" style={{ width: 80 }} min="2" max="50" value={cbD.max_deviation_pct} onChange={e => setCbD(d => ({ ...d, max_deviation_pct: +e.target.value }))} /></label>
              <div className="mz-cb-eff">تجاوزُه = أعِد التوازن نحو السلّة (لا تطارد اسماً)</div>
            </div>
            <div className="mz-cb-row">
              <div className="mz-cb-h">③ رأس المال التجريبيّ — ما تستطيع خسارته كاملاً</div>
              <label className="mz-fl">$<input type="number" className="mz-inp" style={{ width: 120 }} min="0" value={cbD.capital_experimental} onChange={e => setCbD(d => ({ ...d, capital_experimental: Math.max(0, +e.target.value) }))} /></label>
              <div className="mz-cb-eff">ابدأ صغيراً — مبلغٌ خسارتُه كاملاً لا تؤلمك</div>
            </div>
            <div className="mz-cb-row">
              <div className="mz-cb-h">④ جرس «أزمة» النظام (HMM)</div>
              <div className="mz-cb-eff">🔔 <b>معلوماتيّ فقط</b> — لا خروج آليّاً. قِسنا أنّ الخروج على الجرس يضرّ؛ تنبيهٌ لتنتبه، لا أمر.</div>
            </div>
          </div>
          <div className="mz-wt-act">
            <button className="mz-btn gold" style={{ maxWidth: 190, opacity: cbDirty || !cb.approved ? 1 : 0.55 }} onClick={approveCb}>{cb.approved ? "تحديث الإقرار" : "أُقرّ هذه القواطع"}</button>
            {cb.approved && <button className="mz-btn" style={{ maxWidth: 150 }} onClick={resetCb}>إلغاء الإقرار</button>}
          </div>
          {cb.approved_at && <div className="mz-note" style={{ color: POS }}>✓ أقررتَها في {(() => { try { return new Date(cb.approved_at).toLocaleString("ar"); } catch (e) { return cb.approved_at; } })()}</div>}
          {cbMsg && <div className="mz-note" style={{ color: cbMsg.startsWith("خطأ") || cbMsg === "تعذّر" ? NEG : POS }}>{cbMsg}</div>}
          <div className="mz-note ql-dim">محفوظة على القرص بسجلّ تدقيق كامل وقابلة للعكس. هذه ليست نصيحةً ولا أمراً — بروتوكول انضباطٍ تكتبه وتقرأه أنت.</div>
        </div>) : <div className="mz-empty">…يحمّل البطاقة</div>}
      </Panel>

      <Panel title="📓 دفتر النواة الورقيّ — مرآة موازية لقياس انزلاقك" cls="mz-core-ledger"
        right={<button className="mz-btn gold" style={{ maxWidth: 150 }} onClick={startCore}>{cl && cl.open ? "↻ حدّث الدفتر" : "▶ افتح الدفتر"}</button>}>
        {cl ? (cl.open ? (<div>
          <div className="mz-pain-out" style={{ marginBottom: 10 }}>
            <div><span>عائد غير محقّق (بأسعار آخر مسح)</span><b style={{ color: (cl.unrealized_pct || 0) >= 0 ? POS : NEG }}>{pct(cl.unrealized_pct)}</b></div>
            <div><span>عدد المراكز</span><b>{cl.open}</b></div>
            <div><span>القيمة السوقيّة</span><b>{money(cl.market_value)}</b></div>
            <div><span>آخر إعادة توازن</span><b style={{ fontSize: 13 }}>{cl.days_since_rebalance != null ? ("منذ " + cl.days_since_rebalance + " يوم") : "—"}</b></div>
          </div>
          {(cl.positions || []).length ? (
            <table className="mz-tbl mz-tbl-wide"><thead><tr><th className="tl">الرمز</th><th>الدخول</th><th>الحاليّ</th><th>غير محقّق %</th><th>القيمة</th></tr></thead>
              <tbody>{cl.positions.slice(0, 15).map((p, i) => (<tr key={i}><td className="tl mz-fn">{p.symbol}</td><td>{money(p.entry)}</td><td>{money(p.current)}</td><td style={{ color: (p.upl_pct || 0) >= 0 ? POS : NEG }}>{pct(p.upl_pct)}</td><td>{money(p.value)}</td></tr>))}</tbody></table>
          ) : null}
          <div className="mz-note ql-dim">النظام يتداول النواة بالتساوي <b>ورقيّاً</b> بالتوازي (إعادة توازن كلّ ~{cl.rebalance_days} يوم). حين تنفّذ سلّتك الحقيقيّة، قارن عائدك بهذا الرقم = <b>انزلاقك الشخصيّ</b>، أهمّ رقم في مرحلة المال الصغير. محاكاة فقط — لا أوامر حقيقيّة.</div>
        </div>) : (
          <div className="mz-empty">لم تُفتح السلّة الورقيّة بعد — اضغط «▶ افتح الدفتر» لبدء تتبّع النواة الحلال بالتساوي ورقيّاً. يعمل تلقائيّاً أيضاً كلّ ~{cl.rebalance_days || 85} يوم.</div>
        )) : <div className="mz-empty">…يحمّل الدفتر</div>}
        {clMsg && <div className="mz-note" style={{ color: clMsg === "تعذّر" ? NEG : POS }}>{clMsg}</div>}
      </Panel>

      <Panel title="🌙 قمر الزخم — تتبّع أماميّ ظلّيّ (غير منشور حيّاً)" cls="mz-core-ledger"
        right={<button className="mz-btn" style={{ maxWidth: 150 }} onClick={startSat}>{sat && sat.open ? "↻ حدّث القمر" : "▶ افتح القمر"}</button>}>
        <div className="mz-verdict" style={{ borderColor: ACC, color: "var(--text-secondary)", marginBottom: 12, fontWeight: 600 }}>
          🔬 <b>الفرضيّة الوحيدة التي عبرت آلة الزمن بعد التكاليف</b>: زخم 12-1، إعادة توازن شهريّة، أعلى 10. في العيّنة: <b style={{ color: POS }}>+4.6%/سنة</b> فوق النواة — لكن بتراجع أسوأ (<b style={{ color: NEG }}>−37%</b> مقابل −21%)، وأرقام 2022 مشبوهة بانحياز البقاء. لذا <b>لا يُنشَر حيّاً</b>؛ هذا الدفتر يجمع دليلاً <b>أماميّاً</b> (خارج العيّنة) ليكشف إن كانت الألفا حقيقيّة. بقرارك وحدك يصبح قمراً ≤10% لاحقاً.
        </div>
        {sat ? (sat.open ? (<div>
          <div className="mz-pain-out" style={{ marginBottom: 10 }}>
            <div><span>عائد غير محقّق (أماميّ)</span><b style={{ color: (sat.unrealized_pct || 0) >= 0 ? POS : NEG }}>{pct(sat.unrealized_pct)}</b></div>
            <div><span>عدد المراكز</span><b>{sat.open}</b></div>
            <div><span>القيمة السوقيّة</span><b>{money(sat.market_value)}</b></div>
            <div><span>آخر إعادة توازن</span><b style={{ fontSize: 13 }}>{sat.days_since_rebalance != null ? ("منذ " + sat.days_since_rebalance + " يوم") : "—"}</b></div>
          </div>
          {(sat.positions || []).length ? (
            <table className="mz-tbl mz-tbl-wide"><thead><tr><th className="tl">الرمز</th><th>الدخول</th><th>الحاليّ</th><th>غير محقّق %</th><th>القيمة</th></tr></thead>
              <tbody>{sat.positions.slice(0, 12).map((p, i) => (<tr key={i}><td className="tl mz-fn">{p.symbol}</td><td>{money(p.entry)}</td><td>{money(p.current)}</td><td style={{ color: (p.upl_pct || 0) >= 0 ? POS : NEG }}>{pct(p.upl_pct)}</td><td>{money(p.value)}</td></tr>))}</tbody></table>
          ) : null}
          <div className="mz-note ql-dim">تتبّع أماميّ خالص — كلّ يوم يمرّ هو دليل خارج العيّنة لم يكن متاحاً في الاختبار. أعِد التوازن شهريّاً (~{sat.rebalance_days} يوم). ظلّيّ فقط — لا يغيّر تسجيلك الحيّ ولا يرسل أمراً.</div>
        </div>) : (
          <div className="mz-empty">لم يُفتح دفتر القمر بعد — اضغط «▶ افتح القمر» لبدء السجلّ الأماميّ (أعلى 10 بالزخم، شهريّ). يعمل تلقائيّاً أيضاً كلّ ~{sat.rebalance_days || 28} يوم.</div>
        )) : <div className="mz-empty">…يحمّل القمر</div>}
        {satMsg && <div className="mz-note" style={{ color: satMsg === "تعذّر" ? NEG : POS }}>{satMsg}</div>}
      </Panel>

      <Panel title="🚀 المستكشف — صيّاد الذيل (ظلّيّ، رهان يانصيب)" cls="mz-core-ledger"
        right={<button className="mz-btn" style={{ maxWidth: 150 }} onClick={startExp}>{exp && exp.open ? "↻ حدّث المستكشف" : "▶ افتح المستكشف"}</button>}>
        <div className="mz-verdict" style={{ borderColor: WARN, color: "var(--text-secondary)", marginBottom: 12, fontWeight: 600 }}>
          🔬 من <b>تشريح الصاعدين</b> (642 من 9,604 حالة تضاعفت): بصمة الصاروخ = <b>تقلّب عالٍ + بُعد عن قمّة 52 أسبوعاً</b>. لكن ساق "المنهار" <b style={{ color: NEG }}>مسمومة بانحياز البقاء</b> (نرى فقط من ارتدّ)، وساق التقلّب أنظف. لذا هذه <b>سلّة يانصيب صغيرة — تتوقّع فشل معظمها</b>؛ هذا الدفتر الأماميّ يقيس كم من "×3" حقيقيّ. لا يُنشَر حيّاً.
        </div>
        {exp ? (exp.open ? (<div>
          <div className="mz-pain-out" style={{ marginBottom: 10 }}>
            <div><span>عائد غير محقّق (أماميّ)</span><b style={{ color: (exp.unrealized_pct || 0) >= 0 ? POS : NEG }}>{pct(exp.unrealized_pct)}</b></div>
            <div><span>عدد المراكز</span><b>{exp.open}</b></div>
            <div><span>القيمة السوقيّة</span><b>{money(exp.market_value)}</b></div>
            <div><span>آخر إعادة توازن</span><b style={{ fontSize: 13 }}>{exp.days_since_rebalance != null ? ("منذ " + exp.days_since_rebalance + " يوم") : "—"}</b></div>
          </div>
          {(exp.positions || []).length ? (
            <table className="mz-tbl mz-tbl-wide"><thead><tr><th className="tl">الرمز</th><th>الدخول</th><th>الحاليّ</th><th>غير محقّق %</th><th>القيمة</th></tr></thead>
              <tbody>{exp.positions.slice(0, 12).map((p, i) => (<tr key={i}><td className="tl mz-fn">{p.symbol}</td><td>{money(p.entry)}</td><td>{money(p.current)}</td><td style={{ color: (p.upl_pct || 0) >= 0 ? POS : NEG }}>{pct(p.upl_pct)}</td><td>{money(p.value)}</td></tr>))}</tbody></table>
          ) : null}
          <div className="mz-note ql-dim">سلّة صغيرة متساوية من أعلى الأسماء تقلّباً وبُعداً عن القمّة (~{exp.rebalance_days} يوم). العائد سيتقلّب بعنف — هذا متوقّع. قياس فقط — لا صفقات.</div>
        </div>) : (
          <div className="mz-empty">لم يُفتح دفتر المستكشف بعد — اضغط «▶ افتح المستكشف» لبدء تتبّع بصمة الصاروخ أماميّاً. يعمل تلقائيّاً أيضاً كلّ ~{exp.rebalance_days || 28} يوم.</div>
        )) : <div className="mz-empty">…يحمّل المستكشف</div>}
        {expMsg && <div className="mz-note" style={{ color: expMsg === "تعذّر" ? NEG : POS }}>{expMsg}</div>}
      </Panel>

      <Panel title="🏁 سباق المحرّكات الثلاثة — المنحنى الأماميّ" cls="mz-core-ledger">
        <div className="mz-race-legend">
          <span><i style={{ background: ACC }} />النواة {nav && nav.latest ? pct(nav.latest.core_upl) : "…"}</span>
          <span><i style={{ background: POS }} />القمر {nav && nav.latest ? pct(nav.latest.sat_upl) : "…"}</span>
          <span><i style={{ background: WARN }} />المستكشف {nav && nav.latest ? pct(nav.latest.exp_upl) : "…"}</span>
          <span className="ql-dim" style={{ marginInlineStart: "auto" }}>{nav ? (nav.days + " يوم مُسجَّل") : "…"}</span>
        </div>
        <RaceChart rows={nav && nav.rows} />
        <div className="mz-note ql-dim">عائد المراكز المفتوحة لكلّ دفتر بأسعار الإغلاق (يستثني المحقَّق). تُسجَّل لقطة آليّاً كلّ يوم عند إغلاق السوق. هذا هو الدليل خارج العيّنة الذي يحسم إن كانت ألفا الزخم حقيقيّة. قياس فقط — لا صفقات.</div>
      </Panel>

      <Panel title="📜 معايير التخرّج — مقفلة مسبقاً (قبل البيانات)" cls="mz-core-ledger"
        right={gc && <span className="mz-dim3" style={{ color: gc.locked ? POS : WARN }}>{gc.locked ? "مقفلة ✓" : "غير مقفلة"}</span>}>
        <div className="mz-cb-banner">العلم الأمين يقفل قواعد النجاح/الفشل <b>قبل</b> وصول البيانات — كي لا نبرّر أيّ نتيجة لاحقاً. تقفلها مرّةً، فيصبح الحكم <b>آليّاً</b>. قياس فقط: لا يؤرشف ولا يرقّي شيئاً — القرار قرارك.</div>
        <div className="mz-cb-rows">
          <div className="mz-cb-row"><div className="mz-cb-h">① مدّة الانتظار (عدد إعادات التوازن)</div>
            <label className="mz-fl">إعادات<input type="number" className="mz-inp" style={{ width: 80 }} min="1" max="24" value={gcD.min_rebalances} onChange={e => setGcD(d => ({ ...d, min_rebalances: +e.target.value }))} /></label>
            <div className="mz-cb-eff">لا حكم قبلها (~{Math.round((gcD.min_rebalances || 0) * 28 / 30)} شهراً)</div></div>
          <div className="mz-cb-row"><div className="mz-cb-h">② أدنى تفوّق على النواة (ألفا تراكميّة %)</div>
            <label className="mz-fl">%<input type="number" className="mz-inp" style={{ width: 80 }} min="0" max="50" value={gcD.min_alpha_pct} onChange={e => setGcD(d => ({ ...d, min_alpha_pct: +e.target.value }))} /></label>
            <div className="mz-cb-eff">دون هذا = لا قيمة مضافة</div></div>
          <div className="mz-cb-row"><div className="mz-cb-h">③ أقصى تراجع إضافيّ مسموح فوق النواة %</div>
            <label className="mz-fl">%<input type="number" className="mz-inp" style={{ width: 80 }} min="0" max="100" value={gcD.max_worse_dd_pct} onChange={e => setGcD(d => ({ ...d, max_worse_dd_pct: +e.target.value }))} /></label>
            <div className="mz-cb-eff">تراجع أسوأ من النواة بأكثر منه = يسقط</div></div>
        </div>
        <div className="mz-wt-act">
          <button className="mz-btn gold" style={{ maxWidth: 190 }} onClick={approveGc}>{gc && gc.locked ? "تحديث القفل" : "أقفل المعايير"}</button>
        </div>
        {gc && gc.approved_at && <div className="mz-note" style={{ color: POS }}>✓ قُفِلت في {(() => { try { return new Date(gc.approved_at).toLocaleString("ar"); } catch (e) { return gc.approved_at; } })()}</div>}
        {gcMsg && <div className="mz-note" style={{ color: gcMsg.startsWith("خطأ") || gcMsg === "تعذّر" ? NEG : POS }}>{gcMsg}</div>}
        {gcEval && gcEval.engines && (
          <table className="mz-tbl mz-tbl-wide" style={{ marginTop: 10 }}><thead><tr><th className="tl">المحرّك</th><th>ألفا %</th><th>تراجع أسوأ من النواة</th><th>الحكم الآليّ</th></tr></thead>
            <tbody>{["sat", "exp"].map(k => { const e = gcEval.engines[k]; if (!e) return null; const v = VERDICT[e.verdict] || [e.verdict, MUT]; return (<tr key={k}><td className="tl mz-fn">{e.label}</td><td>{e.alpha != null ? pct(e.alpha) : "—"}</td><td>{e.worse_dd_vs_core != null ? (e.worse_dd_vs_core > 0 ? "+" : "") + num(e.worse_dd_vs_core, 1) + "%" : "—"}</td><td style={{ color: v[1], fontWeight: 700 }}>{v[0]}</td></tr>); })}</tbody></table>
        )}
        <div className="mz-note ql-dim">{gcEval ? ("مضى " + gcEval.span_days + " يوم من أصل " + gcEval.min_days + " قبل الحكم. ") : ""}الحكم يُحسَب آليّاً من منحنى السباق مقابل المعايير المقفلة — بلا اجتهاد بشريّ عند لحظة القرار.</div>
      </Panel>
    </div>
  );
}

// t-stat → a beginner "confidence" meter: fill % + plain word (certainty, not good/bad).
function confidenceOf(t) {
  const a = Math.abs(t || 0);
  return { pct: Math.min(100, a / 3 * 100), label: a >= 2 ? "مؤكَّد" : a >= 1 ? "يحتاج وقتاً أطول" : "غير مؤكَّد بعد", strong: a >= 2 };
}
const TL = { bad: NEG, warn: WARN, good: POS, neutral: MUT };  // traffic-light colors

function LabView() {
  const sq = useGet("/api/selection-quality");
  const fic = useGet("/api/factor-ic-multi");
  const cc = useGet("/api/candidate-composites");
  const gc = useGet("/api/gate-config");
  const cv = useGet("/api/candidate-validation");
  const wf = useGet("/api/walk-forward-sim?top_k=5&hold=5&cost_bps=15");
  const mr = useGet("/api/market-relative-race");     // two-tier "rank vs full market" test
  const [spec, setSpec] = useState(null);             // speculation shadow ledger (PVSP)
  const [specMsg, setSpecMsg] = useState("");
  const loadSpec = () => fetch("/api/speculation-ledger").then(r => r.json()).then(setSpec).catch(() => {});
  useEffect(() => { loadSpec(); }, []);
  const startSpec = async () => {
    if (!window.confirm("تشغيل دورة مضاربة ورقيّة الآن؟ محاكاة 100% — لا مال حقيقيّ ولا أمر حقيقيّ. (يعمل تلقائيّاً كلّ ~20 دقيقة أثناء تداول السوق)")) return;
    setSpecMsg("…يشغّل الدورة");
    try { const r = await fetch("/api/speculation-ledger/tick", { method: "POST" }).then(x => x.json());
      setSpecMsg(r.message || "بدأ"); setTimeout(loadSpec, 9000); setTimeout(loadSpec, 22000); } catch (e) { setSpecMsg("تعذّر"); }
  };
  const scanners = (sq && sq.scanners) || [];
  const ov = (sq && sq.overlays) || {};
  const gate = (sq && sq.gate) || {};
  const rg = ov.regime || (sq && sq.regime);
  const facs = fic && fic.attribution && fic.attribution.factors;
  // best + worst factor for the LEARN card (by 10d IC)
  let bestF = null, worstF = null;
  if (facs) { const fl = { mom_12_1: "الزخم", rs: "القوّة النسبية", above_ema20: "شرط فوق EMA20", rsi: "RSI", dist_ema20_pct: "الامتداد", atr_pct: "التذبذب" };
    const arr = Object.entries(facs).map(([k, v]) => ({ k, l: fl[k] || k, ic: (v.h && v.h["10"] || {}).mean_ic })).filter(x => x.ic != null);
    arr.sort((a, b) => b.ic - a.ic); bestF = arr[0]; worstF = arr[arr.length - 1]; }
  const nCands = cc && cc.candidates ? Object.keys(cc.candidates).length : 0;
  const capRows = ov.capture_rows, capLab = ov.capture_labelled;

  const LOOP = [
    { n: "1", tag: "COLLECT", t: "يَجمع", ic: "🔵", desc: "يلتقط صورة للسوق كل يوم ويخزّنها ليتعلّم منها لاحقاً.",
      big: capRows != null ? Number(capRows).toLocaleString("en") : "…", unit: "لقطة مُجمَّعة · تنمو يوميّاً" },
    { n: "2", tag: "ANALYZE", t: "يُحلّل", ic: "🟣", desc: "يقيس أيّ إشارة تنبّأت فعلاً بالربح — لا بالرأي، بل بالأرقام.",
      big: capLab != null ? Number(capLab).toLocaleString("en") : "…", unit: "لقطة مُقاسة بنتيجتها" },
    { n: "3", tag: "LEARN", t: "يتعلّم", ic: "🟡", desc: bestF ? ("اكتشف أن «" + bestF.l + "» أفضل إشارة، و«" + (worstF ? worstF.l : "") + "» تؤذي.") : "يستخلص أيّ الإشارات تنفع وأيّها تضرّ.",
      big: bestF ? bestF.l : "…", unit: "أقوى إشارة مُكتشَفة" },
    { n: "4", tag: "CORRECT", t: "يُصلّح", ic: "🟢", desc: "يجرّب وصفات أفضل في الظلّ، ولا يغيّر شيئاً حيّاً إلا بموافقتك.",
      big: nCands ? nCands : "…", unit: "وصفات تُختبَر الآن" },
  ];

  return (
    <div className="mz-view">
      <div className="mz-ana-top">
        <div className="mz-crumb">🧪 <b>مختبر الفهم</b> — كيف يعمل نظامك ونتائجه، بلغة بسيطة</div>
        <a className="mz-btn" style={{ maxWidth: 170 }} href="/quant-lab" target="_blank" rel="noopener">🔬 المختبر المتقدّم ←</a>
      </div>

      {/* daily one-liner */}
      <div className="mz-day-line">
        📋 <b>حالة اليوم:</b> النظام {rg && typeof rg === "object" ? "" : ""}
        {rg ? <span> — السوق <b style={{ color: ACC }}>{(rg && rg.dominant) === "crisis" ? "أزمة" : (rg && rg.dominant) === "calm_bull" ? "هادئ صاعد" : "هادئ/محايد"}</b></span> : ""}
        {capRows != null && <span> · <b>{Number(capRows).toLocaleString("en")}</b> لقطة في الذاكرة</span>}
        {gate.current_min_rs != null && <span> · عتبة الدخول <b>MIN_RS {num(gate.current_min_rs, 1)}</b></span>}
        {gate.recommendation ? <span style={{ color: WARN }}> · ⚠ قرار ينتظرك (اطّلع أدناه)</span> : <span style={{ color: POS }}> · لا قرارات عاجلة</span>}
      </div>

      {/* Section 1 — the living loop */}
      <div className="mz-sec-h">كيف يعمل النظام؟ — حلقة تتكرّر كل يوم</div>
      <div className="mz-loop">
        {LOOP.map((c, i) => (<React.Fragment key={c.n}>
          <div className="mz-loop-c">
            <div className="mz-loop-tag">{c.ic} {c.tag}</div>
            <div className="mz-loop-t">{c.t}</div>
            <div className="mz-loop-big">{c.big}</div>
            <div className="mz-loop-u">{c.unit}</div>
            <div className="mz-loop-d">{c.desc}</div>
          </div>
          {i < LOOP.length - 1 && <div className="mz-loop-arrow">←</div>}
        </React.Fragment>))}
      </div>

      {/* Section 2 — does it win? */}
      <div className="mz-sec-h">هل النظام يربح؟ — مقارنةً بشراء السوق فقط (SPY)</div>
      <div className="mz-score2">
        {scanners.length ? scanners.map((s, i) => { const conf = confidenceOf(s.alpha_t); const col = TL[s.color] || MUT;
          const beat = s.pct_beat_spy; return (
          <div className="mz-sc-card" key={i} style={{ borderColor: col }}>
            <div className="mz-sc-top"><div className="mz-sc-name">الماسح {s.name}</div><span className="mz-sc-grade" style={{ background: col }}>{s.grade}</span></div>
            <div className="mz-sc-label">{s.label}</div>
            <div className="mz-sc-alpha" style={{ color: (s.alpha || 0) >= 0 ? POS : NEG }}>{(s.alpha || 0) >= 0 ? "+" : ""}{num(s.alpha, 2)}%<span className="mz-sc-vs">مقابل السوق</span></div>
            <div className="mz-sc-facts">
              <span>من <b>{s.n}</b> صفقة</span>
              {beat != null && <span>· <b>{num(beat, 0)}%</b> تفوّقت على السوق</span>}
            </div>
            <div className="mz-sc-conf">
              <div className="mz-sc-conf-l">ثقة القياس: <b>{conf.label}</b></div>
              <div className="mz-sc-conf-bar"><div style={{ width: conf.pct + "%", background: conf.strong ? col : MUT }} /></div>
            </div>
          </div>); }) : <div className="mz-empty">…يقيس أداء الاستراتيجيات</div>}
      </div>
      <div className="mz-note ql-dim">«مقابل السوق» = الفرق بين أداء الاستراتيجية وأداء شراء SPY والانتظار. موجب أخضر = تتفوّق · سالب أحمر = تخسر أمام السوق. «ثقة القياس» تعني كم نحن متأكّدون (ليست ربحاً) — تكبر كلّما زادت الصفقات. أرقام ورقيّة، لا صفقات حقيقيّة.</div>

      {/* Section 2.5 — TIME MACHINE walk-forward */}
      <div className="mz-sec-h" style={{ marginTop: 26 }}>⏱️ آلة الزمن — لو تداولت الوصفة منذ 2022 (أعلى-5 أسبوعيّاً · بعد التكاليف)</div>
      {wf && wf.strategies ? (() => {
        const b = wf.benchmark || {}; const strat = Object.entries(wf.strategies).filter(([k, v]) => v);
        const best = strat.map(([k, v]) => v).sort((a, c) => (c.alpha_cagr || -99) - (a.alpha_cagr || -99))[0];
        const anyBeat = strat.some(([k, v]) => (v.alpha_cagr || 0) > 0);
        return (<div>
          <div className="mz-verdict" style={{ borderColor: anyBeat ? POS : NEG, color: anyBeat ? POS : NEG }}>
            {anyBeat ? "✅ وصفة تتفوّق على «شراء الكون بالتساوي» بعد التكاليف — راجعها أدناه."
              : "⚠️ لا وصفة تتفوّق على «شراء الكون الحلال بالتساوي» بعد التكاليف عبر 4 سنوات — ليست جاهزة للمال الحقيقيّ. (لكنها دفاعيّة: تفوّقت في هبوط 2022.)"}
          </div>
          <table className="mz-tbl mz-tbl-wide" style={{ marginTop: 10 }}>
            <thead><tr><th className="tl">الاستراتيجية</th><th>العائد الكلّي</th><th>CAGR</th><th>أقصى تراجع</th><th>مقابل الكون</th><th>2022</th><th>PF</th></tr></thead>
            <tbody>
              <tr style={{ background: "var(--bg-raised)" }}><td className="tl mz-fn">📊 الكون بالتساوي (المعيار)</td><td>{pct(b.total_return)}</td><td>{pct(b.cagr)}</td><td style={{ color: NEG }}>{pct(b.max_drawdown)}</td><td className="mz-dim2">—</td><td>{pct(b.ret_2022)}</td><td className="mz-dim2">—</td></tr>
              {strat.map(([k, v]) => (<tr key={k}>
                <td className="tl mz-fn">{v.label}</td><td>{pct(v.total_return)}</td><td>{pct(v.cagr)}</td>
                <td style={{ color: NEG }}>{pct(v.max_drawdown)}</td>
                <td style={{ color: (v.alpha_cagr || 0) >= 0 ? POS : NEG, fontWeight: 800 }}>{pct(v.alpha_cagr)}</td>
                <td style={{ color: (v.alpha_2022 || 0) >= 0 ? POS : NEG }}>{pct(v.ret_2022)}</td>
                <td>{num(v.pf, 2)}</td></tr>))}
            </tbody>
          </table>
          {best && best.curve && best.curve.length > 1 && <div style={{ marginTop: 12 }}>
            <div className="mz-sc-conf-l">منحنى رأس المال — {best.label} (يبدأ من 0%)</div>
            <LineArea data={best.curve.map(p => p.eq * 100 - 100)} color={(best.alpha_cagr || 0) >= 0 ? POS : WARN} />
          </div>}
          <div className="mz-note ql-dim">محاكاة walk-forward على لوحة اللقطات ({wf.rebalances} إعادة توازن · {wf.span && wf.span[0]}→{wf.span && wf.span[1]}) مقابل شراء كلّ الأسهم الحلال بالتساوي. انحياز البقاء يضخّم الأرقام المطلقة — اقرأ عمود «مقابل الكون» و«2022» لا الرقم الكلّي. التكاليف على الاستراتيجيّة فقط (متحفّظ). بحثيّ — لا تداول حقيقيّ.</div>
        </div>);
      })() : <div className="mz-empty">…تُشغّل آلة الزمن (محاكاة 4 سنوات على اللوحة)</div>}

      {/* Section 3 — what did it learn? */}
      <div className="mz-sec-h" style={{ marginTop: 26 }}>ماذا تعلّم النظام؟ — اكتشافاته بلغة بسيطة</div>
      <div className="mz-learn">
        {(() => { const cards = [];
          if (bestF && bestF.ic != null) { const c = confidenceOf((facs[bestF.k].h["10"] || {}).ir);
            cards.push({ ic: "✅", t: "«" + bestF.l + "» أفضل إشارة", d: "الأسهم القويّة بهذه الإشارة تميل للاستمرار في الربح.", conf: c, col: POS }); }
          if (worstF && worstF.ic != null && worstF.ic < 0) { const c = confidenceOf((facs[worstF.k].h["10"] || {}).ir);
            cards.push({ ic: "⚠️", t: "«" + worstF.l + "» تؤذي", d: "الاعتماد عليها يجعل النظام يشتري في الوقت الخطأ (سعر ممتدّ).", conf: c, col: WARN }); }
          // best shadow experiment by top-bucket excess (10d)
          if (cc && cc.candidates) { const cand = Object.entries(cc.candidates).filter(([k]) => k !== "mom")
              .map(([k, v]) => ({ k, l: v.label, e: (v.h["10"] || {}).top_excess, tt: (v.h["10"] || {}).top_t })).filter(x => x.e != null).sort((a, b) => b.e - a.e)[0];
            if (cand) { const strong = Math.abs(cand.tt || 0) >= 2;
              cards.push({ ic: "🔬", t: "نجرّب وصفات جديدة", d: "أفضلها للشراء: «" + cand.l + "» (فائض " + (cand.e >= 0 ? "+" : "") + num(cand.e, 2) + "% للقمّة). " + (strong ? "أثبتت تفوّقاً — جاهزة لمراجعتك." : "لكن الدلالة لم تنضج بعد — القياس مستمرّ."), conf: confidenceOf(cand.tt), col: ACC }); } }
          return cards.length ? cards.map((c, i) => (
            <div className="mz-learn-c" key={i}>
              <div className="mz-learn-t"><span className="mz-learn-ic">{c.ic}</span> {c.t}</div>
              <div className="mz-learn-d">{c.d}</div>
              <div className="mz-sc-conf-l">مدى التأكّد: <b>{c.conf.label}</b></div>
              <div className="mz-sc-conf-bar"><div style={{ width: c.conf.pct + "%", background: c.conf.strong ? c.col : MUT }} /></div>
            </div>)) : <div className="mz-empty">…يستخلص الاكتشافات</div>;
        })()}
      </div>

      {/* Section 4 — how does it fix itself? */}
      <div className="mz-sec-h" style={{ marginTop: 26 }}>كيف يصلّح نفسه؟ — قرارات تنتظرك + سجلّ التصحيح</div>
      <div className="mz-r2">
        <Panel title="📥 قرارات تنتظرك">
          <div className="mz-inbox">
            <div className="mz-inbox-c">
              <div className="mz-inbox-t">عتبة الدخول — MIN_RS {gc ? num(gc.min_rs, 1) : num(gate.current_min_rs, 1)} ({(gc && gc.source) === "approved" || gate.source === "approved" ? "معتمَدة" : "افتراضية"})</div>
              <div className="mz-inbox-d">{gate.recommendation ? ("القياس يقترح: " + (gate.recommendation.reason || "مراجعة العتبة")) : "القياس لا يقترح تغييراً الآن."}</div>
              <button className="mz-btn" style={{ maxWidth: 150, marginTop: 6 }} onClick={() => { location.hash = "settings"; }}>مراجعة في الإعدادات ←</button>
            </div>
            {(() => { const cands = (cv && cv.candidates) || {};
              const ready = Object.entries(cands).filter(([k, v]) => v.ready);
              const watch = Object.entries(cands).filter(([k, v]) => !v.ready && v.status === "watching" && k !== "mom");
              if (ready.length) return ready.map(([k, v]) => (
                <div className="mz-inbox-c" key={k} style={{ borderColor: POS, borderWidth: 2 }}>
                  <div className="mz-inbox-t" style={{ color: POS }}>★ وصفة جاهزة للمراجعة: {v.label}</div>
                  <div className="mz-inbox-d">أثبتت جودتها <b>أماميّاً</b> (t={v.recent_t} على آخر {v.recent_dates} تاريخاً · فائض {pct(v.recent_excess, 2)}) <b>و</b> على اللوحة الكاملة (t={v.full_t}). النظام يقترح ترجيح المركّب نحوها — والقرار قرارك وحدك.</div>
                  <button className="mz-btn gold" style={{ maxWidth: 160, marginTop: 6 }} onClick={() => { location.hash = "settings"; }}>راجع الأوزان ←</button>
                </div>));
              return (<div className="mz-inbox-c">
                <div className="mz-inbox-t">وصفات المركّب (الظلّ){cv && cv.recent_dates_used ? " · نافذة أخيرة " + cv.recent_dates_used + " يوم" : ""}</div>
                <div className="mz-inbox-d">{watch.length ? ("قيد المراقبة: " + watch.map(([k, v]) => v.label + (v.recent_t != null ? " (t=" + v.recent_t + ")" : "")).join(" · ") + " — واعدة لكن لم تُثبت أماميّاً بعد (t<2 على النافذة الأخيرة). لن يُطلب قرارك إلا حين تنضج.") : "لا وصفة ناضجة بعد — القياس مستمرّ. النظام يحلّل ويقترح تلقائيّاً، لكنه لا يطبّق شيئاً على التسجيل الحيّ إلا بموافقتك."}</div>
                <button className="mz-btn" style={{ maxWidth: 150, marginTop: 6 }} onClick={() => { location.hash = "factors"; }}>سباق الوصفات ←</button>
              </div>);
            })()}
          </div>
        </Panel>
        <Panel title="📖 دفتر التصحيح — ماذا غيّر النظام ولماذا">
          {gc && (gc.history || []).length ? (
            <div className="mz-ledger">{(gc.history || []).slice(-6).reverse().map((h, i) => (
              <div className="mz-ledger-r" key={i}>
                <span className="mz-ledger-d">{(h.at || "").slice(0, 10)}</span>
                <span>عتبة الدخول: <b>{h.from != null ? num(h.from, 1) : "—"} → {num(h.to, 1)}</b> <span className="mz-dim2">({h.approved_by || "—"})</span></span>
              </div>))}</div>
          ) : <div className="mz-empty">لم يُجرِ النظام تغييراً معتمَداً بعد — كل تعديل ينتظر قرارك. هذا مقصود: القياس يقترح، وأنت تقرّر.</div>}
        </Panel>
      </div>

      <div className="mz-sec-h">🔭 محرّك الاكتشاف — المتابعة (بحثيّ · ظلّيّ)</div>
      <Panel title="حالة الأبحاث الجديدة — تنضج أماميّاً">
        <div className="mz-cb-banner">متابعة الأبحاث التي بنيناها مؤخّراً وهي تجمع الدليل مع الوقت: نموّ اللوحة، الكون البحثيّ ثنائيّ الطبقة، اختبار «الترتيب مقابل السوق»، وعامل الأرباح (PEAD). قياس فقط — لا يمسّ تسجيلاً حيّاً ولا صفقة.</div>
        <div className="mz-pain-out">
          <div><span>حجم اللوحة (لقطات)</span><b>{capRows != null ? Number(capRows).toLocaleString("en") : "…"}</b></div>
          <div><span>تواريخ مُعنونة</span><b>{capLab != null ? Number(capLab).toLocaleString("en") : "…"}</b></div>
          <div><span>عائلات الإشارة</span><b style={{ fontSize: 14 }}>سعر + أرباح</b></div>
          <div><span>وصفات في السباق</span><b>{nCands || "…"}</b></div>
        </div>
        <div className="mz-learn" style={{ marginTop: 13 }}>
          <div className="mz-learn-c">
            <div className="mz-learn-t">🆚 الترتيب مقابل السوق</div>
            {(() => {
              const c = mr && mr.recipes && mr.recipes.adaptive && mr.recipes.adaptive.h && mr.recipes.adaptive.h["20"];
              if (!c || !c.market || c.market.top_excess == null) return <div className="mz-empty">…يقيس</div>;
              const better = (c.halal.top_excess || 0) >= (c.market.top_excess || 0);
              return <div className="mz-learn-d">فائض القمّة (20ي): مقابل السوق <b>{num(c.market.top_excess, 3)}%</b> · مقابل الحلال <b>{num(c.halal.top_excess, 3)}%</b><br /><span style={{ color: better ? NEG : POS }}>{better ? "الحلال مساوٍ/أفضل ⇒ توسيع المُقام لا يتفوّق (مُغلَق)" : "السوق أفضل ⇒ واعد"}</span> · {mr.dates_with_expansion || 0} تاريخاً بتوسعة</div>;
            })()}
          </div>
          <div className="mz-learn-c">
            <div className="mz-learn-t">📊 عامل الأرباح (PEAD) — غير سعريّ</div>
            {(() => {
              const p = facs && facs.pead && facs.pead.h && facs.pead.h["20"];
              if (p && p.n_dates >= 6) return <div className="mz-learn-d">IC(20ي) <b>{num(p.mean_ic, 4)}</b> · IR <b>{p.ir != null ? num(p.ir, 2) : "—"}</b> · على {p.n_dates} تاريخاً — نضج للقراءة.</div>;
              return <div className="mz-learn-d">يتراكم أماميّاً — لم ينضج بعد ({p ? p.n_dates : 0} تاريخاً مُعنوناً). أوّل عائلة إشارات غير سعريّة؛ يظهر IC-ه بعد أسابيع من التعنون.</div>;
            })()}
          </div>
          <div className="mz-learn-c">
            <div className="mz-learn-t">🏁 أقوى وصفة (المشروط بالنظام · 20ي)</div>
            {(() => {
              const c = cc && cc.candidates && cc.candidates.adaptive && cc.candidates.adaptive.h && cc.candidates.adaptive.h["20"];
              if (!c || c.top_excess == null) return <div className="mz-empty">…يحسب</div>;
              return <div className="mz-learn-d">فائض القمّة <b>{num(c.top_excess, 3)}%</b> · دلالة t=<b style={{ color: (c.top_t || 0) >= 2 ? POS : WARN }}>{c.top_t != null ? num(c.top_t, 2) : "—"}</b> على {c.n_dates} تاريخاً.<br /><span className="ql-dim">فائض اختيار قبل التكاليف — لا يعني ربحاً بعدها (آلة الزمن تحكم).</span></div>;
            })()}
          </div>
        </div>
        <div className="mz-note ql-dim">هذه اللوحة للمتابعة فقط — كلّ رقم فيها بحثيّ ظلّيّ. لا وصفة ولا عامل يدخل التسجيل الحيّ إلا بعد أن يعبر آلة الزمن بعد التكاليف، وبقرارك أنت.</div>
      </Panel>

      <Panel title="🎰 دفتر المضاربة السريعة — الحلم مقاساً (ورقيّ 100%)" cls="mz-core-ledger"
        right={<button className="mz-btn" style={{ maxWidth: 150 }} onClick={startSpec}>▶ شغّل دورة</button>}>
        <div className="mz-verdict" style={{ borderColor: WARN, color: "var(--text-secondary)", marginBottom: 12, fontWeight: 600 }}>
يقيس حلم «10-20% أسبوعيّاً» <b>بقواعد روس كاميرون</b>: انتقاء الأسهم الرخيصة (≤${spec && spec.config ? spec.config.max_price : 20}) عالية الحجم النسبيّ (RVOL≥{spec && spec.config ? spec.config.min_rvol : 2})، <b>ويدخل فقط عند طبع نمط علم صاعد / قمّة مسطّحة على شمعة الدقيقة</b> — بوقفٍ عند دعم النمط وهدفٍ 2:1، وبيع النصف عند 1R ونقل الوقف للتعادل. أسعار حيّة وانزلاق على الطرفين. <b>محاكاة 100% — لا مال ولا أمر حقيقيّ.</b> <span style={{ color: WARN }}>قيد أمين: شموع الدقيقة من IEX (جزء من الحجم) — دقيقة للأسماء السائلة؛ لا Level 2، والفلوت مرشّح ليّن (بياناته متقطّعة).</span>
        </div>
        {spec ? (<div>
          <div className="mz-pain-out">
            <div><span>العائد الأسبوعيّ المُركَّب</span><b style={{ color: (spec.weekly_rate_pct || 0) >= 0 ? POS : NEG, fontSize: 20 }}>{spec.weekly_rate_pct != null ? pct(spec.weekly_rate_pct) : "…"}</b></div>
            <div><span>الهدف (الحلم)</span><b style={{ color: WARN }}>10-20%</b></div>
            <div><span>نسبة الفوز</span><b>{spec.win_rate != null ? spec.win_rate + "%" : "—"}</b></div>
            <div><span>صفقات مغلقة</span><b>{spec.closed_n || 0}{spec.days_running ? " · " + spec.days_running + "ي" : ""}</b></div>
            <div><span>متوسّط الرابحة</span><b style={{ color: POS }}>{spec.avg_win_pct != null ? pct(spec.avg_win_pct) : "—"}</b></div>
            <div><span>متوسّط الخاسرة</span><b style={{ color: NEG }}>{spec.avg_loss_pct != null ? pct(spec.avg_loss_pct) : "—"}</b></div>
          </div>
          {(spec.open || []).length ? (
            <table className="mz-tbl mz-tbl-wide" style={{ marginTop: 10 }}><thead><tr><th className="tl">الرمز</th><th>الدخول</th><th>الحاليّ</th><th>غير محقّق %</th><th>ساعات</th></tr></thead>
              <tbody>{spec.open.slice(0, 10).map((p, i) => (<tr key={i}><td className="tl mz-fn">{p.symbol}</td><td>{money(p.entry)}</td><td>{money(p.current)}</td><td style={{ color: (p.upl_pct || 0) >= 0 ? POS : NEG }}>{pct(p.upl_pct)}</td><td className="mz-dim2">{p.hold_hours}س</td></tr>))}</tbody></table>
          ) : <div className="mz-empty" style={{ marginTop: 8 }}>{spec.closed_n ? "لا مراكز مفتوحة الآن" : "لم تبدأ الدورة بعد — تعمل تلقائيّاً عند فتح السوق، أو اضغط «شغّل دورة»."}</div>}
          {spec.exit_reasons && Object.keys(spec.exit_reasons).length ? (
            <div className="mz-note ql-dim">أسباب الخروج: {Object.entries(spec.exit_reasons).map(([k, v]) => ({ tp: "ربح", sl: "وقف", time: "يوم تالٍ", "?": "؟" }[k] || k) + " " + v).join(" · ")}</div>
          ) : null}
          <div className="mz-note ql-dim">قارن «العائد الأسبوعيّ المُركَّب» بالهدف 10-20%. توقّعي الصادق: صفقات مفردة مبهرة، محصّلة قاسية. الحكم للأرقام لا للحماس. ظلّيّ بحت — لا يتداول مالاً.</div>
        </div>) : <div className="mz-empty">…يحمّل دفتر المضاربة</div>}
        {specMsg && <div className="mz-note" style={{ color: specMsg === "تعذّر" ? NEG : POS }}>{specMsg}</div>}
      </Panel>
    </div>
  );
}

function DayTradingView() {
  const [tick, setTick] = useState(0);
  const dt = useGet("/api/screener/daytrade?limit=60&_t=" + tick);
  const [spec, setSpec] = useState(null);
  const [specMsg, setSpecMsg] = useState("");
  const [sortK, setSortK] = useState("explosion_score");
  const loadSpec = () => fetch("/api/speculation-ledger").then(r => r.json()).then(setSpec).catch(() => {});
  useEffect(() => { loadSpec(); }, []);
  const runSpec = async () => {
    if (!window.confirm("تشغيل دورة مضاربة ورقيّة الآن (قواعد كاميرون)؟ محاكاة 100% — لا مال ولا أمر حقيقيّ.")) return;
    setSpecMsg("…يشغّل الدورة");
    try { const r = await fetch("/api/speculation-ledger/tick", { method: "POST" }).then(x => x.json());
      setSpecMsg(r.message || "بدأ"); setTimeout(loadSpec, 9000); setTimeout(loadSpec, 22000); } catch (e) { setSpecMsg("تعذّر"); }
  };
  const rows = (dt && dt.results) || [];
  const scanning = dt && dt.status === "scanning";
  const cfg = spec && spec.config;
  const passCam = r => cfg ? (r.price <= cfg.max_price && (r.rvol || 0) >= cfg.min_rvol && (r.explosion_score || 0) >= cfg.min_score) : false;
  const COLS = [["explosion_score", "الانفجار"], ["rvol", "الحجم النسبيّ"], ["gap_pct", "الفجوة%"], ["momentum_pct", "زخم 3ي%"], ["change_pct", "التغيّر%"], ["price", "السعر"]];
  const sorted = [...rows].sort((a, b) => Math.abs(b[sortK] || 0) - Math.abs(a[sortK] || 0));
  const whyOf = r => { const c = r.components || {}; const e = Object.entries({ "حجم": c.rvol, "زخم": c.momentum, "تقلّب": c.volatility, "فجوة": c.gap }).filter(x => x[1] != null); e.sort((a, b) => b[1] - a[1]); return e.length ? e[0][0] : "—"; };

  return (
    <div className="mz-view mz-wrap-full">
      <div className="mz-ana-top">
        <div className="mz-crumb">⚡ <b>التداول اليومي</b> — بحث في كلّ الأسهم (حلال وغير حلال) · اختبارات ونتائج</div>
        <button className="mz-btn" style={{ maxWidth: 150 }} onClick={() => setTick(t => t + 1)}>↻ إعادة المسح</button>
      </div>
      <div className="mz-verdict" style={{ borderColor: WARN, color: "var(--text-secondary)", marginBottom: 14, fontWeight: 600 }}>
        🔬 بحثيّ بحت: يمسح ~2500 سهماً (S&P + Russell + المتداوَلة النشطة) — <b>حلال وغير حلال</b> — لقياس الانفجار (حجم نسبيّ + زخم + تقلّب + فجوة). ليس توصية ولا دخول دفتر. <b>الشراء الحقيقيّ يبقى حلالاً محجوباً</b> — هذه الصفحة للبحث والقياس فقط.
      </div>

      <Panel title="🎰 نتائج اختبار المضاربة (قواعد روس كاميرون · ورقيّ)" right={<button className="mz-btn" style={{ maxWidth: 130 }} onClick={runSpec}>▶ شغّل دورة</button>}>
        {spec ? (<div>
          <div className="mz-pain-out">
            <div><span>العائد الأسبوعيّ المُركَّب</span><b style={{ color: (spec.weekly_rate_pct || 0) >= 0 ? POS : NEG, fontSize: 20 }}>{spec.weekly_rate_pct != null ? pct(spec.weekly_rate_pct) : "…لم ينضج"}</b></div>
            <div><span>الهدف (الحلم)</span><b style={{ color: WARN }}>10-20%</b></div>
            <div><span>نسبة الفوز</span><b>{spec.win_rate != null ? spec.win_rate + "%" : "—"}</b></div>
            <div><span>صفقات مغلقة</span><b>{spec.closed_n || 0}{spec.days_running ? " · " + spec.days_running + "ي" : ""}</b></div>
            <div><span>متوسّط الرابحة</span><b style={{ color: POS }}>{spec.avg_win_pct != null ? pct(spec.avg_win_pct) : "—"}</b></div>
            <div><span>متوسّط الخاسرة</span><b style={{ color: NEG }}>{spec.avg_loss_pct != null ? pct(spec.avg_loss_pct) : "—"}</b></div>
          </div>
          {cfg ? <div className="mz-note ql-dim">قواعد كاميرون: سعر ≤${cfg.max_price} · RVOL≥{cfg.min_rvol} · {cfg.cameron_patterns ? "دخول عند نمط علم صاعد/قمّة مسطّحة على شمعة الدقيقة، وقف عند دعم النمط، هدف 2:1" : "ربح +" + cfg.tp_pct + "%/وقف −" + cfg.sl_pct + "% (2:1)"}{cfg.scale_out ? " · بيع النصف عند 1R + وقف تعادل" : ""}. قيد أمين: شموع IEX (جزء من الحجم)، لا Level 2، والفلوت مرشّح ليّن.</div> : null}
          {(spec.open || []).length ? (
            <table className="mz-tbl mz-tbl-wide" style={{ marginTop: 8 }}><thead><tr><th className="tl">مركز مفتوح</th><th>النمط</th><th>الدخول</th><th>الحاليّ</th><th>غير محقّق %</th><th>ساعات</th></tr></thead>
              <tbody>{spec.open.slice(0, 8).map((p, i) => (<tr key={i}><td className="tl mz-fn">{p.symbol}</td><td className="mz-dim2">{p.pattern === "flat_top" ? "قمّة مسطّحة" : p.pattern === "bull_flag" ? "علم صاعد" : (p.pattern || "—")}{p.scaled ? " ½" : ""}</td><td>{money(p.entry)}</td><td>{money(p.current)}</td><td style={{ color: (p.upl_pct || 0) >= 0 ? POS : NEG }}>{pct(p.upl_pct)}</td><td className="mz-dim2">{p.hold_hours}س</td></tr>))}</tbody></table>
          ) : null}
        </div>) : <div className="mz-empty">…يحمّل نتائج المضاربة</div>}
        {specMsg && <div className="mz-note" style={{ color: specMsg === "تعذّر" ? NEG : POS }}>{specMsg}</div>}
      </Panel>

      <Panel title={"الأسهم المرشّحة — تقييم الانفجار" + (rows.length ? " (" + rows.length + ")" : "")}
        right={<div className="mz-sort">{COLS.map(c => <button key={c[0]} className={"mz-basket-t" + (sortK === c[0] ? " on" : "")} style={sortK === c[0] ? { color: ACC, borderColor: ACC } : {}} onClick={() => setSortK(c[0])}>{c[1]}</button>)}</div>}>
        {scanning ? <div className="mz-empty">…يمسح ~2500 سهماً (قد يستغرق دقيقة — اضغط «إعادة المسح» بعد قليل)</div>
          : rows.length ? (
            <table className="mz-tbl mz-tbl-wide"><thead><tr><th className="tl">الرمز</th><th>السعر</th><th>التغيّر%</th><th>💥 انفجار</th><th>RVOL</th><th>فجوة%</th><th>زخم3ي%</th><th>لماذا؟</th><th>شرعي</th><th>كاميرون</th></tr></thead>
              <tbody>{sorted.map((r, i) => (
                <tr key={i} className="mz-prow" onClick={() => goAnalyze(r.symbol, "")}>
                  <td className="tl mz-fn">{r.symbol}</td>
                  <td>{money(r.price)}</td>
                  <td style={{ color: (r.change_pct || 0) >= 0 ? POS : NEG }}>{pct(r.change_pct)}</td>
                  <td className="mz-fn" style={{ color: (r.explosion_score || 0) >= 70 ? POS : (r.explosion_score || 0) >= 55 ? WARN : MUT }}>{num(r.explosion_score, 0)}</td>
                  <td>{num(r.rvol, 1)}×</td>
                  <td style={{ color: (r.gap_pct || 0) >= 0 ? POS : NEG }}>{pct(r.gap_pct)}</td>
                  <td style={{ color: (r.momentum_pct || 0) >= 0 ? POS : NEG }}>{pct(r.momentum_pct)}</td>
                  <td><span className="mz-why">{whyOf(r)}</span></td>
                  <td>{r.is_halal ? <span style={{ color: POS }}>✓</span> : <span className="mz-dim2">—</span>}</td>
                  <td>{passCam(r) ? <span style={{ color: ACC, fontWeight: 700 }}>★</span> : <span className="mz-dim2">·</span>}</td>
                </tr>))}</tbody></table>
          ) : <div className="mz-empty">لا نتائج بعد — اضغط «إعادة المسح».</div>}
        <div className="mz-note ql-dim">الترتيب بالانفجار: حجم نسبيّ (RVOL) + زخم + تقلّب + فجوة على شموع يوميّة. ★ كاميرون = يمرّ فلتر المضاربة (سعر/حجم/درجة). انقر أيّ صفّ للتحليل. بحث وقياس — لا توصية.</div>
      </Panel>
    </div>
  );
}

function ReportsView() {
  const ov = useGet("/api/v1/overview");
  const sq = useGet("/api/selection-quality");
  const alpha = useGet("/api/alpha-curve");
  const port = (ov && ov.portfolio) || {};
  const now = new Date();
  const rows = [
    ["قيمة المحفظة", port.equity != null ? money(port.equity) : (port.portfolio_value != null ? money(port.portfolio_value) : "—")],
    ["ربح/خسارة اليوم", port.daily_pnl_pct != null ? pct(port.daily_pnl_pct, 2) : "—"],
    ["المراكز المفتوحة", port.open_positions != null ? port.open_positions : "—"],
    ["نوع الحساب", (port.account_type || "ورقي") + " · " + (port.broker_type || "—")],
    ["ألفا أسبوعي (t)", sq && sq.alpha ? num(sq.alpha.t_stat != null ? sq.alpha.t_stat : sq.alpha.weekly_alpha, 2) : "—"],
    ["ألفا تراكمي", alpha && alpha.cumulative_alpha_pct != null ? pct(alpha.cumulative_alpha_pct, 1) : "—"],
    ["حارس فرط التخصيص (PBO)", sq && sq.pbo && sq.pbo.pbo != null ? num(sq.pbo.pbo, 2) : "—"],
    ["عتبة الدخول MIN_RS", sq && sq.gate && sq.gate.min_rs != null ? num(sq.gate.min_rs, 1) : "—"],
  ];
  return (<div className="mz-view">
    <div className="mz-ana-top">
      <div className="mz-crumb">الرئيسية / <b>التقارير</b></div>
      <button className="mz-btn gold" style={{ maxWidth: 190 }} onClick={() => window.print()}>🖨️ تصدير PDF (طباعة)</button>
    </div>
    <div id="mz-print">
      <Panel title={"ملخّص أداء MIZAN — " + now.toLocaleDateString("ar-EG", { day: "numeric", month: "long", year: "numeric" })}>
        <table className="mz-tbl mz-tbl-wide"><tbody>
          {rows.map(([l, v], i) => (<tr key={i}><td className="tl mz-dim2">{l}</td><td className="tl mz-fn">{v}</td></tr>))}
        </tbody></table>
        <div className="mz-note ql-dim">تقرير ورقي/قياسي — لا صفقات حقيقية. زرّ التصدير يفتح حوار الطباعة (احفظ كـ PDF).</div>
      </Panel>
    </div>
    <Panel title="تقارير تفصيلية">
      <div className="mz-reports">
        <button className="mz-rep" onClick={() => location.hash = "lab"}>📊 مختبر الاستراتيجية — العوامل والباك-تيست</button>
        <button className="mz-rep" onClick={() => location.hash = "portfolio"}>📈 المحفظة — الأداء والمراكز</button>
        <button className="mz-rep" onClick={() => location.hash = "factors"}>🧮 العوامل — IC متعدّد الآفاق</button>
      </div>
    </Panel>
  </div>);
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

function ForecastCone({ stats, p0, w = 660, h = 240 }) {
  if (!stats || stats.length < 2 || p0 == null) return <div className="mz-empty">…يحسب مسار التوقّع (مونت-كارلو)</div>;
  const lo = Math.min(...stats.map(s => s.percentile_5), p0), hi = Math.max(...stats.map(s => s.percentile_95), p0), rg = (hi - lo) || 1;
  const n = stats.length, pad = 34;
  const X = (i) => pad + (i / n) * (w - pad - 6);
  const Y = (v) => 10 + (1 - (v - lo) / rg) * (h - 34);
  const pts = [{ x: X(0), med: p0, p5: p0, p95: p0, p25: p0, p75: p0 }].concat(stats.map((s, i) => ({ x: X(i + 1), med: s.median_price, p5: s.percentile_5, p95: s.percentile_95, p25: s.percentile_25, p75: s.percentile_75 })));
  const band = (a, b) => pts.map(p => `${p.x},${Y(p[a])}`).join(" ") + " " + pts.slice().reverse().map(p => `${p.x},${Y(p[b])}`).join(" ");
  const medLine = pts.map((p, i) => `${i ? "L" : "M"}${p.x},${Y(p.med)}`).join(" ");
  const gl = [hi, (hi + lo) / 2, lo];
  return (<svg width={w} height={h} style={{ width: "100%" }} viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none">
    {gl.map((v, i) => <g key={i}><line x1={pad} y1={Y(v)} x2={w - 6} y2={Y(v)} stroke="var(--border-subtle)" strokeWidth="0.6" strokeDasharray="2 3" /><text x="2" y={Y(v) + 3} fontSize="9" fill="var(--text-muted)">{Math.round(v)}</text></g>)}
    <polygon points={band("p95", "p5")} fill={ACC} opacity="0.10" />
    <polygon points={band("p75", "p25")} fill={ACC} opacity="0.20" />
    <path d={medLine} fill="none" stroke={ACC} strokeWidth="2" />
    <line x1={X(0)} y1={10} x2={X(0)} y2={h - 24} stroke="var(--text-muted)" strokeWidth="0.6" strokeDasharray="2 2" />
    <text x={X(0) + 3} y={h - 12} fontSize="9" fill="var(--text-muted)">اليوم</text>
    <text x={w - 6} y={h - 12} fontSize="9" fill="var(--text-muted)" textAnchor="end">+{n} يوم</text>
  </svg>);
}

const RANGES = [["1D", "5d"], ["1W", "5d"], ["1M", "1mo"], ["3M", "3mo"], ["6M", "6mo"], ["1Y", "1y"], ["2Y", "2y"], ["5Y", "5y"]];

function StockAnalysisView() {
  const dp = useGet("/api/screener/deep-picks?limit=200");
  const rows = (dp && dp.results) || [];
  const sym0 = (() => { try { return sessionStorage.getItem("mz_analyze_sym") || "AAPL"; } catch (e) { return "AAPL"; } })();
  const intent0 = (() => { try { return sessionStorage.getItem("mz_analyze_intent") || ""; } catch (e) { return ""; } })();
  const [sym, setSym] = useState(sym0);
  const [inp, setInp] = useState(sym0);
  const [range, setRange] = useState("6mo");
  const [atab, setAtab] = useState(intent0 === "forecast" ? "forecast" : "overview");
  const [tradeMsg, setTradeMsg] = useState(intent0 === "trade" ? "راجع الخطّة أدناه ثم اضغط «صفقة ورقية»." : "");
  useEffect(() => { try { sessionStorage.removeItem("mz_analyze_intent"); } catch (e) { } }, []);
  const plan = useGet("/api/v1/trade/plan?symbol=" + sym);
  const chart = useGet("/api/stock/chart?symbol=" + sym + "&range=" + range);
  const mc = useGet((atab === "forecast" || atab === "risk") ? "/api/monte-carlo?symbol=" + sym + "&forecast_days=30&n_simulations=300" : null);
  const mcStats = (mc && mc.day_stats) || [], mcSum = (mc && mc.summary) || {};
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
  const entry = plan && (plan.entry || plan.strategy_entry || plan.price) || px;
  const stop = plan && (plan.stop_loss || plan.strategy_stop);
  const tp = plan && (plan.tp1 || plan.strategy_tp1);
  const shares = plan && plan.shares;
  const canTrade = entry > 0 && stop > 0 && tp > 0 && shares > 0 && (pick.is_halal || (plan && plan.halal === "halal"));
  const sendPaper = async () => {
    if (!canTrade) { setTradeMsg("الخطّة غير مكتملة أو غير متوافقة شرعاً — لا يمكن الإرسال."); return; }
    if (!window.confirm("إرسال صفقة ورقيّة إلى IBKR paper؟\n" + sym + " · شراء " + shares + " سهم\nدخول " + money(entry) + " · وقف " + money(stop) + " · هدف " + money(tp) + "\n(حساب ورقيّ — لا أموال حقيقيّة · لن يُنفَّذ إلا بموافقتك الآن)")) return;
    setTradeMsg("…يُرسل الأمر إلى الوسيط الورقي");
    try {
      const r = await fetch("/api/v1/broker/execute", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ symbol: sym, side: "buy", entry_price: entry, stop_loss: stop, take_profit: tp, shares, confidence: (plan && plan.strategy_confidence) || 0 }) }).then(x => x.json());
      if (r.success) setTradeMsg("✓ أُرسلت الصفقة الورقيّة — رقم الأمر " + (r.order_id || r.broker_id || "—"));
      else setTradeMsg("لم تُرسَل: " + ({ halal_blocked: "محجوبة شرعاً", broker_offline: "الوسيط الورقي غير متّصل", broker_error: "خطأ لدى الوسيط" }[r.reason] || r.reason || "سبب غير معروف"));
    } catch (e) { setTradeMsg("تعذّر الإرسال — تحقّق من اتّصال الوسيط الورقي."); }
  };
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
        <div className="mz-sym-pick">
          <input className="mz-inp" value={inp} onChange={e => setInp(e.target.value)} onKeyDown={e => e.key === "Enter" && go()} placeholder="رمز السهم…" />
          <button className="mz-btn gold" style={{ maxWidth: 60 }} onClick={go}>تحليل</button>
          <button className="mz-btn" style={{ maxWidth: 120, opacity: canTrade ? 1 : 0.5 }} disabled={!canTrade} title={canTrade ? "أمر ورقي على IBKR paper — بتأكيدك" : "غير متاح: الخطّة ناقصة أو غير متوافقة شرعاً"} onClick={sendPaper}>💼 صفقة ورقيّة</button>
        </div>
      </div>
      {tradeMsg && <div className="mz-note" style={{ color: tradeMsg.startsWith("✓") ? POS : tradeMsg.startsWith("لم") || tradeMsg.startsWith("تعذّر") || tradeMsg.startsWith("الخطّة") ? NEG : MUT, marginBottom: 8 }}>{tradeMsg}</div>}

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

          <div className="mz-tabs" style={{ marginBottom: 12 }}>{[["overview", "نظرة عامة"], ["forecast", "التوقّعات"], ["risk", "المخاطر"]].map(([k, l]) => <button key={k} className={"mz-tab" + (atab === k ? " on" : "")} onClick={() => setAtab(k)}>{l}</button>)}</div>

          {atab === "overview" && <React.Fragment>
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
          </React.Fragment>}

          {atab === "forecast" && <React.Fragment>
          <Panel title="مسار التوقّع — مونت-كارلو (٣٠ يوم · ٣٠٠ محاكاة · GBM)">
            <ForecastCone stats={mcStats} p0={mcSum.initial_price != null ? mcSum.initial_price : px} />
            <div className="mz-fc-leg"><span><i style={{ background: ACC, opacity: 0.2 }} />النطاق ٢٥–٧٥٪</span><span><i style={{ background: ACC, opacity: 0.1 }} />النطاق ٥–٩٥٪</span><span><i style={{ background: ACC }} />الوسيط</span></div>
          </Panel>
          <div className="mz-ana-metrics">
            {[["السعر المتوقّع (٣٠ي)", mcSum.expected_terminal_price != null ? money(mcSum.expected_terminal_price) : "…", POS],
            ["احتمال الربح", mcSum.prob_profit != null ? Math.round(mcSum.prob_profit * 100) + "%" : "…", (mcSum.prob_profit || 0) >= 0.5 ? POS : NEG],
            ["الانجراف السنوي", mcSum.annualized_drift != null ? pct(mcSum.annualized_drift * 100, 1) : "…", (mcSum.annualized_drift || 0) >= 0 ? POS : NEG],
            ["التقلّب السنوي", mcSum.annualized_volatility != null ? pct(mcSum.annualized_volatility * 100, 1) : "…", WARN],
            ["VaR ٩٥٪ (طرفي)", mcSum.terminal_var_95 != null ? "-" + num(mcSum.terminal_var_95 * 100, 1) + "%" : "…", NEG]].map(([l, v, c], i) => (
              <div className="mz-am" key={i}><div className="mz-am-l">{l}</div><div className="mz-am-v" style={{ color: c }}>{v}</div></div>))}
          </div>
          <div className="mz-note" style={{ marginTop: 10 }}>نموذج GBM ثابت (انجراف/تقلّب مقدّران من التاريخ) — تصوّر للمخاطر لا توصية. الانجراف قد يُقلَّص عبر MC_DRIFT_SHRINK للمعايرة.</div>
          </React.Fragment>}

          {atab === "risk" && <React.Fragment>
          <div className="mz-ana-metrics">
            {[["مستوى المخاطرة", pm.risk === "high" ? "مرتفع" : pm.risk === "low" ? "منخفض" : "متوسط", pm.risk === "high" ? NEG : pm.risk === "low" ? POS : WARN],
            ["عائد:مخاطرة", plan && plan.rr_ratio != null ? num(plan.rr_ratio, 1) : "—", "inherit"],
            ["وقف كارثي", plan && plan.catastrophe_stop_pct != null ? "-" + num(plan.catastrophe_stop_pct, 0) + "%" : "—", NEG],
            ["VaR ٩٥٪ (٣٠ي)", mcSum.terminal_var_95 != null ? "-" + num(mcSum.terminal_var_95 * 100, 1) + "%" : "…", NEG],
            ["CVaR ٩٥٪ (٣٠ي)", mcSum.terminal_cvar_95 != null ? "-" + num(mcSum.terminal_cvar_95 * 100, 1) + "%" : "…", NEG]].map(([l, v, c], i) => (
              <div className="mz-am" key={i}><div className="mz-am-l">{l}</div><div className="mz-am-v" style={{ color: c }}>{v}</div></div>))}
          </div>
          <Panel title="تحليل ما بعد الوفاة (Pre-mortem) — لماذا قد تفشل الصفقة">
            <div className="mz-keys">
              {(pm.flags || []).map((f, i) => <div className="mz-key risk" key={i}>⚠ {f}</div>)}
              {!(pm.flags || []).length && <div className="mz-empty">لا مخاطر بارزة مُحدّدة</div>}
            </div>
            {pm.method && <div className="mz-note" style={{ marginTop: 8 }}>المصدر: {pm.method === "llm" ? "نموذج لغوي" : pm.method}</div>}
          </Panel>
          </React.Fragment>}
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

function timeAgo(ts) {
  const s = Math.max(0, Math.floor(Date.now() / 1000 - (ts || 0)));
  if (s < 60) return "الآن";
  if (s < 3600) return "قبل " + Math.floor(s / 60) + " د";
  if (s < 86400) return "قبل " + Math.floor(s / 3600) + " س";
  return "قبل " + Math.floor(s / 86400) + " ي";
}

function AlertsView() {
  const [st, setSt] = useState(null);
  const [thr, setThr] = useState(80);
  const load = () => fetch("/api/scan-alerts").then(r => r.json()).then(setSt).catch(() => {});
  useEffect(() => { load(); }, []);
  useEffect(() => { fetch("/api/scan-alerts/seen", { method: "POST" }).catch(() => {}); }, []); // opening the tab = read
  const rules = (st && st.rules) || [], events = (st && st.events) || [];
  const addRule = async (rule) => { try { const r = await fetch("/api/scan-alerts/rule", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(rule) }).then(x => x.json()); setSt(s => ({ ...(s || {}), rules: r.rules })); } catch (e) { } };
  const delRule = async (id) => { try { const r = await fetch("/api/scan-alerts/remove", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id }) }).then(x => x.json()); setSt(s => ({ ...(s || {}), rules: r.rules })); } catch (e) { } };
  const hasSB = rules.some(r => r.type === "new_strong_buy");
  return (
    <div className="mz-view">
      <Panel title="قواعد التنبيه" right={<span className="mz-dim3">تُقيَّم مع كل مسح · قياسيّة فقط</span>}>
        <div className="mz-al-rules">
          {rules.map(r => (<div className="mz-al-rule" key={r.id}>
            <span>🔔 {r.label}</span><button className="mz-rg" onClick={() => delRule(r.id)}>✕</button>
          </div>))}
          {!rules.length && <div className="mz-empty">لا قواعد — أضِف قاعدة أدناه.</div>}
        </div>
        <div className="mz-al-add">
          {!hasSB && <button className="mz-rg on" onClick={() => addRule({ type: "new_strong_buy" })}>+ سهم جديد «شراء قوي»</button>}
          <span className="mz-al-thr">درجة ≥ <input type="number" className="mz-inp" style={{ width: 66 }} min="0" max="100" value={thr} onChange={e => setThr(+e.target.value)} />
            <button className="mz-rg on" onClick={() => addRule({ type: "score_above", threshold: thr })}>+ أضِف</button></span>
        </div>
        <div className="mz-note ql-dim">ينطلق التنبيه مرّة حين يَدخل سهم الشرط (لا يتكرّر كل مسح). لا صفقات — إشعار فقط.</div>
      </Panel>
      <Panel title="التنبيهات الأخيرة" right={<button className="mz-rg" onClick={load}>↻ تحديث</button>}>
        {events.length ? (<div className="mz-al-events">
          {events.map((e, i) => (<div className="mz-al-ev" key={i}>
            <span className="mz-al-sym" onClick={() => goAnalyze(e.symbol, "")}>{e.symbol}</span>
            <span className="mz-score" style={{ color: (e.score || 0) >= 72 ? POS : WARN }}>{e.score}</span>
            <span className="mz-al-txt">{e.rule}</span>
            <span className="mz-al-sec mz-dim2">{e.sector || ""}</span>
            <span className="mz-al-t mz-dim2">{timeAgo(e.ts)}</span>
          </div>))}
        </div>) : <div className="mz-empty">لا تنبيهات بعد. تظهر هنا حين يَدخل سهمٌ أحد شروطك في المسح التالي.</div>}
      </Panel>
    </div>
  );
}

function Stub({ label }) {
  return <div className="mz-stub"><div className="mz-stub-ic">🧭</div><div className="mz-stub-t">{label}</div>
    <div className="mz-stub-s">هذا القسم قيد الإعداد ضمن الهيكلة الجديدة — يُبنى ببيانات حقيقية (لا بيانات وهمية).</div></div>;
}

// ── الوكيل الذكي — floating AI agent. Sees the WHOLE platform via POST /agent/chat
//    (analyze/halal/scanners + the paper ledgers, speculation, research-edge & basket tools).
//    Self-contained: inline styles + one scoped <style>, inline SVG (no FontAwesome). ────────
function agentMd(text) {
  const inline = (s) => String(s).split(/(\*\*[^*]+\*\*)/g).map((p, j) =>
    (p.startsWith("**") && p.endsWith("**")) ? <b key={j}>{p.slice(2, -2)}</b> : p);
  const lines = String(text || "").split("\n"); const out = []; let list = null, k = 0;
  const isSep = (s) => /-/.test(s) && /^[\s|:\-]+$/.test(s);
  const cells = (s) => s.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map(c => c.trim());
  const flush = () => { if (list) { out.push(<ul key={"u" + k++} style={{ margin: "4px 0", paddingInlineStart: 18 }}>{list.map((x, j) => <li key={j}>{inline(x)}</li>)}</ul>); list = null; } };
  for (let i = 0; i < lines.length; i++) {
    const t = lines[i].trim();
    if (t.startsWith("|") && i + 1 < lines.length && isSep(lines[i + 1].trim())) {
      flush(); const head = cells(t); i += 2; const rows = [];
      while (i < lines.length && lines[i].trim().startsWith("|")) { rows.push(cells(lines[i])); i++; } i--;
      out.push(<div key={"tw" + k++} style={{ overflowX: "auto" }}><table className="mz-ai-tbl"><thead><tr>{head.map((h, j) => <th key={j}>{inline(h)}</th>)}</tr></thead><tbody>{rows.map((r, ri) => <tr key={ri}>{r.map((c, ci) => <td key={ci}>{inline(c)}</td>)}</tr>)}</tbody></table></div>);
      continue;
    }
    if (/^#{1,6}\s/.test(t)) { flush(); out.push(<div key={"h" + k++} style={{ fontWeight: 700, margin: "6px 0 2px", color: "var(--accent)" }}>{inline(t.replace(/^#{1,6}\s/, ""))}</div>); continue; }
    if (/^[-*]\s/.test(t)) { (list = list || []).push(t.replace(/^[-*]\s/, "")); continue; }
    if (t === "") { flush(); continue; }
    flush(); out.push(<p key={"p" + k++} style={{ margin: "3px 0" }}>{inline(t)}</p>);
  }
  flush(); return <div className="mz-ai-md">{out}</div>;
}

const AGENT_CHIPS = [
  { l: "أفضل الفرص الحلال", q: "ما أفضل الفرص الحلال اليوم من الماسحَين معاً — الأسبوعي (get_buy_signals) والشهري (get_deep_picks)؟ ادمجهما وميّز مصدر كل سهم، ثم حلّل الأقوى بإيجاز مع الدخول/الوقف/الأهداف. اذكر القيود المقاسة ولا تختلق أرقاماً." },
  { l: "محفظة النواة — من يفوز؟", q: "أرِني حالة المحافظ الورقيّة الثلاث (النواة/القمر/المستكشف) وسباق الـNAV بينها ومعايير التخرّج — أيّها يتقدّم؟ استخدم get_paper_portfolios وذكّر أنها ورقيّة بلا مال حقيقيّ." },
  { l: "دفتر المضاربة اليوميّة", q: "ما حالة دفتر المضاربة اليوميّة (روس كاميرون) — المراكز المفتوحة وأنماطها ونسبة الفوز والعائد الأسبوعيّ؟ استخدم get_speculation_ledger وذكّر أنها محاكاة ورقيّة عالية المخاطرة وغير مُثبتة." },
  { l: "هل لدينا أفضليّة فعليّة؟", q: "هل انتقاؤنا للأسهم يتفوّق على السوق فعلاً؟ اعرض قياس الأفضليّة عبر get_research_edge — السباق النسبيّ للسوق وجودة الانتقاء، مع القيمة الإحصائية t وحجم العيّنة، ووضّح أنّ العيّنة الصغيرة إرشاديّة لا إثبات." },
  { l: "حلّل سهماً", q: "حلّل AAPL بالكامل عبر analyze_stock: الحلال، الفنّي، الأساسيّات، المحلّلون، المطّلعون، الأرباح، نظام السوق والأخبار، مع الدخول/الوقف/الأهداف الثلاثة. اذكر ما تؤكّده الأدوات فقط." },
  { l: "حالة السوق", q: "ما حالة السوق الآن (النظام/VIX/الائتمان/الاتّساع)؟ بإيجاز." },
];

function MizanAgent() {
  const [open, setOpen] = useState(false);
  const [msgs, setMsgs] = useState([{ role: "ai", text: "السلام عليكم، أنا **وكيل ميزان الذكي**. أرى كامل بيانات المنصّة — الماسحات، الدفاتر الورقيّة، دفتر المضاربة، وقياس الأفضليّة. اسألني عن سهم أو أداء أو حلال أو السوق.", tools: [] }]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const convId = useRef(null);
  const endRef = useRef(null);
  useEffect(() => { if (endRef.current) endRef.current.scrollIntoView({ behavior: "smooth" }); }, [msgs, open]);
  const send = async (text) => {
    const q = (text != null ? text : input).trim();
    if (!q || busy) return;
    setInput(""); setMsgs(m => [...m, { role: "user", text: q }]); setBusy(true);
    try {
      const r = await fetch("/agent/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message: q, conversation_id: convId.current }) });
      const j = await r.json();
      if (j && j.error) setMsgs(m => [...m, { role: "ai", text: "تعذّر: " + j.error, tools: [] }]);
      else { if (j.conversation_id) convId.current = j.conversation_id; setMsgs(m => [...m, { role: "ai", text: j.response || "—", tools: j.tools_used || [], model: j.model }]); }
    } catch (e) { setMsgs(m => [...m, { role: "ai", text: "خطأ في الشبكة — حاول ثانية.", tools: [] }]); }
    finally { setBusy(false); }
  };
  const Spark = () => (<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M12 3l1.9 4.6L18.5 9.5 13.9 11.4 12 16l-1.9-4.6L5.5 9.5l4.6-1.9z" fill="currentColor" stroke="none"/><path d="M18 14l.8 2 2 .8-2 .8L18 20l-.8-2-2-.8 2-.8z" fill="currentColor" stroke="none"/></svg>);
  const Close = () => (<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>);
  const Send = () => (<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M22 2L11 13M22 2l-7 20-4-9-9-4z"/></svg>);
  return (
    <div dir="rtl" style={{ position: "fixed", insetInlineStart: 20, bottom: 20, zIndex: 9000 }}>
      <style>{`
        @keyframes mzAiPulse{0%{box-shadow:0 0 0 0 var(--accent-dim)}70%{box-shadow:0 0 0 14px rgba(245,166,35,0)}100%{box-shadow:0 0 0 0 rgba(245,166,35,0)}}
        @keyframes mzAiUp{from{opacity:0;transform:translateY(12px)}to{opacity:1;transform:none}}
        .mz-ai-md p:first-child{margin-top:0}
        .mz-ai-tbl{border-collapse:collapse;width:100%;font-size:12.5px;margin:6px 0}
        .mz-ai-tbl th,.mz-ai-tbl td{border:1px solid var(--border-subtle);padding:6px 10px;text-align:start;white-space:nowrap}
        .mz-ai-tbl th{background:var(--bg-raised);color:var(--text-secondary);font-weight:600}
        .mz-ai-chip{background:var(--bg-raised);border:1px solid var(--border);color:var(--text-secondary);border-radius:999px;padding:5px 11px;font-size:11.5px;cursor:pointer;white-space:nowrap;transition:.15s}
        .mz-ai-chip:hover:not(:disabled){border-color:var(--accent);color:var(--accent)}
        .mz-ai-chip:disabled{opacity:.5;cursor:default}
      `}</style>
      {open && (
        <div style={{ position: "absolute", insetInlineStart: 0, bottom: 68, width: "min(860px, calc(100vw - 32px), max(360px, 46vw))", height: "min(900px, calc(100vh - 96px))", display: "flex", flexDirection: "column", background: "var(--bg-surface)", border: "1px solid var(--border)", borderRadius: 16, boxShadow: "var(--shadow-pop)", overflow: "hidden", animation: "mzAiUp .18s ease" }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "11px 14px", borderBottom: "1px solid var(--border-subtle)", background: "linear-gradient(180deg,var(--accent-dim),transparent)" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span style={{ color: "var(--accent)", display: "flex" }}><Spark /></span>
              <div><div style={{ fontWeight: 700, fontSize: 13.5, color: "var(--text-primary)" }}>وكيل ميزان الذكي</div>
                <div style={{ fontSize: 10.5, color: "var(--text-muted)" }}>يرى كل بيانات المنصّة · يحلّل ولا ينفّذ</div></div>
            </div>
            <button onClick={() => setOpen(false)} title="إغلاق" style={{ background: "none", border: "none", color: "var(--text-muted)", cursor: "pointer", display: "flex", padding: 4 }}><Close /></button>
          </div>
          <div style={{ flex: 1, overflowY: "auto", padding: "12px 12px", display: "flex", flexDirection: "column", gap: 10 }}>
            {msgs.map((m, i) => (
              <div key={i} style={{ alignSelf: m.role === "user" ? "flex-start" : "stretch", maxWidth: m.role === "user" ? "85%" : "100%" }}>
                <div style={{ fontSize: 13.5, lineHeight: 1.65, color: "var(--text-primary)", background: m.role === "user" ? "var(--accent-dim)" : "var(--bg-panel)", border: "1px solid " + (m.role === "user" ? "transparent" : "var(--border-subtle)"), borderRadius: 12, padding: "9px 13px" }}>
                  {m.role === "ai" ? agentMd(m.text) : m.text}
                </div>
                {m.tools && m.tools.length > 0 && <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 3, paddingInlineStart: 2 }}>🔧 {m.tools.join(" · ")}</div>}
              </div>
            ))}
            {busy && <div style={{ fontSize: 12, color: "var(--accent)", fontStyle: "italic" }}>يفكّر ويستدعي الأدوات…</div>}
            <div ref={endRef} />
          </div>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap", padding: "8px 12px", borderTop: "1px solid var(--border-subtle)" }}>
            {AGENT_CHIPS.map((c, i) => <button key={i} className="mz-ai-chip" disabled={busy} onClick={() => send(c.q)}>{c.l}</button>)}
          </div>
          <div style={{ display: "flex", gap: 7, padding: "10px 12px", borderTop: "1px solid var(--border-subtle)", alignItems: "center" }}>
            <input value={input} disabled={busy} placeholder="اسأل عن سهم، أداء، حلال، السوق…" onChange={e => setInput(e.target.value)} onKeyDown={e => { if (e.key === "Enter") send(); }}
              style={{ flex: 1, background: "var(--bg-raised)", border: "1px solid var(--border)", borderRadius: 10, color: "var(--text-primary)", padding: "9px 11px", fontSize: 12.5, fontFamily: "inherit", outline: "none" }} />
            <button onClick={() => send()} disabled={busy || !input.trim()} title="إرسال" style={{ background: "var(--accent)", border: "none", borderRadius: 10, color: "#1a1206", cursor: busy || !input.trim() ? "default" : "pointer", opacity: busy || !input.trim() ? 0.5 : 1, display: "flex", padding: "9px 11px" }}><Send /></button>
          </div>
          <div style={{ fontSize: 10, color: "var(--text-muted)", textAlign: "center", padding: "0 12px 9px" }}>إرشاديّ — إشارات كمّية مقاسة، ليست نصيحة مرخّصة. القرار لك.</div>
        </div>
      )}
      <button onClick={() => setOpen(o => !o)} title="وكيل ميزان الذكي" aria-label="MizanAI"
        style={{ width: 56, height: 56, borderRadius: "50%", border: "1px solid var(--accent)", background: "linear-gradient(145deg,var(--accent),#c8791a)", color: "#1a1206", cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "center", boxShadow: "var(--shadow-pop)", animation: open ? "none" : "mzAiPulse 2.6s infinite" }}>
        {open ? <Close /> : <Spark />}
      </button>
    </div>
  );
}

function MizanTerminal() {
  const [view, setView] = useState((location.hash || "").replace(/^#/, "") || "overview");
  const [clock, setClock] = useState("--:--:--");
  const [broker, setBroker] = useState(null);
  const [shared, setShared] = useState({});
  const [unread, setUnread] = useState(0);
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
    const pollAlerts = () => fetch("/api/scan-alerts").then(r => r.json()).then(a => setUnread(a && a.unread || 0)).catch(() => {});
    pollAlerts();
    const at = setInterval(pollAlerts, 60000);
    return () => { clearInterval(t); clearInterval(at); };
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
            <span className="mz-bell" style={{ cursor: "pointer" }} title="تنبيهات المسح" onClick={() => { location.hash = "alerts"; }}>🔔{unread > 0 && <span className="mz-bell-b">{unread > 9 ? "9+" : unread}</span>}</span>
            <button className="mz-lang" title="العربية / English" onClick={() => { if (window.__mizanToggleLang) window.__mizanToggleLang(); }}>{(window.__mizanLang && window.__mizanLang() === "en") ? "ع" : "EN"}</button>
            <span className="mz-theme">🌙</span>
          </div>
        </header>
        <div className="mz-body">
          {view === "overview" ? <div className="mz-ov-wrap"><Overview /><RightRail {...shared} /></div>
            : view === "screener" ? <ScreenerView />
              : view === "daytrade" ? <DayTradingView />
              : view === "analysis" ? <StockAnalysisView />
              : view === "core" ? <CorePortfolioView />
              : view === "lab" ? <LabView />
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
      <MizanAgent />
    </div>
  );
}
ReactDOM.createRoot(document.getElementById("root")).render(<MizanTerminal />);
