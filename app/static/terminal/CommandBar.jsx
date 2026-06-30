// CommandBar.jsx — top command bar

function CommandBar({ etClock, symbolCount, query, setQuery, onRefresh }) {
  return (
    <header className="cmd-bar">
      <span className="cmd-title">Overview</span>
      <div className="cmd-center">
        <div className="cmd-search">
          <i className="fas fa-search" style={{ fontSize: 11, color: "var(--text-muted)" }}></i>
          <input
            placeholder="Search symbol…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && query.trim()) {
                const s = query.trim().toUpperCase();
                if (window.selectIntelSymbol) window.selectIntelSymbol(s);
                setQuery("");
              }
            }}
          />
          <span className="kbd">⌘K</span>
        </div>
      </div>
      <div className="cmd-right">
        <span className="cmd-meta">{symbolCount} symbols</span>
        <span className="cmd-meta">{etClock} ET</span>
        <button className="cmd-btn" title="Refresh" onClick={onRefresh}>
          <i className="fas fa-sync-alt"></i>
        </button>
      </div>
    </header>
  );
}

// MarketStrip.jsx — market-context bar below cmd-bar
function MarketStrip({ market, clock }) {
  const m = market || {};
  const reg = m.spy_regime || "—";
  const regColor = reg === "BULL" ? "var(--positive)" : reg === "BEAR" ? "var(--negative)"
                 : reg === "NEUTRAL" ? "var(--warning)" : "var(--text-secondary)";
  const f = (v, dp, suf = "") => (v == null || isNaN(Number(v))) ? "—" : Number(v).toFixed(dp) + suf;
  // Gold macro signal: uptrend = haven bid (caution, amber), downtrend = risk-on (green).
  const gArrow = m.gold_trend === "uptrend" ? "▲" : m.gold_trend === "downtrend" ? "▼" : "";
  const gColor = m.gold_signal === "haven_bid" ? "var(--warning)" : m.gold_signal === "risk_on" ? "var(--positive)" : "var(--text-secondary)";
  return (
    <div className="market-bar">
      <div className="mb-item">
        <span className="mb-lab">SPY</span>
        <span className="mb-val" style={{ color: regColor }}>{reg}</span>
      </div>
      <div className="mb-item"><span className="mb-lab">VIX</span><span className="mb-val">{f(m.vix, 2)}</span></div>
      <div className="mb-item"><span className="mb-lab">Breadth</span><span className="mb-val">{f(m.breadth, 1, "%")}</span></div>
      <div className="mb-item"><span className="mb-lab">Credit</span><span className="mb-val">{f(m.credit, 4)}</span></div>
      <div className="mb-item"><span className="mb-lab">Liq</span><span className="mb-val">{f(m.liquidity, 1, "%")}</span></div>
      <div className="mb-item" style={{ cursor: "pointer" }} title="حلّل الذهب (GLD) — اضغط"
           onClick={() => window.selectIntelSymbol && window.selectIntelSymbol("GLD")}>
        <span className="mb-lab">Gold</span>
        <span className="mb-val" style={{ color: gColor }}>{gArrow}{f(m.gold_price, 0)}</span>
      </div>
      <div className="mb-clock">{clock}</div>
      <div className="mb-live">
        <span className="dot dot-green pulse"></span>LIVE
      </div>
    </div>
  );
}

Object.assign(window, { CommandBar, MarketStrip });
