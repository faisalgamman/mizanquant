// KellySizing.jsx — Continuous Kelly panel (f* + fractional + per-strategy)

function KellySizing({ kelly, strategies, fractional, setFractional }) {
  const fStarPct = (kelly.fStar * 100).toFixed(1);
  const effPct   = (kelly.effective * 100).toFixed(2);
  const capPct   = (kelly.staticCap * 100).toFixed(1);
  const maxPct   = (kelly.maxKelly * 100).toFixed(0);
  const markPct  = (kelly.fStar / kelly.maxKelly) * 100;
  const capMarkPct = (kelly.staticCap / kelly.maxKelly) * 100;

  return (
    <div className="rd-panel">
      <div className="rd-panel-head">
        <div>
          <div className="rd-panel-title">Kelly position sizing</div>
          <div className="rd-panel-sub">Continuous Kelly · sample-size shrinkage · fractional</div>
        </div>
        <span className="badge b-accent" style={{ background: "var(--accent-dim)", color: "var(--accent)", padding: "2px 7px", borderRadius: 3, fontSize: 9, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.3px" }}>
          Live · Chan Ch.7
        </span>
      </div>

      {/* f* + cap visualization */}
      <div className="kelly-head">
        <div className="kelly-fstar">
          <div className="lab">Raw Kelly · f*</div>
          <div className="val">{fStarPct}%</div>
          <div className="formula">f* = mean(r) / var(r)</div>
        </div>
        <div className="kelly-cap">
          <div className="lab">Effective risk budget</div>
          <div className="kelly-bar-wrap">
            <div className="kelly-bar-fill"></div>
            <div className="kelly-bar-cap"   style={{ left: capMarkPct + "%" }}></div>
            <div className="kelly-bar-mark"  style={{ left: markPct + "%" }}></div>
          </div>
          <div className="kelly-bar-foot">
            <span>0</span>
            <span style={{ color: "var(--accent)", fontWeight: 700 }}>f*·frac = {effPct}%</span>
            <span>static cap {capPct}%</span>
            <span>max {maxPct}%</span>
          </div>
        </div>
      </div>

      {/* Controls */}
      <div className="kelly-controls">
        <div className="kc-item">
          <div className="lab">Fractional Kelly</div>
          <div className="val" style={{ color: "var(--accent)" }}>{(fractional * 100).toFixed(0)}% <span style={{ color: "var(--text-muted)", fontSize: 10, fontWeight: 500 }}>· {fractional === 0.5 ? "half" : fractional === 1 ? "full" : fractional === 0.25 ? "quarter" : "custom"}</span></div>
          <input type="range" min="0.10" max="1.00" step="0.05" value={fractional} onChange={(e) => setFractional(Number(e.target.value))} />
        </div>
        <div className="kc-item">
          <div className="lab">Min trades · activation</div>
          <div className="val">{kelly.enabledAfterTrades}</div>
          <div style={{ fontSize: 9, color: "var(--text-muted)", marginTop: 4 }}>Strategy needs ≥ N trades before Kelly engages.</div>
        </div>
        <div className="kc-item">
          <div className="lab">Hard ceiling · max_kelly</div>
          <div className="val">{maxPct}%</div>
          <div style={{ fontSize: 9, color: "var(--text-muted)", marginTop: 4 }}>Built-in safety cap. Never sized above.</div>
        </div>
      </div>

      {/* Per-strategy breakdown */}
      <table className="kelly-strats">
        <thead>
          <tr>
            <th>Strategy</th>
            <th style={{ textAlign: "right" }}>Trades</th>
            <th style={{ textAlign: "right" }}>μ</th>
            <th style={{ textAlign: "right" }}>σ²</th>
            <th style={{ textAlign: "right" }}>f Kelly</th>
            <th style={{ textAlign: "right" }}>f used</th>
          </tr>
        </thead>
        <tbody>
          {strategies.map((s) => (
            <tr key={s.name} className={s.shrunk ? "shrunk" : ""}>
              <td><span className="mono">{s.name}</span></td>
              <td className="mono" style={{ textAlign: "right" }}>{s.trades}</td>
              <td className="mono" style={{ textAlign: "right" }}>{s.mean.toFixed(2)}%</td>
              <td className="mono" style={{ textAlign: "right" }}>{s.var.toFixed(2)}</td>
              <td className="mono" style={{ textAlign: "right", color: s.shrunk ? "var(--text-muted)" : "var(--text-primary)" }}>{(s.fKelly * 100).toFixed(2)}%</td>
              <td className="mono" style={{ textAlign: "right", fontWeight: 700, color: s.shrunk ? "var(--text-muted)" : "var(--accent)" }}>{(s.fUsed * 100).toFixed(2)}%</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

Object.assign(window, { KellySizing });
