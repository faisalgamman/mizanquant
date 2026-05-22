import PerformanceCards from './components/PerformanceCards';
import EquityCurve from './components/EquityCurve';
import DrawdownChart from './components/DrawdownChart';
import StrategyComparison from './components/StrategyComparison';
import StrategyCards from './components/StrategyCards';
import PositionsTable from './components/PositionsTable';
import { portfolioSummary } from './data/mockData';

function App() {
  return (
    <div className="min-h-screen bg-bg-root">
      <div className="max-w-[1440px] mx-auto px-6 py-5">
        {/* Header */}
        <header className="flex items-center justify-between mb-6 pb-4 border-b border-border-default">
          <div>
            <h1 className="font-display text-lg font-semibold tracking-[0.3px] text-text-primary">
              Mizan<span className="text-accent">Quant</span>
            </h1>
            <div className="text-text-muted text-xs mt-0.5">
              <span className="text-positive">●</span> Live · {new Date().toLocaleTimeString()}
            </div>
          </div>
          <div className="text-right">
            <div className="font-mono text-xl font-semibold tabular-nums text-positive">
              +${(portfolioSummary.totalReturn / 1000).toFixed(1)}K
            </div>
            <div className="text-xs text-text-muted mt-0.5">
              Portfolio · Sharpe {portfolioSummary.sharpe.toFixed(2)}
            </div>
          </div>
        </header>

        {/* Performance Cards */}
        <PerformanceCards />

        {/* Charts Row */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 mb-6">
          <div className="lg:col-span-2">
            <EquityCurve />
            <DrawdownChart />
          </div>
          <div>
            <StrategyComparison />
          </div>
        </div>

        {/* Strategy Cards */}
        <div className="mb-2">
          <h2 className="text-text-primary text-md font-semibold tracking-[0.3px] mb-4">Strategy Breakdown</h2>
        </div>
        <StrategyCards />

        {/* Positions */}
        <PositionsTable />

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
