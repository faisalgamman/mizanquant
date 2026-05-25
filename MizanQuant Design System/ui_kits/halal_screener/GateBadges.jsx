// GateBadges.jsx — market gate row + summary cards

function GateBadges({ market }) {
  return (
    <div className="hs-gates">
      <span className="gate-badge gate-riskon">
        <i className="fas fa-check-circle" style={{ fontSize: 9 }}></i>
        {market.status}
      </span>
      <span className="gate-badge gate-info">Regime · {market.regime}</span>
      <span className="gate-badge gate-info">Min gate · {market.minGate}</span>
      <span className="gate-badge gate-info">Strong gate · {market.strongGate}</span>
      {market.haltPipeline && (
        <span className="gate-badge gate-extreme">PIPELINE HALTED</span>
      )}
    </div>
  );
}

function SummaryCards({ scanned, halalCount, qualifiedCount, watchCount, resultsCount, minScore }) {
  const cards = [
    { lab: "Total scanned",  val: scanned,        color: "var(--text-primary)" },
    { lab: "Halal · pass",   val: halalCount,     color: "var(--positive)" },
    { lab: "Qualified",      val: qualifiedCount, color: "var(--positive)" },
    { lab: "Watch list",     val: watchCount,     color: watchCount > 0 ? "var(--warning)" : "var(--text-muted)" },
    { lab: "Results",        val: resultsCount,   color: "var(--text-primary)" },
    { lab: "Min gate",       val: minScore,       color: "var(--warning)" },
  ];
  return (
    <div className="hs-summary">
      {cards.map((c) => (
        <div key={c.lab} className="summary-card">
          <div className="lab">{c.lab}</div>
          <div className="val" style={{ color: c.color }}>{c.val}</div>
        </div>
      ))}
    </div>
  );
}

Object.assign(window, { GateBadges, SummaryCards });
