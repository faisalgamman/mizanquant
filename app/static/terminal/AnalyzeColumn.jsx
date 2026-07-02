// AnalyzeColumn.jsx — Column 2: the "Decision Card" for the selected signal.
//
// Top half = a fast BUY decision surface: a conviction gauge, a verdict banner, an
// 8-tile SIGNAL MATRIX (every layer the program computes — technical, fundamentals,
// halal, insider, analysts, earnings, market regime, forecast), and a why-buy / watch
// flag split. Bottom half keeps the full detail (technical bars, forecast fan, backtest).
//
// Every number comes from the live APIs (passed in via the `analyze` prop):
//   score breakdown → GET /api/v1/scoring/weighted   (.total, .components)
//   trade plan       → GET /api/v1/trade/plan         (entry/stop/tp/shares/rr +
//                       earnings/analyst/insider/market/fundamentals external signals)
// There are NO synthesized sub-scores and NO mock trade plan. Anything the API does not
// provide renders as "—" rather than an invented value.

const _AN_MAX = { rs: 25, trend: 15, regime: 15, macd: 15, volume: 10, rsi: 8, adx: 7, bb: 5, vwap: 5, gap: 4 };
const _AN_LAB = { rs: "RS vs SPY", trend: "Trend", regime: "Regime", macd: "MACD", volume: "Volume", rsi: "RSI", adx: "ADX", bb: "Bollinger", vwap: "VWAP", gap: "Gap" };
const _AN_TOOLTIP = {
  rs: "Relative strength vs SPY — higher = outperforming", trend: "Trend (above/below the key moving averages)",
  regime: "Broad market state (up / neutral / down)", macd: "Recent MACD cross — momentum signal",
  volume: "Volume confirmation — rising volume backs the move", rsi: "RSI — overbought / oversold",
  adx: "ADX trend strength — above 20 = strong trend", bb: "Bollinger Bands — price position in the band",
  vwap: "VWAP — volume-weighted support/resistance", gap: "Price gap from the prior close"
};
const _anFx = (n, p = 2) => (n == null || isNaN(Number(n))) ? "—" : "$" + Number(n).toFixed(p);
const _anStatusColor = (s) => s === "good" ? "var(--positive)" : s === "warn" ? "var(--amber, #d9a441)" : s === "bad" ? "var(--negative)" : "var(--text-muted)";

// Probabilistic forecast fan chart: P5–P95 + P25–P75 ribbons + median line, with a
// dashed reference line at the current price. Pure function returning an <svg>.
function _forecastFanSvg(fc) {
  const bands = (fc && fc.bands) || [];
  if (bands.length < 2) return null;
  const W = 100, H = 60, padT = 3, padB = 3;
  const dmax = Math.max(...bands.map(b => b.day)) || 1;
  const lo = Math.min(...bands.map(b => b.p5), fc.current_price);
  const hi = Math.max(...bands.map(b => b.p95), fc.current_price);
  const span = (hi - lo) || 1;
  const X = d => (d / dmax) * W;
  const Y = p => padT + (1 - (p - lo) / span) * (H - padT - padB);
  const ribbon = (loKey, hiKey) =>
    bands.map(b => `${X(b.day).toFixed(1)},${Y(b[hiKey]).toFixed(1)}`)
      .concat(bands.slice().reverse().map(b => `${X(b.day).toFixed(1)},${Y(b[loKey]).toFixed(1)}`))
      .join(" ");
  const median = bands.map((b, i) => `${i ? "L" : "M"}${X(b.day).toFixed(1)},${Y(b.median).toFixed(1)}`).join(" ");
  const cy = Y(fc.current_price).toFixed(1);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" style={{ width: "100%", height: 60 }}>
      <polygon points={ribbon("p5", "p95")} fill="var(--accent)" opacity="0.10" />
      <polygon points={ribbon("p25", "p75")} fill="var(--accent)" opacity="0.22" />
      <line x1="0" y1={cy} x2={W} y2={cy} stroke="var(--text-muted)" strokeWidth="0.5" strokeDasharray="2,2" />
      <path d={median} fill="none" stroke="var(--accent)" strokeWidth="1.4" />
    </svg>
  );
}

// P5-P95 range bar mini-visual under forecast numbers
function _rangeBar(p5, p50, p95, currentPrice) {
  const lo = Math.min(p5, currentPrice);
  const hi = Math.max(p95, currentPrice);
  const span = (hi - lo) || 1;
  const pos = v => Math.max(0, Math.min(100, ((v - lo) / span) * 100));
  const p50Pct = pos(p50);
  const currPct = pos(currentPrice);
  return (
    <div className="an-range-bar">
      <div className="an-range-bg">
        <div className="an-range-p5p95" style={{ left: pos(p5) + "%", width: (pos(p95) - pos(p5)) + "%" }} />
        <div className="an-range-median" style={{ left: p50Pct + "%", position: "absolute", width: 1, height: "100%", background: "var(--accent)" }} />
        <div className="an-range-current" style={{ left: currPct + "%", position: "absolute", width: 4, height: 4, borderRadius: 2, background: "var(--text)", top: "50%", marginTop: -2 }} />
      </div>
    </div>
  );
}

// Conviction gauge — a donut whose arc length + color encode the headline score.
function _convictionGauge(score) {
  const pct = Math.max(0, Math.min(100, Number(score) || 0));
  const color = pct >= 65 ? "var(--positive)" : pct >= 45 ? "var(--amber, #d9a441)" : "var(--negative)";
  const R = 30, C = 2 * Math.PI * R;
  const dash = (pct / 100) * C;
  return (
    <svg width="76" height="76" viewBox="0 0 76 76" style={{ flexShrink: 0 }}>
      <circle cx="38" cy="38" r={R} fill="none" stroke="var(--border)" strokeWidth="7" />
      <circle cx="38" cy="38" r={R} fill="none" stroke={color} strokeWidth="7" strokeLinecap="round"
              strokeDasharray={`${dash} ${C}`} transform="rotate(-90 38 38)" />
      <text x="38" y="37" textAnchor="middle" fontSize="20" fontWeight="700" fill="var(--text)">{Math.round(pct)}</text>
      <text x="38" y="50" textAnchor="middle" fontSize="7.5" fill="var(--text-muted)">CONVICTION</text>
    </svg>
  );
}

// Market-context rows (SPY/VIX/credit/breadth/liquidity/sectors) with traffic-light
// verdicts — the broad backdrop, shown right inside the Decision Card.
function _marketRows(market, sectors, planMarket) {
  const m = market || {};
  const amber = "var(--amber, #d9a441)", green = "var(--positive)", red = "var(--negative)", muted = "var(--text-muted)";
  const n2 = (v, d = 2) => (v == null || isNaN(Number(v))) ? "—" : Number(v).toFixed(d);
  // SPY regime: use the SAME SPY-vs-EMA21 measure as the "Market regime" tile (and the USX
  // panel) so the card never contradicts itself. Fall back to the context regime if absent.
  const pm = planMarket || {};
  const spyKnown = pm.known === true;
  const spyBear = spyKnown ? !!pm.spy_bearish : (m.spy_regime === "BEAR");
  const spyPx = pm.spy_price != null ? pm.spy_price : m.spy_price;
  const rows = [
    { lab: "SPY", val: n2(spyPx),
      verdict: spyKnown ? (spyBear ? "BEAR" : "BULL") : (m.spy_regime || "—"),
      color: spyKnown ? (spyBear ? red : green)
           : (m.spy_regime === "BULL" ? green : m.spy_regime === "BEAR" ? red : m.spy_regime === "NEUTRAL" ? amber : muted) },
    { lab: "VIX", val: n2(m.vix),
      verdict: m.vix == null ? "—" : m.vix < 20 ? "NORMAL" : m.vix < 30 ? "ELEVATED" : "STRESS",
      color: m.vix == null ? muted : m.vix < 20 ? green : m.vix < 30 ? amber : red },
    { lab: "VIX %ile", val: m.vix_pctile == null ? "—" : Math.round(m.vix_pctile) + "%",
      verdict: m.vix_pctile == null ? "—" : m.vix_pctile < 80 ? "OK" : "HIGH",
      color: m.vix_pctile == null ? muted : m.vix_pctile < 80 ? green : amber },
    { lab: "HY/IG", val: n2(m.credit, 3),
      verdict: m.credit == null ? "—" : m.credit >= 0.80 ? "OK" : "STRESS",
      color: m.credit == null ? muted : m.credit >= 0.80 ? green : red },
    { lab: "Breadth", val: m.breadth == null ? "—" : n2(m.breadth, 1) + "%",
      verdict: m.breadth == null ? "—" : m.breadth >= 50 ? "OK" : "WEAK",
      color: m.breadth == null ? muted : m.breadth >= 50 ? green : red },
    { lab: "Liquidity (vol)", val: m.liquidity == null ? "—" : Math.round(m.liquidity) + "%",
      verdict: m.liquidity == null ? "—" : "OK", color: m.liquidity == null ? muted : green },
    // Gold as a macro safe-haven / inflation signal: uptrend = haven bid (caution),
    // downtrend = risk appetite. Not a tradable pick — backdrop only.
    { lab: "Gold (GLD)", val: m.gold_price == null ? "—" : "$" + n2(m.gold_price),
      verdict: (m.gold_trend == null || m.gold_trend === "unknown") ? "—"
             : m.gold_signal === "haven_bid" ? "HAVEN" : m.gold_signal === "risk_on" ? "RISK-ON" : "MIXED",
      color: (m.gold_trend == null || m.gold_trend === "unknown") ? muted
           : m.gold_signal === "haven_bid" ? amber : m.gold_signal === "risk_on" ? green : muted },
  ];
  if (sectors && sectors.length) {
    const up = sectors.filter(s => Number(s.chg) > 0).length, tot = sectors.length;
    const r = up / tot;
    rows.push({ lab: "Sectors", val: up + "/" + tot,
      verdict: r >= 0.7 ? "OK+" : r >= 0.5 ? "MIXED+" : "WEAK",
      color: r >= 0.7 ? green : r >= 0.5 ? amber : red });
  }
  return rows;
}

function AnalyzeColumn({ signal, analyze, forecast, horizon, onHorizon, onTrade, brokerHealth, backtest, onBacktest, market, sectors }) {
  if (!signal) {
    return (
      <div className="col col-analyze">
        <div className="wf-section">
          <div className="wf-head"><span className="wf-title">Analyze</span><span className="wf-sub">Selected signal</span></div>
          <div className="analyze-empty"><i className="fas fa-search"></i>Select a signal to analyze</div>
        </div>
      </div>
    );
  }

  const ready   = analyze && analyze.symbol === signal.symbol && !analyze.loading;
  const loading = !ready && (!analyze || analyze.loading || analyze.symbol === signal.symbol);
  const scoring = ready ? analyze.scoring : null;
  const plan    = ready ? analyze.plan : null;
  const scErr   = scoring && scoring.error;

  // HEADLINE = the SAME score + verdict as the scanner row the user clicked.
  const score    = Math.round(Number(signal.score) || (scoring && (scoring.smart_score ?? scoring.total)) || 0);
  const verdict  = signal.verdict || verdictFromScore(score);
  const chgColor = signal.chg >= 0 ? "var(--positive)" : "var(--negative)";
  const mom = signal.momentum_details || {};

  // Real per-factor score components (points). Show points/max.
  const comps = (scoring && scoring.components) || {};
  const compRows = Object.keys(comps).filter(k => _AN_MAX[k]).map(k => {
    const v = Number(comps[k]) || 0;
    const max = _AN_MAX[k];
    const pct = Math.max(0, Math.min(100, (v / max) * 100));
    const rawVal = mom[k + "_raw"] ?? mom[k] ?? null;
    return { lab: _AN_LAB[k] || k, k, v, max, pct, rawVal };
  });
  const techSum = compRows.reduce((a, c) => a + (Number(c.v) || 0), 0);
  const techMax = compRows.reduce((a, c) => a + (Number(c.max) || 0), 0);
  const techStrong = techMax > 0 && techSum / techMax >= 0.65;
  const verdictBuy = /(?:BUY|STRONG)/i.test(verdict);
  const techNote = (compRows.length && scoring && !scErr)
    ? (techStrong && !verdictBuy
        ? "Technically strong, but fundamentals/regime hold the verdict back"
        : (!techStrong && verdictBuy
            ? "Verdict is BUY on fundamentals despite a softer technical read"
            : ""))
    : "";

  // Real trade plan (strategy plan preferred, base ATR plan as fallback).
  const entry    = plan ? (plan.strategy_entry ?? plan.entry_price ?? plan.entry ?? signal.price) : null;
  const stop     = plan ? (plan.strategy_stop  ?? plan.stop_loss   ?? plan.stop) : null;
  const tp1      = plan ? (plan.strategy_tp1   ?? plan.take_profit ?? plan.tp1) : null;
  const tp2      = plan ? (plan.strategy_tp2   ?? plan.tp2) : null;
  const tp3      = plan ? (plan.strategy_tp3   ?? plan.tp3) : null;
  const shares   = plan ? (plan.shares ?? plan.qty) : null;
  const riskAmt  = plan ? plan.risk_amount : null;
  const rr       = plan ? (plan.rr_ratio ?? plan.strategy_rr) : null;
  const strat    = plan ? (plan.strategy || "") : "";
  const holdDays = plan ? (plan.hold_days ?? (plan.earnings && plan.earnings.hold_days)) : null;
  const canTrade = signal.halal && shares != null && Number(shares) > 0;
  const isWait   = verdict === "WAIT" || !/(?:BUY|STRONG)/i.test(verdict);

  const priceNow = Number(signal.price) || 0;
  const entryNow = Number(entry) || 0;
  const stalePct = priceNow && entryNow ? Math.abs(entryNow - priceNow) / priceNow * 100 : 0;
  const isStale = stalePct > 1;

  const equity = brokerHealth && brokerHealth.account ? brokerHealth.account.equity : null;
  const riskPctEq = equity && riskAmt ? (Number(riskAmt) / Number(equity) * 100).toFixed(2) : null;

  // Signal⇄Forecast agreement
  const fcForChip = (forecast && forecast.data && !forecast.data.error) ? forecast.data : null;
  const fcDir = fcForChip ? (fcForChip.expected_change_pct >= 0 ? "up" : "down") : null;
  const sigDir = verdict.includes("SELL") ? "down" : verdict.includes("BUY") ? "up" : null;
  const agreeChip = fcDir && sigDir ? (fcDir === sigDir ? "agree" : "disagree") : null;

  const handleTrade = (sig) => {
    if (isWait && !window.confirm("Plan says WAIT — send anyway?")) return;
    if (onTrade) onTrade(sig);
  };

  // ── SIGNAL MATRIX — one tile per layer the program computes ───────────────
  const fund   = plan && plan.fundamentals && plan.fundamentals.known ? plan.fundamentals : null;
  const ins    = plan && plan.insider && plan.insider.known ? plan.insider : null;
  const ana    = plan && plan.analyst && plan.analyst.known ? plan.analyst : null;
  const earn   = plan && plan.earnings ? plan.earnings : null;
  const mkt    = plan && plan.market && plan.market.known ? plan.market : null;
  // Gold has its OWN ruling (AAOIFI 57: spot + possession), not the equity screen.
  const goldKind = plan && plan.gold_kind;            // "etf" | "miner" | undefined
  const goldEtf  = goldKind === "etf";                // commodity: equity composite N/A
  const goldNote = plan && plan.is_gold ? (plan.halal_note || "") : "";
  const premortem = plan && plan.premortem && (plan.premortem.flags || []).length ? plan.premortem : null;
  const halalV = goldKind === "etf"
    ? "doubtful"                                       // paper gold → debated, never a hard verdict
    : (signal.halalVerdict || (signal.halal ? "halal" : "non_compliant"));
  const r1 = (n) => (n == null || isNaN(Number(n))) ? "—" : Math.round(Number(n));

  const tiles = [];
  tiles.push({ icon: "📊", label: "Technical", value: techMax ? `${techSum}/${techMax}` : "—",
               sub: "RS · MACD · ADX", status: techMax ? (techStrong ? "good" : "warn") : "neutral" });
  if (fund) {
    const fcfTxt = fund.fcf_per_share != null ? (fund.fcf_per_share > 0 ? "FCF+" : "FCF−") : null;
    const sub = [fund.revenue_growth != null ? `Rev ${fund.revenue_growth > 0 ? "+" : ""}${fund.revenue_growth}%` : null,
                 fund.roe != null ? `ROE ${r1(fund.roe)}%` : null, fcfTxt].filter(Boolean).join(" · ") || "quality";
    tiles.push({ icon: "💰", label: "Fundamentals", isNew: true,
                 value: fund.score != null ? `${fund.score}/100` : (fund.revenue_growth != null ? `Rev ${fund.revenue_growth}%` : "—"),
                 sub, status: fund.strong ? "good" : fund.weak ? "bad" : "neutral" });
  } else {
    tiles.push({ icon: "💰", label: "Fundamentals", isNew: true, value: "—", sub: "no data", status: "neutral" });
  }
  tiles.push({ icon: goldEtf ? "🥇" : "🕌", label: "Halal AAOIFI",
               value: goldEtf ? "Review"
                    : halalV === "halal" ? "Compliant" : halalV === "doubtful" ? "Doubtful" : "Non-compliant",
               sub: goldEtf ? "Gold · Std 57" : "Standard 21",
               // Gold "Review" is an informational caveat, NOT a verdict-killing flag.
               status: goldEtf ? "neutral" : halalV === "halal" ? "good" : halalV === "doubtful" ? "warn" : "bad" });
  if (ins) {
    const heavy = ins.heavy_sell, top = ins.top_seller;
    tiles.push({ icon: "👤", label: "Insider 90d",
                 value: heavy ? (top && top.pct_of_stake != null ? `Sold ${top.pct_of_stake}%` : "Heavy selling")
                              : (ins.net_value > 0 ? "Net buying" : ins.sell_value > 0 ? "Some selling" : "Quiet"),
                 sub: heavy ? "of stake — caution" : ins.net_value > 0 ? "insiders bought" : "open-market",
                 status: heavy ? "bad" : ins.net_value > 0 ? "good" : "neutral" });
  } else {
    tiles.push({ icon: "👤", label: "Insider 90d", value: "—", sub: "no data", status: "neutral" });
  }
  if (ana) {
    tiles.push({ icon: "🎯", label: "Analysts", value: ana.bearish ? "Bearish tilt" : "Buy consensus",
                 sub: `${ana.buy} buy · ${ana.hold} hold · ${ana.sell} sell`, status: ana.bearish ? "warn" : "good" });
  } else {
    tiles.push({ icon: "🎯", label: "Analysts", value: "—", sub: "no data", status: "neutral" });
  }
  if (goldEtf) tiles.push({ icon: "📅", label: "Earnings", value: "N/A", sub: "commodity — no earnings", status: "neutral" });
  else if (earn && earn.within_blackout) tiles.push({ icon: "📅", label: "Earnings", value: `In ${earn.business_days}d`, sub: "blackout — don't enter", status: "bad" });
  else if (earn && earn.known) tiles.push({ icon: "📅", label: "Earnings", value: `In ${earn.business_days}d`, sub: "outside blackout", status: "neutral" });
  else tiles.push({ icon: "📅", label: "Earnings", value: "Unconfirmed", sub: "verify before entry", status: "warn" });
  if (mkt) tiles.push({ icon: "📈", label: "Market regime", value: mkt.spy_bearish ? "Risk-off" : "Risk-on",
                        sub: mkt.spy_bearish ? "SPY below avg" : "SPY above avg", status: mkt.spy_bearish ? "warn" : "good" });
  else tiles.push({ icon: "📈", label: "Market regime", value: "—", sub: "SPY trend", status: "neutral" });
  if (fcForChip) {
    const up = fcForChip.expected_change_pct >= 0;
    // Judge by PROBABILITY of profit, not the sign of the expected move: a 48-52%
    // forecast is a coin-flip (no edge) and must NOT count as a "why buy". Only a
    // real edge (>55%) is good; a real negative edge (<45%) is a caution.
    const pp = fcForChip.prob_profit_pct;
    const fcStatus = pp == null ? (up ? "good" : "warn")
                   : pp >= 55 ? "good" : pp < 45 ? "warn" : "neutral";
    tiles.push({ icon: "🔮", label: "Forecast 20d", value: `${up ? "+" : ""}${Number(fcForChip.expected_change_pct).toFixed(1)}%`,
                 sub: pp != null ? `${pp}% prob profit` : "probabilistic",
                 status: fcStatus });
  } else {
    tiles.push({ icon: "🔮", label: "Forecast", value: "—", sub: "loading", status: "neutral" });
  }

  const goodFlags  = tiles.filter(t => t.status === "good").map(t => t.label);
  const watchFlags = tiles.filter(t => t.status === "bad" || t.status === "warn")
                          .map(t => t.label + (t.value && t.value !== "—" ? ` (${t.value})` : ""));
  const nGood = goodFlags.length, nWatch = watchFlags.length;

  // "Why this score" — the REAL signed drivers (no invented numbers): technical factor
  // points (from scoring.components) + the live fundamentals/insider selection adjustments,
  // ranked by magnitude. Gate-type signals (earnings/regime/analyst) listed as cautions.
  const whyPos = [], whyNeg = [];
  compRows.forEach(c => { const v = Number(c.v) || 0; if (v >= 8) whyPos.push({ lab: c.lab, val: v }); });
  if (fund && Number(fund.score_adj)) { const v = Math.round(Number(fund.score_adj)); (v >= 0 ? whyPos : whyNeg).push({ lab: "Fundamentals", val: v }); }
  if (ins && ins.heavy_sell) whyNeg.push({ lab: "Insider selling", val: -15 });
  else if (ins && ins.net_value > 0) whyPos.push({ lab: "Insider buying", val: 5 });
  whyPos.sort((a, b) => b.val - a.val);
  whyNeg.sort((a, b) => a.val - b.val);
  const whyDrivers = whyPos.concat(whyNeg);
  const whyCautions = [];
  if (earn && earn.within_blackout) whyCautions.push("earnings in " + earn.business_days + "d");
  if (mkt && mkt.spy_bearish) whyCautions.push("market risk-off");
  if (ana && ana.bearish) whyCautions.push("analyst bearish tilt");

  // Verdict guard — don't let momentum alone mislabel a weak name. Demote the DISPLAYED
  // verdict when contradictions pile up (more cautions than strengths, weak fundamentals,
  // bearish analysts, a negative/coin-flip forecast, a risk-off market, heavy insider selling,
  // or an earnings blackout). The raw `verdict` still drives the matrix; only the headline is tempered.
  const _fcNeg = !!(fcForChip && (Number(fcForChip.expected_change_pct) < 0
                 || (fcForChip.prob_profit_pct != null && Number(fcForChip.prob_profit_pct) < 50)));

  // ── Gold (ETF): the equity composite (fundamentals/halal) doesn't apply, so it
  // would auto-AVOID any gold regardless of the chart. Derive the headline from the
  // TECHNICAL read + forecast; the Shariah "Review" stays a separate caveat (banner). ──
  let convScore = score, headVerdict = verdict, goldHeadNote = "";
  if (goldEtf) {
    const techPct = techMax > 0 ? techSum / techMax : 0;
    const fcAdj = fcForChip ? Math.max(-8, Math.min(8, Number(fcForChip.expected_change_pct) * 2)) : 0;
    convScore = Math.round(Math.max(0, Math.min(100, techPct * 100 + fcAdj)));
    headVerdict = verdictFromScore(convScore);
    goldHeadNote = "حكم فنّي فقط — معايير الأسهم (أساسيات/حلال) لا تنطبق على سلعة؛ راجع الملاحظة الشرعية.";
  }
  const _headBuy = /(?:BUY|STRONG)/i.test(headVerdict);

  // SEVERE single signals that should temper a BUY on their own, not only when ≥2
  // pile up: an insider dumping a big slice of their OWN stake, or revenue falling
  // hard (a −23% top line is "weak" no matter what the blended fundamental score says).
  const _bigInsiderDump = !!(ins && ins.heavy_sell && ins.top_seller
                             && Number(ins.top_seller.pct_of_stake) >= 15);
  const _revCrash = !!(fund && fund.revenue_growth != null && Number(fund.revenue_growth) <= -15);

  const _redFlags = [
    nWatch > nGood, (fund && fund.weak) || _revCrash, ana && ana.bearish, _fcNeg,
    mkt && mkt.spy_bearish, ins && ins.heavy_sell, earn && earn.within_blackout,
  ].filter(Boolean).length;
  let dispVerdict = headVerdict, guardNote = "";
  // The equity verdict-guard doesn't apply to gold (its verdict is already a pure
  // technical read, not a momentum-vs-fundamentals contradiction).
  if (!goldEtf && _headBuy && (_redFlags >= 2 || _bigInsiderDump)) {
    const _severe = _bigInsiderDump || _redFlags >= 3;
    dispVerdict = _severe ? "CAUTION" : (headVerdict === "STRONG BUY" ? "BUY" : "CAUTION");
    const _rz = [];
    if (_bigInsiderDump) _rz.push(`insider sold ${Math.round(Number(ins.top_seller.pct_of_stake))}% of stake`);
    if (_revCrash) _rz.push(`revenue ${Number(fund.revenue_growth)}%`);
    guardNote = `Momentum-driven — ${_redFlags} signals disagree${_rz.length ? " (" + _rz.join(" · ") + ")" : " (fundamentals · forecast · analysts · regime)"}. The high score is recent price thrust, not high conviction.`;
  }
  const _dispBuy = dispVerdict !== "CAUTION" && /(?:BUY|STRONG)/i.test(dispVerdict);
  const vColor = dispVerdict.includes("SELL") ? "var(--negative)"
               : dispVerdict === "CAUTION" ? "var(--amber, #d9a441)"
               : _dispBuy ? "var(--positive)" : "var(--text-muted)";
  const vBg = dispVerdict.includes("SELL") ? "var(--negative-dim, rgba(220,80,80,0.12))"
            : dispVerdict === "CAUTION" ? "var(--warning-dim, rgba(217,164,65,0.12))"
            : _dispBuy ? "var(--accent-dim, rgba(80,200,120,0.10))" : "var(--bg-raised)";

  // Trade-plan visual axis
  const planVals = [stop, entry, tp1, tp2, tp3].map(Number).filter(v => v > 0);
  const plo = planVals.length ? Math.min(...planVals) : 0;
  const phi = planVals.length ? Math.max(...planVals) : 1;
  const pspan = (phi - plo) || 1;
  const ppos = v => Math.max(0, Math.min(100, ((Number(v) - plo) / pspan) * 100));
  const hasBar = planVals.length >= 2 && entry && stop;

  return (
    <div className="col col-analyze">
      <div className="wf-section">
        <div className="wf-head">
          <span className="wf-title">Analyze</span>
          <span className="wf-sub">{signal.symbol} · {signal.industry}</span>
        </div>
        <div className="an-panel">

          {/* ── HERO: symbol + price + conviction gauge ── */}
          <div className="an-hdr" style={{ alignItems: "center" }}>
            <div>
              <div className="an-sym">{signal.symbol}</div>
              <div className="an-co">{signal.company}</div>
              <div className="an-price" style={{ marginTop: 4 }}>
                <span className="p">${Number(signal.price).toFixed(2)}</span>
                <span className="c" style={{ color: chgColor }}>{fmtPct(signal.chg)} <span style={{ fontSize: 9, color: "var(--text-muted)" }}>1w</span></span>
              </div>
            </div>
            <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 2 }}>
              {_convictionGauge(convScore)}
              <Badge kind={dispVerdict === "CAUTION" ? "amber" : badgeClassFor(dispVerdict).replace("b-", "")}>{dispVerdict}</Badge>
            </div>
          </div>

          <Sparkline points={signal.spark} color={chgColor} height={30} />

          {/* ── VERDICT BANNER ── */}
          <div style={{ marginTop: 8, padding: "7px 11px", borderRadius: 7, background: vBg,
                        display: "flex", alignItems: "center", gap: 9, flexWrap: "wrap" }}>
            <span style={{ fontSize: 14, fontWeight: 800, color: vColor }}>{dispVerdict}</span>
            <span style={{ fontSize: 10.5, color: "var(--text-secondary)" }}>
              {nGood} strong · {nWatch} caution
              {halalV === "halal" ? " · halal ✓" : ""}
              {convScore > 0 ? ` · ${convScore}/100${goldEtf ? " فني" : ""}` : ""}
            </span>
          </div>
          {premortem ? (
            <div style={{ marginTop: 6, padding: "6px 10px", borderRadius: 6,
                          background: premortem.risk === "high" ? "var(--negative-dim, rgba(220,80,80,0.12))" : "var(--warning-dim, rgba(217,164,65,0.10))",
                          border: "1px solid " + (premortem.risk === "high" ? "var(--negative)" : "var(--amber, #d9a441)"),
                          fontSize: 9.5, color: "var(--text-secondary)", lineHeight: 1.5 }}>
              🔮 <strong>Pre-mortem · risk {String(premortem.risk || "").toUpperCase()}</strong>
              {" — "}{(premortem.flags || []).join(" · ")}
            </div>
          ) : null}
          {goldHeadNote ? (
            <div dir="rtl" style={{ marginTop: 5, fontSize: 9.5, color: "var(--text-muted)", lineHeight: 1.5 }}>🥇 {goldHeadNote}</div>
          ) : null}
          {guardNote ? (
            <div style={{ marginTop: 5, fontSize: 9.5, color: "var(--amber, #d9a441)", lineHeight: 1.5 }}>⚖ {guardNote}</div>
          ) : null}
          {goldNote ? (
            <div dir="rtl" style={{ marginTop: 6, padding: "6px 10px", borderRadius: 6,
                                    background: "var(--warning-dim, rgba(217,164,65,0.12))",
                                    border: "1px solid var(--amber, #d9a441)",
                                    fontSize: 9.5, color: "var(--text-secondary)", lineHeight: 1.6 }}>
              🥇 <strong>ملاحظة شرعية للذهب:</strong> {goldNote}
            </div>
          ) : null}
          {(verdict || "").includes("SELL") && (
            <div style={{ fontSize: 8.5, color: "var(--text-muted)", marginTop: 3, lineHeight: 1.4 }}>
              ⚠ SELL signals are historically unreliable (~38% accuracy) — not actioned
            </div>
          )}

          {/* ── SIGNAL MATRIX ── */}
          <div className="an-sect-title">Signal matrix · every layer at a glance</div>
          {loading ? (
            <div className="an-bar-row"><div className="an-skel-bar" style={{ width: "100%", height: 40, borderRadius: 6, background: "var(--bg-raised)", animation: "pulse 1.5s ease-in-out infinite" }}></div></div>
          ) : (
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 7 }}>
              {tiles.map((t, i) => (
                <div key={i} style={{ background: "var(--bg-raised)", borderRadius: 7, padding: "7px 9px",
                       border: `1px solid ${t.status === "bad" ? "var(--negative)" : t.status === "warn" ? "var(--amber, #d9a441)" : "var(--border)"}` }}>
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", fontSize: 10, color: "var(--text-muted)" }}>
                    <span><span style={{ marginRight: 4 }}>{t.icon}</span>{t.label}</span>
                    {t.isNew ? <span style={{ fontSize: 7.5, fontWeight: 700, color: "var(--accent)", border: "1px solid var(--accent)", borderRadius: 3, padding: "0 3px" }}>NEW</span> : null}
                  </div>
                  <div style={{ fontSize: 12.5, fontWeight: 700, marginTop: 2, color: _anStatusColor(t.status) }}>{t.value}</div>
                  <div style={{ fontSize: 9, color: "var(--text-muted)", marginTop: 1 }}>{t.sub}</div>
                </div>
              ))}
            </div>
          )}

          {/* ── WHY BUY / WATCH FLAGS ── */}
          {!loading && (nGood || nWatch) ? (
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 7, marginTop: 8 }}>
              <div style={{ borderRadius: 7, background: "var(--accent-dim, rgba(80,200,120,0.10))", padding: "7px 9px" }}>
                <div style={{ fontSize: 9.5, fontWeight: 700, color: "var(--positive)", marginBottom: 3 }}>✓ why buy</div>
                <div style={{ fontSize: 9.5, color: "var(--text-secondary)", lineHeight: 1.6 }}>{nGood ? goodFlags.join(" · ") : "—"}</div>
              </div>
              <div style={{ borderRadius: 7, background: nWatch ? "var(--negative-dim, rgba(220,80,80,0.10))" : "var(--bg-raised)", padding: "7px 9px" }}>
                <div style={{ fontSize: 9.5, fontWeight: 700, color: nWatch ? "var(--negative)" : "var(--text-muted)", marginBottom: 3 }}>⚠ watch</div>
                <div style={{ fontSize: 9.5, color: "var(--text-secondary)", lineHeight: 1.6 }}>{nWatch ? watchFlags.join(" · ") : "no red flags"}</div>
              </div>
            </div>
          ) : null}

          {/* ── WHY THIS SCORE — the real ranked drivers ── */}
          {!loading && whyDrivers.length ? (
            <>
              <div className="an-sect-title">Why this score</div>
              <div>
                {whyDrivers.slice(0, 7).map((w, i) => (
                  <div key={i} style={{ display: "grid", gridTemplateColumns: "16px 1fr auto", gap: 8, alignItems: "center", fontSize: 10.5, padding: "2px 0" }}>
                    <span style={{ color: w.val >= 0 ? "var(--positive)" : "var(--negative)", fontWeight: 700, textAlign: "center" }}>{w.val >= 0 ? "▲" : "▼"}</span>
                    <span style={{ color: "var(--text-secondary)" }}>{w.lab}</span>
                    <span className="mono" style={{ color: w.val >= 0 ? "var(--positive)" : "var(--negative)", fontWeight: 700 }}>{w.val >= 0 ? "+" : ""}{w.val}</span>
                  </div>
                ))}
                {whyCautions.length ? (
                  <div style={{ fontSize: 9.5, color: "var(--amber, #d9a441)", marginTop: 3, lineHeight: 1.4 }}>⚠ also: {whyCautions.join(" · ")}</div>
                ) : null}
              </div>
              <div style={{ fontSize: 8.5, color: "var(--text-muted)", marginTop: 3, lineHeight: 1.4 }}>
                Real drivers — technical points + fundamentals/insider adjustments (from the live data), biggest first.
              </div>
            </>
          ) : null}

          {/* ── MARKET CONTEXT — the broad backdrop, traffic-light verdicts ── */}
          {market ? (
            <>
              <div className="an-sect-title">Market context</div>
              <div style={{ marginBottom: 2 }}>
                {_marketRows(market, sectors, plan && plan.market).map((r, i) => (
                  <div key={i} style={{ display: "grid", gridTemplateColumns: "1fr auto 58px", gap: 8, alignItems: "center", fontSize: 10, padding: "2.5px 0" }}>
                    <span style={{ color: "var(--text-muted)" }}>{r.lab}</span>
                    <span className="mono" style={{ fontWeight: 600, textAlign: "right" }}>{r.val}</span>
                    <span style={{ fontWeight: 700, color: r.color, textAlign: "right" }}>{r.verdict}</span>
                  </div>
                ))}
              </div>
            </>
          ) : null}

          {/* ── TRADE PLAN — visual axis + metric cards ── */}
          <div className="an-sect-title">Trade plan{strat ? " · " + strat : ""}</div>
          {hasBar ? (
            <div style={{ position: "relative", height: 30, margin: "10px 6px 14px" }}>
              <div style={{ position: "absolute", top: 13, left: 0, right: 0, height: 3, borderRadius: 2, background: "var(--border)" }}></div>
              <div style={{ position: "absolute", top: 13, height: 3, borderRadius: 2, background: "var(--positive)",
                            left: ppos(entry) + "%", width: Math.max(0, ppos(tp3 || tp2 || tp1) - ppos(entry)) + "%" }}></div>
              {[["stop", stop, "var(--negative)"], ["entry", entry, "var(--text)"], ["T1", tp1, "var(--positive)"], ["T2", tp2, "var(--positive)"], ["T3", tp3, "var(--positive)"]]
                .filter(m => m[1] && Number(m[1]) > 0).map((m, i) => (
                <div key={i} style={{ position: "absolute", top: 4, left: ppos(m[1]) + "%", transform: "translateX(-50%)", textAlign: "center" }}>
                  <div style={{ width: 2, height: 17, background: m[2], margin: "0 auto" }}></div>
                  <div style={{ fontSize: 8.5, color: m[2], marginTop: 1, whiteSpace: "nowrap" }}>{m[0]} {Number(m[1]).toFixed(0)}</div>
                </div>
              ))}
            </div>
          ) : null}
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr 1fr", gap: 6 }}>
            <div style={{ background: "var(--bg-raised)", borderRadius: 6, padding: "6px 8px" }}>
              <div style={{ fontSize: 9, color: "var(--text-muted)" }}>R / R</div>
              <div style={{ fontSize: 14, fontWeight: 700 }} title="Blended across a tiered exit (50% TP1 / 30% TP2 / 20% TP3) — not TP1 alone">{rr != null ? "1:" + Number(rr).toFixed(1) : "—"}</div>
            </div>
            <div style={{ background: "var(--bg-raised)", borderRadius: 6, padding: "6px 8px" }}>
              <div style={{ fontSize: 9, color: "var(--text-muted)" }}>Size</div>
              <div style={{ fontSize: 14, fontWeight: 700 }}>{shares != null ? shares : "—"}<span style={{ fontSize: 9, color: "var(--text-muted)" }}>{shares != null ? " sh" : ""}</span></div>
            </div>
            <div style={{ background: "var(--bg-raised)", borderRadius: 6, padding: "6px 8px" }}>
              <div style={{ fontSize: 9, color: "var(--text-muted)" }}>Risk</div>
              <div style={{ fontSize: 14, fontWeight: 700, color: "var(--negative)" }}>{riskAmt != null ? "$" + Number(riskAmt).toLocaleString("en-US", { maximumFractionDigits: 0 }) : "—"}</div>
              {riskPctEq != null && <div style={{ fontSize: 8, color: "var(--text-muted)" }}>{riskPctEq}% eq</div>}
            </div>
            <div style={{ background: "var(--bg-raised)", borderRadius: 6, padding: "6px 8px" }}>
              <div style={{ fontSize: 9, color: "var(--text-muted)" }}>Hold</div>
              <div style={{ fontSize: 14, fontWeight: 700 }}>{holdDays != null ? "~" + holdDays + "d" : "~20d"}</div>
            </div>
          </div>
          {isStale && (
            <div style={{ fontSize: 8.5, color: "var(--amber, #d9a441)", marginTop: 6, lineHeight: 1.4 }}>
              ⚠ Plan computed at ${Number(entry).toFixed(2)} — price is now ${Number(signal.price).toFixed(2)}
            </div>
          )}

          {/* ── BUY BUTTON ── */}
          <button className="an-trade" onClick={() => handleTrade(signal)} disabled={!canTrade} style={{ marginTop: 10 }}>
            <i className="fas fa-paper-plane" style={{ marginRight: 6 }}></i>
            {!signal.halal ? "Blocked — halal fail" : canTrade ? "Send to paper trade" : loading ? <span className="pulse" style={{ opacity: 0.5 }}>Loading plan…</span> : "Sizing unavailable"}
          </button>
          {brokerHealth ? (
            <div style={{ marginTop: 6, fontSize: 9, textAlign: "center", color: brokerHealth.connected ? "var(--positive)" : "var(--text-muted)" }}>
              IBKR paper {brokerHealth.connected ? "✓" : "offline"}
              {brokerHealth.account && brokerHealth.account.equity ? ` · $${Number(brokerHealth.account.equity).toLocaleString()}` : ""}
            </div>
          ) : null}

          {/* ════ DETAILS (deep dive) ════ */}
          <div style={{ marginTop: 14, marginBottom: 2, fontSize: 9.5, fontWeight: 700, color: "var(--text-muted)",
                        borderTop: "1px solid var(--border)", paddingTop: 10, letterSpacing: 0.5 }}>DETAILS</div>

          {/* Technical factor bars */}
          <div className="an-sect-title">Technical factors{compRows.length ? " · " + techSum + "/" + techMax : ""}</div>
          {loading ? (
            <div className="an-bar-row"><div className="an-skel-bar" style={{ width: "100%", height: 4, borderRadius: 2, background: "var(--bg-raised)", animation: "pulse 1.5s ease-in-out infinite" }}></div></div>
          ) : scErr ? (
            <div className="an-bar-row"><span className="lab" style={{ color: "var(--text-muted)" }}>Scoring unavailable</span></div>
          ) : compRows.length ? compRows.map((c) => (
            <div key={c.lab} className="an-bar-row" title={_AN_TOOLTIP[c.k] || ""}>
              <span className="lab">
                {c.lab}
                {c.rawVal != null && <span className="an-raw-val"> · {typeof c.rawVal === "number" ? c.rawVal.toFixed(1) : c.rawVal}</span>}
              </span>
              <div className="bar"><div style={{ width: c.pct + "%", background: scoreColor(c.pct) }}></div></div>
              <span className="num" style={{ color: scoreColor(c.pct) }}>{c.v}/{c.max}</span>
            </div>
          )) : (
            <div className="an-bar-row"><span className="lab" style={{ color: "var(--text-muted)" }}>—</span></div>
          )}
          {techNote && !goldEtf ? (
            <div style={{ marginTop: 4, fontSize: 9, color: "var(--text-muted)", lineHeight: 1.4 }}>ⓘ {techNote}</div>
          ) : null}

          {/* Signal⇄Forecast agreement chip */}
          {agreeChip ? (
            <div style={{ marginTop: 6, display: "flex", alignItems: "center", gap: 6 }}>
              <span className={"an-agree-chip" + (agreeChip === "agree" ? " an-agree-ok" : " an-agree-warn")}
                    title="Early measurement (n=902): agreeing trades did better — small sample, not a guarantee">
                {agreeChip === "agree" ? "✓ forecast agrees with the signal" : "⚠ forecast disagrees with the signal"}
              </span>
            </div>
          ) : null}

          {/* Forecast detail */}
          {(() => {
            const fc = (forecast && forecast.data && !forecast.data.error) ? forecast.data : null;
            const fcLoading = forecast && forecast.loading;
            const last = fc && fc.bands && fc.bands.length ? fc.bands[fc.bands.length - 1] : null;
            return (
              <>
                <div className="an-sect-title" style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                  <span>Forecast · probabilistic</span>
                  <select value={horizon} onChange={(e) => onHorizon && onHorizon(Number(e.target.value))}
                          style={{ background: "var(--bg-raised)", color: "var(--text-secondary)", border: "1px solid var(--border)", borderRadius: 4, fontSize: 9, padding: "1px 4px", cursor: "pointer" }}>
                    {[5, 10, 20, 30].map(h => <option key={h} value={h}>{h}d</option>)}
                  </select>
                </div>
                {fcLoading ? (
                  <div className="an-bar-row"><div className="an-skel-bar" style={{ width: "100%", height: 4, borderRadius: 2, background: "var(--bg-raised)", animation: "pulse 1.5s ease-in-out infinite" }}></div></div>
                ) : fc ? (
                  <>
                    {_forecastFanSvg(fc)}
                    {last && _rangeBar(last.p5, last.median, last.p95, fc.current_price)}
                    <div className="an-grid" style={{ marginTop: 6 }}>
                      <div className="row"><span className="l">Expected</span><span className="v">{_anFx(fc.expected_price)} <span style={{ fontSize: 9, color: fc.expected_change_pct >= 0 ? "var(--positive)" : "var(--negative)" }}>{fmtPct(fc.expected_change_pct)}</span></span></div>
                      <div className="row"><span className="l">Prob profit</span><span className="v">{fc.prob_profit_pct}%</span></div>
                      <div className="row"><span className="l">Range P5–P95</span><span className="v">{last ? "$" + last.p5.toFixed(2) + " – $" + last.p95.toFixed(2) : "—"}</span></div>
                      <div className="row"><span className="l">Annual vol</span><span className="v">{fc.annual_vol_pct}%</span></div>
                    </div>
                    <div style={{ fontSize: 8.5, color: "var(--text-muted)", marginTop: 5, lineHeight: 1.4 }}>
                      Probabilistic range over {fc.horizon}d — not a point prediction · from historical drift + vol
                    </div>
                  </>
                ) : (
                  <div className="an-bar-row"><span className="lab" style={{ color: "var(--text-muted)" }}>Forecast unavailable</span></div>
                )}
              </>
            );
          })()}

          {/* On-demand backtest */}
          {(() => {
            const bt = (backtest && backtest.symbol === signal.symbol) ? backtest : null;
            const s0 = bt && Array.isArray(bt.data) && bt.data.length ? bt.data[0] : null;
            const err = bt && (bt.error || (s0 && (s0.Error || s0.Message)));
            const _n = (v, d = 2) => (v == null || isNaN(Number(v))) ? "—" : Number(v).toFixed(d);
            return (
              <div style={{ marginTop: 8 }}>
                <div className="an-sect-title">Backtest · 2y walk-forward</div>
                {!bt ? (
                  <button onClick={() => onBacktest && onBacktest(signal.symbol)}
                          title="2-year walk-forward backtest — no look-ahead, with costs, Deflated Sharpe"
                          style={{ width: "100%", padding: "6px 10px", fontSize: 11, fontWeight: 700, cursor: "pointer",
                                   borderRadius: 6, border: "1px solid var(--accent)", color: "var(--accent)", background: "var(--accent-dim)" }}>
                    🔬 Run backtest (2 years)
                  </button>
                ) : bt.loading ? (
                  <div className="an-bar-row"><div className="an-skel-bar" style={{ width: "100%", height: 4, borderRadius: 2, background: "var(--bg-raised)", animation: "pulse 1.5s ease-in-out infinite" }}></div></div>
                ) : err ? (
                  <div style={{ fontSize: 9, color: "var(--text-muted)" }}>Backtest failed: {String(err).slice(0, 80)}</div>
                ) : s0 ? (
                  <>
                    <div className="an-grid">
                      <div className="row"><span className="l">Win rate</span><span className="v">{_n(s0["Win Rate %"], 1)}%</span></div>
                      <div className="row"><span className="l">Profit factor</span><span className="v">{_n(s0["Profit Factor"])}</span></div>
                      <div className="row"><span className="l">Trades</span><span className="v">{s0["Total Trades"] != null ? s0["Total Trades"] : "—"}</span></div>
                      <div className="row"><span className="l">Return</span><span className="v">{_n(s0["Return %"], 1)}%</span></div>
                      <div className="row"><span className="l">Max drawdown</span><span className="v txt-negative">{_n(s0["Max Drawdown %"], 1)}%</span></div>
                      <div className="row"><span className="l" title="Deflated Sharpe — penalizes multiple testing (Bailey & López de Prado)">Deflated Sharpe</span><span className="v">{_n(s0["Deflated Sharpe"])}</span></div>
                      <div className="row"><span className="l">Permutation p</span><span className="v">{_n(s0["Permutation p-value"], 3)}</span></div>
                    </div>
                    <div style={{ fontSize: 8.5, color: "var(--text-muted)", marginTop: 5, lineHeight: 1.4 }}>
                      Historical OOS · 2y · no look-ahead · with costs — not a promise. The forward paper ledger is the final judge.
                    </div>
                  </>
                ) : (
                  <div style={{ fontSize: 9, color: "var(--text-muted)" }}>No results</div>
                )}
              </div>
            );
          })()}

        </div>
      </div>
    </div>
  );
}

Object.assign(window, { AnalyzeColumn });
