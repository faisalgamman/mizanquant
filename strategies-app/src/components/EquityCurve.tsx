import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from 'recharts';
import type { EquityPoint } from '../data/types';

function CustomTooltip({ active, payload, label }: any) {
  if (!active || !payload?.length) return null;
  return (
    <div className="bg-bg-overlay border border-border-strong rounded-md px-3 py-2 text-xs font-mono shadow-lg">
      <div className="text-text-muted mb-1">{label}</div>
      <div className="text-positive">${Number(payload[0].value).toLocaleString()}</div>
    </div>
  );
}

export default function EquityCurve({ data, loading }: { data: EquityPoint[]; loading: boolean }) {
  return (
    <div className="bg-bg-surface border border-border-default rounded-lg p-5 mb-4">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-text-primary text-md font-semibold tracking-[0.3px]">Equity Curve</h2>
        {loading && <span className="text-text-muted text-xs animate-pulse">Loading...</span>}
        {!loading && <span className="text-text-muted text-xs">{data.length} trading days</span>}
      </div>
      {data.length === 0 && !loading ? (
        <div className="flex items-center justify-center h-[280px] text-text-muted text-xs">No equity data available</div>
      ) : (
        <ResponsiveContainer width="100%" height={280}>
          <AreaChart data={data} margin={{ top: 5, right: 12, left: 0, bottom: 0 }}>
            <defs>
              <linearGradient id="equityGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#4ade80" stopOpacity={0.25} />
                <stop offset="95%" stopColor="#4ade80" stopOpacity={0.02} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" />
            <XAxis dataKey="date" tick={{ fill: '#5c5c6a', fontSize: 10 }} tickLine={false} axisLine={false} minTickGap={40} />
            <YAxis domain={['dataMin - 5000', 'dataMax + 5000']} tick={{ fill: '#5c5c6a', fontSize: 10 }} tickFormatter={(v: number) => `$${(v / 1000).toFixed(0)}K`} tickLine={false} axisLine={false} width={60} />
            <Tooltip content={<CustomTooltip />} />
            <Area type="monotone" dataKey="balance" stroke="#4ade80" strokeWidth={2} fill="url(#equityGrad)" dot={false} activeDot={{ r: 4, fill: '#4ade80', stroke: '#0c0c10', strokeWidth: 2 }} />
          </AreaChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}
