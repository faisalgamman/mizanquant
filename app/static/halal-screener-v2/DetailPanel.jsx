// DetailPanel.jsx — side-panel breakdown for a selected symbol

function DetailPanel({ row, onClose }) {
  if (!row) return null;
  const allPass = row.screens.every(Boolean);
  const screenValues = [
    { name: "Debt / Market Cap",      val: row.debt.toFixed(1) + "%",     limit: 33, value: row.debt },
    { name: "Interest Income / Rev",  val: row.interest.toFixed(1) + "%", limit:  5, value: row.interest },
    { name: "Cash & AR / Market Cap", val: row.cashRecv.toFixed(1) + "%", limit: 33, value: row.cashRecv },
    { name: "Haram industry",         val: row.haramFlag ? "Flagged" : "Clean", limit: null, value: row.haramFlag ? 1 : 0 },
  ];
  return (
    <>
      <div className="detail-overlay" onClick={onClose}></div>
      <aside className="detail-panel">
        <div className="detail-head">
          <div>
            <div className="sym">{row.sym}</div>
            <div className="name">{row.name}</div>
            <div style={{ marginTop: 8 }}>
              <span className={"halal-cell " + (allPass ? "halal-pass" : "halal-fail")}>
                {allPass ? <i className="fas fa-check" style={{ fontSize: 8 }}></i> : <i className="fas fa-xmark" style={{ fontSize: 8 }}></i>}
                {allPass ? "AAOIFI compliant" : "AAOIFI fail"}
              </span>
            </div>
          </div>
          <button className="detail-close" onClick={onClose}>
            <i className="fas fa-xmark" style={{ fontSize: 12 }}></i>
          </button>
        </div>

        <div className="detail-section-title">AAOIFI four-screen breakdown</div>
        {screenValues.map((s, i) => {
          const pass = row.screens[i];
          return (
            <div key={s.name} className={"detail-screen-row " + (pass ? "pass" : "fail")}>
              <div>
                <div className="detail-screen-name">{i + 1}. {s.name}</div>
                {s.limit != null && (
                  <div style={{ fontSize: 9, color: "var(--text-muted)", marginTop: 3, fontFamily: "var(--font-mono)" }}>
                    threshold &lt; {s.limit}%
                  </div>
                )}
              </div>
              <div className="detail-screen-val" style={{ color: pass ? "var(--positive)" : "var(--negative)" }}>
                {s.val}
              </div>
            </div>
          );
        })}

        <div className="detail-section-title">Fundamentals</div>
        <div className="detail-grid">
          <div className="detail-kv"><span className="l">Market cap</span><span className="v">${(row.mcap / 1e9).toFixed(0)}B</span></div>
          <div className="detail-kv"><span className="l">Sector</span><span className="v">{row.sector}</span></div>
          <div className="detail-kv"><span className="l">RSI (14)</span><span className="v">{row.rsi}</span></div>
          <div className="detail-kv"><span className="l">ADX</span><span className="v">{row.adx}</span></div>
          <div className="detail-kv"><span className="l">Vol ratio</span><span className="v">{row.vol}%</span></div>
          <div className="detail-kv"><span className="l">Change</span><span className="v" style={{ color: row.chg >= 0 ? "var(--positive)" : "var(--negative)" }}>{fmtPct(row.chg)}</span></div>
        </div>

        <div className="detail-section-title">Smart-screener verdict</div>
        <div style={{ background: "var(--bg-raised)", border: "1px solid var(--border-subtle)", borderRadius: "var(--radius-md)", padding: "12px 14px" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
            <span style={{ fontSize: 11, color: "var(--text-muted)" }}>Score</span>
            <span className="mono" style={{ fontSize: 22, fontWeight: 800, color: scoreColor(row.score) }}>{row.score}</span>
          </div>
          <div style={{ display: "flex", justifyContent: "space-between", marginTop: 8, fontSize: 11 }}>
            <span style={{ color: "var(--text-muted)" }}>Signal</span>
            <span style={{ fontFamily: "var(--font-mono)", fontWeight: 700, color: row.signal.includes("BUY") ? "var(--positive)" : row.signal === "WAIT" ? "var(--warning)" : "var(--negative)" }}>{row.signal}</span>
          </div>
          <div style={{ display: "flex", justifyContent: "space-between", marginTop: 4, fontSize: 11 }}>
            <span style={{ color: "var(--text-muted)" }}>Strategy</span>
            <span className="mono" style={{ fontWeight: 600 }}>{row.strategy}</span>
          </div>
        </div>

        <button className="detail-cta" disabled={!allPass}>
          <i className="fas fa-paper-plane" style={{ marginRight: 6 }}></i>
          {allPass ? "Send to paper trade" : "Blocked — AAOIFI fail"}
        </button>
        <div style={{ marginTop: 10, fontSize: 9, color: "var(--text-muted)", textAlign: "center" }}>
          Verified halal (AAOIFI) · purification est. $0.00 / share
        </div>
      </aside>
    </>
  );
}

Object.assign(window, { DetailPanel });
