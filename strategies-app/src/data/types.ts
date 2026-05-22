export interface EquityPoint {
  date: string;
  balance: number;
}

export interface DrawdownPoint {
  date: string;
  drawdown: number;
}

export interface StrategyResult {
  name: string;
  sharpe: number;
  net_profit: number;
  win_rate: number;
  total_trades: number;
  max_drawdown: number;
  status: string;
  signal: string;
  return_pct: number;
}

export interface PerformanceSummary {
  total_return: number;
  total_return_pct: number;
  win_rate: number;
  max_drawdown: number;
  sharpe: number;
  total_trades: number;
  open_positions: number;
}

export interface Position {
  symbol: string;
  qty: number;
  side: string;
  avg_entry_price: number;
  current_price: number;
  market_value: number;
  unrealized_pl: number;
  unrealized_plpc: number;
}

export interface DashboardData {
  performance: PerformanceSummary | null;
  equityCurve: EquityPoint[];
  drawdown: DrawdownPoint[];
  strategies: StrategyResult[];
  positions: Position[];
}
