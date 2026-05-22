import type { Position } from '../data/types';

export default function PositionsTable({ positions, loading }: { positions: Position[]; loading: boolean }) {
  return (
    <div className="bg-bg-surface border border-border-default rounded-lg p-5 mb-4">
      <h2 className="text-text-primary text-md font-semibold tracking-[0.3px] mb-4">Open Positions</h2>
      {loading ? (
        <div className="space-y-2">
          {[1, 2, 3].map((i) => <div key={i} className="h-8 w-full bg-bg-overlay rounded animate-pulse" />)}
        </div>
      ) : positions.length === 0 ? (
        <div className="text-center py-8 text-text-muted text-xs">No open positions</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-text-muted uppercase tracking-[0.5px] border-b border-border-default">
                <th className="text-left py-2 pr-4 font-medium">Symbol</th>
                <th className="text-right py-2 px-4 font-medium">Side</th>
                <th className="text-right py-2 px-4 font-medium">Qty</th>
                <th className="text-right py-2 px-4 font-medium">Entry</th>
                <th className="text-right py-2 px-4 font-medium">Current</th>
                <th className="text-right py-2 px-4 font-medium">P&L</th>
                <th className="text-right py-2 pl-4 font-medium">P&L %</th>
              </tr>
            </thead>
            <tbody>
              {positions.map((p) => {
                const pl = p.unrealized_pl;
                const plPct = p.unrealized_plpc * 100;
                const isPositive = pl >= 0;
                return (
                  <tr key={p.symbol} className="border-b border-border-subtle hover:bg-bg-raised transition-colors duration-fast">
                    <td className="py-2.5 pr-4 font-mono font-semibold text-text-primary">{p.symbol}</td>
                    <td className={`text-right py-2.5 px-4 font-mono tabular-nums ${p.side === 'LONG' ? 'text-positive' : 'text-negative'}`}>{p.side}</td>
                    <td className="text-right py-2.5 px-4 font-mono tabular-nums text-text-primary">{p.qty}</td>
                    <td className="text-right py-2.5 px-4 font-mono tabular-nums text-text-secondary">${p.avg_entry_price.toFixed(2)}</td>
                    <td className="text-right py-2.5 px-4 font-mono tabular-nums text-text-primary">${p.current_price.toFixed(2)}</td>
                    <td className={`text-right py-2.5 px-4 font-mono tabular-nums ${isPositive ? 'text-positive' : 'text-negative'}`}>${pl.toFixed(2)}</td>
                    <td className={`text-right py-2.5 pl-4 font-mono tabular-nums ${isPositive ? 'text-positive' : 'text-negative'}`}>{isPositive ? '+' : ''}{plPct.toFixed(2)}%</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
