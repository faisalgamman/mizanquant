// AAOIFIScreens.jsx — four-screen overview cards (debt · interest · cash · industry)

function AAOIFIScreens({ universe, scanned }) {
  // Each screen, count pass/fail across the visible universe
  const counts = AAOIFI_SCREENS.map((scr, idx) => {
    const pass = universe.filter((u) => u.screens[idx]).length;
    return { ...scr, pass, fail: universe.length - pass };
  });
  return (
    <div>
      <div className="hs-section-title">
        <span>AAOIFI screens · {scanned} symbols</span>
        <span style={{ color: "var(--text-muted)", fontSize: 9, fontFamily: "var(--font-mono)" }}>Standard 21 · 4 gates · all-or-nothing</span>
      </div>
      <div className="aaoifi-grid">
        {counts.map((s) => {
          const passPct = s.pass / universe.length * 100;
          return (
            <div key={s.n} className="aaoifi-card">
              <div className="aaoifi-head">
                <div className="aaoifi-num">{s.n}</div>
                <div>
                  <div className="aaoifi-name">{s.name}</div>
                  <div className="aaoifi-threshold">{s.full} · {s.threshold}</div>
                </div>
              </div>
              <div className="aaoifi-stats">
                <div>
                  <div className="aaoifi-pass">{s.pass}</div>
                  <div className="aaoifi-of">passed</div>
                </div>
                <div style={{ textAlign: "right" }}>
                  <div className="aaoifi-fail">{s.fail}</div>
                  <div className="aaoifi-of" style={{ color: "var(--text-muted)" }}>failed</div>
                </div>
              </div>
              <div className="aaoifi-bar">
                <div className="pass-fill" style={{ width: passPct + "%" }}></div>
                <div className="fail-fill" style={{ width: (100 - passPct) + "%" }}></div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

Object.assign(window, { AAOIFIScreens });
