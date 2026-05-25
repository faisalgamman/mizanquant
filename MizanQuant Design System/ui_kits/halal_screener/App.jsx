// App.jsx — Halal Screener composition

function App() {
  const [scanning, setScanning] = useState(false);
  const [lastScanned, setLastScanned] = useState("2026-05-24 09:30 ET");
  const [progress, setProgress] = useState(100);
  const [progressLabel, setProgressLabel] = useState("");
  const [activeSector, setActiveSector] = useState("ALL");
  const [selectedSym, setSelectedSym] = useState(null);
  const [sortField, setSortField] = useState("score");
  const [sortAsc, setSortAsc] = useState(false);
  const [filters, setFilters] = useState({ sym: "", signal: "", minScore: "0", halal: "" });

  // Filter universe by active sector
  const visibleUniverse = useMemo(() => {
    if (activeSector === "ALL") return mockUniverse;
    return mockUniverse.filter((u) => u.sector === activeSector);
  }, [activeSector]);

  // Pass/fail counts
  const halalCount     = visibleUniverse.filter((u) => u.screens.every(Boolean)).length;
  const qualifiedCount = visibleUniverse.filter((u) => u.screens.every(Boolean) && u.score >= mockMarket.minGate).length;
  const watchCount     = visibleUniverse.filter((u) => u.screens.every(Boolean) && u.score >= mockMarket.strongGate).length;

  const onScan = () => {
    if (scanning) return;
    setScanning(true);
    setProgress(0);
    setProgressLabel("Starting scan…");
    let p = 0;
    const id = setInterval(() => {
      p += 8 + Math.random() * 8;
      if (p >= 100) {
        p = 100;
        setProgress(100);
        setProgressLabel("Scan complete · 502 symbols");
        clearInterval(id);
        setTimeout(() => {
          setScanning(false);
          setLastScanned(new Date().toLocaleString("en-US", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "America/New_York" }) + " ET");
        }, 400);
      } else {
        setProgress(p);
        setProgressLabel(`Scanning ${Math.floor(p * 5.02)} / 502 symbols`);
      }
    }, 180);
  };

  const onSort = (field) => {
    if (sortField === field) setSortAsc(!sortAsc);
    else { setSortField(field); setSortAsc(false); }
  };

  const selectedRow = useMemo(() => mockUniverse.find((u) => u.sym === selectedSym), [selectedSym]);

  return (
    <div className="hs-page">
      <ScreenerHeader
        scanning={scanning}
        lastScanned={lastScanned}
        onScan={onScan}
        onExport={() => alert("CSV export — wires to /api/halal/export")}
      />

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

      <GateBadges market={mockMarket} />
      <SummaryCards
        scanned={mockUniverse.length}
        halalCount={halalCount}
        qualifiedCount={qualifiedCount}
        watchCount={watchCount}
        resultsCount={visibleUniverse.length}
        minScore={mockMarket.minGate}
      />

      <AAOIFIScreens universe={visibleUniverse} scanned={visibleUniverse.length} />

      <SectorFilters
        sectors={mockSectors}
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
