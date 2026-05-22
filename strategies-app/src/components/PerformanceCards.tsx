import type { PerformanceSummary } from '../data/types';

function Card({ label, value, pct, color, loading }: { label: string; value: string; pct: string | null; color: string; loading: boolean }) {
  return (
    <div className="bg-bg-surface border border-border-default rounded-lg p-4">
      <div className="text-text-muted text-xs uppercase tracking-[0.5px] mb-1">{label}</div>
      {loading ? (
        <div className="h-6 w-20 bg-bg-overlay rounded animate-pulse" />
      ) : (
        <div className={`font-mono text-lg font-semibold tabular-nums ${color}`}>
          {value}
          {pct && <span className="text-text-muted text-xs ml-1">{pct}</span>}
        </div>
      )}
    </div>
  );
}

export default function PerformanceCards({ perf, loading }: { perf: PerformanceSummary | null; loading: boolean }) {
  const cards = [
    { label: 'Total Return', value: perf ? `$${(perf.total_return / 1000).toFixed(1)}K` : '—', pct: perf ? `${perf.total_return_pct.toFixed(1)}%` : null, color: perf && perf.total_return >= 0 ? 'text-positive' : 'text-text-primary' },
    { label: 'Win Rate', value: perf ? `${(perf.win_rate * 100).toFixed(0)}%` : '—', pct: null, color: 'text-positive' },
    { label: 'Max Drawdown', value: perf ? `${(perf.max_drawdown * 100).toFixed(1)}%` : '—', pct: null, color: 'text-negative' },
    { label: 'Sharpe Ratio', value: perf ? perf.sharpe.toFixed(2) : '—', pct: null, color: 'text-text-primary' },
    { label: 'Total Trades', value: perf ? perf.total_trades.toLocaleString() : '—', pct: null, color: 'text-text-primary' },
    { label: 'Open Positions', value: perf ? perf.open_positions.toString() : '—', pct: null, color: 'text-text-primary' },
  ];

  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 mb-6">
      {cards.map((c) => <Card key={c.label} {...c} loading={loading} />)}
    </div>
  );
}
