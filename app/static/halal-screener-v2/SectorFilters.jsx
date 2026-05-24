// SectorFilters.jsx — dynamic sector chips with pass-counts

function SectorFilters({ sectors, activeSector, onSelect }) {
  const totalPass = sectors.reduce((a, s) => a + s.pass, 0);
  const totalAll = sectors.reduce((a, s) => a + s.count, 0);
  return (
    <div>
      <div className="hs-section-title">
        <span>Sectors</span>
        <span style={{ color: "var(--text-muted)", fontSize: 9, fontFamily: "var(--font-mono)" }}>{totalPass} of {totalAll} halal-pass</span>
      </div>
      <div className="sector-chips">
        <div
          className={"sector-chip" + (activeSector === "ALL" ? " active" : "")}
          onClick={() => onSelect("ALL")}
        >
          All sectors
          <span className="count">{totalAll}</span>
        </div>
        {sectors.map((s) => (
          <div
            key={s.name}
            className={"sector-chip" + (activeSector === s.name ? " active" : "") + (s.haramOnly ? " haram-only" : "")}
            onClick={() => onSelect(s.name)}
          >
            {s.haramOnly && <i className="fas fa-ban" style={{ fontSize: 9 }}></i>}
            {s.name}
            <span className="count">{s.pass}/{s.count}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

Object.assign(window, { SectorFilters });
