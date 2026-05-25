// AnalyzeColumn.jsx — Column 2 of the workflow: sticky analysis panel

function AnalyzeColumn({ signal, onTrade }) {
  if (!signal) {
    return (
      <div className="col col-analyze">
        <div className="wf-section">
          <div className="wf-head">
            <span className="wf-title">Analyze</span>
            <span className="wf-sub">Selected signal</span>
          </div>
          <div className="analyze-empty">
            <i className="fas fa-search"></i>
            Select a signal to analyze
          </div>
        </div>
      </div>
    );
  }

  const verdict = verdictFromScore(signal.score);
  const chgColor = signal.chg >= 0 ? "var(--positive)" : "var(--negative)";

  // Synthesize sub-scores from the headline score
  const subs = [
    { lab: "Momentum",  v: Math.min(99, signal.score + 6), w: 0.30 },
    { lab: "Trend",     v: Math.min(99, signal.score + 2), w: 0.20 },
    { lab: "Quality",   v: Math.max(35, signal.score - 8), w: 0.20 },
    { lab: "Valuation", v: Math.max(40, signal.score - 14),w: 0.15 },
    { lab: "Sentiment", v: Math.min(95, signal.score + 4), w: 0.15 },
  ];

  // Mock trade plan
  const entry = signal.price;
  const stop  = +(entry * (1 - 0.025)).toFixed(2);
  const tgt1  = +(entry * (1 + 0.04)).toFixed(2);
  const tgt2  = +(entry * (1 + 0.075)).toFixed(2);
  const size  = Math.floor(1000 / entry);

  return (
    <div className="col col-analyze">
      <div className="wf-section">
        <div className="wf-head">
          <span className="wf-title">Analyze</span>
          <span className="wf-sub">{signal.symbol} · {signal.industry}</span>
        </div>
        <div className="an-panel">
          <div className="an-hdr">
            <div>
              <div className="an-sym">{signal.symbol}</div>
              <div className="an-co">{signal.company}</div>
            </div>
            <div style={{ textAlign: "right" }}>
              <Badge kind={badgeClassFor(verdict).replace("b-", "")}>{verdict}</Badge>
              <div style={{ marginTop: 4 }}>
                <Badge kind={signal.halal ? "accent" : "red"}>
                  {signal.halal ? "Halal · pass" : "Halal · fail"}
                </Badge>
              </div>
            </div>
          </div>
          <div className="an-price">
            <span className="p">${signal.price.toFixed(2)}</span>
            <span className="c" style={{ color: chgColor }}>{fmtPct(signal.chg)}</span>
          </div>
          <Sparkline points={signal.spark} color={chgColor} height={36} />

          <div className="an-sect-title">Score breakdown</div>
          {subs.map((sub) => (
            <div key={sub.lab} className="an-bar-row">
              <span className="lab">{sub.lab}</span>
              <div className="bar"><div style={{ width: sub.v + "%", background: scoreColor(sub.v) }}></div></div>
              <span className="num" style={{ color: scoreColor(sub.v) }}>{sub.v}</span>
            </div>
          ))}

          <div className="an-sect-title">Trade plan</div>
          <div className="an-grid">
            <div className="row"><span className="l">Entry</span><span className="v">${entry.toFixed(2)}</span></div>
            <div className="row"><span className="l">Size</span><span className="v">{size} sh</span></div>
            <div className="row"><span className="l">Stop</span><span className="v txt-negative">${stop}</span></div>
            <div className="row"><span className="l">Target 1</span><span className="v txt-positive">${tgt1}</span></div>
            <div className="row"><span className="l">Target 2</span><span className="v txt-positive">${tgt2}</span></div>
            <div className="row"><span className="l">R / R</span><span className="v">1 : 3.0</span></div>
          </div>

          <button className="an-trade" onClick={() => onTrade(signal)} disabled={!signal.halal}>
            <i className="fas fa-paper-plane" style={{ marginRight: 6 }}></i>
            {signal.halal ? "Send to paper trade" : "Blocked — halal fail"}
          </button>
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { AnalyzeColumn });
