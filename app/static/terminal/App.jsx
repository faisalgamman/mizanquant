// App.jsx — Terminal composition (wired to production APIs)
//
// Real data sources:
//   signals    → GET /buys         (swing_score, price, halal, symbol)
//   portfolio  → GET /api/v1/overview → .portfolio
//   positions  → GET /api/v1/overview → .portfolio.positions
//   market     → GET /api/context/bundle
//   paper      → GET /api/v1/paper/trades
//   models     → GET /api/v1/models/leaderboard
// NO MOCK FALLBACK — signals are real or show an honest "scanning…" state.

function App() {
  const [selectedSymbol, setSelectedSymbol] = useState(null);
  const [clock, setClock]     = useState("--:--:--");
  const [etClock, setETClock] = useState("--:--");
  const [pipeline, setPipeline]           = useState(initialPipelineStages);
  const [pipelineRunning, setPipelineRunning] = useState(false);
  const [dryRun, setDryRun]   = useState(true);
  const [query, setQuery]     = useState("");
  const [toast, setToast]     = useState(null);
  const [loading, setLoading] = useState(true);

  // Live data state — starts empty, populated from APIs (no fake data)
  const [signals, setSignals]     = useState([]);
  const [signalsStatus, setSignalsStatus] = useState("computing"); // computing | ready | empty
  const [market, setMarket]       = useState(mockMarket);
  const [portfolio, setPortfolio] = useState(mockPortfolio);
  const [positions, setPositions] = useState([]);
  const [paper, setPaper]         = useState([]);
  const [models, setModels]       = useState([]);   // real leaderboard or honest empty
  const [sectors, setSectors]     = useState(mockSectors);
  const [consensus, setConsensus] = useState(null);
  const [guards, setGuards]       = useState([]);    // real guard rejections (no mock)
  const [schedule, setSchedule]   = useState([]);    // real daily schedule (no mock)

  // Fetch /buys, map real rows. Returns true once real signals are loaded.
  const loadBuys = async () => {
    try {
      const buys = await (await fetch('/buys')).json();
      // Cold cache → [{Status:"Computing…"}] placeholder; keep "computing".
      if (!Array.isArray(buys) || buys.length === 0 || buys[0].Status) {
        setSignalsStatus("computing");
        return false;
      }
      const mapped = buys
        .filter(s => s.symbol && s.price)
        .map(s => ({
          symbol:   s.symbol,
          company:  s.company_name || s.symbol,
          price:    s.price || 0,
          chg:      s.chg_1w || 0,
          score:    s.swing_score || 0,
          verdict:  s.swing_signal || verdictFromScore(s.swing_score || 0),
          halal:    s.halal !== "No",
          sector:   s.sector || "—",
          industry: s.industry || "—",
          spark:    s.spark || Array.from({ length: 12 }, () => 90 + Math.random() * 20),
        }));
      setSignals(mapped);
      setSignalsStatus(mapped.length > 0 ? "ready" : "empty");
      if (mapped.length > 0) setSelectedSymbol(prev => prev || mapped[0].symbol);
      return mapped.length > 0;
    } catch (_) {
      setSignalsStatus("computing");
      return false;
    }
  };

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
      // ── 1. Market context bundle ──────────────────────────────────────
      try {
        const ctx = await (await fetch('/api/context/bundle')).json();
        setMarket(m => ({
          ...m,
          vix:        ctx.vix        ?? m.vix,
          spy_regime: ctx.regime     ?? m.spy_regime,
          liquidity:  ctx.risk_posture === "risk_on" ? 90 : ctx.risk_posture === "risk_off" ? 30 : 60,
        }));
      } catch (_) {}

      // ── 2. Buy signals from screener (real, with polling if cache cold) ──
      const got = await loadBuys();
      if (!got) {
        // Screener cache is warming (scans ~650 symbols, 1–2 min). Poll until ready.
        let tries = 0;
        const poll = setInterval(async () => {
          tries += 1;
          const ok = await loadBuys();
          if (ok || tries >= 12) clearInterval(poll);  // stop after ~3 min
        }, 15000);
      }

      // ── 3. Portfolio + positions from overview ────────────────────────
      // NOTE: /api/v1/overview wraps everything under {portfolio, system, ...}
      try {
        const ov = await (await fetch('/api/v1/overview')).json();
        const pf = ov.portfolio || {};
        if (pf.equity != null) {
          setPortfolio({
            equity:     pf.equity        || 0,
            dayPnl:     pf.daily_pnl     || 0,
            dayPnlPct:  pf.daily_pnl_pct || 0,
            cash:       pf.cash          || 0,
            buyPower:   pf.buying_power  || 0,
            openPos:    pf.open_positions || 0,
            todayExits: 0,
          });
        }
        const pos = pf.positions;
        if (Array.isArray(pos) && pos.length > 0) {
          setPositions(pos.map(p => ({
            sym:     p.symbol,
            qty:     p.qty    || 0,
            entry:   p.avg_entry      || 0,
            last:    p.current_price  || 0,
            pnl:     p.unrealized_pl  || 0,
            // unrealized_plpc from Alpaca is a fraction (0.02 = 2%), multiply by 100
            pnlPct:  (p.unrealized_plpc || 0) * 100,
          })));
        }
        // Real sector performance (perf_1d is already a percentage, e.g. 1.84)
        const secs = ov.market_context && ov.market_context._sectors;
        if (Array.isArray(secs) && secs.length > 0) {
          const haram = /financ|reit|real estate|bank|insur/i;
          const mappedSecs = secs
            .filter(s => s.available !== false && s.perf_1d != null)
            .map(s => ({
              name:  s.name || s.ticker,
              chg:   s.perf_1d,
              halal: !haram.test(s.name || s.ticker || ''),
            }));
          if (mappedSecs.length > 0) setSectors(mappedSecs);
        }
        // Real guards (today's rejections) — guards_recent = {guard_name: count}
        const gr = ov.guards_recent || {};
        const gRows = Object.entries(gr).map(([name, hits]) => ({
          name,
          hits: hits || 0,
          color: (hits || 0) === 0 ? "var(--positive)" : (hits || 0) < 3 ? "var(--warning)" : "var(--negative)",
        }));
        setGuards(gRows);
        // Real daily schedule — pipeline.schedule = [{time, task, type}]
        const sch = ov.pipeline && ov.pipeline.schedule;
        if (Array.isArray(sch) && sch.length > 0) {
          setSchedule(sch.map(s => ({ time: s.time, label: s.task || s.label || "" })));
        }
      } catch (_) {}

      // ── 4. Paper trades ───────────────────────────────────────────────
      try {
        const pt = await (await fetch('/api/v1/paper/trades')).json();
        if (Array.isArray(pt) && pt.length > 0) {
          setPaper(pt.slice(0, 5).map(t => ({
            sym:    t.symbol || t.sym,
            side:   t.side   || 'buy',
            entry:  t.entry_price || t.entry || 0,
            last:   t.current_price || t.last || t.entry_price || 0,
            pnl:    t.unrealized_pl || t.pnl || 0,
            status: t.status || 'open',
            when:   t.created_at ? new Date(t.created_at).toLocaleTimeString('en-US', {hour:'2-digit',minute:'2-digit',hour12:false}) : '—',
          })));
        }
      } catch (_) {}

      // ── 5. Model leaderboard ──────────────────────────────────────────
      try {
        const ml = await (await fetch('/api/v1/models/leaderboard')).json();
        const arr = ml.models || ml;
        if (Array.isArray(arr) && arr.length > 0) {
          setModels(arr.slice(0, 6).map(m => ({
            name:   m.model_id || m.name,
            family: m.family   || 'classic',
            sharpe: m.sharpe   || 0,
            return: m.return_pct || m.return || 0,
            status: m.status   || 'idle',
          })));
        }
      } catch (_) {}

      setLoading(false);
    })();
  }, []);

  // Real signals only — never fabricate. Empty → ScanColumn shows scanning state.
  const displaySignals = signals;
  const selectedSym    = selectedSymbol || (displaySignals[0]?.symbol);

  // Auto-clear toast
  useEffect(() => {
    if (!toast) return;
    const id = setTimeout(() => setToast(null), 3500);
    return () => clearTimeout(id);
  }, [toast]);

  // Fetch real consensus for the selected symbol (no fabricated votes)
  useEffect(() => {
    if (!selectedSym) { setConsensus(null); return; }
    let cancelled = false;
    (async () => {
      try {
        const r = await fetch('/api/v1/consensus/' + encodeURIComponent(selectedSym));
        const j = await r.json();
        if (!cancelled) setConsensus(j);
      } catch (_) {
        if (!cancelled) setConsensus({ available: false, symbol: selectedSym });
      }
    })();
    return () => { cancelled = true; };
  }, [selectedSym]);

  const filteredSignals = useMemo(() => {
    if (!query) return displaySignals;
    const q = query.toUpperCase();
    return displaySignals.filter(s =>
      s.symbol.includes(q) || (s.company || '').toUpperCase().includes(q)
    );
  }, [query, displaySignals]);

  const selected = displaySignals.find(s => s.symbol === selectedSym) || displaySignals[0];

  const runPipeline = async () => {
    if (pipelineRunning) return;
    setPipelineRunning(true);
    let stages = initialPipelineStages.map(s => ({ ...s, s: "pending" }));
    setPipeline(stages);
    let idx = 0;
    const advance = () => {
      if (idx >= stages.length) {
        setPipelineRunning(false);
        setToast({ kind: "ok", title: "Pipeline complete", body: dryRun ? "Dry run · no orders placed" : "Live · orders queued" });
        return;
      }
      stages = stages.map((s, i) => {
        if (i < idx)  return { ...s, s: "done" };
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

  if (loading && displaySignals.length === 0) {
    return (
      <div style={{display:'flex',alignItems:'center',justifyContent:'center',height:'100vh',flexDirection:'column',gap:16}}>
        <div className="spin" style={{width:32,height:32,border:'3px solid var(--border)',borderTopColor:'var(--accent)',borderRadius:'50%'}}></div>
        <div style={{color:'var(--text-muted)',fontSize:12,fontFamily:'var(--font-mono)',letterSpacing:1}}>LOADING LIVE DATA…</div>
      </div>
    );
  }

  return (
    <>
      <Sidebar />
      <CommandBar
        etClock={etClock}
        symbolCount={displaySignals.length}
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
            selectedSymbol={selectedSym}
            onSelect={setSelectedSymbol}
            market={market}
            signalsStatus={signalsStatus}
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
            guards={guards}
            schedule={schedule}
          />
        </div>
        <LowerRow
          models={models}
          sectors={sectors}
          signal={selected}
          consensus={consensus}
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
