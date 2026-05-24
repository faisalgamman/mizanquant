// Sidebar.jsx — left nav, collapsed/expanded on hover

function Sidebar() {
  const sections = [
    { label: "General",    items: mockNav },
    { label: "Forecasting",items: mockNavForecast },
    { label: "Analytics",  items: mockNavAnalytics },
    { label: "Research",   items: mockNavResearch },
  ];
  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="mark">
          <svg viewBox="0 0 64 64" width="20" height="20" aria-hidden="true">
            <circle cx="18" cy="34" r="9.5" fill="none" stroke="currentColor" strokeWidth="3.2" />
            <path d="M27 40 L36 30 L44 36 L54 22" fill="none" stroke="currentColor" strokeWidth="3.2" strokeLinecap="round" strokeLinejoin="round" />
            <circle cx="54" cy="22" r="3" fill="currentColor" />
          </svg>
        </div>
        <div className="txt">
          <h2>mizanquant</h2>
          <small>HALAL TRADING SUITE</small>
        </div>
      </div>
      <nav>
        {sections.map((sec) => (
          <React.Fragment key={sec.label}>
            <div className="nav-section">{sec.label}</div>
            {sec.items.map((n, i) => (
              <a key={n.l + i} className={"nav-item" + (n.active ? " active" : "")}>
                <i className={"fas " + n.i}></i>
                <span>{n.l}</span>
                {n.badge && <span className="nav-badge">{n.badge}</span>}
              </a>
            ))}
          </React.Fragment>
        ))}
      </nav>
      <div className="sb-foot">
        <span className="dot dot-green pulse"></span>
        <div className="sb-foot-txt">
          <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>Connected</div>
          <div style={{ fontSize: 9, color: "var(--text-muted)" }}>alpaca · live</div>
        </div>
      </div>
    </aside>
  );
}

Object.assign(window, { Sidebar });
