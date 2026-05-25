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

  // FRED macro fields embedded in market_context by get_market_status()
  const cpiEl   = $("mbCpi");
  const fedEl   = $("mbFedRate");
  const spdEl   = $("mbSpread");
  const unempEl = $("mbUnemp");
  if (cpiEl && market.cpi_yoy != null) {
    cpiEl.textContent = Number(market.cpi_yoy).toFixed(1) + "%";
    cpiEl.style.color = market.cpi_yoy > 4 ? "var(--negative)" : market.cpi_yoy > 2.5 ? "var(--warning)" : "var(--positive)";
  }
  if (fedEl && market.fed_rate != null) {
    fedEl.textContent = Number(market.fed_rate).toFixed(2) + "%";
  }
  if (spdEl && market.yield_spread != null) {
    const sp = market.yield_spread;
    spdEl.textContent = (sp >= 0 ? "+" : "") + Number(sp).toFixed(2) + "%";
    spdEl.style.color = sp < 0 ? "var(--warning)" : sp < 0.5 ? "var(--accent)" : "var(--positive)";
  }
  if (unempEl && market.unemployment != null) {
    unempEl.textContent = Number(market.unemployment).toFixed(1) + "%";
    unempEl.style.color = market.unemployment > 5 ? "var(--negative)" : "var(--text-primary)";
  }

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

  // Detect divergence between SPY trend and ML regime
  const spyTrend = (market?.spy_regime?.regime ?? market?.spy_regime?.label ?? "").toUpperCase();
  const mlRegime = regime.toUpperCase();
  const diverged = spyTrend && mlRegime !== "UNKNOWN" && spyTrend !== mlRegime;

  $("regimeSub").textContent = `ML · ${regime}${spyTrend ? " · SPY " + spyTrend : ""}`;
  $("regimeBar").innerHTML =
    `<div class="regime-bar" style="height:32px;">
       <div style="background:${bg};color:${color};display:flex;align-items:center;justify-content:center;font-size:10px;font-weight:700;letter-spacing:0.5px;">${regime}</div>
     </div>` +
    (diverged
      ? `<div class="regime-diverge"><i class="fas fa-exclamation-circle"></i>Regime divergence — ML: ${mlRegime} vs SPY: ${spyTrend}. Signals may be lower confidence.</div>`
      : "");
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
  // The redundant top Signals block was removed from /dashboard (Stock
  // Intelligence is the single signal table). No-op if its elements are absent.
  if (!$("signalsFeatured")) return;
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
  if (!$("signalTableBody") || !$("fSignal")) return;  // block removed on /dashboard
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
  openDrawer(symbol);
  $("anSubtitle").textContent = symbol + " · loading…";
  $("analyzePanel").innerHTML = `<div class="card"><div style="text-align:center;padding:20px;color:var(--text-muted);font-size:11px;"><i class="fas fa-circle-notch fa-spin" style="font-size:18px;display:block;margin-bottom:6px;"></i>Loading scoring & trade plan…</div></div>`;

  // Primary: scoring + plan + halal (blocking — needed for the main card)
  const [scoring, plan, halal] = await Promise.all([
    api(`/api/v1/scoring/weighted?symbol=${symbol}`),
    api(`/api/v1/trade/plan?symbol=${symbol}`),
    api(`/api/v1/halal/check?symbol=${symbol}`),
  ]);

  state.selectedAnalyze = { symbol, scoring, plan, halal, esg: null, segments: null, senate: null };
  renderAnalyze();
  renderConsensusEmpty(symbol);   // Heavy endpoint — only load on explicit click

  // Secondary: ESG + revenue segments + senate (non-blocking enrichment)
  Promise.all([
    loadEsgScore(symbol),
    loadRevenueSegments(symbol),
    loadSenateTrades(symbol),
  ]).then(([esg, segments, senate]) => {
    if (state.selectedSymbol !== symbol) return;  // user already navigated away
    state.selectedAnalyze = { ...state.selectedAnalyze, esg, segments, senate };
    renderAnalyze();
  });
}

async function toggleWatch(symbol) {
  const list = state.watchlist || [];
  const on = list.includes(symbol);
  try {
    const res = await api(`/api/v1/watchlist/${on ? "remove" : "add"}/${encodeURIComponent(symbol)}`,
      { method: on ? "DELETE" : "POST" });
    state.watchlist = (res && res.symbols) || (on ? list.filter(s => s !== symbol) : [...list, symbol]);
    toast(on ? `${symbol} removed from watchlist` : `${symbol} added to watchlist`, "ok");
    renderAnalyze();
  } catch (_) {
    toast("Watchlist update failed", "error");
  }
}

function renderAnalyze() {
  const data = state.selectedAnalyze;
  if (!data) {
    $("analyzePanel").innerHTML = `<div class="analyze-empty"><i class="fas fa-search"></i>Select a signal to analyze</div>`;
    return;
  }
  const { symbol, scoring, plan, halal, esg, segments, senate } = data;
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

  // Kelly-aware position sizing (already computed by /api/v1/trade/plan → risk_manager)
  const shares   = plan?.shares ?? plan?.qty ?? null;
  const posValue = plan?.position_value ?? null;
  const riskAmt  = plan?.risk_amount ?? null;
  const portPct  = plan?.portfolio_pct ?? null;
  const rr       = plan?.rr_ratio ?? plan?.strategy_rr ?? null;

  // Watchlist + portfolio risk gate (real fields from /api/risk/status)
  const onWatch = (state.watchlist || []).includes(symbol);
  const rs = state.riskStatus || {};
  const tradingBlocked = rs.market_halt === true;
  const posture = rs.risk_posture ? String(rs.risk_posture).replace("_", "-") : null;
  const gateMsg = tradingBlocked
    ? (rs.market_halt_reason || "Market risk gate halted execution")
    : `Risk gate OK${posture ? " · posture " + posture : ""}${rs.var_95_pct != null ? " · VaR95 " + rs.var_95_pct + "%" : ""}`;

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

  // ESG + revenue segments + senate trading (rendered when data arrives)
  const esgBlock      = renderEsgBlock(esg);
  const segBlock      = renderSegmentsBlock(segments);
  const senateBlock   = renderSenateBlock(senate);

  // Loading placeholder for enrichment sections (shown on first render before secondary loads)
  const enrichLoading = (!esg && !segments && !senate)
    ? `<div style="margin-top:10px;font-size:9px;color:var(--text-muted);text-align:center;padding:8px 0;border-top:1px solid var(--border-light);">
         <i class="fas fa-circle-notch fa-spin" style="margin-right:4px;"></i>Loading ESG · Segments · Senate…
       </div>`
    : "";

  $("analyzePanel").innerHTML = `<div class="card">
    <div class="an-hdr">
      <div>
        <div class="an-sym">${symbol}
          <i class="${onWatch ? 'fas' : 'far'} fa-star" title="${onWatch ? 'Remove from watchlist' : 'Add to watchlist'}"
             onclick="toggleWatch('${symbol}')"
             style="cursor:pointer;font-size:12px;margin-left:8px;color:${onWatch ? 'var(--accent)' : 'var(--text-muted)'};"></i>
        </div>
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

    <div class="an-sect-title">Position sizing · Kelly-aware</div>
    <div class="an-grid">
      <div class="row"><span class="l">Shares</span><span class="v">${shares != null ? shares : "—"}</span></div>
      <div class="row"><span class="l">R : R</span><span class="v ${rr != null && rr >= 2 ? 'txt-green' : ''}">${rr != null ? "1 : " + Number(rr).toFixed(1) : "—"}</span></div>
      <div class="row"><span class="l">Position</span><span class="v">${posValue != null ? "$" + Number(posValue).toLocaleString("en-US",{maximumFractionDigits:0}) : "—"}</span></div>
      <div class="row"><span class="l">% Equity</span><span class="v">${portPct != null ? Number(portPct).toFixed(1) + "%" : "—"}</span></div>
      <div class="row"><span class="l">Risk $</span><span class="v txt-red">${riskAmt != null ? "$" + Number(riskAmt).toLocaleString("en-US",{maximumFractionDigits:0}) : "—"}</span></div>
    </div>

    <div class="an-gate" style="margin-top:10px;padding:7px 10px;border-radius:var(--radius-md);font-size:10px;
         display:flex;align-items:center;gap:7px;
         background:${tradingBlocked ? 'var(--negative-dim,rgba(248,113,113,0.12))' : 'var(--positive-dim,rgba(74,222,128,0.10))'};
         color:${tradingBlocked ? 'var(--negative)' : 'var(--positive)'};
         border:1px solid ${tradingBlocked ? 'var(--negative)' : 'var(--border-default)'};">
      <i class="fas fa-${tradingBlocked ? 'ban' : 'shield-alt'}"></i>${gateMsg}
    </div>

    ${esgBlock}
    ${segBlock}
    ${senateBlock}
    ${enrichLoading}

    <button class="an-trade" onclick="sendToPaperTrade()" ${verdict === "AVOID" || tradingBlocked ? "disabled" : ""} style="margin-top:12px;${tradingBlocked ? 'opacity:0.5;cursor:not-allowed;' : ''}">
      <i class="fas fa-paper-plane"></i> ${tradingBlocked ? "Blocked by risk gate" : "Send to paper trade"}
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

  // Use the REAL Kelly-aware share count from the trade plan; fall back to a
  // ~$1k position only if sizing is unavailable.
  const kellyShares = plan?.shares ?? plan?.qty;
  const shares = (kellyShares && kellyShares > 0) ? kellyShares : Math.max(1, Math.floor(1000 / entry));

  const body = {
    symbol, side: "buy", entry_price: entry,
    stop_loss: stop, take_profit: tp,
    shares,
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

/* ─── Macro Indicators (FRED) ─────────────────────────────────── */
async function loadMacroIndicators() {
  const data = await api("/api/macro/indicators");
  if (!data) return;

  // ── Market Bar items ──────────────────────────────────────────
  const cpiEl    = $("mbCpi");
  const fedEl    = $("mbFedRate");
  const spdEl    = $("mbSpread");
  const unempEl  = $("mbUnemp");

  if (cpiEl) {
    cpiEl.textContent = data.cpi_yoy != null ? Number(data.cpi_yoy).toFixed(1) + "%" : "—";
    cpiEl.style.color = data.cpi_yoy != null && data.cpi_yoy > 4
      ? "var(--negative)" : data.cpi_yoy != null && data.cpi_yoy > 2.5
      ? "var(--warning)" : "var(--positive)";
  }
  if (fedEl) {
    fedEl.textContent = data.fed_rate != null ? Number(data.fed_rate).toFixed(2) + "%" : "—";
    fedEl.style.color = "var(--text-primary)";
  }
  if (spdEl) {
    const spread = data.t10y2y;
    spdEl.textContent = spread != null ? (spread >= 0 ? "+" : "") + Number(spread).toFixed(2) + "%" : "—";
    // Inverted yield curve = warning
    spdEl.style.color = spread != null
      ? (spread < 0 ? "var(--warning)" : spread < 0.5 ? "var(--accent)" : "var(--positive)")
      : "var(--text-primary)";
  }
  if (unempEl) {
    unempEl.textContent = data.unemployment != null ? Number(data.unemployment).toFixed(1) + "%" : "—";
    unempEl.style.color = data.unemployment != null && data.unemployment > 5
      ? "var(--negative)" : "var(--text-primary)";
  }

  // ── Macro Panel (lower row 4th column) ────────────────────────
  renderMacroPanel(data);
}

function renderMacroPanel(data) {
  const el = $("macroPanelBody");
  if (!el) return;

  const noData = !data || Object.values({
    cpi: data.cpi_yoy, fed: data.fed_rate,
    spread: data.t10y2y, unemp: data.unemployment, hy: data.hy_spread
  }).every(v => v == null);

  if (noData) {
    el.innerHTML = `<div style="color:var(--text-muted);font-size:10px;padding:8px 0;">
      Set <code>FRED_API_KEY</code> for live data</div>`;
    return;
  }

  const rows = [
    {
      lab: "CPI YoY", val: data.cpi_yoy,
      fmt: v => Number(v).toFixed(1) + "%",
      color: v => v > 4 ? "var(--negative)" : v > 2.5 ? "var(--warning)" : "var(--positive)",
      hint: "Consumer Price Index, year-over-year",
    },
    {
      lab: "Fed Rate", val: data.fed_rate,
      fmt: v => Number(v).toFixed(2) + "%",
      color: () => "var(--text-primary)",
      hint: "Effective Federal Funds Rate (DFF)",
    },
    {
      lab: "Yield Spread", val: data.t10y2y,
      fmt: v => (v >= 0 ? "+" : "") + Number(v).toFixed(2) + "%",
      color: v => v < 0 ? "var(--warning)" : v < 0.5 ? "var(--accent)" : "var(--positive)",
      hint: "10Y−2Y Treasury spread (negative = inversion)",
      badge: data.t10y2y != null && data.t10y2y < 0 ? "INVERTED" : null,
    },
    {
      lab: "Unemployment", val: data.unemployment,
      fmt: v => Number(v).toFixed(1) + "%",
      color: v => v > 5 ? "var(--negative)" : "var(--positive)",
      hint: "US Unemployment Rate (UNRATE)",
    },
    {
      lab: "HY Spread", val: data.hy_spread,
      fmt: v => Number(v).toFixed(2) + "%",
      color: v => v > 5 ? "var(--negative)" : v > 3.5 ? "var(--warning)" : "var(--positive)",
      hint: "ICE BofA US High Yield Credit Spread",
    },
  ];

  el.innerHTML = rows.map(r => {
    if (r.val == null) return "";
    const color = r.color(r.val);
    const badge = r.badge
      ? `<span style="margin-left:4px;padding:1px 5px;border-radius:4px;font-size:8px;font-weight:700;background:var(--warning-dim,#fbbf2422);color:var(--warning,#fbbf24);border:1px solid var(--warning-dim,#fbbf2444);">${r.badge}</span>`
      : "";
    return `<div class="wf-row" title="${r.hint}" style="cursor:default;">
      <span class="wf-label">${r.lab}</span>
      <span class="wf-value" style="color:${color};font-family:var(--mono);">${r.fmt(r.val)}${badge}</span>
    </div>`;
  }).join("");

  // Source caption
  el.innerHTML += `<div style="font-size:8px;color:var(--text-muted);padding-top:6px;border-top:1px solid var(--border-light);margin-top:4px;">
    Source: FRED / Federal Reserve  ·  ${data.source || "FRED"}
  </div>`;
}

/* ─── ESG Score ───────────────────────────────────────────────── */
async function loadEsgScore(symbol) {
  const data = await api(`/api/stock/esg?symbol=${symbol}`);
  return data && !data.error ? data : null;
}

function renderEsgBlock(esg) {
  if (!esg) return "";
  const total = esg.total ?? null;
  const envir = esg.environmental ?? null;
  const soc   = esg.social ?? null;
  const gov   = esg.governance ?? null;
  const cont  = esg.controversy ?? null;

  if (total == null && envir == null) return "";

  const scoreBar = (label, val, max = 100) => {
    if (val == null) return "";
    const pct = Math.min((val / max) * 100, 100);
    return `<div style="display:flex;align-items:center;gap:6px;margin-bottom:3px;">
      <span style="font-size:9px;color:var(--text-muted);width:72px;flex-shrink:0;">${label}</span>
      <div style="flex:1;height:4px;background:var(--bg-primary);border-radius:2px;">
        <div style="width:${pct}%;height:100%;background:var(--accent);border-radius:2px;"></div>
      </div>
      <span style="font-size:10px;font-family:var(--mono);color:var(--accent);width:30px;text-align:right;">${Number(val).toFixed(0)}</span>
    </div>`;
  };

  const contColor = cont == null ? "var(--text-muted)"
    : cont <= 1 ? "var(--positive)" : cont <= 3 ? "var(--warning)" : "var(--negative)";
  const contLabel = cont == null ? "—" : ["None","Low","Moderate","High","Very High","Extreme"][Math.min(cont, 5)] ?? cont;

  return `<div class="an-sect-title" style="margin-top:12px;">ESG Score <span style="font-size:8px;color:var(--text-muted);font-weight:400;"> · yfinance</span></div>
    ${total != null ? `<div style="font-size:22px;font-family:var(--mono);font-weight:700;color:var(--accent);margin-bottom:6px;">${Number(total).toFixed(0)}<span style="font-size:11px;color:var(--text-muted);font-weight:400;">/100</span></div>` : ""}
    ${scoreBar("Environment", envir)}
    ${scoreBar("Social", soc)}
    ${scoreBar("Governance", gov)}
    <div style="display:flex;align-items:center;gap:6px;margin-top:4px;">
      <span style="font-size:9px;color:var(--text-muted);">Controversy</span>
      <span style="font-size:10px;font-weight:600;color:${contColor};">${contLabel}</span>
    </div>`;
}

/* ─── Revenue Segments ────────────────────────────────────────── */
async function loadRevenueSegments(symbol) {
  const data = await api(`/api/stock/revenue-segments?symbol=${symbol}`);
  return data?.segments?.length ? data.segments : null;
}

function renderSegmentsBlock(segments) {
  if (!segments || !segments.length) return "";
  const total = segments.reduce((s, r) => s + Math.abs(Number(r.value || 0)), 0) || 1;
  const rows = segments.slice(0, 8).map(r => {
    const val = Number(r.value || 0);
    const pct = (Math.abs(val) / total) * 100;
    const isRisky = pct > 5 && /alcohol|gambling|tobacco|pork|weapon|adult|cannabis|interest|bank|insur/i.test(r.name);
    const barColor = isRisky ? "var(--negative)" : "var(--accent)";
    return `<div style="display:flex;align-items:center;gap:5px;margin-bottom:3px;" title="${isRisky ? 'Review for halal compliance' : ''}">
      <span style="font-size:9px;color:var(--text-secondary);flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${r.name}</span>
      <div style="width:60px;height:4px;background:var(--bg-primary);border-radius:2px;flex-shrink:0;">
        <div style="width:${Math.min(pct, 100)}%;height:100%;background:${barColor};border-radius:2px;"></div>
      </div>
      <span style="font-size:10px;font-family:var(--mono);color:${barColor};width:36px;text-align:right;flex-shrink:0;">${pct.toFixed(1)}%</span>
      ${isRisky ? `<span style="font-size:8px;color:var(--warning);" title="Review for halal">⚠</span>` : ""}
    </div>`;
  });

  return `<div class="an-sect-title" style="margin-top:12px;">Revenue Segments <span style="font-size:8px;color:var(--text-muted);font-weight:400;"> · FMP · AAOIFI 5% rule</span></div>
    ${rows.join("")}`;
}

/* ─── Senate Trading ──────────────────────────────────────────── */
async function loadSenateTrades(symbol) {
  const data = await api(`/api/stock/senate-trading?symbol=${symbol}`);
  return data?.trades?.length ? data.trades : null;
}

function renderSenateBlock(trades) {
  if (!trades || !trades.length) return "";
  const rows = trades.slice(0, 5).map(t => {
    const side = (t.type || t.transaction_type || "").toUpperCase();
    const isBuy  = /BUY|PURCHASE/.test(side);
    const isSell = /SELL|SALE/.test(side);
    const color  = isBuy ? "var(--positive)" : isSell ? "var(--negative)" : "var(--text-secondary)";
    const date   = t.transaction_date || t.date || "";
    const name   = (t.first_name || "") + " " + (t.last_name || t.senator || "");
    const amt    = t.amount_range || t.amount || "—";
    return `<div style="display:flex;align-items:center;gap:5px;padding:4px 0;border-bottom:1px solid var(--border-light);">
      <span style="font-size:9px;color:var(--text-muted);width:56px;flex-shrink:0;font-family:var(--mono);">${date.slice(0, 10)}</span>
      <span style="font-size:9px;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${name.trim()}</span>
      <span style="font-size:9px;font-weight:700;color:${color};width:32px;text-align:center;flex-shrink:0;">${isBuy ? "BUY" : isSell ? "SELL" : side || "—"}</span>
      <span style="font-size:9px;color:var(--text-secondary);font-family:var(--mono);">${amt}</span>
    </div>`;
  });

  return `<div class="an-sect-title" style="margin-top:12px;">Senate Trading <span style="font-size:8px;color:var(--text-muted);font-weight:400;"> · STOCK Act · FMP</span></div>
    ${rows.join("")}`;
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
  loadMacroIndicators();
  api("/api/v1/sectors/performance").then(s => {
    state.sectors = s;
    renderSectors(Array.isArray(s) ? s : s?.sectors || []);
  });
  api("/api/v1/guards/summary").then(g => {
    state.guards = g;
    renderGuards(g);
    renderAlertBanner(ov?.system, g);
  });
  loadPaper();
  loadModels();
  loadEquityCurve();
  loadDeepPicks();
  loadAccuracy();
  // Watchlist + portfolio risk gate (for the decision drawer)
  api("/api/v1/watchlist").then(w => { state.watchlist = (w && w.symbols) || []; });
  api("/api/risk/status").then(r => { state.riskStatus = r || {}; });
}

/* ─── Track record — REAL signal accuracy (credibility layer) ──── */
async function loadAccuracy() {
  const el = $("intelTrackRecord");
  if (!el) return;
  // /signals/accuracy returns a list; first row Source==="OVERALL".
  const rows = await api("/signals/accuracy?period=30");
  const overall = Array.isArray(rows) ? rows.find(r => r && r.Source === "OVERALL") : null;

  if (!overall) {
    // Honest empty state — signals need ~5 days to mature.
    el.style.display = "flex";
    el.innerHTML = `<span style="color:var(--text-muted)">
      <i class="fas fa-clock" style="margin-right:6px;"></i>
      Track record building — evaluated signals appear after they mature (~5d). No fabricated stats.
    </span>`;
    return;
  }

  const wr   = overall["Win Rate %"];
  const avg  = overall["Avg Return %"];
  const pf   = overall["Profit Factor"];
  const tot  = overall["Total Signals"];
  const wrColor  = wr >= 55 ? "var(--positive)" : wr >= 45 ? "var(--warning)" : "var(--negative)";
  const avgColor = avg >= 0 ? "var(--positive)" : "var(--negative)";
  const cell = (label, val, color) =>
    `<span><span style="color:var(--text-muted)">${label}</span>
       <strong style="color:${color || 'var(--text-primary)'};margin-left:5px;">${val}</strong></span>`;

  el.style.display = "flex";
  el.innerHTML =
    `<span style="color:var(--accent);font-weight:700;letter-spacing:0.5px;">TRACK RECORD · 30d</span>` +
    cell("Win rate", (wr ?? 0) + "%", wrColor) +
    cell("Avg return", ((avg ?? 0) >= 0 ? "+" : "") + (avg ?? 0) + "%", avgColor) +
    cell("Profit factor", pf ?? "—") +
    cell("Evaluated", tot ?? 0) +
    `<span style="margin-left:auto;color:var(--text-muted);font-size:9px;">real outcomes · matured signals only</span>`;
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

/* ─── Alert Banner ────────────────────────────────────────────── */
function renderAlertBanner(system, guards) {
  const banner = $("alertBanner");
  if (!banner) return;

  const killSwitch = system?.kill_switch === true || system?.killswitch === true;
  const drawdown = system?.max_drawdown_pct ?? system?.drawdown ?? null;
  const drawdownThreshold = 10; // show warning above 10%

  let severity = null;
  let msg = "";
  let actionLabel = "";

  if (killSwitch) {
    severity = "critical";
    msg = "Kill switch is active — all automated trading halted.";
    actionLabel = "Details";
  } else if (drawdown != null && Math.abs(drawdown) >= drawdownThreshold) {
    severity = "warning";
    msg = `Max drawdown at ${Math.abs(drawdown).toFixed(1)}% — review risk settings.`;
    actionLabel = "Review";
  } else if (guards?.active_blocks > 0) {
    severity = "warning";
    msg = `${guards.active_blocks} guard block${guards.active_blocks > 1 ? "s" : ""} active — some signals suppressed.`;
    actionLabel = "View";
  }

  if (!severity) {
    banner.className = "alert-banner";
    document.querySelector(".main")?.classList.remove("has-alert");
    return;
  }

  $("alertMsg").textContent = msg;
  const actionBtn = $("alertActionBtn");
  if (actionBtn) {
    actionBtn.textContent = actionLabel;
    actionBtn.style.display = actionLabel ? "" : "none";
  }
  banner.className = `alert-banner visible ${severity}`;
  document.querySelector(".main")?.classList.add("has-alert");
}

function dismissAlert() {
  const banner = $("alertBanner");
  if (banner) banner.className = "alert-banner";
  document.querySelector(".main")?.classList.remove("has-alert");
}

function handleAlertAction() {
  const system = state.overview?.system;
  if (system?.kill_switch || system?.killswitch) {
    toast("Kill switch is ON — disable from the Risk Desk.", "error");
  } else {
    // Scroll to guards section
    const el = $("guards");
    if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
  }
}

/* ─── Analyze Drawer ──────────────────────────────────────────── */
function openDrawer(symbol) {
  const drawer = $("analyzeDrawer");
  if (drawer) drawer.classList.add("open");
}

function closeDrawer() {
  const drawer = $("analyzeDrawer");
  if (drawer) drawer.classList.remove("open");
  state.selectedSymbol = null;
  renderSignals();
}

/* ─── Equity Curve ────────────────────────────────────────────── */
async function loadEquityCurve() {
  const data = await api("/api/dashboard/equity-curve");
  if (!data?.equity_curve?.length) return;

  const curve = data.equity_curve;
  const last = curve[curve.length - 1];
  const first = curve[0];
  const currentBalance = last?.balance ?? 0;
  const startBalance = first?.balance ?? currentBalance;
  const pnlPct = startBalance > 0 ? ((currentBalance - startBalance) / startBalance) * 100 : 0;

  const wrap = $("equityChartWrap");
  const valEl = $("equityVal");
  const pnlEl = $("equityPnl");
  const svgEl = $("equityChartSvg");
  if (!wrap || !valEl || !pnlEl || !svgEl) return;

  valEl.textContent = fmt$(currentBalance, 0);
  pnlEl.textContent = fmtPct(pnlPct);
  pnlEl.style.color = pnlPct >= 0 ? "var(--positive)" : "var(--negative)";

  const balances = curve.map(p => p.balance ?? 0);
  svgEl.innerHTML = areaChartSvg(balances, pnlPct >= 0 ? "var(--positive)" : "var(--negative)");
  wrap.style.display = "";
}

function areaChartSvg(values, color) {
  if (!values || values.length < 2) return "";
  const W = 300, H = 52, pad = 2;
  const min = Math.min(...values), max = Math.max(...values);
  const range = max - min || 1;
  const step = (W - pad * 2) / (values.length - 1);

  const pts = values.map((v, i) => {
    const x = (pad + i * step).toFixed(1);
    const y = (pad + ((max - v) / range) * (H - pad * 2)).toFixed(1);
    return `${x},${y}`;
  });

  const linePath = "M" + pts.join(" L");
  const areaPath = `${linePath} L${pts[pts.length - 1].split(",")[0]},${H} L${pad},${H} Z`;

  return `<path d="${areaPath}" fill="${color}" fill-opacity="0.12"/>
          <path d="${linePath}" fill="none" stroke="${color}" stroke-width="1.5" stroke-linejoin="round"/>`;
}

/* ─── Stock Intelligence ──────────────────────────────────────── */
var _intelData = null;
var _intelFilter = "all";
var _intelSort = { key: "composite_score", dir: -1 };  // default: score desc

async function loadDeepPicks(forceRefresh = false) {
  const tbody = $("intelTableBody");
  if (!tbody) return;

  tbody.innerHTML = `<tr><td colspan="14" class="intel-loading">
    <i class="fas fa-circle-notch"></i><br>Running intelligence scan…
  </td></tr>`;

  const useCache = forceRefresh ? "false" : "true";
  const data = await api(`/api/screener/deep-picks?limit=15&use_cache=${useCache}`);

  if (!data || data.status === "scanning") {
    tbody.innerHTML = `<tr><td colspan="14" class="intel-empty">
      <i class="fas fa-satellite-dish"></i><br>
      Smart screener is initializing. Trigger a scan from
      <a href="/halal-screener" style="color:var(--accent)">Halal Screener</a>
      or click <b>Scan</b>.
    </td></tr>`;
    // Auto-trigger screener in background
    api("/api/stock/smart-screener?use_cache=false&max_results=50");
    return;
  }

  _intelData = data;
  _renderIntelTable(data);

  // Meta line
  const regime = data.regime || "—";
  const regimeColor = regime === "BULL" ? "var(--positive)" : regime === "BEAR" ? "var(--negative)" : "var(--warning)";
  const intelMeta = $("intelMeta");
  if (intelMeta) {
    intelMeta.textContent = `${data.total} picks · ${data.scanned || 0} scanned`;
  }
  const regimeBadge = $("intelRegimeBadge");
  if (regimeBadge) {
    regimeBadge.innerHTML = `<span style="padding:2px 7px;border-radius:10px;font-size:9px;font-weight:700;
      background:${regimeColor}22;color:${regimeColor};border:1px solid ${regimeColor}44;">
      ${regime}
    </span>`;
  }
  const scanMeta = $("intelScanMeta");
  if (scanMeta && data.screener_ts) {
    const ts = new Date(data.screener_ts);
    const ageMin = Math.max(0, Math.round((Date.now() - ts.getTime()) / 60000));
    // Fresh < 15m (green), stale < 60m (amber), old ≥ 60m (red)
    const color = ageMin < 15 ? "var(--positive)" : ageMin < 60 ? "var(--warning)" : "var(--negative)";
    const ageStr = ageMin < 1 ? "just now" : ageMin < 60 ? `${ageMin}m ago` : `${Math.round(ageMin / 60)}h ago`;
    const hhMM = ts.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" });
    scanMeta.innerHTML =
      `<span style="display:inline-flex;align-items:center;gap:5px;">
         <span style="width:6px;height:6px;border-radius:50%;background:${color};"></span>
         <span style="color:${color};">${ageStr}</span>
         <span style="color:var(--text-muted);">· ${hhMM}</span>
       </span>`;
  }
}

function _renderIntelTable(data) {
  const tbody = $("intelTableBody");
  if (!tbody || !data?.results) return;

  let rows = [...data.results];

  // Apply filter
  if (_intelFilter === "strong")  rows = rows.filter(r => r.signal_composite === "STRONG BUY");
  if (_intelFilter === "buy")     rows = rows.filter(r => ["STRONG BUY","BUY"].includes(r.signal_composite));
  if (_intelFilter === "bullish") rows = rows.filter(r => r.sentiment_label === "bullish");
  if (_intelFilter === "upside")  rows = rows.filter(r => (r.analyst_upside || 0) > 10);

  // Apply sort (stable, nulls last)
  const sk = _intelSort.key, sd = _intelSort.dir;
  rows.sort((a, b) => {
    let av = a[sk], bv = b[sk];
    if (sk === "symbol") { av = av || ""; bv = bv || ""; return sd * String(av).localeCompare(String(bv)); }
    av = (av == null ? -Infinity : Number(av));
    bv = (bv == null ? -Infinity : Number(bv));
    return sd * (av - bv);
  });
  // Reflect active sort in the header carets
  document.querySelectorAll(".sort-caret").forEach(c => {
    c.textContent = (c.getAttribute("data-k") === sk) ? (sd < 0 ? "▾" : "▴") : "";
  });

  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="14" class="intel-empty">
      <i class="fas fa-filter"></i><br>No results for this filter.
    </td></tr>`;
    return;
  }

  tbody.innerHTML = rows.map((r, i) => {
    const rank = i + 1;
    const rankClass = rank === 1 ? "r1" : rank === 2 ? "r2" : rank === 3 ? "r3" : "";
    const composite = r.composite_score ?? 0;
    const gaugeColor = composite >= 70 ? "var(--positive)" : composite >= 50 ? "var(--accent)" : composite >= 35 ? "var(--warning)" : "var(--negative)";

    // Sub-score bars (T/F/S/AI)
    const subBars = [
      { v: r.score_tech      ?? 0,  max: 30, c: "#60a5fa", l: "T" },
      { v: r.score_fund      ?? 0,  max: 25, c: "#c8963e", l: "F" },
      { v: r.score_sentiment,       max: 20, c: "#a78bfa", l: "S" },  // null when no real data
      { v: r.score_ai        ?? 0,  max: 15, c: "#34d399", l: "AI" },
    ].map(s => {
      const na = s.v == null;
      const pct = na ? 0 : Math.round(s.v / s.max * 100);
      return `<div class="intel-sub" title="${s.l}: ${na ? 'n/a — no data' : s.v + '/' + s.max}">
        <div class="intel-sub-bar-wrap">
          <div class="intel-sub-bar" style="height:${pct}%;background:${na ? 'var(--text-muted)' : s.c};opacity:${na ? 0.25 : 1};"></div>
        </div>
        <span class="intel-sub-lbl">${s.l}</span>
      </div>`;
    }).join("");

    const sentNA = r.sentiment_available === false || r.sentiment_label === "n/a";
    const sent = sentNA ? "neutral" : (r.sentiment_label || "neutral");
    const sentDisplay = sentNA ? "n/a" : sent;
    const sentIcon = sentNA ? "" : sent === "bullish" ? "▲" : sent === "bearish" ? "▼" : "●";
    const upside = r.analyst_upside;
    const upsideClass = (upside != null && upside > 10) ? "upside-pos" : (upside != null && upside < -5) ? "upside-neg" : "upside-neu";
    const upsideStr = (upside != null && upside !== 0) ? `${upside > 0 ? "+" : ""}${upside.toFixed(1)}%` : "—";
    const rating = r.analyst_rating ? r.analyst_rating.replace(/_/g, " ").toUpperCase() : "—";

    const sig = (r.signal_composite || "WATCH").replace(/ /g, "_");
    const chgPct = r.change_pct ?? 0;
    const chgClass = chgPct >= 0 ? "pos" : "neg";

    return `<tr onclick="selectSignal('${r.symbol}')" title="Click to analyze ${r.symbol}">
      <td><span class="intel-rank ${rankClass}">${rank}</span></td>
      <td>
        <div class="intel-sym">${r.symbol}</div>
        <div class="intel-co">${r.company || ""}</div>
      </td>
      <td>
        <div class="intel-gauge-wrap">
          <span class="intel-gauge-val" style="color:${gaugeColor}">${composite}</span>
          <div class="intel-gauge-bar">
            <div class="intel-gauge-fill" style="width:${composite}%;background:${gaugeColor};"></div>
          </div>
        </div>
      </td>
      <td><span style="font-family:var(--mono);font-size:11px;color:#60a5fa">${r.score_tech ?? 0}</span></td>
      <td><span style="font-family:var(--mono);font-size:11px;color:#c8963e">${r.score_fund ?? 0}</span></td>
      <td><span style="font-family:var(--mono);font-size:11px;color:${r.score_sentiment == null ? 'var(--text-muted)' : '#a78bfa'}">${r.score_sentiment ?? "—"}</span></td>
      <td><span style="font-family:var(--mono);font-size:11px;color:#34d399">${r.score_ai ?? 0}</span></td>
      <td><span class="f-grade ${r.f_grade || "D"}">${r.f_grade || "D"}</span></td>
      <td><span class="sent-badge ${sentNA ? 'neutral' : sent}" style="${sentNA ? 'opacity:0.5' : ''}">${sentIcon} ${sentDisplay}</span></td>
      <td>
        <div style="line-height:1.3">
          <span class="${upsideClass}" style="font-size:11px">${upsideStr}</span>
          <div style="font-size:9px;color:var(--text-muted)">${rating}</div>
        </div>
      </td>
      <td>
        <span style="font-family:var(--mono);font-size:11px">$${(r.price ?? 0).toFixed(2)}</span>
      </td>
      <td>
        <span class="${chgClass}" style="font-size:11px">
          ${chgPct >= 0 ? "+" : ""}${chgPct.toFixed(2)}%
        </span>
      </td>
      <td><span class="sig-badge ${sig}">${(r.signal_composite || "WATCH").replace("STRONG BUY","SB")}</span></td>
      <td style="color:var(--text-muted);font-size:10px">${r.sector || "—"}</td>
    </tr>`;
  }).join("");
}

function setIntelFilter(f, btn) {
  _intelFilter = f;
  document.querySelectorAll(".intel-filter-btn").forEach(b => b.classList.remove("active"));
  if (btn) btn.classList.add("active");
  if (_intelData) _renderIntelTable(_intelData);
}

function setIntelSort(key) {
  // Toggle direction if same column; new column defaults to descending
  // (ascending for the alphabetic Symbol column).
  if (_intelSort.key === key) _intelSort.dir *= -1;
  else _intelSort = { key, dir: key === "symbol" ? 1 : -1 };
  if (_intelData) _renderIntelTable(_intelData);
}

function refreshDeepPicks() {
  loadDeepPicks(true);
}

// Expose for inline onclick handlers
window.selectSignal = selectSignal;
window.sendToPaperTrade = sendToPaperTrade;
window.runPipeline = runPipeline;
window.loadAll = loadAll;
window.loadConsensus = loadConsensus;
window.closeDrawer = closeDrawer;
window.dismissAlert = dismissAlert;
window.handleAlertAction = handleAlertAction;
window.setIntelFilter = setIntelFilter;
window.setIntelSort = setIntelSort;
window.refreshDeepPicks = refreshDeepPicks;
window.toggleWatch = toggleWatch;
