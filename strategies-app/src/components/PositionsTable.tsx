import { positions } from '../data/mockData';

export default function PositionsTable() {
  return (
    <div className="bg-bg-surface border border-border-default rounded-lg p-5 mb-4">
      <h2 className="text-text-primary text-md font-semibold tracking-[0.3px] mb-4">Open Positions</h2>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-text-muted uppercase tracking-[0.5px] border-b border-border-default">
              <th className="text-left py-2 pr-4 font-medium">Symbol</th>
              <th className="text-right py-2 px-4 font-medium">Direction</th>
              <th className="text-right py-2 px-4 font-medium">Size</th>
              <th className="text-right py-2 px-4 font-medium">Entry</th>
              <th className="text-right py-2 px-4 font-medium">Current</th>
              <th className="text-right py-2 px-4 font-medium">P&L</th>
              <th className="text-right py-2 pl-4 font-medium">P&L %</th>
            </tr>
          </thead>
          <tbody>
            {positions.map((p) => {
              const isPositive = p.pnl >= 0;
              return (
                <tr key={p.symbol} className="border-b border-border-subtle hover:bg-bg-raised transition-colors duration-fast">
                  <td className="py-2.5 pr-4 font-mono font-semibold text-text-primary">{p.symbol}</td>
                  <td className={`text-right py-2.5 px-4 font-mono tabular-nums ${p.direction === 'LONG' ? 'text-positive' : 'text-negative'}`}>{p.direction}</td>
                  <td className="text-right py-2.5 px-4 font-mono tabular-nums text-text-primary">{p.size}</td>
                  <td className="text-right py-2.5 px-4 font-mono tabular-nums text-text-secondary">${p.entry.toFixed(2)}</td>
                  <td className="text-right py-2.5 px-4 font-mono tabular-nums text-text-primary">${p.current.toFixed(2)}</td>
                  <td className={`text-right py-2.5 px-4 font-mono tabular-nums ${isPositive ? 'text-positive' : 'text-negative'}`}>${p.pnl.toFixed(2)}</td>
                  <td className={`text-right py-2.5 pl-4 font-mono tabular-nums ${isPositive ? 'text-positive' : 'text-negative'}`}>{isPositive ? '+' : ''}{p.pnlPct.toFixed(2)}%</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
