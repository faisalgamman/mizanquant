// ScanColumn.jsx — Column 1 of the workflow: market · regime · signal cards · table

function MarketCards({ market }) {
  const m = market || {};
  const reg = m.spy_regime || "—";
  const items = [
    { lab: "VIX",     val: m.vix == null ? "—" : m.vix.toFixed(2),
      sub: m.vix == null ? "—" : m.vix < 20 ? "calm" : m.vix < 30 ? "elevated" : "stress",
      color: m.vix == null ? "var(--text-muted)" : m.vix < 20 ? "var(--positive)" : m.vix < 30 ? "var(--warning)" : "var(--negative)" },
    { lab: "SPY",     val: reg,        sub: m.spy_trend || "",
      color: reg === "BULL" ? "var(--positive)" : reg === "BEAR" ? "var(--negative)" : reg === "NEUTRAL" ? "var(--warning)" : "var(--text-secondary)" },
    { lab: "Breadth", val: m.breadth == null ? "—" : m.breadth.toFixed(1) + "%", sub: "advance/decline",
      color: m.breadth == null ? "var(--text-muted)" : m.breadth >= 50 ? "var(--positive)" : "var(--negative)" },
    { lab: "Credit",  val: m.credit == null ? "—" : m.credit.toFixed(4), sub: "HYG / LQD", color: "var(--text-primary)" },
    { lab: "Liq",     val: m.liquidity == null ? "—" : m.liquidity.toFixed(1) + "%", sub: m.market_open ? "open" : "closed", color: "var(--text-primary)" },
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
        <span className="sfc-time">{signal.halal ? "halal · DJIM" : "halal · fail"}</span>
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
              <th>Symbol</th><th>Score</th><th>Signal</th><th>Price</th><th>Chg 1w</th><th>Sector</th><th>USX</th>
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
                  <td>
                    {s.usx_score != null ? (
                      <span style={{ fontSize: 10, color: s.usx_pass ? "var(--positive)" : "var(--text-muted)" }}>
                        {s.usx_score} {s.usx_pass ? "✓" : "·"}
                        <span style={{ fontSize: 8, marginLeft: 3 }}>
                          {(s.usx_signals || []).slice(0, 2).join(" ")}
                        </span>
                      </span>
                    ) : <span style={{ color: "var(--text-muted)" }}>—</span>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div style={{ marginTop: 6, fontSize: 9, color: "var(--text-muted)", textAlign: "center" }}>
        USX early-entry overlay — leading signals, not a guarantee.
      </div>
    </>
  );
}

// Monthly composite table — real sub-score breakdown (Technical / Fundamental /
// Sentiment / AI). The AI column is flagged: it comes from ~coin-flip models, so
// it is shown for transparency but discounted in the conviction logic.
function MonthlyTable({ signals, selectedSymbol, onSelect }) {
  const cell = (v) => (v == null ? <span style={{ color: "var(--text-muted)" }}>—</span> : v);
  return (
    <div className="s-table-wrap">
      <table className="s-table">
        <thead>
          <tr>
            <th>Symbol</th><th>Comp</th><th>Signal</th><th>Price</th>
            <th title="Technical /30">T</th><th title="Fundamental /25">F</th>
            <th title="Sentiment /20">S</th><th title="AI/ML /15 — unvalidated (~coin-flip)">AI*</th>
            <th title="Fundamental grade">Gr</th>
          </tr>
        </thead>
        <tbody>
          {signals.map((s) => {
            const verdict = s.verdict || verdictFromScore(s.score);
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
                <td className="mono">${(s.price || 0).toFixed(2)}</td>
                <td className="mono">{cell(s.tech)}</td>
                <td className="mono">{cell(s.fund)}</td>
                <td className="mono">{cell(s.sent)}</td>
                <td className="mono" style={{ color: "var(--text-muted)" }}>{cell(s.ai)}</td>
                <td className="mono">{cell(s.grade)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <div style={{ fontSize: 8, color: "var(--text-muted)", padding: "6px 10px", lineHeight: 1.5 }}>
        * AI/ML sub-score (15) is from models with ~coin-flip directional accuracy — shown for
        transparency, discounted in conviction. The composite leans on Technical + Fundamental.
      </div>
    </div>
  );
}

// Weekly | Monthly scanner switch.
function ScanTabs({ mode, onMode }) {
  const tab = (id, label, sub) => (
    <button
      type="button"
      className={"scan-tab" + (mode === id ? " active" : "")}
      onClick={() => onMode(id)}
      style={{
        flex: 1, padding: "8px 10px", cursor: "pointer", textAlign: "center",
        background: mode === id ? "var(--accent-dim)" : "transparent",
        border: "1px solid " + (mode === id ? "var(--accent)" : "var(--border)"),
        color: mode === id ? "var(--accent)" : "var(--text-secondary)",
        borderRadius: 6, fontSize: 11, fontWeight: 700, letterSpacing: 0.3,
      }}>
      {label}
      <span style={{ display: "block", fontSize: 8, fontWeight: 500, color: "var(--text-muted)", marginTop: 2 }}>{sub}</span>
    </button>
  );
  return (
    <div style={{ display: "flex", gap: 8, marginBottom: 10 }}>
      {tab("weekly", "Weekly Scanner", "swing · technical · Option-A")}
      {tab("monthly", "Monthly Scanner", "composite · fundamental · rebalanced")}
    </div>
  );
}

// Honest per-scanner paper-ledger banner: real money only after graduation.
function LedgerBanner({ ledger, kind }) {
  const g = (ledger && ledger.graduation) || {};
  const grad = !!g.graduated;
  const n = g.n_trades;
  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap",
      padding: "7px 10px", marginBottom: 10, borderRadius: 6, fontSize: 10,
      background: grad ? "var(--positive-dim)" : "var(--warning-dim)",
      border: "1px solid " + (grad ? "var(--positive)" : "var(--warning)"),
      color: grad ? "var(--positive)" : "var(--warning)",
    }}>
      <i className={"fas " + (grad ? "fa-check-circle" : "fa-flask")}></i>
      <b>{kind} paper ledger</b>
      <span style={{ color: "var(--text-secondary)" }}>
        {ledger ? `${ledger.open ?? 0} open · ${ledger.closed ?? 0} closed` : "loading…"}
      </span>
      <span style={{ marginLeft: "auto", fontWeight: 700 }}>
        {grad ? "GRADUATED" : "NOT graduated — no real money yet"}{n != null ? ` (n=${n})` : ""}
      </span>
    </div>
  );
}

function ScanEmpty({ status, mode }) {
  const computing = status === "computing";
  const monthly = mode === "monthly";
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
        {computing ? "Scanning the halal universe…" : (monthly ? "No composite picks right now" : "No buy signals right now")}
      </div>
      <div style={{ fontSize: 10, color: "var(--text-muted)", fontFamily: "var(--font-mono)", letterSpacing: 0.4 }}>
        {computing
          ? "Screening ~650 symbols across AAOIFI gates · 1–2 min"
          : (monthly
              ? "Composite screener cache is empty · trigger the smart screener first"
              : "Nothing scored ≥ 55 this cycle · check the watchlist")}
      </div>
    </div>
  );
}

function ScanColumn(props) {
  const { scanMode, onScanMode, signals, monthlySignals, selectedSymbol, onSelect, market,
          signalsStatus, monthlyStatus, ledgerWeekly, ledgerMonthly, watch } = props;
  const [filterSignal, setFilterSignal] = useState("all");
  const [filterScore, setFilterScore] = useState("0");
  const [halalOnly, setHalalOnly] = useState(true);

  const monthlyMode = scanMode === "monthly";
  const list   = monthlyMode ? (monthlySignals || []) : signals;
  const status = monthlyMode ? monthlyStatus : signalsStatus;
  const top3   = list.slice(0, 3);
  const empty  = list.length === 0;

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
          <span className="wf-title">Scanners</span>
          <span className="wf-sub">{monthlyMode ? "monthly · ~650 symbols · composite" : "weekly · ~650 symbols · swing"}</span>
        </div>
        <ScanTabs mode={scanMode} onMode={onScanMode} />
        <LedgerBanner ledger={monthlyMode ? ledgerMonthly : ledgerWeekly} kind={monthlyMode ? "Monthly" : "Weekly"} />
        {empty ? (
          <ScanEmpty status={status} mode={scanMode} />
        ) : (
          <>
            <div className="signals-featured">
              {top3.map((s) => (
                <SignalHeroCard key={s.symbol} signal={s} selected={s.symbol === selectedSymbol} onSelect={onSelect} />
              ))}
            </div>
            {monthlyMode ? (
              <MonthlyTable signals={list} selectedSymbol={selectedSymbol} onSelect={onSelect} />
            ) : (
              <SignalTable
                signals={list}
                selectedSymbol={selectedSymbol}
                onSelect={onSelect}
                filterSignal={filterSignal} setFilterSignal={setFilterSignal}
                filterScore={filterScore} setFilterScore={setFilterScore}
                halalOnly={halalOnly} setHalalOnly={setHalalOnly}
              />
            )}
          </>
        )}
      </div>
      <div className="wf-section">
        <div className="wf-head">
          <span className="wf-title">WATCH — قريبون</span>
          <span className="wf-sub">{watch && watch.watch ? watch.watch.length + " stocks" : "—"} · display only</span>
        </div>
        {watch && watch.watch && watch.watch.length > 0 ? (
          <>
            {watch.market_block ? (
              <div className="watch-banner">لماذا ينتظر الجميع الآن: {watch.market_block}</div>
            ) : null}
            <div className="watch-list">
              {watch.watch.map((w) => (
                <div key={w.symbol} className="watch-row"
                     onClick={() => window.selectIntelSymbol && window.selectIntelSymbol(w.symbol)}
                     title="اعرض البطاقة">
                  <div className="watch-top">
                    <span className="watch-sym">{w.symbol}</span>
                    <span className="watch-name">{w.name || "—"}</span>
                    <span className="watch-score">{w.composite_score != null ? w.composite_score : "—"}/100</span>
                    <span className="watch-tech">فني {w.score_tech != null ? w.score_tech : "—"}</span>
                  </div>
                  <div className="watch-reason">🔒 {w.watch_reason || "—"}</div>
                </div>
              ))}
            </div>
            <div className="watch-note">WATCH = إعداد قوي لكن بوّابة تقول انتظر — ليست إشارة شراء.</div>
          </>
        ) : (
          <div className="watch-empty">لا قريبون الآن</div>
        )}
      </div>
    </div>
  );
}

Object.assign(window, { ScanColumn });
