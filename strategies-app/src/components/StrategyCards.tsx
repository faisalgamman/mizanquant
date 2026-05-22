import type { StrategyResult } from '../data/types';

const signalLabels: Record<string, { label: string; cls: string }> = {
  buy: { label: '▲ Buy Signal', cls: 'text-positive' },
  wait: { label: '◆ Hold', cls: 'text-warning' },
  avoid: { label: '▼ Avoid', cls: 'text-negative' },
};

const statusColors: Record<string, string> = {
  active: 'text-positive',
  inactive: 'text-text-muted',
};

function Row({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div className="flex justify-between text-xs">
      <span className="text-text-muted">{label}</span>
      <span className={`font-mono tabular-nums ${color}`}>{value}</span>
    </div>
  );
}

export default function StrategyCards({ strategies, loading }: { strategies: StrategyResult[]; loading: boolean }) {
  if (loading) {
    return (
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 mb-6">
        {[1, 2, 3, 4].map((i) => (
          <div key={i} className="bg-bg-surface border border-border-default rounded-lg p-4">
            <div className="h-4 w-24 bg-bg-overlay rounded animate-pulse mb-3" />
            <div className="space-y-2">
              {[1, 2, 3, 4, 5].map((j) => <div key={j} className="h-3 w-full bg-bg-overlay rounded animate-pulse" />)}
            </div>
          </div>
        ))}
      </div>
    );
  }

  if (strategies.length === 0) {
    return (
      <div className="text-center py-12 text-text-muted text-xs mb-6">No strategy data available</div>
    );
  }

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 mb-6">
      {strategies.map((s) => {
        const sig = signalLabels[s.signal] || signalLabels.wait;
        return (
          <div key={s.name} className="bg-bg-surface border border-border-default rounded-lg p-4">
            <div className="flex justify-between items-center mb-3">
              <span className="text-text-primary text-sm font-semibold font-mono">{s.name}</span>
              <span className={`text-xs font-medium ${statusColors[s.status] || 'text-text-muted'}`}>{s.status}</span>
            </div>
            <div className="space-y-1.5">
              <Row label="Sharpe" value={s.sharpe.toFixed(2)} color="text-text-primary" />
              <Row label="Net Profit" value={`$${(s.net_profit / 1000).toFixed(1)}K`} color={s.net_profit >= 0 ? 'text-positive' : 'text-negative'} />
              <Row label="Win Rate" value={`${(s.win_rate * 100).toFixed(0)}%`} color="text-text-primary" />
              <Row label="Max DD" value={`${(s.max_drawdown * 100).toFixed(1)}%`} color="text-negative" />
              <Row label="Trades" value={s.total_trades.toString()} color="text-text-primary" />
            </div>
            <div className={`mt-3 pt-3 border-t border-border-default text-xs font-semibold tracking-[0.3px] uppercase ${sig.cls}`}>
              {sig.label}
            </div>
          </div>
        );
      })}
    </div>
  );
}
