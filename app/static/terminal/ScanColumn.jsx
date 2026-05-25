// ScanColumn.jsx — Column 1 of the workflow: market · regime · signal cards · table

function MarketCards({ market }) {
  const items = [
    { lab: "VIX",     val: market.vix.toFixed(2),    sub: market.vix < 20 ? "calm" : market.vix < 30 ? "elevated" : "stress",
      color: market.vix < 20 ? "var(--positive)" : market.vix < 30 ? "var(--warning)" : "var(--negative)" },
    { lab: "SPY",     val: market.spy_regime,        sub: market.spy_trend,
      color: market.spy_regime === "BULL" ? "var(--positive)" : "var(--negative)" },
    { lab: "Breadth", val: market.breadth.toFixed(1) + "%", sub: "advance/decline",
      color: market.breadth >= 50 ? "var(--positive)" : "var(--negative)" },
    { lab: "Credit",  val: market.credit.toFixed(4), sub: "HYG / LQD", color: "var(--text-primary)" },
    { lab: "Liq",     val: market.liquidity.toFixed(1) + "%", sub: market.market_open ? "open" : "closed", color: "var(--text-primary)" },
  ];
  return (
    <div className="mc-strip">
      {items.map((i) => (
        <div key={i.lab} className="mc-card">
          <div className="mc-lab">{i.lab}</div>
          <div className="mc-val" style={{ color: i.color }}>{i.val}</div>
          <div className="mc-sub">{i.sub}</div>
        </div>
      ))}
    </div>
  );
}

function RegimeBar({ regime }) {
  const config = {
    BULL: { color: "var(--positive)", bg: "var(--positive-dim)" },
    BEAR: { color: "var(--negative)", bg: "var(--negative-dim)" },
    NEUTRAL: { color: "var(--warning)", bg: "var(--warning-dim)" },
  };
  const c = config[regime] || config.NEUTRAL;
  return (
    <div className="regime-bar">
      <div style={{
        flex: 1, background: c.bg, color: c.color,
        display: "flex", alignItems: "center", justifyContent: "center",
        fontSize: 10, fontWeight: 700, letterSpacing: "0.5px",
      }}>{regime}</div>
    </div>
  );
}

function SignalHeroCard({ signal, selected, onSelect }) {
  const verdict = verdictFromScore(signal.score);
  const chgColor = signal.chg >= 0 ? "var(--positive)" : "var(--negative)";
  return (
    <div className={"sfc" + (selected ? " selected" : "")} onClick={() => onSelect(signal.symbol)}>
      <div className="sfc-top">
        <Ring score={signal.score} />
        <div>
          <div className="sfc-sym">{signal.symbol}</div>
          <div className="sfc-chg" style={{ color: chgColor }}>{fmtPct(signal.chg)}</div>
        </div>
      </div>
      <div className="sfc-spark">
        <Sparkline points={signal.spark} color={chgColor} />
      </div>
      <div className="sfc-foot">
        <span className="sfc-time">{signal.halal ? "halal · pass" : "halal · fail"}</span>
        <Badge kind={badgeClassFor(verdict).replace("b-", "")}>{verdict}</Badge>
      </div>
    </div>
  );
}

function SignalTable({ signals, selectedSymbol, onSelect, filterSignal, setFilterSignal, filterScore, setFilterScore, halalOnly, setHalalOnly }) {
  const filtered = signals.filter((s) => {
    if (halalOnly && !s.halal) return false;
    const verdict = verdictFromScore(s.score);
    if (filterSignal !== "all" && verdict !== filterSignal) return false;
    if (s.score < Number(filterScore)) return false;
    return true;
  });
  return (
    <>
      <div className="filter-bar">
        <select value={filterSignal} onChange={(e) => setFilterSignal(e.target.value)}>
          <option value="all">Signal: All</option>
          <option value="STRONG BUY">STRONG BUY</option>
          <option value="BUY">BUY</option>
          <option value="WAIT">WAIT</option>
          <option value="AVOID">AVOID</option>
        </select>
        <select value={filterScore} onChange={(e) => setFilterScore(e.target.value)}>
          <option value="0">Score: All</option>
          <option value="60">60+</option>
          <option value="75">75+</option>
          <option value="90">90+</option>
        </select>
        <label>
          <input type="checkbox" checked={halalOnly} onChange={(e) => setHalalOnly(e.target.checked)} />
          Halal
        </label>
        <span style={{ marginLeft: "auto", fontSize: 9, color: "var(--text-muted)" }}>{filtered.length} of {signals.length}</span>
      </div>
      <div className="s-table-wrap">
        <table className="s-table">
          <thead>
            <tr>
              <th>Symbol</th><th>Score</th><th>Signal</th><th>Price</th><th>Chg</th><th>Sector</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((s) => {
              const verdict = verdictFromScore(s.score);
              const chgColor = s.chg >= 0 ? "var(--positive)" : "var(--negative)";
              return (
                <tr key={s.symbol} className={s.symbol === selectedSymbol ? "selected" : ""} onClick={() => onSelect(s.symbol)}>
                  <td style={{ fontWeight: 600 }}>{s.symbol}</td>
                  <td>
                    <div className="score-bar">
                      <div className="score-bar-fill"><div style={{ width: s.score + "%", background: scoreColor(s.score) }}></div></div>
                      <span className="mono" style={{ fontWeight: 600 }}>{s.score}</span>
                    </div>
                  </td>
                  <td><Badge kind={badgeClassFor(verdict).replace("b-", "")}>{verdict}</Badge></td>
                  <td className="mono">${s.price.toFixed(2)}</td>
                  <td className="mono" style={{ color: chgColor, fontWeight: 600 }}>{fmtPct(s.chg)}</td>
                  <td style={{ color: "var(--text-secondary)" }}>{s.sector}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}

function ScanEmpty({ status }) {
  const computing = status === "computing";
  return (
    <div style={{
      display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
      gap: 12, padding: "48px 16px", textAlign: "center",
    }}>
      {computing && (
        <div className="spin" style={{
          width: 26, height: 26, border: "3px solid var(--border)",
          borderTopColor: "var(--accent)", borderRadius: "50%",
        }}></div>
      )}
      <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text-secondary)" }}>
        {computing ? "Scanning the halal universe…" : "No buy signals right now"}
      </div>
      <div style={{ fontSize: 10, color: "var(--text-muted)", fontFamily: "var(--font-mono)", letterSpacing: 0.4 }}>
        {computing
          ? "Screening ~650 symbols across AAOIFI gates · 1–2 min"
          : "Nothing scored ≥ 55 this cycle · check the watchlist"}
      </div>
    </div>
  );
}

function ScanColumn(props) {
  const { signals, selectedSymbol, onSelect, market, signalsStatus } = props;
  const [filterSignal, setFilterSignal] = useState("all");
  const [filterScore, setFilterScore] = useState("0");
  const [halalOnly, setHalalOnly] = useState(true);
  const top3 = signals.slice(0, 3);
  const empty = signals.length === 0;
  return (
    <div className="col col-scan">
      <div className="wf-section">
        <div className="wf-head">
          <span className="wf-title">Market</span>
          <span className="wf-sub">VIX · SPY · Breadth · Credit · Liquidity</span>
        </div>
        <MarketCards market={market} />
      </div>
      <div className="wf-section">
        <div className="wf-head">
          <span className="wf-title">Regime</span>
          <span className="wf-sub">current · {market.spy_regime}</span>
        </div>
        <RegimeBar regime={market.spy_regime} />
      </div>
      <div className="wf-section">
        <div className="wf-head">
          <span className="wf-title">Signals</span>
          <span className="wf-sub">High-conviction opportunities</span>
        </div>
        {empty ? (
          <ScanEmpty status={signalsStatus} />
        ) : (
          <>
            <div className="signals-featured">
              {top3.map((s) => (
                <SignalHeroCard key={s.symbol} signal={s} selected={s.symbol === selectedSymbol} onSelect={onSelect} />
              ))}
            </div>
            <SignalTable
              signals={signals}
              selectedSymbol={selectedSymbol}
              onSelect={onSelect}
              filterSignal={filterSignal} setFilterSignal={setFilterSignal}
              filterScore={filterScore} setFilterScore={setFilterScore}
              halalOnly={halalOnly} setHalalOnly={setHalalOnly}
            />
          </>
        )}
      </div>
    </div>
  );
}

Object.assign(window, { ScanColumn });
