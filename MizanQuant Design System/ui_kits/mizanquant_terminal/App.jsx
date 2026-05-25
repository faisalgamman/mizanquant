// App.jsx — composition + interactive state for the click-thru

function App() {
  const [selectedSymbol, setSelectedSymbol] = useState("AAPL");
  const [clock, setClock]   = useState("--:--:--");
  const [etClock, setETClock] = useState("--:--");
  const [pipeline, setPipeline] = useState(initialPipelineStages);
  const [pipelineRunning, setPipelineRunning] = useState(false);
  const [dryRun, setDryRun] = useState(true);
  const [query, setQuery] = useState("");
  const [toast, setToast] = useState(null);

  // Clock tick (ET)
  useEffect(() => {
    const tick = () => {
      const now = new Date();
      const et = new Intl.DateTimeFormat("en-US", {
        timeZone: "America/New_York",
        hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
      }).format(now);
      setClock(et);
      setETClock(et.slice(0, 5));
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, []);

  // Auto-clear toast
  useEffect(() => {
    if (!toast) return;
    const id = setTimeout(() => setToast(null), 3500);
    return () => clearTimeout(id);
  }, [toast]);

  const filteredSignals = useMemo(() => {
    if (!query) return mockSignals;
    const q = query.toUpperCase();
    return mockSignals.filter((s) => s.symbol.includes(q) || s.company.toUpperCase().includes(q));
  }, [query]);

  const selected = mockSignals.find((s) => s.symbol === selectedSymbol);

  const runPipeline = () => {
    if (pipelineRunning) return;
    setPipelineRunning(true);
    // Reset stages
    let stages = initialPipelineStages.map((s) => ({ ...s, s: "pending" }));
    setPipeline(stages);
    // Advance one stage every 600ms
    let idx = 0;
    const advance = () => {
      if (idx >= stages.length) {
        setPipelineRunning(false);
        setToast({ kind: "ok", title: "Pipeline complete", body: dryRun ? "Dry run · no orders placed" : "Live · 2 orders queued" });
        return;
      }
      stages = stages.map((s, i) => {
        if (i < idx) return { ...s, s: "done" };
        if (i === idx) return { ...s, s: "running" };
        return { ...s, s: "pending" };
      });
      setPipeline(stages);
      idx++;
      setTimeout(advance, 600);
    };
    setTimeout(advance, 200);
  };

  const sendToPaper = (sig) => {
    if (!sig.halal) {
      setToast({ kind: "error", title: "Blocked", body: `${sig.symbol} failed AAOIFI screen` });
      return;
    }
    const size = Math.floor(1000 / sig.price);
    setToast({ kind: "ok", title: "Paper trade sent", body: `${sig.symbol} · BUY · ${size} sh @ $${sig.price.toFixed(2)}` });
  };

  return (
    <>
      <Sidebar />
      <CommandBar
        etClock={etClock}
        symbolCount={mockSignals.length}
        query={query}
        setQuery={setQuery}
        onRefresh={() => setToast({ kind: "ok", title: "Refreshed", body: "Live data · just now" })}
      />
      <MarketStrip market={mockMarket} clock={clock} />

      <main className="main">
        <div className="workflow">
          <ScanColumn
            signals={filteredSignals}
            selectedSymbol={selectedSymbol}
            onSelect={setSelectedSymbol}
            market={mockMarket}
          />
          <AnalyzeColumn
            signal={selected}
            onTrade={sendToPaper}
          />
          <TradeColumn
            portfolio={mockPortfolio}
            positions={mockPositions}
            paper={mockPaper}
            pipeline={pipeline}
            running={pipelineRunning}
            dryRun={dryRun}
            setDryRun={setDryRun}
            onRunPipeline={runPipeline}
            guards={mockGuards}
            schedule={mockSchedule}
          />
        </div>
        <LowerRow
          models={mockModels}
          sectors={mockSectors}
          signal={selected}
          votes={mockConsensusVotes}
        />
      </main>

      <StatusBar pipelineRunning={pipelineRunning} />

      {toast && (
        <div className={"toast" + (toast.kind === "error" ? " error" : "")}>
          <div className="title">{toast.title}</div>
          <div className="body">{toast.body}</div>
        </div>
      )}
    </>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
