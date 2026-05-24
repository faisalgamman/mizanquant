// RiskAlerts.jsx — recent risk events feed

function RiskAlerts({ alerts, onAck }) {
  const iconMap = {
    ok:      "fa-check",
    info:    "fa-info",
    warning: "fa-triangle-exclamation",
    danger:  "fa-circle-exclamation",
  };
  return (
    <div className="rd-panel">
      <div className="rd-panel-head">
        <div>
          <div className="rd-panel-title">Risk alerts</div>
          <div className="rd-panel-sub">Today · {alerts.length} events</div>
        </div>
        <span style={{ fontSize: 10, color: "var(--text-muted)" }}>{alerts.filter((a) => a.kind === "warning" || a.kind === "danger").length} actionable</span>
      </div>
      <div className="alert-list">
        {alerts.map((a, i) => (
          <div key={i} className={"alert-item " + a.kind}>
            <div className="alert-icon"><i className={"fas " + (iconMap[a.kind] || "fa-info")} style={{ fontSize: 10 }}></i></div>
            <div className="alert-text">
              <span className="lab">{a.lab}</span>
              <span className="ts">{a.ts}</span>
            </div>
            <button className="alert-ack" onClick={() => onAck(i)}>Ack</button>
          </div>
        ))}
      </div>
    </div>
  );
}

Object.assign(window, { RiskAlerts });
