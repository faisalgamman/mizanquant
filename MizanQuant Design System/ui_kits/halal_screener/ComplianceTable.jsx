// ComplianceTable.jsx — per-symbol AAOIFI verdict table

const SCREEN_LETTERS = ["D", "I", "C", "H"];   // Debt · Interest · Cash · Haram-industry

function ComplianceTable({ universe, sortField, sortAsc, onSort, selectedSym, onSelect, filters, setFilters }) {
  const filtered = useMemo(() => {
    return universe.filter((r) => {
      if (filters.sym && !r.sym.toUpperCase().includes(filters.sym.toUpperCase())) return false;
      if (filters.signal && r.signal !== filters.signal) return false;
      if (filters.minScore && r.score < Number(filters.minScore)) return false;
      if (filters.halal === "pass" && !r.screens.every(Boolean)) return false;
      if (filters.halal === "fail" &&  r.screens.every(Boolean)) return false;
      return true;
    });
  }, [universe, filters]);

  const sorted = useMemo(() => {
    return [...filtered].sort((a, b) => {
      let va = a[sortField] ?? 0;
      let vb = b[sortField] ?? 0;
      if (typeof va === "string") va = va.toUpperCase();
      if (typeof vb === "string") vb = vb.toUpperCase();
      if (va < vb) return sortAsc ? -1 : 1;
      if (va > vb) return sortAsc ?  1 : -1;
      return 0;
    });
  }, [filtered, sortField, sortAsc]);

  const sortArrow = (field) => sortField === field ? (sortAsc ? "↑" : "↓") : "↕";

  return (
    <div>
      <div className="hs-section-title">
        <span>Compliance table</span>
        <span style={{ color: "var(--text-muted)", fontSize: 9 }}>Click any row for full AAOIFI breakdown</span>
      </div>

      <div className="filter-bar">
        <input
          type="text"
          placeholder="Symbol…"
          value={filters.sym}
          onChange={(e) => setFilters({ ...filters, sym: e.target.value })}
        />
        <select value={filters.signal} onChange={(e) => setFilters({ ...filters, signal: e.target.value })}>
          <option value="">All signals</option>
          <option value="STRONG BUY">STRONG BUY</option>
          <option value="BUY">BUY</option>
          <option value="WAIT">WAIT</option>
          <option value="AVOID">AVOID</option>
        </select>
        <select value={filters.minScore} onChange={(e) => setFilters({ ...filters, minScore: e.target.value })}>
          <option value="0">Min score: any</option>
          <option value="60">60+</option>
          <option value="75">75+</option>
          <option value="90">90+</option>
        </select>
        <select value={filters.halal} onChange={(e) => setFilters({ ...filters, halal: e.target.value })}>
          <option value="">Halal: any</option>
          <option value="pass">Pass only</option>
          <option value="fail">Fail only</option>
        </select>
        <span className="count">{sorted.length} of {universe.length}</span>
      </div>

      <div className="hs-table-wrap">
        <table className="hs-table">
          <thead>
            <tr>
              {[
                { k: "sym",   l: "Symbol" },
                { k: "score", l: "Score" },
                { k: null,    l: "Halal" },
                { k: null,    l: "Screens (D·I·C·H)" },
                { k: "chg",   l: "Chg" },
                { k: "mcap",  l: "Mkt cap" },
                { k: "sector",l: "Sector" },
                { k: "rsi",   l: "RSI" },
                { k: "adx",   l: "ADX" },
                { k: null,    l: "Signal" },
                { k: null,    l: "Strategy" },
              ].map((h) => (
                <th key={h.l}
                    className={h.k && h.k === sortField ? "active" : ""}
                    onClick={() => h.k && onSort(h.k)}>
                  {h.l}
                  {h.k && <span className="sort-arrow">{sortArrow(h.k)}</span>}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sorted.map((r) => {
              const allPass = r.screens.every(Boolean);
              return (
                <tr key={r.sym}
                    className={selectedSym === r.sym ? "selected" : ""}
                    onClick={() => onSelect(r.sym)}>
                  <td style={{ fontWeight: 700 }}>{r.sym}</td>
                  <td>
                    <div className="score-bar">
                      <div className="score-bar-fill"><div style={{ width: r.score + "%", background: scoreColor(r.score) }}></div></div>
                      <span className="num" style={{ color: scoreColor(r.score) }}>{r.score}</span>
                    </div>
                  </td>
                  <td>
                    <span className={"halal-cell " + (allPass ? "halal-pass" : "halal-fail")}>
                      {allPass ? <i className="fas fa-check" style={{ fontSize: 8 }}></i> : <i className="fas fa-xmark" style={{ fontSize: 8 }}></i>}
                      {allPass ? "Pass" : "Fail"}
                    </span>
                  </td>
                  <td>
                    <div className="screen-pills">
                      {r.screens.map((p, i) => (
                        <span key={i} className={"screen-pill " + (p ? "pass" : "fail")}>{SCREEN_LETTERS[i]}</span>
                      ))}
                    </div>
                  </td>
                  <td className="mono" style={{ color: r.chg >= 0 ? "var(--positive)" : "var(--negative)", fontWeight: 600 }}>{fmtPct(r.chg)}</td>
                  <td className="mono" style={{ color: "var(--text-secondary)" }}>${(r.mcap / 1e9).toFixed(0)}B</td>
                  <td style={{ color: "var(--text-secondary)" }}>{r.sector}</td>
                  <td className="mono" style={{ color: "var(--text-secondary)" }}>{r.rsi}</td>
                  <td className="mono" style={{ color: "var(--text-secondary)" }}>{r.adx}</td>
                  <td>
                    <span className={"halal-cell " + (
                      r.signal.includes("BUY") ? "halal-pass" :
                      r.signal === "WAIT" ? "halal-review" : "halal-fail"
                    )} style={{ background: "transparent", color: r.signal.includes("BUY") ? "var(--positive)" : r.signal === "WAIT" ? "var(--warning)" : "var(--negative)" }}>
                      {r.signal}
                    </span>
                  </td>
                  <td style={{ color: "var(--text-muted)", fontSize: 10 }}>{r.strategy}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

Object.assign(window, { ComplianceTable });
