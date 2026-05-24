// RiskHeader.jsx — page title + risk-level indicator

function RiskHeader({ level }) {
  const config = {
    LOW:    { color: "var(--positive)", label: "Low" },
    MEDIUM: { color: "var(--warning)",  label: "Medium" },
    HIGH:   { color: "var(--negative)", label: "High" },
  };
  const c = config[level] || config.MEDIUM;
  return (
    <header className="rd-header">
      <div className="rd-title">
        <div className="rd-mark">
          <i className="fas fa-shield-halved" style={{ fontSize: 16 }}></i>
        </div>
        <div>
          <h1>Risk desk</h1>
          <div className="sub">Portfolio risk · position sizing · system guards</div>
        </div>
      </div>
      <div className="rd-risk-level">
        <span className="rd-risk-dot pulse" style={{ background: c.color, color: c.color }}></span>
        <div>
          <div className="rd-risk-lab">Aggregate risk</div>
          <div className="rd-risk-val" style={{ color: c.color }}>{c.label}</div>
        </div>
      </div>
    </header>
  );
}

Object.assign(window, { RiskHeader });
