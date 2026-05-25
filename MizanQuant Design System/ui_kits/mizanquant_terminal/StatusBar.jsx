// StatusBar.jsx — fixed footer

function StatusBar({ pipelineRunning }) {
  return (
    <div className="status-bar">
      <div className="sb-side">
        <span className="dot dot-green"></span>
        <span>Broker · Alpaca</span>
        <span className="sep">·</span>
        <span>Auto-trade ON</span>
        <span className="sep">·</span>
        <span>Regime <span style={{ color: "var(--positive)", fontWeight: 600 }}>BULL</span></span>
      </div>
      <div className="sb-side">
        <span>Pipeline <span style={{ color: pipelineRunning ? "var(--accent)" : "var(--text-secondary)" }}>{pipelineRunning ? "running" : "idle"}</span></span>
        <span className="sep">·</span>
        <span>Kill-switch <span style={{ color: "var(--positive)" }}>armed</span></span>
      </div>
    </div>
  );
}

Object.assign(window, { StatusBar });
