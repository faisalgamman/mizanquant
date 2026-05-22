import { portfolioSummary } from '../data/mockData';

const cards = [
  { label: 'Total Return', value: `$${(portfolioSummary.totalReturn / 1000).toFixed(1)}K`, pct: `${portfolioSummary.totalReturnPct.toFixed(1)}%`, color: 'text-positive' },
  { label: 'Win Rate', value: `${(portfolioSummary.winRate * 100).toFixed(0)}%`, pct: null, color: 'text-positive' },
  { label: 'Max Drawdown', value: `${portfolioSummary.maxDrawdown.toFixed(1)}%`, pct: null, color: 'text-negative' },
  { label: 'Sharpe Ratio', value: portfolioSummary.sharpe.toFixed(2), pct: null, color: 'text-primary' },
  { label: 'Total Trades', value: portfolioSummary.totalTrades.toLocaleString(), pct: null, color: 'text-primary' },
  { label: 'Open Positions', value: portfolioSummary.openPositions.toString(), pct: null, color: 'text-primary' },
];

export default function PerformanceCards() {
  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 mb-6">
      {cards.map((c) => (
        <div key={c.label} className="bg-bg-surface border border-border-default rounded-lg p-4">
          <div className="text-text-muted text-xs uppercase tracking-[0.5px] mb-1">{c.label}</div>
          <div className={`font-mono text-lg font-semibold tabular-nums ${c.color}`}>
            {c.value}
            {c.pct && <span className="text-text-muted text-xs ml-1">{c.pct}</span>}
          </div>
        </div>
      ))}
    </div>
  );
}
