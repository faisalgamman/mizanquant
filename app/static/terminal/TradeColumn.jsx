// TradeColumn.jsx — Column 3 of the workflow: portfolio, positions, paper, pipeline, guards, schedule
const { useState } = React;

function PortfolioStrip({ p }) {
  const cells = [
    { l: "Equity",      v: fmt$(p.equity, 0) },
    { l: "Day P&L",     v: (p.dayPnl >= 0 ? "+" : "") + fmt$(p.dayPnl, 2), c: p.dayPnl >= 0 ? "var(--positive)" : "var(--negative)" },
    { l: "Cash",        v: fmt$(p.cash, 0) },
    { l: "Buy power",   v: fmt$(p.buyPower, 0) },
    { l: "Open pos",    v: p.openPos },
    { l: "Today exits", v: p.todayExits },
  ];
  return (
    <div className="pc">
      {cells.map((c) => (
        <div key={c.l} className="pc-row">
          <span className="l">{c.l}</span>
          <span className="v" style={{ color: c.c || "var(--text-primary)" }}>{c.v}</span>
        </div>
      ))}
    </div>
  );
}

function PositionsTable({ positions, onSell }) {
  const [closing, setClosing] = useState(null);
  const doSell = (sym) => {
    if (!window.confirm("بيع كل مركز " + sym + "؟")) return;
    setClosing(sym);
    onSell && onSell(sym).finally(() => setClosing(null));
  };
  return (
    <table className="pos-table">
      <thead><tr><th>SYM</th><th>QTY</th><th>ENTRY</th><th>LAST</th><th>VALUE</th><th>P&L</th><th>P&L%</th><th></th></tr></thead>
      <tbody>
        {positions.map((p) => (
          <tr key={p.sym}
              onClick={() => window.selectIntelSymbol && window.selectIntelSymbol(p.sym)}
              style={{ cursor: "pointer" }}
              title="اعرض البطاقة">
            <td style={{ fontWeight: 600 }}>{p.sym || "—"}</td>
            <td className="mono">{p.qty != null ? p.qty : "—"}</td>
            <td className="mono">{p.entry ? "$" + p.entry.toFixed(2) : "—"}</td>
            <td className="mono">{p.last ? "$" + p.last.toFixed(2) : "—"}</td>
            <td className="mono">{p.mktVal ? "$" + p.mktVal.toFixed(0) : "—"}</td>
            <td className="mono" style={{ textAlign: "right", fontWeight: 600, color: (p.pnl || 0) >= 0 ? "var(--positive)" : "var(--negative)" }}>
              {p.pnl != null ? ((p.pnl >= 0 ? "+" : "") + p.pnl.toFixed(2)) : "—"}
            </td>
            <td className="mono" style={{ textAlign: "right", color: (p.pnlPct || 0) >= 0 ? "var(--positive)" : "var(--negative)" }}>
              {p.pnlPct != null ? ((p.pnlPct >= 0 ? "+" : "") + p.pnlPct.toFixed(1) + "%") : "—"}
            </td>
            <td onClick={(e) => e.stopPropagation()}>
              <button className="pos-sell-btn" disabled={closing === p.sym}
                      onClick={() => doSell(p.sym)} title="بيع">
                {closing === p.sym ? "…" : "✕ بيع"}
              </button>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function PaperTradesList({ trades }) {
  return (
    <div>
      {trades.map((t, i) => (
        <div key={i} className="ptt-row">
          <span className="ptt-sym">{t.sym}</span>
          <span className={"ptt-side " + t.side}>{t.side}</span>
          <span className="ptt-meta mono">${t.entry.toFixed(2)} → ${t.last.toFixed(2)}</span>
          <span className={"ptt-status " + t.status}>{t.status}</span>
          <span className="ptt-pnl" style={{ color: t.pnl >= 0 ? "var(--positive)" : "var(--negative)" }}>
            {(t.pnl >= 0 ? "+" : "") + t.pnl.toFixed(2)}
          </span>
        </div>
      ))}
    </div>
  );
}

function PipelineFlow({ stages, running, dryRun, setDryRun, onRun }) {
  return (
    <>
      <div className="pflow">
        {stages.map((st, i) => (
          <React.Fragment key={st.n}>
            <div className="pflow-stage">
              <div className={"pflow-dot " + st.s}>
                {st.s === "done" ? "✓" : st.s === "running" ? "●" : i + 1}
              </div>
              <div className="pflow-lab">{st.n}</div>
            </div>
            {i < stages.length - 1 && (
              <div className={"pflow-conn" + (st.s === "done" ? " done" : "")}></div>
            )}
          </React.Fragment>
        ))}
      </div>
      <div className="pflow-toolbar">
        <button className="pflow-run" onClick={onRun} disabled={running}>
          <i className={"fas " + (running ? "fa-spinner spin" : "fa-play")}></i>
          {running ? "Running…" : "Run"}
        </button>
        <label style={{ fontSize: 9, color: "var(--text-muted)", display: "flex", alignItems: "center", gap: 4 }}>
          <input type="checkbox" checked={dryRun} onChange={(e) => setDryRun(e.target.checked)} style={{ accentColor: "var(--accent)" }} /> Dry run
        </label>
      </div>
    </>
  );
}

function GuardsList({ guards }) {
  if (!guards || guards.length === 0) {
    return <div style={{ fontSize: 10, color: "var(--text-muted)", padding: "8px 2px" }}>No guard rejections today</div>;
  }
  const max = Math.max(3, ...guards.map((g) => g.hits));
  return (
    <div className="guard-list">
      {guards.map((g) => (
        <div key={g.name} className="guard-row">
          <span className="guard-lab">{g.name}</span>
          <div className="guard-bar"><div style={{ width: (g.hits / max * 100) + "%", background: g.color }}></div></div>
          <span className="guard-ct">{g.hits}</span>
        </div>
      ))}
    </div>
  );
}

function Schedule({ items }) {
  if (!items || items.length === 0) {
    return <div style={{ fontSize: 10, color: "var(--text-muted)", padding: "8px 2px" }}>Schedule loading…</div>;
  }
  return (
    <div>
      {items.map((it) => (
        <div key={it.time} className="sch-row">
          <span className="sch-time">{it.time}</span>
          <span className="sch-lab">{it.label}</span>
        </div>
      ))}
    </div>
  );
}

function TradeColumn(props) {
  const { portfolio, positions, paper, pipeline, running, dryRun, setDryRun, onRunPipeline, guards, schedule, onSell } = props;
  return (
    <div className="col col-trade">
      <div className="wf-section">
        <div className="wf-head"><span className="wf-title">Portfolio</span><span className="wf-sub">Equity · P&L</span></div>
        <PortfolioStrip p={portfolio} />
      </div>
      <div className="wf-section">
        <div className="wf-head"><span className="wf-title">Positions</span><span className="wf-sub">Open · real-time</span></div>
        <PositionsTable positions={positions} onSell={onSell} />
      </div>
      <div className="wf-section">
        <div className="wf-head"><span className="wf-title">Paper</span><span className="wf-sub">recent trades</span></div>
        <PaperTradesList trades={paper} />
      </div>
      <div className="wf-section">
        <div className="wf-head"><span className="wf-title">Pipeline</span><span className="wf-sub">{running ? "running" : "idle"} · {(pipeline || []).length} stages</span></div>
        <PipelineFlow stages={pipeline} running={running} dryRun={dryRun} setDryRun={setDryRun} onRun={onRunPipeline} />
      </div>
      <div className="wf-section">
        <div className="wf-head"><span className="wf-title">Guards</span><span className="wf-sub">Rejections today</span></div>
        <GuardsList guards={guards} />
      </div>
      <div className="wf-section">
        <div className="wf-head"><span className="wf-title">Schedule</span><span className="wf-sub">Daily cycle</span></div>
        <Schedule items={schedule} />
      </div>
    </div>
  );
}

Object.assign(window, { TradeColumn });
