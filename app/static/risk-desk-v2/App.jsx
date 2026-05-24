// App.jsx — Risk Desk composition (wired to /api/risk/status + guards)

function App() {
  const [blocks, setBlocks]           = useState(initialBlocks);
  const [fractional, setFractional]   = useState(0.5);
  const [alerts, setAlerts]           = useState(mockAlerts);
  const [stress, setStress]           = useState(mockStressTests);
  const [stressRunning, setStressRunning] = useState(false);
  const [riskMetrics, setRiskMetrics] = useState(mockRiskMetrics);
  const [kelly, setKelly]             = useState(mockKelly);
  const [strategies, setStrategies]   = useState(mockStrategies);
  const [positions, setPositions]     = useState(mockPositionRisk);
  const [loading, setLoading]         = useState(true);

  // Fetch real data on mount
  useEffect(() => {
    (async () => {
      try {
        // 1. Risk status from our /api/risk/status endpoint
        const rsRes = await fetch('/api/risk/status');
        if (rsRes.ok) {
          const rs = await rsRes.json();

          // Map to riskMetrics shape
          setRiskMetrics(prev => ({
            ...prev,
            maxDrawdown:  -(rs.portfolio_drawdown_pct || 0),
            level:        rs.portfolio_drawdown_pct > 10 ? "HIGH"
                          : rs.portfolio_drawdown_pct > 5 ? "MEDIUM" : "LOW",
          }));

          // Map to Kelly shape
          setKelly(prev => ({
            ...prev,
            enabledAfterTrades: rs.kelly_enabled_after_trades || prev.enabledAfterTrades,
            staticCap:          (rs.max_position_pct || 20) / 100,
          }));

          // Map blocks: kill_switch, modular_guards state
          setBlocks(prev => prev.map(b => {
            if (b.id === "kill")     return { ...b, toggle: !!rs.kill_switch, state: rs.kill_switch ? "armed" : "off" };
            if (b.id === "halal")    return { ...b, toggle: !!rs.modular_guards, state: rs.modular_guards ? "on" : "off" };
            if (b.id === "autotrade") return { ...b, state: rs.trading_blocked ? "off" : "on" };
            return b;
          }));
        }

        // 2. Guard events from /api/v1/guards/recent (if available)
        try {
          const gRes = await fetch('/api/v1/guards/recent');
          if (gRes.ok) {
            const guards = await gRes.json();
            if (Array.isArray(guards) && guards.length > 0) {
              setAlerts(guards.slice(0, 6).map(g => ({
                kind: g.passed ? "ok" : (g.blocking ? "danger" : "warning"),
                lab:  `${g.guard_name || g.name}: ${g.reason || g.message || "event"}`,
                ts:   g.ts || g.created_at ? new Date(g.created_at || g.ts).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", hour12: false }) + " ET" : "—",
              })));
            }
          }
        } catch (_) {/* silent fallback to mockAlerts */}

        // 3. Overview positions for position risk table
        try {
          const ovRes = await fetch('/api/v1/overview');
          if (ovRes.ok) {
            const ov = await ovRes.json();
            const pos = ov.positions || ov.open_positions;
            if (Array.isArray(pos) && pos.length > 0) {
              setPositions(pos.map(p => ({
                sym:       p.symbol || p.sym,
                weight:    p.market_value_pct || p.weight || 0,
                varContrib: p.var_contrib || 0,
                corr:      p.correlation || 0,
                beta:      p.beta || 1,
                mDD:       Math.abs(p.max_drawdown_pct || p.mdd || 0),
              })));
            }
          }
        } catch (_) {/* silent */}

      } catch (err) {
        console.warn("Risk Desk: API fetch failed, showing mock data.", err.message);
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const toggleBlock = (id) => {
    setBlocks((prev) => prev.map((b) => b.id === id ? { ...b, toggle: !b.toggle } : b));
  };

  const ackAlert = (i) => {
    setAlerts((prev) => prev.filter((_, idx) => idx !== i));
  };

  const runStress = () => {
    setStressRunning(true);
    setTimeout(() => {
      setStress((prev) => prev.map((t) => ({ ...t })));
      setStressRunning(false);
    }, 800);
  };

  const effectiveKelly = useMemo(() => {
    const eff = Math.min(kelly.staticCap, kelly.fStar * fractional);
    return { ...kelly, fractional, effective: eff };
  }, [fractional, kelly]);

  return (
    <div className="rd-page">
      <RiskHeader level={riskMetrics.level} />
      <VaRMetrics m={riskMetrics} />
      <KellySizing kelly={effectiveKelly} strategies={strategies} fractional={fractional} setFractional={setFractional} />

      <div className="rd-grid-2" style={{ marginTop: 14 }}>
        <PositionRisk positions={positions} />
        <BlockToggles blocks={blocks} onToggle={toggleBlock} />
      </div>

      <div className="rd-grid-2">
        <StressTests tests={stress} onRun={runStress} running={stressRunning} />
        <RiskAlerts alerts={alerts} onAck={ackAlert} />
      </div>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
