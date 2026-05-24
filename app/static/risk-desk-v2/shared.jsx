// shared.jsx — Risk Desk primitives + mock data

const { useState, useEffect, useMemo, useRef } = React;

const fmt$ = (n, dp = 2) =>
  n == null ? "—" : (n < 0 ? "−$" : "$") + Math.abs(Number(n)).toLocaleString("en-US", { minimumFractionDigits: dp, maximumFractionDigits: dp });
const fmtPct = (n, dp = 2) =>
  n == null ? "—" : (n >= 0 ? "+" : "") + Number(n).toFixed(dp) + "%";
const fmtSigned = (n, dp = 2) =>
  n == null ? "—" : (n >= 0 ? "+" : "−") + Math.abs(n).toFixed(dp);

// ─── Mock data ───────────────────────────────────────────────────────
const mockRiskMetrics = {
  var95:        -2.5,   // % of equity at risk, 95% confidence, 1-day
  cvar95:       -3.8,   // tail expected loss
  sharpe:        1.85,
  maxDrawdown: -8.2,
  // deltas vs 30d avg
  varDelta:     -0.2,    // improvement
  cvarDelta:     0.3,    // worsening
  sharpeDelta:   0.15,   // improvement
  ddDelta:       1.1,    // worsening (less negative)
  level: "MEDIUM",       // LOW / MEDIUM / HIGH
};

const mockKelly = {
  fStar:       0.18,   // raw Kelly fraction
  fractional:  0.5,    // half-Kelly setting
  effective:   0.09,   // f* × fractional (capped by static budget)
  staticCap:   0.10,   // hard ceiling
  maxKelly:    0.25,   // built-in safety cap
  enabledAfterTrades: 10,
};

const mockStrategies = [
  { name: "strat_A_breakout",  trades: 142, mean: 0.84, var: 6.42, fKelly: 0.131, fUsed: 0.065, shrunk: false },
  { name: "strat_B_mean_rev",  trades:  98, mean: 0.42, var: 4.18, fKelly: 0.100, fUsed: 0.050, shrunk: false },
  { name: "strat_C_momentum",  trades:  64, mean: 1.12, var: 8.92, fKelly: 0.126, fUsed: 0.063, shrunk: false },
  { name: "strat_D_pairs",     trades:   8, mean: 0.60, var: 3.10, fKelly: 0.000, fUsed: 0.000, shrunk: true  },
];

const initialBlocks = [
  { id: "kill",       icon: "fa-power-off",        name: "kill_switch",          desc: "Hard-stop · cancel all open orders, no new entries", state: "armed",  toggle: true,  dangerous: true  },
  { id: "halt",       icon: "fa-pause",            name: "halt_pipeline",        desc: "Pause the 6-stage signal pipeline · current state held", state: "off",   toggle: false, dangerous: false },
  { id: "autotrade",  icon: "fa-robot",            name: "auto_trade",           desc: "Allow pipeline to send orders to live broker",            state: "on",    toggle: true,  dangerous: false },
  { id: "dry",        icon: "fa-flask",            name: "dry_run",              desc: "All orders synthetic · paper book only",                  state: "off",   toggle: false, dangerous: false },
  { id: "broker",     icon: "fa-plug",             name: "broker_alpaca",        desc: "Alpaca Markets · live · 4 of 5 concurrent connections",   state: "on",    toggle: true,  dangerous: false },
  { id: "ibkr",       icon: "fa-plug-circle-bolt", name: "broker_ibkr",          desc: "Interactive Brokers · disabled (not yet implemented)",     state: "off",   toggle: false, dangerous: false },
  { id: "halal",      icon: "fa-mosque",           name: "halal_gate_enforce",   desc: "Block any signal that fails AAOIFI screening",            state: "on",    toggle: true,  dangerous: false },
  { id: "tg",         icon: "fa-paper-plane",      name: "telegram_alerts",      desc: "Push trade fills + daily report to ميزان كوانت channel",  state: "on",    toggle: true,  dangerous: false },
];

const mockPositionRisk = [
  { sym: "AAPL", weight: 18.2, varContrib: 22.4, corr: 0.62, beta: 1.15, mDD:  4.1 },
  { sym: "NVDA", weight: 14.6, varContrib: 28.8, corr: 0.71, beta: 1.84, mDD:  9.4 },
  { sym: "MSFT", weight: 12.8, varContrib: 13.1, corr: 0.58, beta: 0.92, mDD:  3.2 },
  { sym: "AMZN", weight: 10.4, varContrib: 11.6, corr: 0.55, beta: 1.10, mDD:  4.8 },
  { sym: "GOOGL",weight:  9.2, varContrib:  8.9, corr: 0.49, beta: 1.02, mDD:  3.6 },
  { sym: "UNH",  weight:  8.6, varContrib:  4.2, corr: 0.18, beta: 0.62, mDD:  2.4 },
  { sym: "Cash", weight: 26.2, varContrib:  0.0, corr: 0.00, beta: 0.00, mDD:  0.0 },
];

const mockStressTests = [
  { name: "Market crash · S&P −20%",       impact: -9650, pct: 88, color: "var(--negative)" },
  { name: "VIX spike · +15 vol",           impact: -3420, pct: 42, color: "var(--negative)" },
  { name: "Sector rotation · Tech −10%",   impact: -2480, pct: 28, color: "var(--negative)" },
  { name: "Rate hike · 75bps",             impact:  -820, pct: 12, color: "var(--warning)"  },
  { name: "Halal-screen rebalance",        impact:   240, pct:  4, color: "var(--positive)" },
];

const mockAlerts = [
  { kind: "warning", lab: "VIX above 20 threshold — increased volatility expected. Position-size shrinkage applied.", ts: "12:42 ET" },
  { kind: "ok",      lab: "Halal-screen full universe rescan complete. 0 new haram flags.",                          ts: "12:00 ET" },
  { kind: "ok",      lab: "All positions within per-symbol weight cap (max 20%).",                                   ts: "11:32 ET" },
  { kind: "warning", lab: "Strategy strat_D_pairs has 8 trades — below Kelly minimum (10). Position size = 0.",      ts: "10:08 ET" },
  { kind: "danger",  lab: "Correlation cluster: AAPL · NVDA · MSFT all > 0.6. Concentration risk elevated.",         ts: "09:45 ET" },
  { kind: "info",    lab: "Pre-market scan: 502 symbols · 387 halal-pass · 12 high-conviction (score ≥ 75).",        ts: "06:00 ET" },
];

// ─── Gauge primitive ─────────────────────────────────────────────────
function Gauge({ value, max = 100, label, color = "var(--accent)", size = 96 }) {
  const r = size / 2 - 5;
  const c = 2 * Math.PI * r;
  const pct = Math.min(1, Math.abs(value) / max);
  const dash = pct * c * 0.75; // 270deg arc
  const total = c * 0.75;
  return (
    <div style={{ position: "relative", width: size, height: size }}>
      <svg width={size} height={size} style={{ transform: "rotate(135deg)" }}>
        <circle cx={size/2} cy={size/2} r={r} fill="none" stroke="var(--bg-root)" strokeWidth={5}
                strokeDasharray={`${total} ${c}`} strokeLinecap="round" />
        <circle cx={size/2} cy={size/2} r={r} fill="none" stroke={color} strokeWidth={5}
                strokeDasharray={`${dash} ${c}`} strokeLinecap="round" />
      </svg>
      <div style={{
        position: "absolute", inset: 0,
        display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
      }}>
        <div style={{ fontFamily: "var(--font-mono)", fontSize: 18, fontWeight: 800, color, letterSpacing: "-0.5px" }}>{value}</div>
        <div style={{ fontSize: 9, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.4px", fontWeight: 600 }}>{label}</div>
      </div>
    </div>
  );
}

Object.assign(window, {
  fmt$, fmtPct, fmtSigned,
  Gauge,
  mockRiskMetrics, mockKelly, mockStrategies, initialBlocks, mockPositionRisk, mockStressTests, mockAlerts,
});
