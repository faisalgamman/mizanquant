// PositionRisk.jsx — per-position risk decomposition

function PositionRisk({ positions }) {
  const totalVarContrib = positions.reduce((a, p) => a + p.varContrib, 0);
  const maxVar = Math.max(...positions.map((p) => p.varContrib));
  return (
    <div className="rd-panel">
      <div className="rd-panel-head">
        <div>
          <div className="rd-panel-title">Position risk decomposition</div>
          <div className="rd-panel-sub">Weight · VaR contribution · correlation · beta · max drawdown</div>
        </div>
        <span style={{ fontSize: 10, color: "var(--text-muted)" }}>{positions.length - 1} symbols + cash</span>
      </div>
      <table className="pr-table">
        <thead>
          <tr>
            <th>Symbol</th>
            <th style={{ textAlign: "right" }}>Weight</th>
            <th>VaR contribution</th>
            <th style={{ textAlign: "right" }}>Corr (SPY)</th>
            <th style={{ textAlign: "right" }}>β</th>
            <th style={{ textAlign: "right" }}>Max DD</th>
          </tr>
        </thead>
        <tbody>
          {positions.map((p) => {
            const concentrated = p.varContrib > 20;
            const varColor = concentrated ? "var(--warning)" : "var(--text-primary)";
            return (
              <tr key={p.sym}>
                <td style={{ fontWeight: 600 }}>{p.sym}</td>
                <td className="mono" style={{ textAlign: "right" }}>{p.weight.toFixed(1)}%</td>
                <td>
                  <div className="pr-bar">
                    <div className="pr-bar-fill"><div style={{ width: (p.varContrib / maxVar * 100) + "%", background: concentrated ? "var(--warning)" : "var(--accent)" }}></div></div>
                    <span className="pr-pct" style={{ color: varColor }}>{p.varContrib.toFixed(1)}%</span>
                  </div>
                </td>
                <td className="mono" style={{ textAlign: "right", color: p.corr > 0.6 ? "var(--warning)" : "var(--text-primary)" }}>{p.corr.toFixed(2)}</td>
                <td className="mono" style={{ textAlign: "right" }}>{p.beta.toFixed(2)}</td>
                <td className="mono" style={{ textAlign: "right", color: p.mDD > 5 ? "var(--negative)" : "var(--text-primary)" }}>{p.mDD === 0 ? "—" : "−" + p.mDD.toFixed(1) + "%"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <div style={{ marginTop: 12, fontSize: 10, color: "var(--text-muted)", display: "flex", justifyContent: "space-between" }}>
        <span>Σ VaR contribution: <span className="mono" style={{ color: "var(--text-secondary)" }}>{totalVarContrib.toFixed(1)}%</span></span>
        <span>Concentrated &gt; 20% → <span style={{ color: "var(--warning)" }}>warning</span></span>
      </div>
    </div>
  );
}

Object.assign(window, { PositionRisk });
