// StressTests.jsx — canonical scenarios with impact bars

function StressTests({ tests, onRun, running }) {
  return (
    <div className="rd-panel">
      <div className="rd-panel-head">
        <div>
          <div className="rd-panel-title">Stress tests</div>
          <div className="rd-panel-sub">PnL impact on $48,284 portfolio · last run 09:30 ET</div>
        </div>
        <button
          onClick={onRun}
          disabled={running}
          style={{
            padding: "5px 12px", borderRadius: 5,
            border: "1px solid var(--accent)",
            background: running ? "var(--accent)" : "var(--accent-dim)",
            color: running ? "var(--bg-root)" : "var(--accent)",
            fontSize: 10, fontWeight: 600, cursor: running ? "wait" : "pointer",
            fontFamily: "inherit",
          }}
        >
          <i className={"fas " + (running ? "fa-spinner" : "fa-bolt")} style={{ marginRight: 6, animation: running ? "spin 0.8s linear infinite" : "none" }}></i>
          {running ? "Running…" : "Re-run"}
        </button>
      </div>
      <div className="stress-list">
        {tests.map((t, i) => (
          <div key={t.name} className="stress-row">
            <div className="stress-head">
              <div>
                <div className="stress-name">{t.name}</div>
              </div>
              <div className="stress-impact" style={{ color: t.color }}>{fmt$(t.impact, 0)}</div>
            </div>
            <div className="stress-bar"><div style={{ width: t.pct + "%", background: t.color, animationDelay: (i * 80) + "ms" }}></div></div>
          </div>
        ))}
      </div>
    </div>
  );
}

Object.assign(window, { StressTests });
