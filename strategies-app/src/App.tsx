import { useDashboardData } from './hooks/useDashboardData';
import PerformanceCards from './components/PerformanceCards';
import EquityCurve from './components/EquityCurve';
import DrawdownChart from './components/DrawdownChart';
import StrategyComparison from './components/StrategyComparison';
import StrategyCards from './components/StrategyCards';
import PositionsTable from './components/PositionsTable';

function App() {
  const { data, loading, error, refresh } = useDashboardData();
  const p = data.performance;

  return (
    <div className="min-h-screen bg-bg-root">
      <div className="max-w-[1440px] mx-auto px-6 py-5">
        {/* Header */}
        <header className="flex items-center justify-between mb-6 pb-4 border-b border-border-default">
          <div>
            <h1 className="font-display text-lg font-semibold tracking-[0.3px] text-text-primary">
              Mizan<span className="text-accent">Quant</span>
            </h1>
            <div className="text-text-muted text-xs mt-0.5 flex items-center gap-2">
              <span className="text-positive">●</span>
              {loading ? 'Loading...' : `Live · ${new Date().toLocaleTimeString()}`}
              <button onClick={refresh} className="text-accent hover:text-accent-hover text-xs ml-2 cursor-pointer bg-transparent border-none">↻</button>
            </div>
          </div>
          <div className="text-right">
            {p ? (
              <>
                <div className={`font-mono text-xl font-semibold tabular-nums ${p.total_return >= 0 ? 'text-positive' : 'text-negative'}`}>
                  {p.total_return >= 0 ? '+' : ''}${(p.total_return / 1000).toFixed(1)}K
                </div>
                <div className="text-xs text-text-muted mt-0.5">
                  Portfolio · Sharpe {p.sharpe.toFixed(2)}
                </div>
              </>
            ) : (
              <div className="text-text-muted text-xs">Loading metrics...</div>
            )}
          </div>
        </header>

        {error && (
          <div className="mb-4 px-4 py-2.5 bg-negative-dim border border-negative/20 rounded-md text-negative text-xs flex items-center gap-2">
            <span>⚠</span> {error}
            <button onClick={refresh} className="ml-auto text-negative underline bg-transparent border-none cursor-pointer">Retry</button>
          </div>
        )}

        {/* Performance Cards */}
        <PerformanceCards perf={p} loading={loading} />

        {/* Charts Row */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 mb-6">
          <div className="lg:col-span-2">
            <EquityCurve data={data.equityCurve} loading={loading} />
            <DrawdownChart data={data.drawdown} loading={loading} />
          </div>
          <div>
            <StrategyComparison strategies={data.strategies} loading={loading} />
          </div>
        </div>

        {/* Strategy Cards */}
        <div className="mb-2">
          <h2 className="text-text-primary text-md font-semibold tracking-[0.3px] mb-4">Strategy Breakdown</h2>
        </div>
        <StrategyCards strategies={data.strategies} loading={loading} />

        {/* Positions */}
        <PositionsTable positions={data.positions} loading={loading} />

        {/* Footer */}
        <footer className="mt-8 pt-3 border-t border-border-default text-xs text-text-muted flex justify-between">
          <span>MizanQuant v2 — Terminal</span>
          <span>Data updates every 30s</span>
        </footer>
      </div>
    </div>
  );
}

export default App;
