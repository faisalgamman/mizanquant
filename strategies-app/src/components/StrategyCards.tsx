import { strategyResults } from '../data/mockData';

const signalColors: Record<string, string> = {
  buy: 'text-positive',
  wait: 'text-warning',
  avoid: 'text-negative',
};

const statusColors: Record<string, string> = {
  active: 'text-positive',
  inactive: 'text-text-muted',
};

export default function StrategyCards() {
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 mb-6">
      {strategyResults.map((s) => (
        <div key={s.name} className="bg-bg-surface border border-border-default rounded-lg p-4">
          <div className="flex justify-between items-center mb-3">
            <span className="text-text-primary text-sm font-semibold font-mono">{s.name}</span>
            <span className={`text-xs font-medium ${statusColors[s.status]}`}>{s.status}</span>
          </div>
          <div className="space-y-1.5">
            <Row label="Sharpe" value={s.sharpe.toFixed(2)} color="text-primary" />
            <Row label="Net Profit" value={`$${(s.netProfit / 1000).toFixed(1)}K`} color={s.netProfit >= 0 ? 'text-positive' : 'text-negative'} />
            <Row label="Win Rate" value={`${(s.winRate * 100).toFixed(0)}%`} color="text-primary" />
            <Row label="Max DD" value={`${s.maxDrawdown.toFixed(1)}%`} color="text-negative" />
            <Row label="Trades" value={s.totalTrades.toString()} color="text-primary" />
          </div>
          <div className={`mt-3 pt-3 border-t border-border-default text-xs font-semibold tracking-[0.3px] uppercase ${signalColors[s.signal]}`}>
            {s.signal === 'buy' ? '▲ Buy Signal' : s.signal === 'wait' ? '◆ Hold' : '▼ Avoid'}
          </div>
        </div>
      ))}
    </div>
  );
}

function Row({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div className="flex justify-between text-xs">
      <span className="text-text-muted">{label}</span>
      <span className={`font-mono tabular-nums ${color}`}>{value}</span>
    </div>
  );
}
