/* mizanquant — Overview v2
   Wires the new design to existing /api/v1/* endpoints.
   No frameworks, no build step — vanilla JS.

   Endpoints used:
     GET  /api/v1/overview                  (aggregator: system+portfolio+market+pipeline+top_signals+guards)
     GET  /api/v1/sectors/performance
     GET  /api/v1/guards/summary
     GET  /api/v1/paper/trades?limit=8
     GET  /api/v1/scoring/weighted?symbol=…  (on signal select)
     GET  /api/v1/trade/plan?symbol=…        (on signal select)
     GET  /api/v1/halal/check?symbol=…       (on signal select)
     GET  /api/v1/pipeline/run?dry_run=…     (on Run click)
     POST /api/v1/paper/execute              (on Send to paper trade)
     GET  /screener                          (full signal list — already cached by backend)
*/

const $ = (id) => document.getElementById(id);
const fmt$ = (n, dp = 2) => n == null ? "—" : "$" + Number(n).toLocaleString("en-US", { minimumFractionDigits: dp, maximumFractionDigits: dp });
const fmtPct = (n, dp = 2) => n == null ? "—" : (n >= 0 ? "+" : "") + Number(n).toFixed(dp) + "%";
const clamp = (n, lo, hi) => Math.max(lo, Math.min(hi, n));

const state = {
  overview: null,
  sectors: null,
  guards: null,
  paper: null,
  signals: [],
  selectedSymbol: null,
  selectedAnalyze: null,
  selectedConsensus: null,
  loadingAnalyze: false,
  pipelineRunning: false,
};

/* ─── Fetch helper ────────────────────────────────────────────── */
async function api(path, opts = {}) {
  try {
    const res = await fetch(path, opts);
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return await res.json();
  } catch (err) {
    console.warn(`API error ${path}:`, err.message);
    return null;
  }
}

function toast(msg, kind = "ok") {
  const t = $("toast");
  t.textContent = msg;
  t.className = "toast show" + (kind === "error" ? " error" : "");
  setTimeout(() => t.className = "toast", 3500);
}

/* ─── Clock ───────────────────────────────────────────────────── */
function tickClock() {
  const now = new Date();
  const et = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  }).format(now);
  $("mbClock").textContent = et;
  $("etClock").textContent = et.slice(0, 5) + " ET";
}
setInterval(tickClock, 1000); tickClock();

/* ─── Market Strip ────────────────────────────────────────────── */
function renderMarket(market) {
  if (!market) return;
  const vix = market.vix?.value ?? market.vix?.vix ?? null;
  const spy = market.spy_regime?.label ?? market.spy_regime?.regime ?? "—";
  const breadth = market.breadth?.breadth_pct ?? null;
  const credit = market.credit?.ratio ?? null;
  const liq = market.liquidity?.liquidity_pct ?? null;

  // Top market-bar values
  $("mbSpy").textContent     = spy ?? "—";
  $("mbSpy").style.color     = spy === "BULL" ? "var(--green)" : spy === "BEAR" ? "var(--red)" : "var(--text-primary)";
  $("mbVix").textContent     = vix != null ? Number(vix).toFixed(2) : "—";
  $("mbBreadth").textContent = breadth != null ? Number(breadth).toFixed(1) + "%" : "—";
  $("mbCredit").textContent  = credit != null ? Number(credit).toFixed(4) : "—";
  $("mbLiq").textContent     = liq != null ? Number(liq).toFixed(1) + "%" : "—";

  // Bigger MC strip
  const items = [
    { lab: "VIX",     val: vix     != null ? Number(vix).toFixed(2)        : "—", sub: vix != null && vix < 20 ? "calm" : vix != null && vix < 30 ? "elevated" : "stress",
      color: vix != null ? (vix < 20 ? "var(--green)" : vix < 30 ? "var(--amber)" : "var(--red)") : "var(--text-primary)" },
    { lab: "SPY",     val: spy ?? "—", sub: market.spy_regime?.trend ?? "regime",
      color: spy === "BULL" ? "var(--green)" : spy === "BEAR" ? "var(--red)" : "var(--text-primary)" },
    { lab: "Breadth", val: breadth != null ? Number(breadth).toFixed(1) + "%" : "—", sub: "advance/decline",
      color: breadth != null && breadth >= 50 ? "var(--green)" : "var(--red)" },
    { lab: "Credit",  val: credit  != null ? Number(credit).toFixed(4) : "—", sub: "HYG / LQD",
      color: "var(--text-primary)" },
    { lab: "Liq",     val: liq     != null ? Number(liq).toFixed(1) + "%" : "—", sub: market._market_open ? "open" : "closed",
      color: "var(--text-primary)" },
  ];
  $("mcStrip").innerHTML = items.map(i =>
    `<div class="mc-card">
       <div class="mc-lab">${i.lab}</div>
       <div class="mc-val" style="color:${i.color};">${i.val}</div>
       <div class="mc-sub">${i.sub}</div>
     </div>`
  ).join("");
}

/* ─── Regime timeline ─────────────────────────────────────────── */
function renderRegime(system, market) {
  const regime = system?.regime ?? "UNKNOWN";
  const color = regime === "BULL" ? "var(--green)" : regime === "BEAR" ? "var(--red)" : "var(--amber)";
  const bg    = regime === "BULL" ? "var(--green-dim)" : regime === "BEAR" ? "var(--red-dim)" : "var(--amber-dim)";

  $("regimeSub").textContent = `current · ${regime}`;
  $("regimeBar").innerHTML =
    `<div class="regime-bar" style="height:32px;">
       <div style="background:${bg};color:${color};display:flex;align-items:center;justify-content:center;font-size:10px;font-weight:700;letter-spacing:0.5px;">${regime}</div>
     </div>`;
}

/* ─── Top signals (3 hero cards) + signal table ───────────────── */
function sparkSvg(points, color, height = 20) {
  if (!points || points.length < 2) return "";
  const w = 100;
  const min = Math.min(...points), max = Math.max(...points);
  const range = max - min || 1;
  const step = w / (points.length - 1);
  const path = points.map((y, i) =>
    `${i ? "L" : "M"}${(i * step).toFixed(1)},${((max - y) / range * (height - 4) + 2).toFixed(1)}`
  ).join(" ");
  return `<svg viewBox="0 0 ${w} ${height}" preserveAspectRatio="none" style="width:100%;height:${height}px;">
            <path d="${path}" fill="none" stroke="${color}" stroke-width="1.2"/>
          </svg>`;
}

function ringSvg(score) {
  const color = score >= 75 ? "var(--green)" : score >= 60 ? "var(--amber)" : "var(--red)";
  const r = 18, c = 2 * Math.PI * r;
  const dash = (score / 100) * c;
  return `<div class="ring">
    <svg width="44" height="44">
      <circle cx="22" cy="22" r="${r}" fill="none" stroke="var(--bg-primary)" stroke-width="3"/>
      <circle cx="22" cy="22" r="${r}" fill="none" stroke="${color}" stroke-width="3"
              stroke-dasharray="${dash} ${c}" stroke-linecap="round"/>
    </svg>
    <div class="ring-num" style="color:${color};">${score}</div>
  </div>`;
}

function verdictFromScore(score) {
  if (score >= 90) return "STRONG BUY";
  if (score >= 75) return "BUY";
  if (score >= 55) return "WAIT";
  return "AVOID";
}

function badgeClassFor(verdict) {
  if (verdict?.includes("BUY"))  return "b-green";
  if (verdict?.includes("SELL")) return "b-red";
  if (verdict === "WAIT")        return "b-amber";
  return "b-red";
}

function renderSignals() {
  const arr = state.signals;
  const top3 = arr.slice(0, 3);
  const rest = arr.slice(3);

  // Hero trio
  if (!top3.length) {
    $("signalsFeatured").innerHTML = `
      <div class="skeleton" style="height:90px;"></div>
      <div class="skeleton" style="height:90px;"></div>
      <div class="skeleton" style="height:90px;"></div>`;
  } else {
    $("signalsFeatured").innerHTML = top3.map(s => {
      const sel = s.symbol === state.selectedSymbol ? " selected" : "";
      const verdict = s.consensus || s.verdict || verdictFromScore(s.score_avg ?? s.score ?? 0);
      const score = Math.round(s.score_avg ?? s.score ?? 0);
      const chg = s.change_pct ?? s.chg ?? 0;
      const spark = (s.spark || gradientSpark(score));
      return `<div class="sfc${sel}" onclick="selectSignal('${s.symbol}')">
        <div class="sfc-top">
          ${ringSvg(score)}
          <div>
            <div class="sfc-sym">${s.symbol}</div>
            <div class="sfc-chg ${chg >= 0 ? 'txt-green' : 'txt-red'}">${fmtPct(chg)}</div>
          </div>
        </div>
        <div class="sfc-spark">${sparkSvg(spark, chg >= 0 ? "var(--green)" : "var(--red)")}</div>
        <div class="sfc-foot">
          <span class="sfc-time">${verdict}</span>
          <span class="badge ${badgeClassFor(verdict)}">${verdict}</span>
        </div>
      </div>`;
    }).join("");
  }

  renderSignalTable();
}

function gradientSpark(score) {
  // Synthesize a sparkline based on score — not real data, just visual rhythm.
  const out = [];
  const base = score / 100;
  for (let i = 0; i < 11; i++) {
    out.push(base + Math.sin(i * 0.9) * 0.06 + (i / 30));
  }
  return out;
}

function renderSignalTable() {
  const sFilter = $("fSignal").value;
  const scoreMin = Number($("fScore").value);
  const halalOnly = $("fHalal").checked;

  let rows = state.signals.slice(3);
  rows = rows.filter(r => {
    const score = Math.round(r.score_avg ?? r.score ?? 0);
    const verdict = r.consensus || r.verdict || verdictFromScore(score);
    if (sFilter !== "all" && verdict !== sFilter) return false;
    if (score < scoreMin) return false;
    if (halalOnly && r.is_halal === false) return false;
    return true;
  });

  if (!rows.length) {
    $("signalTableBody").innerHTML = `<tr><td colspan="6" style="text-align:center;padding:16px;color:var(--text-muted);font-size:10px;">No signals match the filters</td></tr>`;
    return;
  }
  $("signalTableBody").innerHTML = rows.map(r => {
    const score = Math.round(r.score_avg ?? r.score ?? 0);
    const verdict = r.consensus || r.verdict || verdictFromScore(score);
    const chg = r.change_pct ?? r.chg ?? 0;
    const sel = r.symbol === state.selectedSymbol ? " selected" : "";
    const barColor = score >= 75 ? "var(--green)" : score >= 60 ? "var(--amber)" : "var(--red)";
    return `<tr class="${sel}" onclick="selectSignal('${r.symbol}')">
      <td style="font-weight:700;">${r.symbol}</td>
      <td>
        <div style="display:flex;align-items:center;gap:6px;">
          <div class="score-bar"><div style="width:${score}%;background:${barColor};"></div></div>
          <span class="mono" style="font-size:10px;">${score}</span>
        </div>
      </td>
      <td><span class="badge ${badgeClassFor(verdict)}">${verdict}</span></td>
      <td class="mono">${r.price ? "$" + Number(r.price).toFixed(2) : "—"}</td>
      <td class="mono ${chg >= 0 ? 'txt-green' : 'txt-red'}">${fmtPct(chg)}</td>
      <td style="color:var(--text-secondary);">${r.sector || "—"}</td>
    </tr>`;
  }).join("");
}

/* ─── Analyze panel ───────────────────────────────────────────── */
async function selectSignal(symbol) {
  state.selectedSymbol = symbol;
  renderSignals();
  $("anSubtitle").textContent = symbol + " · loading…";
  $("analyzePanel").innerHTML = `<div class="card"><div style="text-align:center;padding:20px;color:var(--text-muted);font-size:11px;"><i class="fas fa-circle-notch fa-spin" style="font-size:18px;display:block;margin-bottom:6px;"></i>Loading scoring & trade plan…</div></div>`;

  const [scoring, plan, halal] = await Promise.all([
    api(`/api/v1/scoring/weighted?symbol=${symbol}`),
    api(`/api/v1/trade/plan?symbol=${symbol}`),
    api(`/api/v1/halal/check?symbol=${symbol}`),
  ]);

  state.selectedAnalyze = { symbol, scoring, plan, halal };
  renderAnalyze();
  renderConsensusEmpty(symbol);   // Heavy endpoint — only load on explicit click
}

function renderAnalyze() {
  const data = state.selectedAnalyze;
  if (!data) {
    $("analyzePanel").innerHTML = `<div class="analyze-empty"><i class="fas fa-search"></i>Select a signal to analyze</div>`;
    return;
  }
  const { symbol, scoring, plan, halal } = data;
  const score = Math.round(scoring?.total ?? scoring?.weighted_score ?? 0);
  const verdict = verdictFromScore(score);
  const price = plan?.current_price ?? plan?.price ?? scoring?.price ?? null;
  const entry = plan?.strategy_entry ?? plan?.entry_price ?? plan?.entry ?? price;
  const stop  = plan?.strategy_stop  ?? plan?.stop_loss   ?? plan?.stop;
  const tp1   = plan?.strategy_tp1   ?? plan?.take_profit ?? plan?.tp1;
  const tp2   = plan?.strategy_tp2   ?? plan?.tp2;
  const tp3   = plan?.strategy_tp3   ?? plan?.tp3;
  const strat = plan?.strategy ?? "—";
  const isHalal = halal?.halal === true;
  const chg = scoring?.change_pct ?? plan?.change_pct ?? 0;
  $("anSubtitle").textContent = verdict;

  // Score breakdown — try to pull subcomponents; fall back to a single bar.
  const breakdown = scoring?.components ?? scoring?.breakdown ?? {};
  const componentBars = Object.keys(breakdown).slice(0, 4).map(k => {
    const raw = breakdown[k];
    const v = typeof raw === "object" ? (raw.value ?? raw.score ?? 0) : Number(raw);
    const max = typeof raw === "object" && raw.max ? raw.max : 100;
    const pct = clamp((v / max) * 100, 0, 100);
    const color = pct >= 60 ? "var(--green)" : pct >= 40 ? "var(--accent)" : "var(--amber)";
    return `<div class="an-bar-row">
      <span class="lab">${k}</span>
      <div class="bar"><div style="width:${pct}%;background:${color};"></div></div>
      <span class="num">${v.toFixed ? v.toFixed(1) : v}</span>
    </div>`;
  }).join("") || `<div class="an-bar-row">
      <span class="lab">Total</span>
      <div class="bar"><div style="width:${score}%;background:var(--accent);"></div></div>
      <span class="num">${score}</span>
    </div>`;

  $("analyzePanel").innerHTML = `<div class="card">
    <div class="an-hdr">
      <div>
        <div class="an-sym">${symbol}</div>
        <div class="an-co">${halal?.details?.reason ?? (isHalal ? "Halal · verified" : "Halal status unknown")}</div>
      </div>
      <span class="badge ${badgeClassFor(verdict)}">${verdict}</span>
    </div>
    <div class="an-price">
      <span class="p">${price != null ? "$" + Number(price).toFixed(2) : "—"}</span>
      <span class="c ${chg >= 0 ? 'txt-green' : 'txt-red'}">${fmtPct(chg)}</span>
    </div>

    <div class="an-sect-title">Score breakdown</div>
    ${componentBars}

    <div class="an-sect-title">Trade plan</div>
    <div class="an-grid">
      <div class="row"><span class="l">Entry</span><span class="v">${entry != null ? "$" + Number(entry).toFixed(2) : "—"}</span></div>
      <div class="row"><span class="l">Halal</span><span class="v ${isHalal ? 'txt-green' : 'txt-red'}">${isHalal ? "verified" : (halal === null ? "—" : "flag")}</span></div>
      <div class="row"><span class="l">Stop</span><span class="v txt-red">${stop != null ? "$" + Number(stop).toFixed(2) : "—"}</span></div>
      <div class="row"><span class="l">TP1</span><span class="v txt-green">${tp1 != null ? "$" + Number(tp1).toFixed(2) : "—"}</span></div>
      <div class="row"><span class="l">TP2</span><span class="v txt-green">${tp2 != null ? "$" + Number(tp2).toFixed(2) : "—"}</span></div>
      <div class="row"><span class="l">TP3</span><span class="v txt-green">${tp3 != null ? "$" + Number(tp3).toFixed(2) : "—"}</span></div>
      <div class="row" style="grid-column:1 / -1;"><span class="l">Strategy</span><span class="v">${strat}</span></div>
    </div>

    <button class="an-trade" onclick="sendToPaperTrade()" ${verdict === "AVOID" ? "disabled" : ""}>
      <i class="fas fa-paper-plane"></i> Send to paper trade
    </button>
    <button class="an-trade" style="background:var(--bg-tertiary);color:var(--text-secondary);border-color:var(--border-light);margin-top:6px;" onclick="loadConsensus('${symbol}')">
      <i class="fas fa-vote-yea"></i> Run AI consensus
    </button>
  </div>`;
}

async function sendToPaperTrade() {
  const a = state.selectedAnalyze;
  if (!a) return;
  const { symbol, plan, scoring } = a;
  const entry = plan?.strategy_entry ?? plan?.entry_price ?? plan?.entry ?? plan?.current_price;
  if (!entry) { toast("No entry price available", "error"); return; }
  const stop = plan?.strategy_stop ?? plan?.stop_loss;
  const tp   = plan?.strategy_tp1  ?? plan?.take_profit;
  const conf = (scoring?.total ?? 0) / 100;

  const body = {
    symbol, side: "buy", entry_price: entry,
    stop_loss: stop, take_profit: tp,
    shares: Math.max(1, Math.floor(1000 / entry)),  // ~$1k position default
    confidence: clamp(conf, 0, 1),
  };
  const result = await api("/api/v1/paper/execute", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (result?.id) {
    toast(`Paper trade #${result.id} submitted: ${symbol} × ${body.shares} @ $${entry.toFixed(2)}`);
    loadPaper();
  } else {
    toast("Paper trade failed — check backend logs", "error");
  }
}

/* ─── Portfolio ───────────────────────────────────────────────── */
function renderPortfolio(p) {
  if (!p) {
    $("portfolioCompact").innerHTML = `
      <div class="pc-row"><span class="l">Equity</span><span class="v">—</span></div>
      <div class="pc-row"><span class="l">Positions</span><span class="v">—</span></div>
      <div class="pc-row"><span class="l">P&L</span><span class="v">—</span></div>
      <div class="pc-row"><span class="l">Cash</span><span class="v">—</span></div>`;
    return;
  }
  const pnlColor = p.daily_pnl > 0 ? "var(--green)" : p.daily_pnl < 0 ? "var(--red)" : "var(--text-primary)";
  $("portfolioCompact").innerHTML = `
    <div class="pc-row"><span class="l">Equity</span><span class="v">${fmt$(p.equity, 0)}</span></div>
    <div class="pc-row"><span class="l">Positions</span><span class="v">${p.open_positions}</span></div>
    <div class="pc-row"><span class="l">P&L</span><span class="v" style="color:${pnlColor};">${p.daily_pnl >= 0 ? "+" : ""}${fmt$(p.daily_pnl, 0).replace("$","$")}</span></div>
    <div class="pc-row"><span class="l">Cash</span><span class="v">${fmt$(p.cash, 0)}</span></div>`;
  $("portSubtitle").textContent = p.broker_type || "broker";
}

/* ─── Open positions ──────────────────────────────────────────── */
function renderPositions(positions) {
  if (!positions || !positions.length) {
    $("positionsTable").innerHTML = `<div style="text-align:center;padding:12px;color:var(--text-muted);font-size:10px;">No open positions</div>`;
    $("posSubtitle").textContent = "0 open";
    return;
  }
  $("posSubtitle").textContent = `${positions.length} open`;
  $("positionsTable").innerHTML = `<table class="pos-table">
    <thead><tr><th>Sym</th><th>Qty</th><th>Entry</th><th>Last</th><th>P&L</th></tr></thead>
    <tbody>${positions.map(p => {
      const pos = (p.unrealized_pl ?? 0) >= 0;
      return `<tr>
        <td style="font-weight:700;">${p.symbol}</td>
        <td class="mono">${p.qty}</td>
        <td class="mono">${fmt$(p.avg_entry)}</td>
        <td class="mono">${fmt$(p.current_price)}</td>
        <td class="mono" style="font-weight:700;color:${pos ? 'var(--green)' : 'var(--red)'};">${pos ? "+" : ""}${fmt$(p.unrealized_pl)}</td>
      </tr>`;
    }).join("")}</tbody>
  </table>`;
}

/* ─── Paper trades ────────────────────────────────────────────── */
async function loadPaper() {
  const trades = await api("/api/v1/paper/trades?limit=8");
  state.paper = Array.isArray(trades) ? trades : [];
  renderPaper();
}

function renderPaper() {
  const trades = state.paper || [];
  if (!trades.length) {
    $("paperTrades").innerHTML = `<div style="text-align:center;padding:12px;color:var(--text-muted);font-size:10px;">No paper trades yet</div>`;
    $("paperSubtitle").textContent = "0 trades";
    return;
  }
  const open = trades.filter(t => t.status !== "closed").length;
  const win  = trades.filter(t => (t.pnl ?? 0) > 0).length;
  const total = trades.length;
  $("paperSubtitle").textContent = `${total} trades · ${win}/${total} win`;

  const sum = `<div class="ptt-sum">
    <div class="ptt-stat"><div class="ptt-lab">Total</div><div class="ptt-val">${total}</div></div>
    <div class="ptt-stat"><div class="ptt-lab">Win</div><div class="ptt-val txt-green">${win}</div></div>
    <div class="ptt-stat"><div class="ptt-lab">Open</div><div class="ptt-val">${open}</div></div>
  </div>`;

  const rows = trades.slice(0, 5).map(t => {
    const pnl = t.pnl ?? 0;
    const pnlPct = t.pnl_pct ?? 0;
    const pnlPos = pnl >= 0;
    return `<div class="ptt-row">
      <span class="ptt-sym">${t.symbol}</span>
      <span class="ptt-side ${t.side}">${t.side}</span>
      <span class="ptt-entry">${fmt$(t.entry_price)}</span>
      <span class="ptt-meta">${t.strategy_id || "—"}</span>
      <span style="flex:1;"></span>
      <span class="ptt-pnl" style="color:${pnlPos ? 'var(--green)' : 'var(--red)'};">${pnlPos ? "+" : ""}${fmtPct(pnlPct, 1)}</span>
      <span class="ptt-status ${t.status === 'closed' ? 'closed' : 'open'}">${t.status === 'closed' ? 'CLOSED' : 'OPEN'}</span>
    </div>`;
  }).join("");

  $("paperTrades").innerHTML = sum + rows;
}

/* ─── Pipeline ────────────────────────────────────────────────── */
function renderPipeline(pipe) {
  if (!pipe?.stages) {
    $("pipeline").innerHTML = `<div class="skeleton" style="height:36px;"></div>`;
    return;
  }
  const stages = pipe.stages;
  $("pipelineSubtitle").textContent = `${stages.length} stages · ${pipe.pipeline_runs_today || 0} runs today`;
  $("pipeline").innerHTML = `<div class="pflow">${
    stages.map((s, i) => `
      <div class="pflow-stage" title="${s.label}">
        <div class="pflow-dot ${s.status}">${(s.name || "?")[0].toUpperCase()}</div>
        <div class="pflow-lab">${(s.label || s.name).slice(0, 6)}</div>
      </div>${i < stages.length - 1 ? `<div class="pflow-conn"></div>` : ""}`
    ).join("")
  }</div>`;
}

async function runPipeline() {
  const btn = $("runPipelineBtn");
  if (state.pipelineRunning) return;
  state.pipelineRunning = true;
  btn.disabled = true;
  btn.innerHTML = `<i class="fas fa-circle-notch fa-spin"></i> Running…`;

  const dryRun = $("dryRun").checked;
  const result = await api(`/api/v1/pipeline/run?dry_run=${dryRun}&strategy=ABC`);
  state.pipelineRunning = false;
  btn.disabled = false;
  btn.innerHTML = `<i class="fas fa-play"></i> Run`;
  if (result) {
    toast(`Pipeline finished in ${result.elapsed_s?.toFixed(1)}s · ${result.signals_passed ?? 0} passed`);
    loadAll();
  } else {
    toast("Pipeline run failed", "error");
  }
}

/* ─── Guards ──────────────────────────────────────────────────── */
function renderGuards(summary) {
  const data = summary || {};
  const entries = Object.entries(data).sort((a, b) => b[1] - a[1]);
  if (!entries.length) {
    $("guards").innerHTML = `<div style="text-align:center;padding:12px;color:var(--text-muted);font-size:10px;">No rejections today</div>`;
    return;
  }
  const max = Math.max(...entries.map(e => e[1]));
  $("guards").innerHTML = `<div class="guard-list">${
    entries.slice(0, 6).map(([name, count]) => {
      const pct = (count / max) * 100;
      const color = count > 10 ? "var(--red)" : count > 3 ? "var(--amber)" : "var(--green)";
      return `<div class="guard-row">
        <span class="guard-lab">${name}</span>
        <div class="guard-bar"><div style="width:${pct}%;background:${color};"></div></div>
        <span class="guard-ct">${count}</span>
      </div>`;
    }).join("")
  }</div>`;
}

/* ─── Schedule ────────────────────────────────────────────────── */
function renderSchedule(pipe) {
  const sch = pipe?.schedule || [];
  if (!sch.length) {
    $("schedule").innerHTML = `<div style="text-align:center;padding:12px;color:var(--text-muted);font-size:10px;">No schedule available</div>`;
    return;
  }
  $("schedule").innerHTML = sch.slice(0, 6).map(s =>
    `<div class="sch-row">
       <span class="sch-time">${s.time}</span>
       <span class="sch-lab">${s.task}</span>
       <span class="badge b-${s.type === 'pipeline' ? 'accent' : s.type === 'signal' ? 'green' : 'muted'}">${s.type}</span>
     </div>`
  ).join("");
}

/* ─── Sectors heatmap ─────────────────────────────────────────── */
const HARAM_SECTORS = new Set(["Financial Services", "Financials", "Banks"]);

function renderSectors(sectors) {
  if (!sectors || !sectors.length) {
    $("sectorGrid").innerHTML = `<div style="grid-column:1/-1;text-align:center;padding:20px;color:var(--text-muted);font-size:10px;">No sector data</div>`;
    return;
  }
  $("secSub").textContent = `${sectors.length} sector ETFs · daily`;
  $("sectorGrid").innerHTML = sectors.map(s => {
    const chg = Number(s.perf_1d ?? s.change_pct ?? s.daily_change ?? 0);
    const isHaram = HARAM_SECTORS.has(s.name || s.sector) || s.haram === true;
    const intensity = clamp(Math.abs(chg) / 2.5, 0, 1);
    const bg = chg >= 0
      ? `oklch(0.68 0.16 150 / ${0.10 + intensity * 0.35})`
      : `oklch(0.55 0.14 20 / ${0.10 + intensity * 0.35})`;
    return `<div class="sec-tile${isHaram ? ' haram' : ''}" style="background:${bg};" title="${isHaram ? 'haram — auto-excluded' : 'halal'}">
      <div class="sec-name">${s.name || s.sector || "—"}</div>
      <div class="sec-chg ${chg >= 0 ? 'txt-green' : 'txt-red'}">${fmtPct(chg)}</div>
    </div>`;
  }).join("");
}

/* ─── Models leaderboard ──────────────────────────────────────── */
// Models data isn't exposed by a single API — derive a static-ish leaderboard
// from `/v1/forecast/models` if present, else fall back to a placeholder list
// matching the family of models the codebase ships.
const MODEL_FAMILIES = [
  { name: "Transformer", fam: "transformer", status: "production" },
  { name: "GRU-Seq2Seq", fam: "gru",         status: "production" },
  { name: "Ensemble v3", fam: "ensemble",    status: "production" },
  { name: "LSTM-VAE",    fam: "lstm",        status: "staging"    },
  { name: "Dilated CNN", fam: "cnn",         status: "staging"    },
  { name: "Double DQN",  fam: "deep-rl",     status: "idle"       },
  { name: "ARIMA-GARCH", fam: "statistical", status: "production" },
  { name: "MovingAvg",   fam: "classic",     status: "idle"       },
];

async function loadModels() {
  // Try a possible models endpoint; if not available, fall back.
  const live = await api("/api/v1/models/leaderboard");
  let rows;
  if (Array.isArray(live) && live.length) {
    rows = live.slice(0, 8);
  } else {
    rows = MODEL_FAMILIES.map(m => ({
      ...m,
      sharpe: 0,    // unknown — will render as "—"
      win: null,
      trades: null,
      spark: [],
    }));
  }
  renderModels(rows);
}

function renderModels(rows) {
  $("modelGrid").innerHTML = rows.map(m => {
    const sharpe = m.sharpe ?? null;
    const color = sharpe != null && sharpe >= 1.5 ? "var(--green)" :
                  sharpe != null && sharpe >= 1.0 ? "var(--amber)" : "var(--text-muted)";
    return `<div class="ml-card fam-${m.fam}">
      <div class="ml-top">
        <span class="ml-name">${m.name}</span>
        <span class="ml-status ${m.status}">${m.status}</span>
      </div>
      <div class="ml-metrics">
        <div class="ml-metric"><span class="ml-mval" style="color:${color};">${sharpe != null && sharpe > 0 ? sharpe.toFixed(2) : "—"}</span><span class="ml-mlab">sharpe</span></div>
        <div class="ml-metric"><span class="ml-mval">${m.win != null ? m.win + "%" : "—"}</span><span class="ml-mlab">win</span></div>
        <div class="ml-metric"><span class="ml-mval">${m.trades ?? "—"}</span><span class="ml-mlab">trades</span></div>
      </div>
      <div class="ml-spark">${sparkSvg(m.spark, color, 16)}</div>
    </div>`;
  }).join("");
}

/* ─── AI Consensus ────────────────────────────────────────────── */
function renderConsensusEmpty(symbol) {
  $("cnSubtitle").textContent = `${symbol} · not run`;
  $("consensus").innerHTML = `<div class="analyze-empty">
    <i class="fas fa-vote-yea"></i>
    Press <strong>"Run AI consensus"</strong> in the Analyze panel.
    <div style="margin-top:6px;font-size:9px;">14-tool ensemble, ~3–5s.</div>
  </div>`;
}

async function loadConsensus(symbol) {
  $("cnSubtitle").textContent = `${symbol} · running…`;
  $("consensus").innerHTML = `<div class="analyze-empty"><i class="fas fa-circle-notch fa-spin"></i>Running 14-tool consensus…</div>`;
  const c = await api(`/consensus?symbol=${symbol}&horizon=5&episodes=5`);
  if (!c || c.error || (Array.isArray(c) && c[0]?.Status)) {
    $("consensus").innerHTML = `<div class="analyze-empty"><i class="fas fa-exclamation-triangle"></i>${c?.[0]?.Status || c?.error || "Consensus unavailable"}</div>`;
    $("cnSubtitle").textContent = `${symbol} · unavailable`;
    return;
  }
  renderConsensus(symbol, c);
}

function renderConsensus(symbol, data) {
  // Backend returns either a list of dict rows or a single dict;
  // shape varies. Try to extract verdict, confidence, votes.
  let verdict, conf, votes = [];
  if (Array.isArray(data)) {
    const lines = data;
    const summary = lines.find(r => r.Recommendation || r.recommendation || r.Verdict);
    if (summary) {
      verdict = summary.Recommendation || summary.recommendation || summary.Verdict;
      conf = parseFloat(summary.Confidence || summary.confidence || 0);
    }
    votes = lines.filter(r => r.Tool || r.Model).map(r => ({
      model: r.Tool || r.Model,
      vote:  r.Vote || r.Verdict || (r.Signal || ""),
    }));
  } else {
    verdict = data.recommendation || data.verdict;
    conf = data.confidence ?? 0;
    votes = (data.votes || data.results || []).map(v => ({
      model: v.model || v.tool || v.name,
      vote:  v.vote || v.verdict || v.signal,
    }));
  }

  const head = verdict?.includes("BUY") ? "buy" : verdict?.includes("SELL") ? "sell" : verdict?.includes("HOLD") ? "hold" : "idle";
  const color = head === "buy" ? "var(--green)" : head === "sell" ? "var(--red)" : head === "hold" ? "var(--amber)" : "var(--text-muted)";
  const buys  = votes.filter(v => /BUY/.test(v.vote)).length;
  const sells = votes.filter(v => /SELL/.test(v.vote)).length;
  const holds = votes.filter(v => /HOLD|NEUTRAL/.test(v.vote)).length;

  $("cnSubtitle").textContent = `${symbol} · ${votes.length} models`;
  $("consensus").innerHTML = `
    <div class="cn-head ${head}">
      <div>
        <div class="cn-sym" style="color:${color};">${symbol} · ${verdict || "—"}</div>
        <div class="cn-sub" style="color:${color};">${votes.length}-model ensemble</div>
      </div>
      <div>
        <div class="cn-conf" style="color:${color};">${conf ? Number(conf).toFixed(1) + "%" : "—"}</div>
        <div class="cn-conflab" style="color:${color};">confidence</div>
      </div>
    </div>
    ${votes.length ? `<div class="cn-sum">
      <div class="cn-sum-cell buy"><div class="cn-sum-val">${buys}</div><div class="cn-sum-lab">BUY</div></div>
      <div class="cn-sum-cell hold"><div class="cn-sum-val">${holds}</div><div class="cn-sum-lab">HOLD</div></div>
      <div class="cn-sum-cell sell"><div class="cn-sum-val">${sells}</div><div class="cn-sum-lab">SELL</div></div>
    </div>` : ""}
    <div style="max-height:180px;overflow-y:auto;">${
      votes.map(v => `<div class="cn-vote">
        <span class="model">${v.model}</span>
        <span class="badge ${/BUY/.test(v.vote) ? 'b-green' : /SELL/.test(v.vote) ? 'b-red' : 'b-muted'}">${v.vote || "—"}</span>
      </div>`).join("")
    }</div>`;
}

/* ─── Status bar ──────────────────────────────────────────────── */
function renderStatus(system) {
  if (!system) return;
  const broker = system.broker || "unknown";
  $("sbBroker").textContent  = `Broker · ${broker}`;
  $("sbBrokerDot").className = "dot " + (broker === "connected" ? "dot-green" : broker === "not_configured" ? "dot-amber" : "dot-red");
  $("sbAutoTrade").textContent = `Auto-trade ${system.auto_trading || "off"}`;
  $("sbRegime").textContent    = `Regime ${system.regime || "?"}`;
  $("sbKillSwitch").textContent = `Kill-switch ${system.kill_switch ? "ON" : "off"}`;
  const healthy = system.status === "ok" || system.status === "operational";
  $("sysText").textContent = healthy ? "Connected" : "Degraded";
  $("sysDot").className = "dot " + (healthy ? "dot-green" : "dot-amber");
  $("sbPipeline").textContent = `Pipeline · ${system.uptime_seconds ? "alive" : "—"}`;
}

/* ─── Load signals ────────────────────────────────────────────── */
async function loadSignals() {
  // Top signals from the aggregator (small list, fast)
  const top = state.overview?.top_signals || [];
  // Full screener list (cached by backend)
  const full = await api("/screener");
  let arr = [];
  if (Array.isArray(full)) {
    arr = full.filter(r => r.symbol).map(r => ({
      symbol: r.symbol,
      score_avg: r.swing_score ?? r.smart_score ?? r.score ?? 0,
      score: r.swing_score ?? r.smart_score ?? r.score ?? 0,
      consensus: r.swing_signal ?? r.signal ?? r.verdict,
      price: r.price ?? r.last_price,
      change_pct: r.change_pct ?? r.day_change_pct ?? 0,
      sector: r.sector,
      is_halal: r.halal === "Yes" || r.is_halal === true || r.halal === true,
    }));
  }
  // Merge top into front (preserving order)
  if (top.length) {
    const topSymbols = new Set(top.map(t => t.symbol));
    const topRows = top.map(t => ({
      symbol: t.symbol,
      score_avg: t.score_avg ?? 0,
      consensus: t.consensus,
      price: t.price ?? null,
      change_pct: 0,
      sector: t.sector ?? "—",
      is_halal: true,
    }));
    arr = topRows.concat(arr.filter(r => !topSymbols.has(r.symbol)));
  }
  state.signals = arr;
  renderSignals();
  $("symbolCount").textContent = `${arr.length} symbols`;
}

/* ─── Master loader ───────────────────────────────────────────── */
async function loadAll(force = false) {
  const url = `/api/v1/overview${force ? "?force_refresh=true" : ""}`;
  const ov = await api(url);
  state.overview = ov || {};

  renderMarket(ov?.market_context);
  renderRegime(ov?.system, ov?.market_context);
  renderPortfolio(ov?.portfolio);
  renderPositions(ov?.portfolio?.positions);
  renderPipeline(ov?.pipeline);
  renderSchedule(ov?.pipeline);
  renderStatus(ov?.system);

  // Parallel loads
  loadSignals();
  api("/api/v1/sectors/performance").then(s => {
    state.sectors = s;
    renderSectors(Array.isArray(s) ? s : s?.sectors || []);
  });
  api("/api/v1/guards/summary").then(g => {
    state.guards = g;
    renderGuards(g);
  });
  loadPaper();
  loadModels();
}

/* ─── Boot ────────────────────────────────────────────────────── */
window.addEventListener("DOMContentLoaded", () => {
  loadAll();
  // Auto-refresh every 30s
  setInterval(() => loadAll(false), 30000);

  // ⌘K to focus search
  document.addEventListener("keydown", e => {
    if ((e.metaKey || e.ctrlKey) && e.key === "k") {
      e.preventDefault();
      $("symbolSearch").focus();
    }
  });
  $("symbolSearch").addEventListener("keydown", e => {
    if (e.key === "Enter") {
      const v = e.target.value.trim().toUpperCase();
      if (v) selectSignal(v);
    }
  });
});

// Expose for inline onclick handlers
window.selectSignal = selectSignal;
window.sendToPaperTrade = sendToPaperTrade;
window.runPipeline = runPipeline;
window.loadAll = loadAll;
window.loadConsensus = loadConsensus;
