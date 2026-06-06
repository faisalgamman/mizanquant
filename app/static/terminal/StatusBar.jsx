// StatusBar.jsx — fixed footer.
// Every value is REAL, from /api/v1/overview .system (auto_trading, kill_switch,
// regime, broker). Nothing here is hardcoded; shows "—" until status loads.

function StatusBar({ pipelineRunning, system }) {
  const brokerRaw = (system && (system.broker_type || system.broker)) || "";
  const broker = brokerRaw ? brokerRaw.charAt(0).toUpperCase() + brokerRaw.slice(1) : "—";

  const at = system && system.auto_trading;
  const autoTrade = at === "enabled" ? "ON" : at === "disabled" ? "OFF" : "—";
  // ON is amber, not green: auto-execution being active is a state to notice.
  const autoColor = at === "enabled" ? "var(--warning)" : "var(--text-secondary)";

  const regime = (system && system.regime) || "—";
  const regimeColor = regime === "BULL" ? "var(--positive)"
                    : regime === "BEAR" ? "var(--negative)"
                    : regime === "NEUTRAL" ? "var(--warning)" : "var(--text-secondary)";

  const killed = !!(system && system.kill_switch);

  return (
    <div className="status-bar">
      <div className="sb-side">
        <span className={"dot " + (broker !== "—" ? "dot-green" : "")}></span>
        <span>Broker · {broker}</span>
        <span className="sep">·</span>
        <span>Auto-trade <span style={{ color: autoColor, fontWeight: 600 }}>{autoTrade}</span></span>
        <span className="sep">·</span>
        <span>Regime <span style={{ color: regimeColor, fontWeight: 600 }}>{regime}</span></span>
      </div>
      <div className="sb-side">
        <span>Pipeline <span style={{ color: pipelineRunning ? "var(--accent)" : "var(--text-secondary)" }}>{pipelineRunning ? "running" : "idle"}</span></span>
        <span className="sep">·</span>
        <span>Kill-switch <span style={{ color: killed ? "var(--negative)" : "var(--text-secondary)", fontWeight: 600 }}>{killed ? "ON" : "off"}</span></span>
      </div>
    </div>
  );
}

Object.assign(window, { StatusBar });
