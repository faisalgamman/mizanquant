// BlockToggles.jsx — System-wide guard toggles (kill-switch, halt, auto-trade, brokers…)

function BlockToggles({ blocks, onToggle }) {
  return (
    <div className="rd-panel">
      <div className="rd-panel-head">
        <div>
          <div className="rd-panel-title">System blocks</div>
          <div className="rd-panel-sub">Global guards · affect all strategies</div>
        </div>
        <span style={{ fontSize: 10, color: "var(--text-muted)" }}>
          {blocks.filter((b) => b.toggle).length} of {blocks.length} engaged
        </span>
      </div>
      <div className="blocks-list">
        {blocks.map((b) => {
          const rowClass = b.toggle
            ? (b.dangerous ? "engaged" : "active")
            : (b.state === "armed" ? "armed" : "");
          return (
            <div key={b.id} className={"block-row " + rowClass}>
              <div className="block-icon"><i className={"fas " + b.icon} style={{ fontSize: 11 }}></i></div>
              <div className="block-info">
                <div className="block-name">{b.name}</div>
                <div className="block-desc">{b.desc}</div>
              </div>
              <span className={"block-state " + (b.toggle ? (b.dangerous && b.id === "kill" ? "armed" : "on") : "off")}>
                {b.toggle ? (b.dangerous && b.id === "kill" ? "armed" : "on") : "off"}
              </span>
              <div
                className={"toggle" + (b.toggle ? " on" : "") + (b.dangerous ? " dangerous" : "")}
                onClick={() => onToggle(b.id)}
                role="switch"
                aria-checked={b.toggle}
              ></div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

Object.assign(window, { BlockToggles });
