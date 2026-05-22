import type { PerformanceSummary, EquityPoint, DrawdownPoint, StrategyResult, Position } from '../data/types';

const BASE = '';

export async function fetchPerformanceSummary(): Promise<PerformanceSummary> {
  const res = await fetch(`${BASE}/api/dashboard/performance-summary`);
  if (!res.ok) throw new Error(`Performance summary: ${res.status}`);
  return res.json();
}

export async function fetchEquityCurve(): Promise<{ equity_curve: EquityPoint[]; drawdown: DrawdownPoint[] }> {
  const res = await fetch(`${BASE}/api/dashboard/equity-curve`);
  if (!res.ok) throw new Error(`Equity curve: ${res.status}`);
  return res.json();
}

export async function fetchStrategiesStats(): Promise<StrategyResult[]> {
  const res = await fetch(`${BASE}/api/dashboard/strategies-stats`);
  if (!res.ok) throw new Error(`Strategies stats: ${res.status}`);
  return res.json();
}

export async function fetchPositions(): Promise<Position[]> {
  const res = await fetch(`${BASE}/api/public/positions`);
  if (!res.ok) throw new Error(`Positions: ${res.status}`);
  return res.json();
}
