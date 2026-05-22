import { useState, useEffect, useCallback } from 'react';
import type { DashboardData, PerformanceSummary, EquityPoint, DrawdownPoint, StrategyResult, Position } from '../data/types';
import { fetchPerformanceSummary, fetchEquityCurve, fetchStrategiesStats, fetchPositions } from '../api/dashboardApi';

const EMPTY: DashboardData = {
  performance: null,
  equityCurve: [],
  drawdown: [],
  strategies: [],
  positions: [],
};

export function useDashboardData(refreshMs = 30_000): {
  data: DashboardData;
  loading: boolean;
  error: string | null;
  refresh: () => void;
} {
  const [data, setData] = useState<DashboardData>(EMPTY);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setError(null);
      const [perf, eq, strat, positions] = await Promise.all([
        fetchPerformanceSummary().catch((): PerformanceSummary => ({
          total_return: 0, total_return_pct: 0, win_rate: 0,
          max_drawdown: 0, sharpe: 0, total_trades: 0, open_positions: 0,
        })),
        fetchEquityCurve().catch((): { equity_curve: EquityPoint[]; drawdown: DrawdownPoint[] } => ({
          equity_curve: [], drawdown: [],
        })),
        fetchStrategiesStats().catch((): StrategyResult[] => []),
        fetchPositions().catch((): Position[] => []),
      ]);
      setData({
        performance: perf,
        equityCurve: eq.equity_curve,
        drawdown: eq.drawdown,
        strategies: strat,
        positions,
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load dashboard data');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const interval = setInterval(load, refreshMs);
    return () => clearInterval(interval);
  }, [load, refreshMs]);

  return { data, loading, error, refresh: load };
}
