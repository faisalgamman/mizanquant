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

  // Live data state — starts EMPTY/null, populated from APIs. No mock fallback:
  // every value is real or shows "—" until the API answers (honest display).
  const [signals, setSignals]     = useState([]);
  const [signalsStatus, setSignalsStatus] = useState("computing"); // computing | ready | empty
  const [market, setMarket]       = useState({ vix: null, spy_regime: "—", spy_trend: "", breadth: null, credit: null, liquidity: null, market_open: false });
  const [portfolio, setPortfolio] = useState({ equity: null, dayPnl: null, dayPnlPct: null, cash: null, buyPower: null, openPos: null, todayExits: null });
  const [positions, setPositions] = useState([]);
  const [paper, setPaper]         = useState([]);
  const [models, setModels]       = useState([]);   // real leaderboard or honest empty
  const [sectors, setSectors]     = useState([]);   // real sector perf or honest empty
  const [consensus, setConsensus] = useState(null);
  const [guards, setGuards]       = useState([]);    // real guard rejections (no mock)
  const [schedule, setSchedule]   = useState([]);    // real daily schedule (no mock)
  const [watch, setWatch] = useState({market_block:"", watch:[]});  // WATCH list — near-miss stocks
  const [system, setSystem]       = useState(null);  // real system status (auto-trade/kill-switch/regime)
  const [realStages, setRealStages] = useState([]);  // real pipeline stages from /api/v1/overview
  const [analyze, setAnalyze]     = useState(null);  // {symbol, scoring, plan} — real scoring + trade plan
  const [forecast, setForecast]   = useState(null);  // {symbol, expected, prob_profit, ...} — ML forecast
  const [forecastHorizon, setForecastHorizon] = useState(20);  // selectable forecast horizon (days)
  const [risers, setRisers] = useState([]);     // predicted risers (Monte Carlo)
  const [risersMeta, setRisersMeta] = useState({}); // market_soft flag etc.
  const [marketNews, setMarketNews] = useState([]);  // general market news
  const [indicators, setIndicators] = useState([]);  // main market indicators
  // Two-scanner split: Weekly (swing /buys, Option-A) vs Monthly (composite
  // deep-picks, rebalanced). Each scanner has its OWN simulated paper ledger
  // (weekly=PV, monthly=PVM); real money for either half is released ONLY after
  // its ledger graduates. The tab below switches the ScanColumn's source.
  const [scanMode, setScanMode]   = useState(() =>
    (typeof location !== "undefined" && location.hash.replace("#", "") === "monthly") ? "monthly" : "weekly");
  const [monthly, setMonthly]     = useState([]);          // composite picks (real or honest empty)
  const [monthlyStatus, setMonthlyStatus] = useState("computing"); // computing | ready | empty
  const [ledgerW, setLedgerW]     = useState(null);        // weekly paper-ledger status (PV)
  const [ledgerM, setLedgerM]     = useState(null);        // monthly paper-ledger status (PVM)
  const [cardSymbol, setCardSymbol] = useState(null);      // Stock ID Card target symbol
  const [brokerHealth, setBrokerHealth] = useState(null);    // IBKR paper connectivity

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
          spark:    Array.isArray(s.spark) ? s.spark : [],  // real sparkline only; empty → no chart (no random noise)
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

  // Aggregate overview — market, portfolio, positions, sectors, guards,
  // schedule, system status, and REAL pipeline stages. Everything real; any
  // missing field falls through to a "—"/empty state (never a mock value).
  const loadOverview = async () => {
    try {
      const ov = await (await fetch('/api/v1/overview')).json();
      const num = (x) => (x == null ? null : (typeof x === 'number' ? x : Number(x)));

      // Market context (real VIX / regime / breadth / credit / liquidity).
      const mc = ov.market_context || {};
      const sreg = mc.spy_regime || {};
      setMarket({
        vix:        num((sreg && sreg.vix) ?? (mc.vix && mc.vix.vix) ?? mc.vix),
        spy_regime: (sreg.regime ?? mc.regime ?? "—"),
        spy_trend:  (sreg.ema_50 != null && sreg.ema_200 != null) ? "EMA50 vs EMA200" : "",
        breadth:    num((mc.breadth && mc.breadth.breadth_pct) ?? mc.breadth),
        credit:     num((mc.credit && mc.credit.ratio) ?? mc.credit),
        liquidity:  num((mc.liquidity && mc.liquidity.liquidity_pct) ?? mc.liquidity),
        market_open: mc._market_open ?? false,
      });

      // Portfolio + positions.
      const pf = ov.portfolio || {};
      if (pf.equity != null) {
        setPortfolio({
          equity: pf.equity || 0, dayPnl: pf.daily_pnl || 0, dayPnlPct: pf.daily_pnl_pct || 0,
          cash: pf.cash || 0, buyPower: pf.buying_power || 0, openPos: pf.open_positions || 0, todayExits: 0,
        });
      }
      const pos = pf.positions;
      if (Array.isArray(pos)) {
        setPositions(pos.map(p => ({
          sym: p.symbol, qty: p.qty || 0, entry: p.avg_entry || 0,
          last: p.current_price || 0, pnl: p.unrealized_pl || 0,
          pnlPct: (p.unrealized_plpc || 0) * 100,
          mktVal: p.market_value || 0,
        })));
      }

      // Real sector performance (1-day change).
      const secs = mc._sectors;
      if (Array.isArray(secs)) {
        const haram = /financ|reit|real estate|bank|insur/i;
        setSectors(secs.filter(s => s.available !== false && s.perf_1d != null).map(s => ({
          name: s.name || s.ticker, chg: s.perf_1d, halal: !haram.test(s.name || s.ticker || ''),
        })));
      }

      // Real guards + schedule.
      const gr = ov.guards_recent || {};
      setGuards(Object.entries(gr).map(([name, hits]) => ({
        name, hits: hits || 0,
        color: (hits || 0) === 0 ? "var(--positive)" : (hits || 0) < 3 ? "var(--warning)" : "var(--negative)",
      })));
      const sch = ov.pipeline && ov.pipeline.schedule;
      if (Array.isArray(sch)) setSchedule(sch.map(s => ({ time: s.time, label: s.task || s.label || "" })));

      // Real pipeline stages (status from the orchestrator).
      const stmap = { completed: "done", ok: "done", running: "running", failed: "failed" };
      const stages = (ov.pipeline && ov.pipeline.stages) || [];
      if (Array.isArray(stages) && stages.length > 0) {
        setRealStages(stages.map(s => ({ n: s.label || s.name, s: stmap[s.status] || "pending" })));
      }

      // Real system status (auto-trade / kill-switch / regime) for the footer.
      if (ov.system) setSystem(ov.system);
    } catch (_) {}

    // Paper trades (real).
    try {
      const pt = await (await fetch('/api/v1/paper/trades')).json();
      if (Array.isArray(pt)) {
        setPaper(pt.slice(0, 5).map(t => ({
          sym: t.symbol || t.sym, side: t.side || 'buy',
          entry: t.entry_price || t.entry || 0, last: t.current_price || t.last || t.entry_price || 0,
          pnl: t.unrealized_pl || t.pnl || 0, status: t.status || 'open',
          when: t.created_at ? new Date(t.created_at).toLocaleTimeString('en-US', {hour:'2-digit',minute:'2-digit',hour12:false}) : '—',
        })));
      }
    } catch (_) {}

    // Model leaderboard (real or honest empty).
    try {
      const ml = await (await fetch('/api/v1/models/leaderboard')).json();
      const arr = ml.models || ml;
      if (Array.isArray(arr) && arr.length > 0) {
        setModels(arr.slice(0, 6).map(m => ({
          name: m.model_id || m.name, family: m.family || 'classic',
          sharpe: m.sharpe || 0, return: m.return_pct || m.return || 0, status: m.status || 'idle',
        })));
      }
    } catch (_) {}
  };

  // Monthly composite scanner — real deep-picks (technical + FUNDAMENTAL +
  // sentiment + AI + halal). Honest empty/"computing" when the screener cache
  // is still warming. Never fabricates rows.
  const loadMonthly = async () => {
    try {
      const j = await (await fetch('/api/screener/deep-picks?limit=25')).json();
      const rows = (j && Array.isArray(j.results)) ? j.results : [];
      if (rows.length === 0) {
        setMonthlyStatus((j && j.status === "scanning") ? "computing" : "empty");
        return false;
      }
      setMonthly(rows.filter(r => r.symbol && r.price).map(r => ({
        symbol:  r.symbol,
        company: r.company_name || r.symbol,
        price:   r.price || 0,
        chg:     r.chg_1w || r.change_pct || 0,
        score:   r.composite_score || 0,
        verdict: r.signal_composite || verdictFromScore(r.composite_score || 0),
        halal:   r.is_halal !== false,
        sector:  r.sector || "—",
        spark:   Array.isArray(r.spark) ? r.spark : [],
        // Composite breakdown (real sub-scores from deep-picks)
        tech:   r.score_tech, fund: r.score_fund, sent: r.score_sentiment,
        ai:     r.score_ai,   halalS: r.score_halal, grade: r.f_grade,
      })));
      setMonthlyStatus("ready");
      return true;
    } catch (_) {
      setMonthlyStatus("computing");
      return false;
    }
  };

  // Per-scanner paper-ledger status (graduation / open / closed) for the
  // honest "not graduated → no real money" banner on each tab.
  const loadLedgers = async () => {
    try { setLedgerW(await (await fetch('/paper_validation/status?scanner=weekly')).json()); } catch (_) {}
    try { setLedgerM(await (await fetch('/paper_validation/status?scanner=monthly')).json()); } catch (_) {}
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

  // Load live data on mount, then poll the aggregate overview every 60s so the
  // market strip / portfolio / pipeline never go stale (fixes the old
  // set-once-at-mount divergence). Signals poll separately until the cache warms.
  useEffect(() => {
    (async () => {
      await loadOverview();
      const got = await loadBuys();
      if (!got) {
        let tries = 0;
        const poll = setInterval(async () => {
          tries += 1;
          const ok = await loadBuys();
          if (ok || tries >= 12) clearInterval(poll);  // stop after ~3 min
        }, 15000);
      }
      setLoading(false);
    })();
    const id = setInterval(loadOverview, 60000);
    return () => clearInterval(id);
  }, []);

  // Load both paper-ledger statuses on mount, refresh every 60s.
  useEffect(() => {
    loadLedgers();
    const id = setInterval(loadLedgers, 60000);
    return () => clearInterval(id);
  }, []);

  // Fix: define window.selectIntelSymbol so clicking a Stock Intelligence row
  // actually opens the Stock ID Card (was called in StockIntel.jsx but never defined).
  useEffect(() => {
    window.selectIntelSymbol = (sym) => setCardSymbol(sym);
    return () => { try { delete window.selectIntelSymbol; } catch (e) {} };
  }, []);

  // Fetch IBKR paper broker health on mount, refresh every 60s.
  useEffect(() => {
    const probe = async () => {
      try { const r = await fetch('/api/v1/broker/health'); setBrokerHealth(await r.json()); }
      catch (_) { setBrokerHealth({ connected: false }); }
    };
    probe();
    const id = setInterval(probe, 60000);
    return () => clearInterval(id);
  }, []);

  // Sidebar
  useEffect(() => {
    const onHash = () => {
      const h = location.hash.replace("#", "");
      if (h === "weekly" || h === "monthly") setScanMode(h);
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  // Lazily load the monthly composite scan the first time its tab is opened,
  // polling until the screener cache warms (same pattern as weekly /buys).
  useEffect(() => {
    if (scanMode !== "monthly" || monthly.length > 0) return;
    let tries = 0, stop = false;
    (async () => {
      const ok = await loadMonthly();
      if (ok || stop) return;
      const poll = setInterval(async () => {
        tries += 1;
        if (stop || await loadMonthly() || tries >= 12) clearInterval(poll);
      }, 15000);
    })();
    return () => { stop = true; };
  }, [scanMode]);

  // Fetch WATCH list — near-miss stocks (display only, never traded)
  useEffect(() => {
    const fetchWatch = async () => {
      try { const w = await (await fetch('/api/screener/watch?limit=20')).json(); setWatch(w); }
      catch (_) {}
    };
    fetchWatch();
    const id = setInterval(fetchWatch, 120000);  // every 2 min
    return () => clearInterval(id);
  }, []);

  // Fetch bottom panel: risers + market news + indicators
  useEffect(() => {
    const fetchAll = async () => {
      try { const r = await (await fetch('/api/forecast/risers?limit=8')).json();
            setRisers(r.risers || []); setRisersMeta({market_soft: r.market_soft}); } catch (_) {}
      try { setMarketNews((await (await fetch('/api/market/news?limit=8')).json()).news || []); } catch (_) {}
      try { setIndicators((await (await fetch('/api/market/indicators')).json()).indicators || []); } catch (_) {}
    };
    fetchAll();
    const id = setInterval(fetchAll, 120000);
    return () => clearInterval(id);
  }, []);

  // Real signals only — never fabricate. Empty → ScanColumn shows scanning state.
  const displaySignals = signals;
  // The active list depends on the scanner tab; the selected symbol can come
  // from either (Analyze/Forecast/Consensus are symbol-keyed, mode-agnostic).
  const activeList     = scanMode === "monthly" ? monthly : displaySignals;
  const selectedSym    = selectedSymbol || activeList[0]?.symbol || displaySignals[0]?.symbol;

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

  // Fetch REAL scoring + trade plan for the selected symbol. Replaces the old
  // synthesized sub-scores / "Mock trade plan" that AnalyzeColumn used to invent.
  useEffect(() => {
    if (!selectedSym) { setAnalyze(null); return; }
    let cancelled = false;
    setAnalyze({ symbol: selectedSym, scoring: null, plan: null, loading: true });
    (async () => {
      const [scoring, plan] = await Promise.all([
        fetch('/api/v1/scoring/weighted?symbol=' + encodeURIComponent(selectedSym)).then(r => r.json()).catch(() => null),
        fetch('/api/v1/trade/plan?symbol=' + encodeURIComponent(selectedSym)).then(r => r.json()).catch(() => null),
      ]);
      if (!cancelled) setAnalyze({ symbol: selectedSym, scoring, plan, loading: false });
    })();
    return () => { cancelled = true; };
  }, [selectedSym]);

  // Probabilistic price forecast for the selected symbol (re-fetches on horizon change).
  useEffect(() => {
    if (!selectedSym) { setForecast(null); return; }
    let cancelled = false;
    setForecast({ symbol: selectedSym, loading: true, data: null });
    (async () => {
      let data = null;
      try {
        data = await fetch('/api/v1/forecast/' + encodeURIComponent(selectedSym) + '?horizon=' + forecastHorizon)
          .then(r => r.json());
      } catch (_) { data = null; }
      if (!cancelled) setForecast({ symbol: selectedSym, loading: false, data });
    })();
    return () => { cancelled = true; };
  }, [selectedSym, forecastHorizon]);

  const _filterByQuery = (list) => {
    if (!query) return list;
    const q = query.toUpperCase();
    return list.filter(s => s.symbol.includes(q) || (s.company || '').toUpperCase().includes(q));
  };
  const filteredSignals = useMemo(() => _filterByQuery(displaySignals), [query, displaySignals]);
  const filteredMonthly = useMemo(() => _filterByQuery(monthly), [query, monthly]);

  const selected =
    activeList.find(s => s.symbol === selectedSym) ||
    displaySignals.find(s => s.symbol === selectedSym) ||
    monthly.find(s => s.symbol === selectedSym) ||
    activeList[0] || displaySignals[0];

  // Trigger the REAL pipeline and reflect its actual stage status (no fake
  // timed animation). Progress is shown from /api/v1/overview pipeline.stages.
  const runPipeline = async () => {
    if (pipelineRunning) return;
    setPipelineRunning(true);
    try {
      await fetch(`/api/v1/pipeline/run?dry_run=${dryRun}`, { method: 'POST' });
      setToast({ kind: "ok", title: "Pipeline triggered", body: dryRun ? "Dry run · no orders placed" : "Live · orders may be queued" });
    } catch (_) {
      setToast({ kind: "error", title: "Pipeline failed to start", body: "Could not reach the pipeline endpoint" });
    }
    await loadOverview();   // pull the real stage status
    setPipelineRunning(false);
  };

  const sendToPaper = async (sig) => {
    if (!sig.halal) {
      setToast({ kind: "error", title: "Blocked", body: `${sig.symbol} failed the halal screen` });
      return;
    }
    // Real risk-based size + full bracket levels from the loaded trade plan.
    const plan = (analyze && analyze.symbol === sig.symbol) ? analyze.plan : null;
    const shares = plan ? (plan.shares ?? plan.qty) : null;
    const entry  = plan ? (plan.strategy_entry ?? plan.entry_price ?? plan.entry ?? sig.price) : null;
    const stop   = plan ? (plan.strategy_stop ?? plan.stop_loss ?? plan.stop) : null;
    const tp     = plan ? (plan.strategy_tp1 ?? plan.take_profit ?? plan.tp1) : null;
    if (!shares || shares <= 0 || !entry || !stop || !tp) {
      setToast({ kind: "error", title: "Plan incomplete",
                 body: `Open ${sig.symbol} in Analyze so its entry/stop/TP/size load first` });
      return;
    }
    setToast({ kind: "ok", title: "Sending…", body: `${sig.symbol} → IBKR paper (bracket)` });
    try {
      const r = await fetch('/api/v1/broker/execute', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol: sig.symbol, side: 'buy', entry_price: entry,
                               stop_loss: stop, take_profit: tp, shares }),
      });
      const j = await r.json();
      if (j && j.success) {
        setToast({ kind: "ok", title: "IBKR paper order placed",
                   body: `${sig.symbol} · BUY ${shares} sh · id ${j.broker_order_id}` });
      } else {
        const why = (j && j.reason) === "broker_offline"
          ? "IB Gateway offline — start the gateway service"
          : ((j && (j.detail || j.reason)) || "rejected");
        setToast({ kind: "error", title: "Not placed", body: `${sig.symbol}: ${why}` });
      }
    } catch (e) {
      setToast({ kind: "error", title: "Network error", body: `${sig.symbol}: ${e}` });
    }
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
            scanMode={scanMode}
            onScanMode={(m) => {
              setScanMode(m);
              if (typeof history !== "undefined" && history.replaceState) {
                history.replaceState(null, "", "#" + m);
              }
            }}
            signals={filteredSignals}
            monthlySignals={filteredMonthly}
            selectedSymbol={selectedSym}
            onSelect={setSelectedSymbol}
            market={market}
            signalsStatus={signalsStatus}
            monthlyStatus={monthlyStatus}
            ledgerWeekly={ledgerW}
            ledgerMonthly={ledgerM}
            watch={watch}
          />
          <AnalyzeColumn
            signal={selected}
            analyze={analyze}
            forecast={forecast}
            horizon={forecastHorizon}
            onHorizon={setForecastHorizon}
            onTrade={sendToPaper}
            brokerHealth={brokerHealth}
          />
          <TradeColumn
            portfolio={portfolio}
            positions={positions}
            paper={paper}
            pipeline={realStages.length ? realStages : pipeline}
            running={pipelineRunning}
            dryRun={dryRun}
            setDryRun={setDryRun}
            onRunPipeline={runPipeline}
            guards={guards}
            schedule={schedule}
          />
        </div>
        <StockIntel />
        {cardSymbol && (
          <StockCard
            symbol={cardSymbol}
            account={(portfolio && portfolio.equity) || 10000}
            onClose={() => setCardSymbol(null)}
          />
        )}
        <LowerRow
          risers={risers}
          marketNews={marketNews}
          indicators={indicators}
          risersMeta={risersMeta}
        />
      </main>

      <StatusBar pipelineRunning={pipelineRunning} system={system} />

      {toast && (
        <div className={"toast" + (toast.kind === "error" ? " error" : "")}>
          <div className="title">{toast.title}</div>
          <div className="body">{toast.body}</div>
        </div>
      )}
      <AiWidget />
    </>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
