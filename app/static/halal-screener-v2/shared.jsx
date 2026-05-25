// shared.jsx — Halal Screener primitives + mock universe

const { useState, useEffect, useMemo, useRef } = React;

const fmt$ = (n, dp = 0) =>
  n == null ? "—" : "$" + Number(n).toLocaleString("en-US", { minimumFractionDigits: dp, maximumFractionDigits: dp });
const fmtPct = (n, dp = 2) =>
  n == null ? "—" : (n >= 0 ? "+" : "") + Number(n).toFixed(dp) + "%";
const fmtRatio = (n, dp = 1) =>
  n == null ? "—" : Number(n).toFixed(dp) + "%";

// ─── AAOIFI screen definitions ──────────────────────────────────────
const AAOIFI_SCREENS = [
  { n: 1, name: "Debt screen",        full: "Total Debt / Market Cap",        threshold: "< 33%" },
  { n: 2, name: "Interest income",    full: "Interest Income / Revenue",      threshold: "< 5%"  },
  { n: 3, name: "Cash & receivables", full: "(Cash + AR) / Market Cap",       threshold: "< 33%" },
  { n: 4, name: "Haram industries",   full: "AAOIFI excluded sector list",    threshold: "Excluded" },
];

const HARAM_INDUSTRIES = [
  "Conventional banking", "Insurance", "Alcohol", "Tobacco",
  "Pork", "Gambling", "Adult entertainment", "Defense / weapons",
];

// Score color (same as terminal kit)
function scoreColor(s) {
  if (s >= 80) return "var(--positive)";
  if (s >= 60) return "var(--warning)";
  if (s >= 40) return "#f97316";
  return "var(--negative)";
}


// ─── Real API fetch functions ────────────────────────────────────
async function fetchUniverse() {
  const res = await fetch('/screener');
  if (!res.ok) throw new Error('Screener returned ' + res.status);
  const data = await res.json();
  // Map backend fields to kit schema
  return (Array.isArray(data) ? data : (data.results || data.signals || [])).map(row => ({
    sym:       row.symbol || row.sym || '?',
    name:      row.company_name || row.company || row.name || row.symbol || '?',
    sector:    row.sector || '—',
    mcap:      row.market_cap || row.mcap || 0,
    chg:       row.chg_1w || row.chg_pct || row.change_pct || row.chg || 0,
    score:     row.swing_score || row.score || 0,
    rsi:       row.rsi || 0,
    vol:       row.volume_ratio || row.vol || 0,
    adx:       row.adx || 0,
    signal:    row.swing_signal || row.signal || row.verdict || 'WAIT',
    strategy:  row.strategy_id || row.strategy || '—',
    // AAOIFI fields (may come from halal_status or be pre-computed)
    debt:      row.debt_ratio != null ? row.debt_ratio * 100 : null,
    interest:  row.interest_ratio != null ? row.interest_ratio * 100 : null,
    cashRecv:  row.cash_ratio != null ? row.cash_ratio * 100 : null,
    haramFlag: row.haram_sector || false,
    // screens: 4 AAOIFI booleans. Backend may not return them individually;
    // if halal="Yes" all 4 passed. Detail panel can call /halal_status for breakdown.
    screens:   row.screens || (
      row.halal === "Yes"
        ? [true, true, true, true]
        : [
            row.debt_ok !== false,
            row.interest_ok !== false,
            row.cash_ok !== false,
            !row.haram_sector,
          ]
    ),
  }));
}

async function fetchHalalDetail(sym) {
  const res = await fetch('/halal_status?symbol=' + encodeURIComponent(sym));
  if (!res.ok) return null;
  const arr = await res.json();
  return Array.isArray(arr) ? arr[0] : arr;
}

async function fetchMarketContext() {
  try {
    const res = await fetch('/api/context/bundle');
    if (!res.ok) return null;
    return await res.json();
  } catch { return null; }
}

// ─── Halal universe (mock - used as fallback/placeholder) ──────────────
// screens = [debt, interestIncome, cashRecv, haramSector] booleans
const mockUniverse = [
  { sym: "AAPL",  name: "Apple Inc.",            sector: "Tech",        mcap: 2840e9, chg:  1.84, score: 88, rsi: 62, vol: 142, adx: 24, signal: "BUY",        strategy: "breakout",
    debt: 18.2, interest: 0.4, cashRecv: 22.1, haramFlag: false, screens: [true, true, true, true] },
  { sym: "NVDA",  name: "NVIDIA Corp.",          sector: "Tech",        mcap: 2200e9, chg:  2.41, score: 91, rsi: 71, vol: 188, adx: 31, signal: "STRONG BUY", strategy: "momentum",
    debt:  4.1, interest: 0.0, cashRecv: 14.8, haramFlag: false, screens: [true, true, true, true] },
  { sym: "MSFT",  name: "Microsoft Corp.",       sector: "Tech",        mcap: 3060e9, chg:  0.94, score: 82, rsi: 58, vol: 102, adx: 22, signal: "BUY",        strategy: "momentum",
    debt: 22.8, interest: 1.1, cashRecv: 18.4, haramFlag: false, screens: [true, true, true, true] },
  { sym: "GOOGL", name: "Alphabet Inc. (A)",     sector: "Comms",       mcap: 1980e9, chg:  0.32, score: 76, rsi: 54, vol:  88, adx: 19, signal: "BUY",        strategy: "trend",
    debt: 11.4, interest: 0.6, cashRecv: 24.2, haramFlag: false, screens: [true, true, true, true] },
  { sym: "AMZN",  name: "Amazon.com Inc.",       sector: "Consumer",    mcap: 2010e9, chg:  1.12, score: 79, rsi: 56, vol: 124, adx: 23, signal: "BUY",        strategy: "breakout",
    debt: 24.8, interest: 0.8, cashRecv: 19.6, haramFlag: false, screens: [true, true, true, true] },
  { sym: "META",  name: "Meta Platforms Inc.",   sector: "Comms",       mcap: 1240e9, chg:  0.18, score: 71, rsi: 52, vol:  92, adx: 18, signal: "WAIT",       strategy: "—",
    debt: 12.6, interest: 0.2, cashRecv: 28.4, haramFlag: true,  screens: [true, true, true, false] },
  { sym: "TSLA",  name: "Tesla Inc.",            sector: "Auto",        mcap:  790e9, chg: -0.62, score: 64, rsi: 48, vol: 156, adx: 16, signal: "WAIT",       strategy: "—",
    debt:  6.4, interest: 0.0, cashRecv: 31.2, haramFlag: false, screens: [true, true, true, true] },
  { sym: "UNH",   name: "UnitedHealth Group",    sector: "Health",      mcap:  486e9, chg:  0.21, score: 67, rsi: 51, vol:  78, adx: 14, signal: "WAIT",       strategy: "—",
    debt: 19.2, interest: 0.8, cashRecv:  9.4, haramFlag: false, screens: [true, true, true, true] },
  { sym: "XOM",   name: "Exxon Mobil Corp.",     sector: "Energy",      mcap:  442e9, chg: -1.12, score: 48, rsi: 41, vol:  88, adx: 12, signal: "AVOID",      strategy: "—",
    debt: 14.1, interest: 0.0, cashRecv:  8.2, haramFlag: false, screens: [true, true, true, true] },
  { sym: "JPM",   name: "JPMorgan Chase",        sector: "Financials",  mcap:  564e9, chg: -0.41, score: 35, rsi: 38, vol: 102, adx: 10, signal: "AVOID",      strategy: "—",
    debt: 82.4, interest:42.6, cashRecv: 38.8, haramFlag: true,  screens: [false, false, false, false] },
  { sym: "BAC",   name: "Bank of America",       sector: "Financials",  mcap:  316e9, chg: -0.28, score: 32, rsi: 36, vol:  92, adx:  8, signal: "AVOID",      strategy: "—",
    debt: 88.2, interest:38.4, cashRecv: 42.1, haramFlag: true,  screens: [false, false, false, false] },
  { sym: "BRK.B", name: "Berkshire Hathaway B",  sector: "Financials",  mcap:  922e9, chg:  0.42, score: 58, rsi: 50, vol:  68, adx: 12, signal: "WAIT",       strategy: "—",
    debt: 11.4, interest: 4.2, cashRecv: 26.4, haramFlag: false, screens: [true, true, true, true] },
  { sym: "JNJ",   name: "Johnson & Johnson",     sector: "Health",      mcap:  402e9, chg:  0.08, score: 62, rsi: 49, vol:  72, adx: 11, signal: "WAIT",       strategy: "—",
    debt: 21.1, interest: 0.4, cashRecv: 12.2, haramFlag: false, screens: [true, true, true, true] },
  { sym: "WMT",   name: "Walmart Inc.",          sector: "Consumer",    mcap:  604e9, chg:  0.31, score: 70, rsi: 53, vol:  84, adx: 17, signal: "BUY",        strategy: "trend",
    debt: 18.4, interest: 0.2, cashRecv:  6.8, haramFlag: false, screens: [true, true, true, true] },
  { sym: "PG",    name: "Procter & Gamble",      sector: "Consumer",    mcap:  386e9, chg:  0.12, score: 65, rsi: 50, vol:  62, adx: 13, signal: "WAIT",       strategy: "—",
    debt: 16.8, interest: 0.6, cashRecv:  8.2, haramFlag: false, screens: [true, true, true, true] },
  { sym: "MO",    name: "Altria Group",          sector: "Consumer",    mcap:   89e9, chg: -0.18, score: 28, rsi: 32, vol:  48, adx:  6, signal: "AVOID",      strategy: "—",
    debt: 32.8, interest: 1.4, cashRecv:  6.2, haramFlag: true,  screens: [true, true, true, false] },
  { sym: "LMT",   name: "Lockheed Martin",       sector: "Industrials", mcap:  118e9, chg:  0.24, score: 42, rsi: 44, vol:  58, adx:  9, signal: "AVOID",      strategy: "—",
    debt: 28.2, interest: 1.8, cashRecv: 11.4, haramFlag: true,  screens: [true, true, true, false] },
  { sym: "NEE",   name: "NextEra Energy",        sector: "Utilities",   mcap:  152e9, chg:  0.18, score: 61, rsi: 51, vol:  64, adx: 12, signal: "WAIT",       strategy: "—",
    debt: 26.4, interest: 4.2, cashRecv:  8.8, haramFlag: false, screens: [true, true, true, true] },
];

const mockMarket = {
  status: "RISK ON",
  regime: "BULL",
  minGate: 50,
  strongGate: 65,
  haltPipeline: false,
};

const mockSectors = [
  { name: "Tech",        count: 4, pass: 4, haramOnly: false },
  { name: "Comms",       count: 2, pass: 1, haramOnly: false },
  { name: "Consumer",    count: 4, pass: 3, haramOnly: false },
  { name: "Auto",        count: 1, pass: 1, haramOnly: false },
  { name: "Energy",      count: 1, pass: 1, haramOnly: false },
  { name: "Health",      count: 2, pass: 2, haramOnly: false },
  { name: "Industrials", count: 1, pass: 0, haramOnly: true  },
  { name: "Utilities",   count: 1, pass: 1, haramOnly: false },
  { name: "Financials",  count: 3, pass: 1, haramOnly: false },
];

Object.assign(window, {
  fmt$, fmtPct, fmtRatio, scoreColor,
  AAOIFI_SCREENS, HARAM_INDUSTRIES,
  mockUniverse, mockMarket, mockSectors,
});
