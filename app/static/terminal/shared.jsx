// shared.jsx — primitives & mock data for the MizanQuant UI Kit

const { useState, useEffect, useMemo, useRef } = React;

// ─── Formatting helpers ──────────────────────────────────────────────
const fmt$ = (n, dp = 2) =>
  n == null ? "—" : "$" + Number(n).toLocaleString("en-US", { minimumFractionDigits: dp, maximumFractionDigits: dp });
const fmtPct = (n, dp = 2) =>
  n == null ? "—" : (n >= 0 ? "+" : "") + Number(n).toFixed(dp) + "%";

// ─── Score → verdict → color ─────────────────────────────────────────
function verdictFromScore(score) {
  if (score >= 90) return "STRONG BUY";
  if (score >= 75) return "BUY";
  if (score >= 55) return "WAIT";
  return "AVOID";
}
function badgeClassFor(verdict) {
  if (verdict?.includes("BUY")) return "b-green";
  if (verdict?.includes("SELL")) return "b-red";
  if (verdict === "WAIT") return "b-amber";
  return "b-red";
}
function scoreColor(score) {
  if (score >= 75) return "var(--positive)";
  if (score >= 55) return "var(--warning)";
  return "var(--negative)";
}

// ─── Ring (score gauge) ──────────────────────────────────────────────
function Ring({ score, size = 44 }) {
  const r = size / 2 - 4;
  const c = 2 * Math.PI * r;
  const dash = (score / 100) * c;
  const color = scoreColor(score);
  return (
    <div className="ring" style={{ width: size, height: size }}>
      <svg width={size} height={size}>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--bg-root)" strokeWidth={3} />
        <circle
          cx={size / 2} cy={size / 2} r={r} fill="none"
          stroke={color} strokeWidth={3}
          strokeDasharray={`${dash} ${c}`} strokeLinecap="round"
        />
      </svg>
      <div className="ring-num" style={{ color }}>{score}</div>
    </div>
  );
}

// ─── Sparkline ───────────────────────────────────────────────────────
function Sparkline({ points, color, height = 20 }) {
  if (!points || points.length < 2) return null;
  const w = 100;
  const min = Math.min(...points);
  const max = Math.max(...points);
  const range = max - min || 1;
  const step = w / (points.length - 1);
  const path = points
    .map((y, i) => `${i ? "L" : "M"}${(i * step).toFixed(1)},${((max - y) / range * (height - 4) + 2).toFixed(1)}`)
    .join(" ");
  return (
    <svg viewBox={`0 0 ${w} ${height}`} preserveAspectRatio="none" style={{ width: "100%", height }}>
      <path d={path} fill="none" stroke={color} strokeWidth={1.4} />
    </svg>
  );
}

// ─── Badge ──────────────────────────────────────────────────────────
function Badge({ children, kind = "muted", pill = false, style }) {
  const cls = `badge b-${kind}` + (pill ? " pill" : "");
  return <span className={cls} style={style}>{children}</span>;
}

// ─── Mock data ──────────────────────────────────────────────────────
const mockSignals = [
  { symbol: "AAPL",  company: "Apple Inc.",          score: 88, chg:  1.84, price: 182.34, sector: "Tech",        halal: true,  spark: [12,18,15,20,22,28,26,32,30,38,35,40], industry: "Consumer electronics" },
  { symbol: "NVDA",  company: "NVIDIA Corp.",        score: 91, chg:  2.41, price: 924.10, sector: "Tech",        halal: true,  spark: [20,22,21,24,28,26,32,34,38,42,40,46], industry: "Semiconductors" },
  { symbol: "MSFT",  company: "Microsoft Corp.",     score: 82, chg:  0.94, price: 412.20, sector: "Tech",        halal: true,  spark: [25,27,26,28,29,30,32,31,33,34,36,38], industry: "Cloud & software" },
  { symbol: "GOOGL", company: "Alphabet Inc. (A)",   score: 76, chg:  0.32, price: 158.40, sector: "Comms",       halal: true,  spark: [22,21,23,22,24,25,24,26,25,27,28,28], industry: "Internet platform" },
  { symbol: "TSLA",  company: "Tesla Inc.",          score: 64, chg: -0.62, price: 248.50, sector: "Auto",        halal: true,  spark: [30,28,32,30,26,28,22,24,20,18,22,20], industry: "EV manufacturer" },
  { symbol: "META",  company: "Meta Platforms Inc.", score: 71, chg:  0.18, price: 488.30, sector: "Comms",       halal: false, spark: [18,19,18,20,19,21,20,22,21,23,22,24], industry: "Social platform" },
  { symbol: "AMZN",  company: "Amazon.com Inc.",     score: 79, chg:  1.12, price: 192.80, sector: "Consumer",    halal: true,  spark: [16,17,18,18,20,21,21,23,24,25,27,28], industry: "E-commerce / Cloud" },
  { symbol: "JPM",   company: "JPMorgan Chase",      score: 35, chg: -0.41, price: 198.50, sector: "Financials",  halal: false, spark: [28,27,26,28,25,24,26,23,22,24,21,20], industry: "Banking — interest-bearing" },
  { symbol: "XOM",   company: "Exxon Mobil Corp.",   score: 48, chg: -1.12, price: 108.22, sector: "Energy",      halal: true,  spark: [22,23,22,21,20,19,18,19,17,16,18,17], industry: "Integrated oil & gas" },
  { symbol: "UNH",   company: "UnitedHealth Group",  score: 67, chg:  0.21, price: 524.40, sector: "Health",      halal: true,  spark: [20,21,20,21,22,21,23,22,24,23,25,24], industry: "Managed healthcare" },
];

const mockMarket = {
  vix: 14.82,
  spy_regime: "BULL",
  spy_trend: "EMA50 > EMA200",
  breadth: 62.4,
  credit: 1.124,
  liquidity: 88.2,
  market_open: true,
};

const mockPortfolio = {
  equity: 48284,
  dayPnl: 348.20,
  cash: 12820,
  buyPower: 25640,
  openPos: 7,
  todayExits: 2,
};

const mockPositions = [
  { sym: "AAPL", qty: 12, entry: 178.50, last: 182.34, pnl: 46.08, pnlPct: 2.15 },
  { sym: "NVDA", qty: 3,  entry: 905.00, last: 924.10, pnl: 57.30, pnlPct: 2.11 },
  { sym: "MSFT", qty: 6,  entry: 408.00, last: 412.20, pnl: 25.20, pnlPct: 1.03 },
  { sym: "AMZN", qty: 8,  entry: 195.00, last: 192.80, pnl: -17.60, pnlPct: -1.13 },
];

const mockPaper = [
  { sym: "GOOGL", side: "buy",  entry: 156.20, last: 158.40, pnl: 2.20,  status: "open",   when: "12:32" },
  { sym: "NVDA",  side: "buy",  entry: 905.00, last: 924.10, pnl: 19.10, status: "open",   when: "11:48" },
  { sym: "TSLA",  side: "sell", entry: 250.20, last: 248.50, pnl: 1.70,  status: "closed", when: "09:14" },
  { sym: "UNH",   side: "buy",  entry: 521.00, last: 524.40, pnl: 3.40,  status: "open",   when: "10:02" },
];

const initialPipelineStages = [
  { n: "Scan",      s: "done" },
  { n: "Feature",   s: "done" },
  { n: "Score",     s: "done" },
  { n: "Consensus", s: "running" },
  { n: "Risk",      s: "pending" },
  { n: "Execute",   s: "pending" },
];

const mockGuards = [
  { name: "max_concurrent",  hits: 0, color: "var(--positive)" },
  { name: "halal_fail",      hits: 3, color: "var(--negative)" },
  { name: "drawdown_cap",    hits: 0, color: "var(--positive)" },
  { name: "correlation",     hits: 1, color: "var(--warning)" },
  { name: "low_liquidity",   hits: 2, color: "var(--warning)" },
];

const mockSchedule = [
  { time: "06:00", label: "Pre-market scan" },
  { time: "09:30", label: "Open · regime update" },
  { time: "10:00", label: "Halal screener refresh" },
  { time: "12:30", label: "Mid-day pipeline run" },
  { time: "15:55", label: "EOD risk check" },
  { time: "16:30", label: "Daily report · Telegram" },
];

const mockSectors = [
  { name: "Tech",       chg:  1.84, halal: true },
  { name: "Comms",      chg:  0.32, halal: true },
  { name: "Consumer",   chg:  0.88, halal: true },
  { name: "Auto",       chg: -0.62, halal: true },
  { name: "Energy",     chg: -1.12, halal: true },
  { name: "Health",     chg:  0.18, halal: true },
  { name: "Materials",  chg: -0.24, halal: true },
  { name: "Utilities",  chg:  0.05, halal: true },
  { name: "Industrials",chg:  0.62, halal: true },
  { name: "Financials", chg: -0.41, halal: false },
  { name: "REIT",       chg:  0.12, halal: false },
];

const mockModels = [
  { name: "transformer",    family: "transformer", sharpe: 1.84, return: 18.4, status: "production" },
  { name: "ensemble_v3",    family: "ensemble",    sharpe: 1.62, return: 14.8, status: "production" },
  { name: "gru_seq2seq",    family: "gru",         sharpe: 1.21, return: 11.2, status: "staging" },
  { name: "lstm_2path",     family: "lstm",        sharpe: 1.04, return:  9.6, status: "production" },
  { name: "dilated_cnn",    family: "cnn",         sharpe: 0.88, return:  7.4, status: "staging" },
  { name: "bigru_seq2seq",  family: "gru",         sharpe: 0.62, return:  5.1, status: "idle" },
];

const mockConsensusVotes = [
  { model: "transformer",   v: "BUY"  },
  { model: "ensemble_v3",   v: "BUY"  },
  { model: "gru_seq2seq",   v: "BUY"  },
  { model: "lstm_2path",    v: "BUY"  },
  { model: "dilated_cnn",   v: "WAIT" },
  { model: "bigru_seq2seq", v: "BUY"  },
  { model: "evolution",     v: "WAIT" },
  { model: "moving_avg",    v: "BUY"  },
  { model: "turtle",        v: "BUY"  },
  { model: "arima",         v: "WAIT" },
  { model: "novelty",       v: "BUY"  },
  { model: "kelly",         v: "BUY"  },
  { model: "claude",        v: "BUY"  },
  { model: "gpt-4",         v: "WAIT" },
];

const mockNav = [
  { i: "fa-th-large",      l: "Overview",        active: true, badge: "Live", href: "/terminal" },
  { i: "fa-calendar-week", l: "Weekly Scanner",  href: "/terminal#weekly" },
  { i: "fa-calendar-alt",  l: "Monthly Scanner", href: "/terminal#monthly" },
  { i: "fa-list-ul",       l: "Weekly Picks",    href: "/weekly-picks" },
];
const mockNavForecast = [
  // Forecast Lab / Trading Lab / Analysis Lab removed from nav on purpose:
  // that model zoo is unvalidated research (directional accuracy ~= coin-flip,
  // Sharpe ~= 0) and is NOT used in the live trading decision, so it must not be
  // presented as a trading tool. The pages still exist for research at their URLs.
  { i: "fa-history",       l: "Backtest",        href: "/backtest" },
];
const mockNavAnalytics = [
  { i: "fa-flask",         l: "Quant Lab",       badge: "New", href: "/quant-lab" },
  { i: "fa-shield-alt",    l: "Risk Desk",       badge: "2", href: "/risk-desk-v2" },
  { i: "fa-chart-bar",     l: "Strategies",      href: "/strategy-dashboard" },
];
const mockNavResearch = [
  { i: "fa-mosque",        l: "Halal Screener",  href: "/halal-screener-v2" },
  { i: "fa-star",          l: "Insights",        href: "/investors" },
  { i: "fa-bell",          l: "Alerts",          href: "/alerts" },
  { i: "fa-robot",         l: "MizanAI",         href: "/assistant" },
];
const mockNavMarket = [
  { i: "fa-landmark",      l: "Macro / FRED",    href: "/macro" },
  { i: "fa-layer-group",   l: "ETF Explorer",    href: "/etf" },
  { i: "fa-sync",          l: "Rotation",        href: "/rotation" },
];

Object.assign(window, {
  // hooks/utils
  fmt$, fmtPct, verdictFromScore, badgeClassFor, scoreColor,
  // primitives
  Ring, Sparkline, Badge,
  // mocks
  mockSignals, mockMarket, mockPortfolio, mockPositions, mockPaper,
  initialPipelineStages, mockGuards, mockSchedule, mockSectors, mockModels,
  mockConsensusVotes, mockNav, mockNavForecast, mockNavAnalytics, mockNavResearch, mockNavMarket,
});
