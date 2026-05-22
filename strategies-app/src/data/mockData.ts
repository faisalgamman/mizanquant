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
  netProfit: number;
  winRate: number;
  totalTrades: number;
  maxDrawdown: number;
  status: 'active' | 'inactive';
  signal: 'buy' | 'wait' | 'avoid';
  returnPct: number;
}

export interface PortfolioSummary {
  totalReturn: number;
  totalReturnPct: number;
  winRate: number;
  maxDrawdown: number;
  sharpe: number;
  totalTrades: number;
  openPositions: number;
}

export interface Position {
  symbol: string;
  entry: number;
  current: number;
  pnl: number;
  pnlPct: number;
  direction: 'LONG' | 'SHORT';
  size: number;
}

export const equityCurveData: EquityPoint[] = Array.from({ length: 252 }, (_, i) => {
  const base = 100000;
  const drift = (i / 252) * 18500;
  const noise = Array.from({ length: i + 1 }, () => (Math.random() - 0.48) * 400).reduce((a, b) => a + b, 0);
  return {
    date: new Date(2024, 0, i + 1).toISOString().slice(0, 10),
    balance: Math.round(base + drift + noise),
  };
});

export const drawdownData: DrawdownPoint[] = equityCurveData.map((p, i) => {
  const peak = Math.max(...equityCurveData.slice(0, i + 1).map((x) => x.balance));
  return {
    date: p.date,
    drawdown: -Math.round((1 - p.balance / peak) * 10000) / 100,
  };
});

export const strategyResults: StrategyResult[] = [
  { name: 'random_forest', sharpe: 1.87, netProfit: 21450, winRate: 0.62, totalTrades: 147, maxDrawdown: -0.084, status: 'active', signal: 'buy', returnPct: 21.45 },
  { name: 'xgboost', sharpe: 1.65, netProfit: 18320, winRate: 0.58, totalTrades: 132, maxDrawdown: -0.092, status: 'active', signal: 'buy', returnPct: 18.32 },
  { name: 'turtle', sharpe: 1.42, netProfit: 15780, winRate: 0.47, totalTrades: 98, maxDrawdown: -0.112, status: 'active', signal: 'wait', returnPct: 15.78 },
  { name: 'moving_average', sharpe: 1.38, netProfit: 14210, winRate: 0.51, totalTrades: 115, maxDrawdown: -0.076, status: 'active', signal: 'wait', returnPct: 14.21 },
  { name: 'mean_reversion', sharpe: 1.12, netProfit: 9870, winRate: 0.44, totalTrades: 203, maxDrawdown: -0.131, status: 'active', signal: 'wait', returnPct: 9.87 },
  { name: 'breakout', sharpe: 0.95, netProfit: 7640, winRate: 0.39, totalTrades: 87, maxDrawdown: -0.154, status: 'inactive', signal: 'avoid', returnPct: 7.64 },
  { name: 'momentum', sharpe: 0.88, netProfit: 6230, winRate: 0.41, totalTrades: 76, maxDrawdown: -0.128, status: 'inactive', signal: 'avoid', returnPct: 6.23 },
  { name: 'lstm_regression', sharpe: 2.04, netProfit: 24680, winRate: 0.65, totalTrades: 161, maxDrawdown: -0.071, status: 'active', signal: 'buy', returnPct: 24.68 },
];

export const portfolioSummary: PortfolioSummary = {
  totalReturn: 87540,
  totalReturnPct: 87.54,
  winRate: 0.53,
  maxDrawdown: -0.154,
  sharpe: 1.46,
  totalTrades: 1019,
  openPositions: 6,
};

export const positions: Position[] = [
  { symbol: 'AAPL', entry: 178.50, current: 192.30, pnl: 13.80, pnlPct: 7.73, direction: 'LONG', size: 150 },
  { symbol: 'MSFT', entry: 405.20, current: 428.10, pnl: 22.90, pnlPct: 5.65, direction: 'LONG', size: 75 },
  { symbol: 'GOOGL', entry: 141.80, current: 138.20, pnl: -3.60, pnlPct: -2.54, direction: 'SHORT', size: 200 },
  { symbol: 'AMZN', entry: 185.00, current: 197.40, pnl: 12.40, pnlPct: 6.70, direction: 'LONG', size: 100 },
  { symbol: 'TSLA', entry: 248.30, current: 235.60, pnl: -12.70, pnlPct: -5.11, direction: 'SHORT', size: 60 },
  { symbol: 'NVDA', entry: 820.50, current: 875.20, pnl: 54.70, pnlPct: 6.67, direction: 'LONG', size: 40 },
];
