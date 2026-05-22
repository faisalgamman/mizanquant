import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from 'recharts';
import type { StrategyResult } from '../data/types';

function CustomTooltip({ active, payload, label }: any) {
  if (!active || !payload?.length) return null;
  return (
    <div className="bg-bg-overlay border border-border-strong rounded-md px-3 py-2 text-xs font-mono shadow-lg">
      <div className="text-text-muted mb-1">{label}</div>
      {payload.map((p: any, i: number) => (
        <div key={i} style={{ color: p.color }} className="tabular-nums">
          {p.name}: {p.dataKey === 'sharpe' ? Number(p.value).toFixed(2) : `$${(Number(p.value) / 1000).toFixed(1)}K`}
        </div>
      ))}
    </div>
  );
}

export default function StrategyComparison({ strategies, loading }: { strategies: StrategyResult[]; loading: boolean }) {
  const active = strategies.filter((s) => s.status === 'active');
  const chartData = active.map((s) => ({
    name: s.name,
    sharpe: s.sharpe,
    netProfit: Math.round(s.net_profit / 1000),
  }));

  return (
    <div className="bg-bg-surface border border-border-default rounded-lg p-5">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-text-primary text-md font-semibold tracking-[0.3px]">Strategy Comparison</h2>
        {loading && <span className="text-text-muted text-xs animate-pulse">Loading...</span>}
        {!loading && <span className="text-text-muted text-xs">{active.length} active</span>}
      </div>
      {strategies.length === 0 && !loading ? (
        <div className="flex items-center justify-center h-[200px] text-text-muted text-xs">No strategy data available</div>
      ) : (
        <ResponsiveContainer width="100%" height={chartData.length * 50 + 20}>
          <BarChart data={chartData} layout="vertical" margin={{ top: 5, right: 20, left: 80, bottom: 5 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" horizontal={false} />
            <XAxis type="number" tick={{ fill: '#5c5c6a', fontSize: 10 }} tickLine={false} axisLine={false} />
            <YAxis type="category" dataKey="name" tick={{ fill: '#9494a0', fontSize: 11 }} tickLine={false} axisLine={false} width={90} />
            <Tooltip content={<CustomTooltip />} />
            <Bar dataKey="sharpe" fill="#c8963e" fillOpacity={0.8} radius={[0, 3, 3, 0]} barSize={12} />
            <Bar dataKey="netProfit" fill="#4ade80" fillOpacity={0.6} radius={[0, 3, 3, 0]} barSize={12} />
          </BarChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}
