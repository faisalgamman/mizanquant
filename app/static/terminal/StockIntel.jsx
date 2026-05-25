// StockIntel.jsx — Stock Intelligence (deep-picks composite) + Track Record.
// Ported into the terminal so /terminal is the single dashboard.
// Data: GET /api/screener/deep-picks (composite T/F/S/AI/Halal) + GET /signals/accuracy.
// No fabricated data — real values or honest "—".

function TrackRecordStrip({ overall }) {
  if (!overall) {
    return (
      <div className="si-track" style={{ color: "var(--text-muted)" }}>
        <i className="fas fa-clock" style={{ marginRight: 6 }}></i>
        Track record building — evaluated signals appear after they mature (~5d). No fabricated stats.
      </div>
    );
  }
  const wr  = overall["Win Rate %"];
  const avg = overall["Avg Return %"];
  const pf  = overall["Profit Factor"];
  const tot = overall["Total Signals"];
  const wrColor  = wr >= 55 ? "var(--positive)" : wr >= 45 ? "var(--warning)" : "var(--negative)";
  const avgColor = (avg ?? 0) >= 0 ? "var(--positive)" : "var(--negative)";
  const cell = (label, val, color) => (
    <span><span style={{ color: "var(--text-muted)" }}>{label}</span>
      <strong style={{ color: color || "var(--text-primary)", marginLeft: 5 }}>{val}</strong></span>
  );
  return (
    <div className="si-track">
      <span style={{ color: "var(--accent)", fontWeight: 700, letterSpacing: 0.5 }}>TRACK RECORD · 30d</span>
      {cell("Win rate", (wr ?? 0) + "%", wrColor)}
      {cell("Avg return", ((avg ?? 0) >= 0 ? "+" : "") + (avg ?? 0) + "%", avgColor)}
      {cell("Profit factor", pf ?? "—")}
      {cell("Evaluated", tot ?? 0)}
      <span style={{ marginLeft: "auto", color: "var(--text-muted)", fontSize: 9 }}>real outcomes · matured signals only</span>
    </div>
  );
}

const SI_ETF_TO_SECTOR = {
  XLK: "Technology", XLF: "Financials", XLV: "Health Care", XLE: "Energy",
  XLI: "Industrials", XLP: "Consumer Staples", XLU: "Utilities",
  XLB: "Materials", XLRE: "Real Estate", XLC: "Communication Services",
  XLY: "Consumer Discretionary",
};

function StockIntel() {
  const [data, setData]         = useState(null);
  const [overall, setOverall]   = useState(null);
  const [scanning, setScanning] = useState(false);
  const [filter, setFilter]     = useState("all");
  const [sort, setSort]         = useState({ key: "composite_score", dir: -1 });
  const [riskStatus, setRiskStatus] = useState(null);
  const [sectorRank, setSectorRank] = useState({});

  const loadPicks = async (force) => {
    try {
      const d = await (await fetch(`/api/screener/deep-picks?limit=15&use_cache=${force ? "false" : "true"}`)).json();
      setData(d);
      return d && d.results && d.results.length > 0;
    } catch (_) { setData({ status: "error" }); return false; }
  };

  useEffect(() => {
    loadPicks(false);
    (async () => {
      try {
        const rows = await (await fetch("/signals/accuracy?period=30")).json();
        setOverall(Array.isArray(rows) ? rows.find(r => r && r.Source === "OVERALL") : null);
      } catch (_) {}
      try {
        const rs = await (await fetch("/api/risk/status")).json();
        setRiskStatus(rs || {});
      } catch (_) {}
      try {
        const rot = await (await fetch("/api/chart/rotation")).json();
        const map = {};
        ((rot && rot.data) || []).forEach(d => {
          const name = SI_ETF_TO_SECTOR[(d.symbol || d.ticker || "").toUpperCase()];
          const q = (d.quadrant || d.classification || "").toLowerCase();
          if (name && q) map[name] = q;
        });
        setSectorRank(map);
      } catch (_) {}
    })();
  }, []);

  const onScan = async () => {
    if (scanning) return;
    setScanning(true);
    fetch("/api/stock/smart-screener?use_cache=false&max_results=50").catch(() => {});
    let tries = 0;
    const poll = setInterval(async () => {
      tries += 1;
      const ok = await loadPicks(true);
      if (ok || tries >= 12) { clearInterval(poll); setScanning(false); }
    }, 15000);
  };

  const setSortKey = (key) => {
    setSort(s => s.key === key ? { key, dir: -s.dir } : { key, dir: key === "symbol" ? 1 : -1 });
  };

  const results = (data && Array.isArray(data.results)) ? data.results : [];
  const computing = !data || data.status === "scanning" || (scanning && results.length === 0);

  // filter + sort
  let rows = results.slice();
  if (filter === "strong") rows = rows.filter(r => r.signal_composite === "STRONG BUY");
  if (filter === "buy")    rows = rows.filter(r => ["STRONG BUY", "BUY"].includes(r.signal_composite));
  const sk = sort.key, sd = sort.dir;
  rows.sort((a, b) => {
    let av = a[sk], bv = b[sk];
    if (sk === "symbol") return sd * String(av || "").localeCompare(String(bv || ""));
    av = (av == null ? -Infinity : Number(av));
    bv = (bv == null ? -Infinity : Number(bv));
    return sd * (av - bv);
  });

  const posture = (riskStatus || {}).risk_posture || null;
  const riskOff = posture === "risk_off";
  const secRankMap = sectorRank || {};

  const caret = (key) => sort.key === key ? (sort.dir < 0 ? " ▾" : " ▴") : "";
  const sigColor = (sig) => /STRONG BUY/.test(sig) ? "var(--positive)" : /BUY/.test(sig) ? "var(--positive)"
    : /WATCH/.test(sig) ? "var(--warning)" : "var(--negative)";
  const gradeColor = (g) => g === "A" ? "var(--positive)" : g === "B" ? "var(--accent)" : g === "C" ? "var(--warning)" : "var(--negative)";

  return (
    <div className="wf-section si-section">
      <div className="wf-head">
        <span className="wf-title"><i className="fas fa-satellite-dish" style={{ marginRight: 6, color: "var(--accent)" }}></i>Stock Intelligence</span>
        <span className="wf-sub">
          {data && data.total != null ? `${data.total} picks · ${data.scanned || 0} scanned` : "composite · T/F/S/AI/Halal"}
        </span>
      </div>

      <div className="si-toolbar">
        {["all", "strong", "buy"].map(f => (
          <button key={f} className={"si-filter" + (filter === f ? " active" : "")} onClick={() => setFilter(f)}>
            {f === "all" ? "All" : f === "strong" ? "Strong Buy" : "Buy"}
          </button>
        ))}
        <button className="si-scan" onClick={onScan} disabled={scanning}>
          <i className={"fas " + (scanning ? "fa-circle-notch spin" : "fa-brain")}></i> {scanning ? "Scanning…" : "Scan"}
        </button>
      </div>

      <TrackRecordStrip overall={overall} />

      {computing ? (
        <div className="si-empty">
          <div className="spin" style={{ width: 24, height: 24, border: "3px solid var(--border)", borderTopColor: "var(--accent)", borderRadius: "50%", margin: "0 auto 10px" }}></div>
          Smart screener initializing — click <b>Scan</b> (1–2 min for ~650 symbols).
        </div>
      ) : rows.length === 0 ? (
        <div className="si-empty">No picks match this filter.</div>
      ) : (
        <div className="si-table-wrap">
          <table className="si-table">
            <thead>
              <tr>
                <th>#</th>
                <th onClick={() => setSortKey("symbol")} style={{ cursor: "pointer" }}>Symbol{caret("symbol")}</th>
                <th onClick={() => setSortKey("composite_score")} style={{ cursor: "pointer" }}>Score{caret("composite_score")}</th>
                <th onClick={() => setSortKey("score_tech")} style={{ cursor: "pointer" }} title="Technical">T{caret("score_tech")}</th>
                <th onClick={() => setSortKey("score_fund")} style={{ cursor: "pointer" }} title="Fundamental">F{caret("score_fund")}</th>
                <th title="Sentiment">S</th>
                <th onClick={() => setSortKey("score_ai")} style={{ cursor: "pointer" }} title="AI/ML">AI{caret("score_ai")}</th>
                <th>Grade</th>
                <th>Analyst</th>
                <th onClick={() => setSortKey("price")} style={{ cursor: "pointer" }}>Price{caret("price")}</th>
                <th onClick={() => setSortKey("change_pct")} style={{ cursor: "pointer" }}>Chg{caret("change_pct")}</th>
                <th>Signal</th>
                <th>Sector</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => {
                const comp = r.composite_score ?? 0;
                const cColor = comp >= 70 ? "var(--positive)" : comp >= 50 ? "var(--accent)" : comp >= 35 ? "var(--warning)" : "var(--negative)";
                const chg = r.change_pct ?? 0;
                const upside = r.analyst_upside;
                const upStr = (upside != null && upside !== 0) ? ((upside > 0 ? "+" : "") + upside.toFixed(1) + "%") : "—";
                const rating = r.analyst_rating ? r.analyst_rating.replace(/_/g, " ").toUpperCase() : "—";
                const sQuad = secRankMap[r.sector] || null;
                const sLead = sQuad === "leading" || sQuad === "improving";
                const sLag  = sQuad === "lagging" || sQuad === "weakening";
                const isBuy = ["STRONG BUY", "BUY"].includes(r.signal_composite);
                return (
                  <tr key={r.symbol + i} onClick={() => window.selectIntelSymbol && window.selectIntelSymbol(r.symbol)} style={{ cursor: "pointer" }}>
                    <td className="si-rank">{i + 1}</td>
                    <td><b>{r.symbol}</b><div className="si-co">{r.company || ""}</div></td>
                    <td>
                      <div className="si-gauge">
                        <span style={{ color: cColor, fontWeight: 700 }}>{comp}</span>
                        <div className="si-gauge-bar"><div style={{ width: comp + "%", background: cColor }}></div></div>
                      </div>
                    </td>
                    <td style={{ color: "#60a5fa", fontFamily: "var(--font-mono)" }}>{r.score_tech ?? 0}</td>
                    <td style={{ color: "#c8963e", fontFamily: "var(--font-mono)" }}>{r.score_fund ?? 0}</td>
                    <td style={{ color: r.score_sentiment == null ? "var(--text-muted)" : "#a78bfa", fontFamily: "var(--font-mono)" }}>{r.score_sentiment ?? "—"}</td>
                    <td style={{ color: "#34d399", fontFamily: "var(--font-mono)" }}>{r.score_ai ?? 0}</td>
                    <td><span style={{ color: gradeColor(r.f_grade), fontWeight: 700 }}>{r.f_grade || "D"}</span></td>
                    <td>
                      <span style={{ fontSize: 11, color: (upside != null && upside > 10) ? "var(--positive)" : (upside != null && upside < -5) ? "var(--negative)" : "var(--text-secondary)" }}>{upStr}</span>
                      <div style={{ fontSize: 9, color: "var(--text-muted)" }}>{rating}</div>
                    </td>
                    <td style={{ fontFamily: "var(--font-mono)" }}>${(r.price ?? 0).toFixed(2)}</td>
                    <td style={{ fontFamily: "var(--font-mono)", color: chg >= 0 ? "var(--positive)" : "var(--negative)" }}>{(chg >= 0 ? "+" : "") + chg.toFixed(2)}%</td>
                    <td>
                      <span className="si-sig" style={{ color: sigColor(r.signal_composite || "WATCH") }}>
                        {(r.signal_composite || "WATCH").replace("STRONG BUY", "SB")}
                      </span>
                      {riskOff && isBuy ? <span title="Posture risk-off — reduced conviction" style={{ color: "var(--warning)", fontSize: 9, marginLeft: 3 }}>⚠</span> : null}
                    </td>
                    <td style={{ color: "var(--text-muted)", fontSize: 10 }}>
                      {r.sector || "—"}
                      {sQuad ? <span title={"rotation: " + sQuad} style={{ color: sLead ? "var(--positive)" : sLag ? "var(--negative)" : "var(--text-muted)", fontSize: 9, marginLeft: 3 }}>{sLead ? "▲" : sLag ? "▾" : "◆"}</span> : null}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

Object.assign(window, { StockIntel });
