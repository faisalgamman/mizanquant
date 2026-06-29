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
  const { portfolio, positions, onSell } = props;
  const p = portfolio || {};
  const pos = positions || [];
  const pnlC = (p.dayPnl || 0) >= 0 ? "var(--positive)" : "var(--negative)";
  // Aggregate the open book (from the live IBKR positions).
  const totVal = pos.reduce((a, x) => a + (Number(x.mktVal) || 0), 0);
  const totUpl = pos.reduce((a, x) => a + (Number(x.pnl) || 0), 0);
  const dayPct = p.dayPnlPct != null ? ` (${p.dayPnlPct >= 0 ? "+" : ""}${Number(p.dayPnlPct).toFixed(2)}%)` : "";
  const cards = [
    { l: "Equity", v: fmt$(p.equity, 0) },
    { l: "Day P&L", v: (p.dayPnl >= 0 ? "+" : "") + fmt$(p.dayPnl, 2) + dayPct, c: pnlC },
    { l: "Cash", v: fmt$(p.cash, 0) },
    { l: "Buying power", v: fmt$(p.buyPower, 0) },
    { l: "Positions value", v: fmt$(totVal, 0) },
    { l: "Open P&L", v: (totUpl >= 0 ? "+" : "") + fmt$(totUpl, 0), c: totUpl >= 0 ? "var(--positive)" : "var(--negative)" },
    { l: "Open positions", v: p.openPos != null ? p.openPos : pos.length },
  ];
  return (
    <div className="ibkr-cockpit">
      <div className="wf-section">
        <div className="wf-head">
          <span className="wf-title">Interactive Brokers · Paper</span>
          <span className="wf-sub">
            {p.ibkrOffline
              ? <span style={{ color: "var(--negative)" }}>⚠️ IBKR offline — Alpaca fallback · restart the gateway</span>
              : <span style={{ color: "var(--positive)" }}>IBKR ✓ · real-time</span>}
          </span>
        </div>
        <div className="ibkr-strip">
          {cards.map((c) => (
            <div key={c.l} className="ibkr-card">
              <div className="ibkr-card-l">{c.l}</div>
              <div className="ibkr-card-v" style={{ color: c.c || "var(--text-primary)" }}>{c.v}</div>
            </div>
          ))}
        </div>
      </div>
      <div className="wf-section">
        <div className="wf-head"><span className="wf-title">Positions</span><span className="wf-sub">{pos.length} open · real-time · click a row to analyze</span></div>
        {pos.length ? <PositionsTable positions={pos} onSell={onSell} />
          : <div style={{ fontSize: 11, color: "var(--text-muted)", padding: "14px 4px", textAlign: "center" }}>No open positions on the IBKR paper account.</div>}
      </div>
    </div>
  );
}

// OpsBand — Pipeline · Guards · Schedule, pulled out of the tall Trade column
// into a full-width 3-up row so the workflow columns stay balanced (kills the
// black L-void). Reuses the same sub-components as before.
function OpsBand(props) {
  const { pipeline, running, dryRun, setDryRun, onRunPipeline, guards, schedule } = props;
  return (
    <div className="ops-band">
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

Object.assign(window, { TradeColumn, OpsBand });
