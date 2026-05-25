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
  return (
    <div className="market-bar">
      <div className="mb-item">
        <span className="mb-lab">SPY</span>
        <span className="mb-val" style={{ color: market.spy_regime === "BULL" ? "var(--positive)" : "var(--negative)" }}>{market.spy_regime}</span>
      </div>
      <div className="mb-item"><span className="mb-lab">VIX</span><span className="mb-val">{market.vix.toFixed(2)}</span></div>
      <div className="mb-item"><span className="mb-lab">Breadth</span><span className="mb-val">{market.breadth.toFixed(1)}%</span></div>
      <div className="mb-item"><span className="mb-lab">Credit</span><span className="mb-val">{market.credit.toFixed(4)}</span></div>
      <div className="mb-item"><span className="mb-lab">Liq</span><span className="mb-val">{market.liquidity.toFixed(1)}%</span></div>
      <div className="mb-clock">{clock}</div>
      <div className="mb-live">
        <span className="dot dot-green pulse"></span>LIVE
      </div>
    </div>
  );
}

Object.assign(window, { CommandBar, MarketStrip });
