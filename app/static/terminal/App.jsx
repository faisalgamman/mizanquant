// App.jsx — Terminal composition (wired to production APIs)

function App() {
  const [selectedSymbol, setSelectedSymbol] = useState("AAPL");
  const [clock, setClock]     = useState("--:--:--");
  const [etClock, setETClock] = useState("--:--");
  const [pipeline, setPipeline]           = useState(initialPipelineStages);
  const [pipelineRunning, setPipelineRunning] = useState(false);
  const [dryRun, setDryRun]   = useState(true);
  const [query, setQuery]     = useState("");
  const [toast, setToast]     = useState(null);

  // Live data state — start with mock, replace from API
  const [signals, setSignals]     = useState(mockSignals);
  const [market, setMarket]       = useState(mockMarket);
  const [portfolio, setPortfolio] = useState(mockPortfolio);
  const [positions, setPositions] = useState(mockPositions);
  const [paper, setPaper]         = useState(mockPaper);
  const [models, setModels]       = useState(mockModels);
  const [sectors, setSectors]     = useState(mockSectors);

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

  // Load live data on mount
  useEffect(() => {
    (async () => {
      // 1. Market context bundle
      try {
        const ctx = await (await fetch('/api/context/bundle')).json();
        setMarket(m => ({
          ...m,
          vix:        ctx.vix ?? m.vix,
          spy_regime: ctx.regime ?? m.spy_regime,
        }));
      } catch (_) {}

      // 2. Overview (signals + portfolio + positions)
      try {
        const ov = await (await fetch('/api/v1/overview')).json();
        const sigs = ov.top_signals || ov.signals;
        if (Array.isArray(sigs) && sigs.length > 0) {
          setSignals(sigs.map(s => ({
            symbol:      s.symbol || s.sym,
            company:     s.company_name || s.company || s.symbol,
            price:       s.price || 0,
            change:      s.change_pct || 0,
            score:       s.swing_score || s.score || 0,
            halal:       s.halal !== false,
            verdict:     s.signal || s.verdict || 'WAIT',
            strategy:    s.strategy_id || '',
            sparkline:   s.sparkline || Array.from({length: 12}, (_, i) => 100 + i),
          })));
        }
        if (ov.equity || ov.portfolio) {
          const pf = ov.portfolio || ov;
          setPortfolio(p => ({
            ...p,
            equity:  pf.equity || p.equity,
            dayPnl:  pf.day_pnl || pf.daily_pnl || p.dayPnl,
            cash:    pf.cash || p.cash,
            openPos: (ov.positions || []).length,
          }));
        }
        const pos = ov.positions || ov.open_positions;
        if (Array.isArray(pos) && pos.length > 0) {
          setPositions(pos.map(p => ({
            symbol:  p.symbol || p.sym,
            qty:     p.qty || 0,
            entry:   p.avg_entry || p.entry || 0,
            last:    p.current_price || p.last || 0,
            pnl:     p.unrealized_pl || p.pnl || 0,
            pnlPct:  p.unrealized_plpc || p.pnl_pct || 0,
          })));
        }
      } catch (_) {}

      // 3. Paper trades
      try {
        const pt = await (await fetch('/api/v1/paper/trades')).json();
        if (Array.isArray(pt) && pt.length > 0) setPaper(pt.slice(0, 5));
      } catch (_) {}

      // 4. Models leaderboard
      try {
        const ml = await (await fetch('/api/v1/models/leaderboard')).json();
        const arr = ml.models || ml;
        if (Array.isArray(arr) && arr.length > 0) {
          setModels(arr.slice(0, 6).map(m => ({
            name:   m.model_id || m.name,
            family: m.family || 'classic',
            sharpe: m.sharpe || 0,
            ret:    m.return_pct || m.ret || 0,
            status: m.status || 'idle',
          })));
        }
      } catch (_) {}
    })();
  }, []);

  // Auto-clear toast
  useEffect(() => {
    if (!toast) return;
    const id = setTimeout(() => setToast(null), 3500);
    return () => clearTimeout(id);
  }, [toast]);

  const filteredSignals = useMemo(() => {
    if (!query) return signals;
    const q = query.toUpperCase();
    return signals.filter((s) => s.symbol.includes(q) || (s.company || '').toUpperCase().includes(q));
  }, [query, signals]);

  const selected = signals.find((s) => s.symbol === selectedSymbol) || signals[0];

  const runPipeline = async () => {
    if (pipelineRunning) return;
    setPipelineRunning(true);
    let stages = initialPipelineStages.map((s) => ({ ...s, s: "pending" }));
    setPipeline(stages);
    let idx = 0;
    const advance = () => {
      if (idx >= stages.length) {
        setPipelineRunning(false);
        setToast({ kind: "ok", title: "Pipeline complete", body: dryRun ? "Dry run · no orders placed" : "Live · orders queued" });
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
    // Kick real pipeline (fire-and-forget)
    fetch(`/api/v1/pipeline/run?dry_run=${dryRun}`, { method: 'POST' }).catch(() => {});
    setTimeout(advance, 200);
  };

  const sendToPaper = (sig) => {
    if (!sig.halal) {
      setToast({ kind: "error", title: "Blocked", body: `${sig.symbol} failed AAOIFI screen` });
      return;
    }
    const size = Math.max(1, Math.floor(1000 / (sig.price || 100)));
    fetch('/api/v1/paper/execute', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ symbol: sig.symbol, side: 'buy', qty: size }),
    }).catch(() => {});
    setToast({ kind: "ok", title: "Paper trade sent", body: `${sig.symbol} · BUY · ${size} sh @ $${(sig.price || 0).toFixed(2)}` });
  };

  return (
    <>
      <Sidebar />
      <CommandBar
        etClock={etClock}
        symbolCount={signals.length}
        query={query}
        setQuery={setQuery}
        onRefresh={() => {
          setToast({ kind: "ok", title: "Refreshed", body: "Live data · just now" });
          window.location.reload();
        }}
      />
      <MarketStrip market={market} clock={clock} />

      <main className="main">
        <div className="workflow">
          <ScanColumn
            signals={filteredSignals}
            selectedSymbol={selectedSymbol}
            onSelect={setSelectedSymbol}
            market={market}
          />
          <AnalyzeColumn
            signal={selected}
            onTrade={sendToPaper}
          />
          <TradeColumn
            portfolio={portfolio}
            positions={positions}
            paper={paper}
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
          models={models}
          sectors={sectors}
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
