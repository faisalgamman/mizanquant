// VaRMetrics.jsx — Portfolio VaR / CVaR / Sharpe / Max DD strip

function VaRMetrics({ m }) {
  const fmtArrow = (d) => d >= 0 ? "↑" : "↓";
  const cards = [
    {
      lab: "Portfolio VaR (95%)", val: fmtPct(m.var95, 1),
      stripe: "var(--negative)", color: "var(--text-primary)",
      delta: m.varDelta, deltaText: `${fmtArrow(-m.varDelta)} ${Math.abs(m.varDelta).toFixed(1)}% vs 30d`,
      deltaColor: m.varDelta <= 0 ? "var(--positive)" : "var(--warning)",
    },
    {
      lab: "CVaR (95%) · tail",   val: fmtPct(m.cvar95, 1),
      stripe: "var(--negative)", color: "var(--text-primary)",
      deltaText: `${fmtArrow(m.cvarDelta)} ${Math.abs(m.cvarDelta).toFixed(1)}% vs 30d`,
      deltaColor: m.cvarDelta <= 0 ? "var(--positive)" : "var(--warning)",
    },
    {
      lab: "Sharpe ratio",        val: m.sharpe.toFixed(2),
      stripe: "var(--accent)",   color: "var(--text-primary)",
      deltaText: `${fmtArrow(m.sharpeDelta)} ${Math.abs(m.sharpeDelta).toFixed(2)} vs 30d`,
      deltaColor: m.sharpeDelta >= 0 ? "var(--positive)" : "var(--negative)",
    },
    {
      lab: "Max drawdown",        val: fmtPct(m.maxDrawdown, 1),
      stripe: "var(--warning)",  color: "var(--text-primary)",
      deltaText: `${fmtArrow(m.ddDelta)} ${Math.abs(m.ddDelta).toFixed(1)}% vs 30d`,
      deltaColor: m.ddDelta <= 0 ? "var(--positive)" : "var(--warning)",
    },
  ];

  return (
    <div className="rd-metrics">
      {cards.map((c) => (
        <div key={c.lab} className="rd-metric-card">
          <div className="accent-stripe" style={{ background: c.stripe }}></div>
          <div className="lab">{c.lab}</div>
          <div className="val" style={{ color: c.color }}>{c.val}</div>
          <div className="sub">
            <span className="delta" style={{ color: c.deltaColor }}>{c.deltaText}</span>
          </div>
        </div>
      ))}
    </div>
  );
}

Object.assign(window, { VaRMetrics });
