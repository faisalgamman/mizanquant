// App.jsx — Halal Screener composition (wired to production API)

function App() {
  const [scanning, setScanning]       = useState(false);
  const [lastScanned, setLastScanned] = useState("—");
  const [progress, setProgress]       = useState(100);
  const [progressLabel, setProgressLabel] = useState("");
  const [activeSector, setActiveSector]   = useState("ALL");
  const [selectedSym, setSelectedSym]     = useState(null);
  const [sortField, setSortField]         = useState("score");
  const [sortAsc, setSortAsc]             = useState(false);
  const [filters, setFilters]             = useState({ sym: "", signal: "", minScore: "0", halal: "" });
  const [universe, setUniverse]           = useState(mockUniverse);   // start with mock, replace on scan
  const [market, setMarket]               = useState(mockMarket);
  const [errorMsg, setErrorMsg]           = useState(null);

  // Load market context once on mount
  useEffect(() => {
    fetchMarketContext().then(ctx => {
      if (!ctx) return;
      setMarket(m => ({
        ...m,
        status:  ctx.risk_posture === "risk_on" ? "RISK ON"
                 : ctx.risk_posture === "risk_off" ? "RISK OFF" : "NEUTRAL",
        regime:  ctx.regime || m.regime,
      }));
    });
    // Also load current screener cache (non-blocking)
    fetchUniverse().then(rows => {
      if (rows && rows.length > 0) {
        setUniverse(rows);
        setLastScanned("cached · " + new Date().toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", hour12: false }) + " ET");
      }
    }).catch(() => {/* silent – fall back to mock */});
  }, []);

  // Filter universe by active sector
  const visibleUniverse = useMemo(() => {
    if (activeSector === "ALL") return universe;
    return universe.filter((u) => u.sector === activeSector);
  }, [activeSector, universe]);

  const halalCount     = visibleUniverse.filter((u) => u.screens.every(Boolean)).length;
  const qualifiedCount = visibleUniverse.filter((u) => u.screens.every(Boolean) && u.score >= market.minGate).length;
  const watchCount     = visibleUniverse.filter((u) => u.screens.every(Boolean) && u.score >= market.strongGate).length;

  // Build sector list from live universe
  const sectors = useMemo(() => {
    const map = {};
    universe.forEach(u => {
      const s = u.sector || "Other";
      if (!map[s]) map[s] = { sector: s, total: 0, halal: 0 };
      map[s].total++;
      if (u.screens.every(Boolean)) map[s].halal++;
    });
    return Object.values(map);
  }, [universe]);

  const onScan = async () => {
    if (scanning) return;
    setScanning(true);
    setProgress(0);
    setProgressLabel("Fetching screener data…");
    setErrorMsg(null);

    // Animate progress while fetching
    let p = 0;
    const id = setInterval(() => {
      p = Math.min(p + 6 + Math.random() * 8, 85);
      setProgress(p);
      setProgressLabel(`Scanning ${Math.floor(p * 5.02)} / 502 symbols`);
    }, 200);

    try {
      const rows = await fetchUniverse();
      clearInterval(id);
      setProgress(100);
      const count = rows.length;
      setProgressLabel(`Scan complete · ${count} symbol${count !== 1 ? 's' : ''}`);
      setUniverse(rows.length > 0 ? rows : mockUniverse);
      setLastScanned(new Date().toLocaleString("en-US", {
        hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "America/New_York"
      }) + " ET");
    } catch (err) {
      clearInterval(id);
      setProgress(100);
      setProgressLabel("Scan failed — showing cached data");
      setErrorMsg("Could not reach /screener: " + (err.message || "Network error"));
    } finally {
      setTimeout(() => setScanning(false), 600);
    }
  };

  const onSort = (field) => {
    if (sortField === field) setSortAsc(!sortAsc);
    else { setSortField(field); setSortAsc(false); }
  };

  const selectedRow = useMemo(() => universe.find((u) => u.sym === selectedSym), [selectedSym, universe]);

  return (
    <div className="hs-page">
      <ScreenerHeader
        scanning={scanning}
        lastScanned={lastScanned}
        onScan={onScan}
        onExport={() => window.open('/api/halal/export', '_blank')}
      />

      {errorMsg && (
        <div style={{
          background: "var(--negative-dim)", border: "1px solid var(--negative)",
          borderRadius: "var(--radius-md)", padding: "8px 14px", marginBottom: "12px",
          fontSize: "var(--text-sm)", color: "var(--negative)"
        }}>
          {errorMsg}
        </div>
      )}

      {scanning && (
        <div className="hs-progress">
          <div className="hs-progress-head">
            <span>{progressLabel}</span>
            <span>{progress.toFixed(0)}%</span>
          </div>
          <div className="hs-progress-bar">
            <div className="hs-progress-fill" style={{ width: progress + "%" }}></div>
          </div>
        </div>
      )}

      <GateBadges market={market} />
      <SummaryCards
        scanned={universe.length}
        halalCount={halalCount}
        qualifiedCount={qualifiedCount}
        watchCount={watchCount}
        resultsCount={visibleUniverse.length}
        minScore={market.minGate}
      />

      <AAOIFIScreens universe={visibleUniverse} scanned={visibleUniverse.length} />

      <SectorFilters
        sectors={sectors.length > 0 ? sectors : mockSectors}
        activeSector={activeSector}
        onSelect={setActiveSector}
      />

      <ComplianceTable
        universe={visibleUniverse}
        sortField={sortField}
        sortAsc={sortAsc}
        onSort={onSort}
        selectedSym={selectedSym}
        onSelect={setSelectedSym}
        filters={filters}
        setFilters={setFilters}
      />

      <DetailPanel row={selectedRow} onClose={() => setSelectedSym(null)} />
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
