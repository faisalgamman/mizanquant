import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine } from 'recharts';
import { drawdownData } from '../data/mockData';

function CustomTooltip({ active, payload, label }: any) {
  if (!active || !payload?.length) return null;
  return (
    <div className="bg-bg-overlay border border-border-strong rounded-md px-3 py-2 text-xs font-mono shadow-lg">
      <div className="text-text-muted mb-1">{label}</div>
      <div className="text-negative">{Number(payload[0].value).toFixed(2)}%</div>
    </div>
  );
}

export default function DrawdownChart() {
  return (
    <div className="bg-bg-surface border border-border-default rounded-lg p-5">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-text-primary text-md font-semibold tracking-[0.3px]">Drawdown</h2>
        <span className="text-text-muted text-xs">peak-to-trough</span>
      </div>
      <ResponsiveContainer width="100%" height={140}>
        <BarChart data={drawdownData} margin={{ top: 5, right: 12, left: 0, bottom: 0 }}>
          <XAxis dataKey="date" tick={{ fill: '#5c5c6a', fontSize: 10 }} tickLine={false} axisLine={false} minTickGap={60} />
          <YAxis domain={['auto', 0]} tick={{ fill: '#5c5c6a', fontSize: 10 }} tickFormatter={(v: number) => `${v.toFixed(0)}%`} tickLine={false} axisLine={false} width={40} />
          <Tooltip content={<CustomTooltip />} />
          <ReferenceLine y={0} stroke="rgba(255,255,255,0.06)" />
          <Bar dataKey="drawdown" fill="#f87171" fillOpacity={0.6} radius={[2, 2, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
