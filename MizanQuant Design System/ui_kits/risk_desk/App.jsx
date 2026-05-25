// App.jsx — Risk Desk composition

function App() {
  const [blocks, setBlocks] = useState(initialBlocks);
  const [fractional, setFractional] = useState(0.5);
  const [alerts, setAlerts] = useState(mockAlerts);
  const [stress, setStress] = useState(mockStressTests);
  const [stressRunning, setStressRunning] = useState(false);

  const toggleBlock = (id) => {
    setBlocks((prev) => prev.map((b) => b.id === id ? { ...b, toggle: !b.toggle } : b));
  };

  const ackAlert = (i) => {
    setAlerts((prev) => prev.filter((_, idx) => idx !== i));
  };

  const runStress = () => {
    setStressRunning(true);
    // Re-shuffle to re-animate bars
    setTimeout(() => {
      setStress((prev) => prev.map((t) => ({ ...t })));
      setStressRunning(false);
    }, 800);
  };

  // Effective Kelly = f* × fractional, capped by static budget
  const effectiveKelly = useMemo(() => {
    const eff = Math.min(mockKelly.staticCap, mockKelly.fStar * fractional);
    return { ...mockKelly, fractional, effective: eff };
  }, [fractional]);

  return (
    <div className="rd-page">
      <RiskHeader level={mockRiskMetrics.level} />
      <VaRMetrics m={mockRiskMetrics} />
      <KellySizing kelly={effectiveKelly} strategies={mockStrategies} fractional={fractional} setFractional={setFractional} />

      <div className="rd-grid-2" style={{ marginTop: 14 }}>
        <PositionRisk positions={mockPositionRisk} />
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
