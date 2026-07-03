// MizanTerminal.jsx — unified quant terminal shell (sidebar + topbar + view router).
// Phase 1: the shell + a real-data Overview home. Other sections are wired incrementally;
// panels without a real backend show an honest "قيد الإعداد" state (never fake data).
const { useState, useEffect } = React;

const pct = (v, d = 2) => (v == null || isNaN(v)) ? "—" : (v >= 0 ? "+" : "") + Number(v).toFixed(d) + "%";
const num = (v, d = 2) => (v == null || isNaN(v)) ? "—" : Number(v).toFixed(d);
const gcol = (k) => (k === "good" || k === "alpha") ? "var(--positive)" : (k === "warn" || k === "beta") ? "var(--warning)" : (k === "bad" || k === "negative") ? "var(--negative)" : "var(--text-muted)";
const icCol = (v) => v == null ? "var(--text-secondary)" : v > 0.02 ? "var(--positive)" : v < -0.02 ? "var(--negative)" : "var(--text-secondary)";

const NAV = [
  { key: "overview", label: "نظرة عامة", icon: "🏠", group: "" },
  { key: "screener", label: "المسح", icon: "🔍", group: "التحليل", href: "/halal-screener-v2" },
  { key: "analysis", label: "تحليل الأسهم", icon: "📊", group: "التحليل", href: "/terminal#analyze" },
  { key: "factors", label: "العوامل (IC Lab)", icon: "🧬", group: "التحليل", href: "/quant-lab" },
  { key: "lab", label: "مختبر الاستراتيجية", icon: "🧪", group: "التحليل", href: "/quant-lab" },
  { key: "portfolio", label: "المحفظة", icon: "💼", group: "التنفيذ", href: "/terminal" },
  { key: "ledger", label: "الدفتر الورقي", icon: "📒", group: "التنفيذ", href: "/terminal#weekly" },
  { key: "broker", label: "البروكر", icon: "🔌", group: "التنفيذ" },
  { key: "market", label: "السوق", icon: "🌍", group: "المتابعة" },
  { key: "models", label: "الموديلات", icon: "📈", group: "المتابعة" },
  { key: "reports", label: "التقارير", icon: "📄", group: "المتابعة" },
  { key: "alerts", label: "التنبيهات", icon: "🔔", group: "المتابعة" },
  { key: "settings", label: "الإعدادات", icon: "⚙", group: "" },
];

function Panel({ title, tag, right, children, className }) {
  return (
    <div className={"mz-panel " + (className || "")}>
      <div className="mz-panel-h">
        <span className="mz-panel-title">{title}{tag && <span className="mz-tag">{tag}</span>}</span>
        {right}
      </div>
      <div className="mz-panel-b">{children}</div>
    </div>
  );
}

function Sidebar({ view, setView, broker }) {
  let lastGroup = null;
  return (
    <aside className="mz-side">
      <div className="mz-logo"><span className="mz-logo-mark">◈</span><div><div className="mz-logo-t">MIZAN</div><div className="mz-logo-s">QUANT TERMINAL</div></div></div>
      <nav className="mz-nav">
        {NAV.map(n => {
          const grp = n.group && n.group !== lastGroup ? (lastGroup = n.group, <div className="mz-nav-grp" key={"g" + n.key}>{n.group}</div>) : null;
          const active = view === n.key;
          const el = n.href
            ? <a className={"mz-nav-i" + (active ? " active" : "")} href={n.href} key={n.key}><span className="mz-ni-ic">{n.icon}</span>{n.label}</a>
            : <button className={"mz-nav-i" + (active ? " active" : "")} onClick={() => setView(n.key)} key={n.key}><span className="mz-ni-ic">{n.icon}</span>{n.label}</button>;
          return <React.Fragment key={"f" + n.key}>{grp}{el}</React.Fragment>;
        })}
      </nav>
      <div className="mz-side-foot">
        <span className={"mz-dot " + (broker && broker.connected ? "on" : "off")} />
        <div><div className="mz-bk-t">{broker && broker.connected ? "IBKR" : "البروكر"}</div><div className="mz-bk-s">{broker && broker.connected ? "متّصل · LIVE" : "غير متّصل"}</div></div>
      </div>
    </aside>
  );
}

function TopBar({ title, clock }) {
  return (
    <header className="mz-top">
      <div className="mz-top-title">{title}</div>
      <div className="mz-search">🔎 <input placeholder="ابحث عن رمز أو إسم…" /></div>
      <div className="mz-top-r">
        <span className="mz-sys"><span className="mz-dot on" /> النظام يعمل</span>
        <span className="mz-clock">{clock} UTC</span>
      </div>
    </header>
  );
}

// ── Overview home (real data) ────────────────────────────────────────────────
function Overview() {
  const [d, setD] = useState({});
  useEffect(() => {
    let alive = true;
    const g = (u) => fetch(u).then(r => r.json()).catch(() => null);
    Promise.all([
      g("/api/selection-quality"), g("/api/factor-ic-multi"), g("/api/regime-hmm"),
      g("/api/context/bundle"), g("/api/v1/overview"), g("/buys"),
    ]).then(([sq, fic, rg, mk, ov, buys]) => { if (alive) setD({ sq, fic, rg, mk, ov, buys }); });
    return () => { alive = false; };
  }, []);

  const sq = d.sq || {}, ov2 = sq.overlays || {}, est = sq.estimate || {}, gate = sq.gate || {};
  const scanners = Array.isArray(sq.scanners) ? sq.scanners : [];
  const weekly = scanners.find(s => s.scanner === "weekly") || {};
  const monthly = scanners.find(s => s.scanner === "monthly") || {};
  const regime = d.rg && d.rg.regime, bm = (d.rg && d.rg.book_multiplier) || {};
  const mk = d.mk || {}, port = (d.ov && d.ov.portfolio) || {};
  const attr = d.fic && d.fic.attribution && d.fic.attribution.factors;
  const buys = Array.isArray(d.buys) ? d.buys : (d.buys && d.buys.buys) || [];
  const top = buys[0] || null;
  const regState = (mk.spy_regime || "").toUpperCase();
  const regBull = regState.includes("BULL");

  // status cards
  const cards = [
    { l: "حالة السوق", v: regState || "—", s: regBull ? "Risk-On" : (regState.includes("BEAR") ? "Risk-Off" : "Neutral"), c: regBull ? "var(--positive)" : regState.includes("BEAR") ? "var(--negative)" : "var(--warning)", big: true },
    { l: "اتّساع السوق (Breadth)", v: mk.breadth != null ? Math.round(mk.breadth) + "%" : "—", s: "نسبة الصاعد", c: "var(--positive)" },
    { l: "ألفا الاختيار (أسبوعي)", v: pct(weekly.alpha), s: "t " + num(weekly.alpha_t), c: gcol(weekly.color) },
    { l: "حالة الدفتر الورقي", v: (weekly.n || 0) + " صفقة", s: "مقاس مقابل SPY", c: "var(--accent)" },
    { l: "نموذج Meta", v: ov2.meta_status === "trained" ? "AUC " + num(ov2.meta_oos_auc) : "…", s: ov2.meta_trusted ? "موثوق" : "قيد التعلّم", c: ov2.meta_trusted ? "var(--positive)" : "var(--text-muted)" },
  ];
  const strip = [
    ["SPY", mk.spy_price, mk.spy_chg_pct], ["VIX", mk.vix, null], ["Breadth", mk.breadth != null ? mk.breadth + "%" : null, null],
    ["Gold", mk.gold_price, mk.gold_chg_pct], ["Credit", mk.credit, null], ["Liquidity", mk.liquidity, null],
  ];
  const HS = ["10", "20", "5"];

  return (
    <div className="mz-ov">
      <div className="mz-cards">
        {cards.map((c, i) => (
          <div className="mz-card" key={i}>
            <div className="mz-card-l">{c.l}</div>
            <div className={"mz-card-v" + (c.big ? " big" : "")} style={{ color: c.c }}>{c.v}</div>
            <div className="mz-card-s">{c.s}</div>
          </div>
        ))}
      </div>

      <Panel title="نظرة على السوق">
        <div className="mz-strip">
          {strip.map(([k, v, ch], i) => (
            <div className="mz-strip-i" key={i}>
              <div className="mz-strip-k">{k}</div>
              <div className="mz-strip-v">{v == null ? "—" : (typeof v === "number" ? num(v) : v)}</div>
              {ch != null && <div className="mz-strip-c" style={{ color: ch >= 0 ? "var(--positive)" : "var(--negative)" }}>{pct(ch)}</div>}
            </div>
          ))}
        </div>
      </Panel>

      <div className="mz-grid2">
        <Panel title="القوى · IC القوى (IC)" tag="من قاعدة الالتقاط" right={<a className="mz-more" href="/quant-lab">المزيد ←</a>}>
          {attr ? (
            <table className="mz-tbl">
              <thead><tr><th>العامل</th>{HS.map(h => <th key={h}>IC{h}</th>)}<th>الاتجاه</th></tr></thead>
              <tbody>
                {Object.entries(attr).map(([f, v]) => (
                  <tr key={f}>
                    <td className="mz-fn">{f}{f === "mom_12_1" && <span style={{ color: "var(--accent)" }}> ★</span>}</td>
                    {HS.map(h => { const x = (v.h && v.h[h]) || {}; return <td key={h} style={{ color: icCol(x.mean_ic) }}>{x.mean_ic != null ? num(x.mean_ic, 3) : "—"}</td>; })}
                    <td style={{ color: v.direction && v.direction.includes("↑") ? "var(--positive)" : v.direction && v.direction.includes("↓") ? "var(--negative)" : "var(--text-muted)" }}>{v.direction}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : <div className="mz-empty">…يحمّل العوامل</div>}
        </Panel>

        <Panel title="نظام السوق (HMM)" tag="④">
          {regime ? (
            <div>
              {[["هادئ", regime.calm_bull, "var(--positive)"], ["تذبذب", regime.choppy, "var(--warning)"], ["أزمة", regime.crisis, "var(--negative)"]].map(([nm, v, c]) => (
                <div className="mz-rb" key={nm}>
                  <div className="mz-rb-l"><span>{nm}</span><b>{Math.round((v || 0) * 100)}%</b></div>
                  <div className="mz-rb-tr"><div className="mz-rb-f" style={{ width: Math.round((v || 0) * 100) + "%", background: c }} /></div>
                </div>
              ))}
              <div className="mz-note">مضاعِف الدفتر <b>×{num(bm.mult, 2)}</b> · الحالة: <b>{regime.dominant === "calm_bull" ? "هادئ" : regime.dominant === "crisis" ? "أزمة" : "تذبذب"}</b></div>
            </div>
          ) : <div className="mz-empty">…يحسب النظام</div>}
        </Panel>
      </div>

      <div className="mz-grid2">
        <Panel title="أفضل فرصة اليوم">
          {top ? (
            <div className="mz-top-pick">
              <div className="mz-tp-sym">{top.symbol}</div>
              <div className="mz-tp-score" style={{ color: gcol("good") }}>{Math.round(top.swing_score || top.score || 0)}<span>/100</span></div>
              <div className="mz-tp-sig">{top.verdict || top.signal || "—"} · {top.is_halal ? "حلال ✓" : "—"}</div>
            </div>
          ) : <div className="mz-empty">لا فرص مؤكّدة الآن</div>}
          <a className="mz-more" href="/terminal">فتح التفاصيل ←</a>
        </Panel>

        <Panel title="صحّة النظام">
          <div className="mz-health">
            <div><div className="mz-h-v" style={{ color: "var(--positive)" }}>{d.fic && d.fic.status ? (d.fic.status.rows || 0).toLocaleString("en") : "…"}</div><div className="mz-h-l">لقطات مُلتقطة</div></div>
            <div><div className="mz-h-v" style={{ color: ov2.pbo_trust === "high" ? "var(--positive)" : "inherit" }}>{ov2.pbo != null ? num(ov2.pbo) : "…"}</div><div className="mz-h-l">PBO (ثقة)</div></div>
            <div><div className="mz-h-v">{est.rs_ic != null ? num(est.rs_ic, 3) : "…"}</div><div className="mz-h-l">IC القوّة النسبية</div></div>
            <div><div className="mz-h-v" style={{ color: gate.source === "approved" ? "var(--accent)" : "inherit" }}>MIN_RS {num(gate.current_min_rs, 1)}%</div><div className="mz-h-l">عتبة البوّابة</div></div>
          </div>
        </Panel>
      </div>

      <Panel title="آخر الإشارات القوية" right={<a className="mz-more" href="/terminal">الكل ←</a>}>
        {buys.length ? (
          <table className="mz-tbl">
            <thead><tr><th>الرمز</th><th>الإشارة</th><th>الدرجة</th><th>حلال</th></tr></thead>
            <tbody>
              {buys.slice(0, 6).map((b, i) => (
                <tr key={i}>
                  <td className="mz-fn">{b.symbol}</td>
                  <td style={{ color: "var(--positive)" }}>{b.verdict || b.signal || "BUY"}</td>
                  <td>{Math.round(b.swing_score || b.score || 0)}</td>
                  <td>{b.is_halal ? "✓" : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : <div className="mz-empty">…يحمّل الإشارات</div>}
      </Panel>
    </div>
  );
}

function Stub({ label }) {
  return (
    <div className="mz-stub">
      <div className="mz-stub-ic">🧭</div>
      <div className="mz-stub-t">{label}</div>
      <div className="mz-stub-s">هذا القسم قيد الإعداد ضمن الهيكلة الجديدة — يُبنى في المرحلة القادمة ببيانات حقيقية (لا بيانات وهمية).</div>
    </div>
  );
}

function MizanTerminal() {
  const [view, setView] = useState(() => (location.hash || "#overview").slice(1) || "overview");
  const [clock, setClock] = useState("--:--:--");
  const [broker, setBroker] = useState(null);
  useEffect(() => {
    const t = setInterval(() => setClock(new Date().toUTCString().slice(17, 25)), 1000);
    fetch("/api/v1/broker/health").then(r => r.json()).then(setBroker).catch(() => {});
    return () => clearInterval(t);
  }, []);
  useEffect(() => { location.hash = view; }, [view]);

  const cur = NAV.find(n => n.key === view) || NAV[0];
  return (
    <div className="mz">
      <Sidebar view={view} setView={setView} broker={broker} />
      <div className="mz-main">
        <TopBar title={cur.label} clock={clock} />
        <div className="mz-body">
          {view === "overview" ? <Overview /> : <Stub label={cur.label} />}
        </div>
      </div>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<MizanTerminal />);
