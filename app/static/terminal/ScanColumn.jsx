// ScanColumn.jsx — Column 1 of the workflow: market · regime · signal cards · table

function MarketCards({ market }) {
  const m = market || {};
  const reg = m.spy_regime || "—";
  const items = [
    { lab: "VIX",     val: m.vix == null ? "—" : m.vix.toFixed(2),
      sub: m.vix == null ? "—" : m.vix < 20 ? "calm" : m.vix < 30 ? "elevated" : "stress",
      color: m.vix == null ? "var(--text-muted)" : m.vix < 20 ? "var(--positive)" : m.vix < 30 ? "var(--warning)" : "var(--negative)" },
    { lab: "SPY",     val: reg,        sub: m.spy_trend || "",
      color: reg === "BULL" ? "var(--positive)" : reg === "BEAR" ? "var(--negative)" : reg === "NEUTRAL" ? "var(--warning)" : "var(--text-secondary)" },
    { lab: "Breadth", val: m.breadth == null ? "—" : m.breadth.toFixed(1) + "%", sub: "advance/decline",
      color: m.breadth == null ? "var(--text-muted)" : m.breadth >= 50 ? "var(--positive)" : "var(--negative)" },
    { lab: "Credit",  val: m.credit == null ? "—" : m.credit.toFixed(4), sub: "HYG / LQD", color: "var(--text-primary)" },
    { lab: "VOL",     val: m.liquidity == null ? "—" : m.liquidity.toFixed(1) + "%", sub: m.market_open ? "open" : "closed", color: "var(--text-primary)", title: "حجم اليوم ÷ متوسط 20 يوماً" },
  ];
  return (
    <div className="mc-strip">
      {items.map((i) => (
        <div key={i.lab} className="mc-card" title={i.title || ""}>
          <div className="mc-lab">{i.lab}</div>
          <div className="mc-val" style={{ color: i.color }}>{i.val}</div>
          <div className="mc-sub">{i.sub}</div>
        </div>
      ))}
    </div>
  );
}

function RegimeBar({ regime }) {
  const config = {
    BULL: { color: "var(--positive)", bg: "var(--positive-dim)" },
    BEAR: { color: "var(--negative)", bg: "var(--negative-dim)" },
    NEUTRAL: { color: "var(--warning)", bg: "var(--warning-dim)" },
  };
  const c = config[regime] || config.NEUTRAL;
  return (
    <div className="regime-bar">
      <div style={{
        flex: 1, background: c.bg, color: c.color,
        display: "flex", alignItems: "center", justifyContent: "center",
        fontSize: 10, fontWeight: 700, letterSpacing: "0.5px",
      }}>{regime}</div>
    </div>
  );
}

function SignalHeroCard({ signal, selected, onSelect }) {
  // Use the scanner's own verdict (same as the table) — not a re-derived threshold,
  // which made the top cards show WAIT while the table showed BUY for the same score.
  const verdict = signal.verdict || verdictFromScore(signal.score);
  const chgColor = signal.chg >= 0 ? "var(--positive)" : "var(--negative)";
  return (
    <div className={"sfc" + (selected ? " selected" : "")} onClick={() => onSelect(signal.symbol)}>
      <div className="sfc-top">
        <Ring score={signal.score} />
        <div>
          <div className="sfc-sym">{signal.symbol}</div>
          <div className="sfc-chg" style={{ color: chgColor }}>{fmtPct(signal.chg)}</div>
        </div>
      </div>
      <div className="sfc-spark">
        <Sparkline points={signal.spark} color={chgColor} />
      </div>
      <div className="sfc-foot">
        <span className="sfc-time">{signal.halal ? "halal · DJIM" : "halal · fail"}</span>
        <Badge kind={badgeClassFor(verdict).replace("b-", "")}>{verdict}</Badge>
      </div>
    </div>
  );
}

function SignalTable({ signals, selectedSymbol, onSelect, filterSignal, setFilterSignal, filterScore, setFilterScore, halalOnly, setHalalOnly }) {
  const filtered = signals.filter((s) => {
    if (halalOnly && !s.halal) return false;
    const verdict = s.verdict || verdictFromScore(s.score);
    if (filterSignal !== "all" && verdict !== filterSignal) return false;
    if (s.score < Number(filterScore)) return false;
    return true;
  });
  return (
    <>
      <div className="filter-bar">
        <select value={filterSignal} onChange={(e) => setFilterSignal(e.target.value)}>
          <option value="all">Signal: All</option>
          <option value="STRONG BUY">STRONG BUY</option>
          <option value="BUY">BUY</option>
          <option value="WAIT">WAIT</option>
          <option value="AVOID">AVOID</option>
        </select>
        <select value={filterScore} onChange={(e) => setFilterScore(e.target.value)}>
          <option value="0">Score: All</option>
          <option value="60">60+</option>
          <option value="75">75+</option>
          <option value="90">90+</option>
        </select>
        <label>
          <input type="checkbox" checked={halalOnly} onChange={(e) => setHalalOnly(e.target.checked)} />
          Halal
        </label>
        <span style={{ marginLeft: "auto", fontSize: 9, color: "var(--text-muted)" }}>{filtered.length} of {signals.length}</span>
      </div>
      <div className="s-table-wrap">
        <table className="s-table">
          <thead>
            <tr>
              <th>Symbol</th><th>Score</th><th>Signal</th><th>Price</th><th>Chg 1w</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((s) => {
              const verdict = s.verdict || verdictFromScore(s.score);
              const chgColor = s.chg >= 0 ? "var(--positive)" : "var(--negative)";
              return (
                <tr key={s.symbol} className={s.symbol === selectedSymbol ? "selected" : ""} onClick={() => onSelect(s.symbol)}>
                  <td style={{ fontWeight: 600 }}>{s.symbol}</td>
                  <td>
                    <div className="score-bar">
                      <div className="score-bar-fill"><div style={{ width: s.score + "%", background: scoreColor(s.score) }}></div></div>
                      <span className="mono" style={{ fontWeight: 600 }}>{s.score}</span>
                    </div>
                  </td>
                  <td><Badge kind={badgeClassFor(verdict).replace("b-", "")}>{verdict}</Badge></td>
                  <td className="mono">${s.price.toFixed(2)}</td>
                  <td className="mono" style={{ color: chgColor, fontWeight: 600 }}>{fmtPct(s.chg)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}

// Monthly composite table — real sub-score breakdown (Technical / Fundamental /
// Sentiment / AI). The AI column is flagged: it comes from ~coin-flip models, so
// it is shown for transparency but discounted in the conviction logic.
function MonthlyTable({ signals, selectedSymbol, onSelect }) {
  const cell = (v) => (v == null ? <span style={{ color: "var(--text-muted)" }}>—</span> : v);
  return (
    <div className="s-table-wrap">
      <table className="s-table">
        <thead>
          <tr>
            <th>Symbol</th><th>Comp</th><th>Signal</th><th>Price</th>
            <th title="Technical /30">T</th><th title="Fundamental /25">F</th>
            <th title="Sentiment /20">S</th><th title="AI/ML /15 — unvalidated (~coin-flip)">AI*</th>
            <th title="Fundamental grade">Gr</th>
          </tr>
        </thead>
        <tbody>
          {signals.map((s) => {
            const verdict = s.verdict || verdictFromScore(s.score);
            return (
              <tr key={s.symbol} className={s.symbol === selectedSymbol ? "selected" : ""} onClick={() => onSelect(s.symbol)}>
                <td style={{ fontWeight: 600 }}>{s.symbol}</td>
                <td>
                  <div className="score-bar">
                    <div className="score-bar-fill"><div style={{ width: s.score + "%", background: scoreColor(s.score) }}></div></div>
                    <span className="mono" style={{ fontWeight: 600 }}>{s.score}</span>
                  </div>
                </td>
                <td><Badge kind={badgeClassFor(verdict).replace("b-", "")}>{verdict}</Badge></td>
                <td className="mono">${(s.price || 0).toFixed(2)}</td>
                <td className="mono">{cell(s.tech)}</td>
                <td className="mono">{cell(s.fund)}</td>
                <td className="mono">{cell(s.sent)}</td>
                <td className="mono" style={{ color: "var(--text-muted)" }}>{cell(s.ai)}</td>
                <td className="mono">{cell(s.grade)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <div style={{ fontSize: 8, color: "var(--text-muted)", padding: "6px 10px", lineHeight: 1.5 }}>
        * AI/ML sub-score (15) is from models with ~coin-flip directional accuracy — shown for
        transparency, discounted in conviction. The composite leans on Technical + Fundamental.
      </div>
    </div>
  );
}

// Weekly | Monthly scanner switch.
function ScanTabs({ mode, onMode }) {
  const tab = (id, label, sub) => (
    <button
      type="button"
      className={"scan-tab" + (mode === id ? " active" : "")}
      onClick={() => onMode(id)}
      style={{
        flex: 1, padding: "8px 10px", cursor: "pointer", textAlign: "center",
        background: mode === id ? "var(--accent-dim)" : "transparent",
        border: "1px solid " + (mode === id ? "var(--accent)" : "var(--border)"),
        color: mode === id ? "var(--accent)" : "var(--text-secondary)",
        borderRadius: 6, fontSize: 11, fontWeight: 700, letterSpacing: 0.3,
      }}>
      {label}
      <span style={{ display: "block", fontSize: 8, fontWeight: 500, color: "var(--text-muted)", marginTop: 2 }}>{sub}</span>
    </button>
  );
  return (
    <div style={{ display: "flex", gap: 6, marginBottom: 10 }}>
      {tab("weekly", "Weekly", "swing · technical")}
      {tab("monthly", "Monthly", "composite · fund.")}
      {tab("pairs", "Pairs", "cointegration")}
      {tab("daytrade", "Explosion", "technical · research")}
    </div>
  );
}

// Day-trade "explosion" research scanner — technical-only, ALL stocks (NOT halal-gated).
// Ranks by a 0-100 explosion score (RVOL + momentum + volatility + gap) on daily bars.
// Clicking a row selects the symbol so the Analyze column shows its full card + Monte Carlo.
function ExplosionList({ daytrade, selectedSymbol, onSelect }) {
  const muted = "var(--text-muted)";
  if (daytrade == null) {
    return <div style={{ padding: "32px 12px", textAlign: "center", fontSize: 12, color: muted }}>Scanning for explosive movers…</div>;
  }
  if (daytrade.error) {
    return <div style={{ padding: "28px 12px", textAlign: "center", fontSize: 12, color: "var(--red)" }}>Scan failed: {String(daytrade.error).slice(0, 120)}</div>;
  }
  const rows = daytrade.results || [];
  if (rows.length === 0) {
    return <div style={{ padding: "32px 12px", textAlign: "center", fontSize: 12, color: muted }}>Scanning thousands of liquid US stocks for explosion candidates… ~1–3 min, then cached.</div>;
  }
  return (
    <>
      <div className="watch-banner" style={{ color: "var(--red)", borderColor: "var(--red)" }}>
        🔬 Technical research · all liquid US stocks · NOT halal-screened · not traded
      </div>
      <div style={{ fontSize: 9, color: muted, margin: "6px 0" }}>
        {rows.length} stocks · ranked by explosion score (volume + momentum + volatility + gap) · daily bars
      </div>
      <div className="s-table-wrap">
        <table className="s-table">
          <thead><tr><th>Symbol</th><th>Explosion</th><th>Chg</th><th>RVOL</th><th>Gap</th></tr></thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={r.symbol + i} onClick={() => r.symbol && onSelect && onSelect(r.symbol)}
                  className={r.symbol === selectedSymbol ? "active" : ""}
                  style={{ cursor: "pointer" }} title="Open full card + Monte Carlo">
                <td style={{ fontWeight: 600 }}>{r.symbol}</td>
                <td><strong style={{ color: "var(--accent)" }}>{r.explosion_score}</strong></td>
                <td className="mono" style={{ color: (r.change_pct || 0) >= 0 ? "var(--positive)" : "var(--negative)" }}>{((r.change_pct || 0) >= 0 ? "+" : "") + r.change_pct}%</td>
                <td className="mono">{r.rvol}x</td>
                <td className="mono">{((r.gap_pct || 0) >= 0 ? "+" : "") + r.gap_pct}%</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div style={{ marginTop: 6, fontSize: 9, color: muted, textAlign: "center", lineHeight: 1.5 }}>
        Pure technical scan for day / short-term trading — click any row for the full card + Monte Carlo. Research view only; the buy path stays halal-gated (a non-halal stock can't be traded).
      </div>
    </>
  );
}

// Honest per-scanner paper-ledger banner: real money only after graduation.
function LedgerBanner({ ledger, kind, onRecord, recording }) {
  const g = (ledger && ledger.graduation) || {};
  const grad = !!g.graduated;
  const n = g.n_trades;
  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap",
      padding: "7px 10px", marginBottom: 10, borderRadius: 6, fontSize: 10,
      background: grad ? "var(--positive-dim)" : "var(--warning-dim)",
      border: "1px solid " + (grad ? "var(--positive)" : "var(--warning)"),
      color: grad ? "var(--positive)" : "var(--warning)",
    }}>
      <i className={"fas " + (grad ? "fa-check-circle" : "fa-flask")}></i>
      <b>{kind} paper ledger</b>
      <span style={{ color: "var(--text-secondary)" }}>
        {ledger ? `${ledger.open ?? 0} open · ${ledger.closed ?? 0} closed` : "loading…"}
      </span>
      <span style={{ fontWeight: 700 }}>
        {grad ? "GRADUATED" : "NOT graduated — no real money yet"}{n != null ? ` (n=${n})` : ""}
      </span>
      {onRecord ? (
        <button
          onClick={onRecord}
          disabled={recording}
          title="سجّل توصيات الفاحص الآن في الدفتر الورقي (محاكاة)"
          style={{
            marginLeft: "auto", cursor: recording ? "default" : "pointer",
            fontSize: 10, fontWeight: 700, padding: "3px 8px", borderRadius: 5,
            border: "1px solid var(--accent)", color: "var(--accent)",
            background: "var(--accent-dim)", opacity: recording ? 0.6 : 1,
          }}>
          {recording ? "… يسجّل" : "سجّل الآن"}
        </button>
      ) : null}
    </div>
  );
}

// Halal long-only pairs (cointegration) — relative-value signals + the PVP ledger.
function PairsList({ pairs }) {
  const muted = "var(--text-muted)";
  // 1) still scanning (no response yet) — the only case that should say "scanning".
  if (pairs == null) {
    return (
      <div style={{ padding: "32px 12px", textAlign: "center", fontSize: 12, color: muted }}>
        جارٍ مسح الأزواج المتكاملة (cointegration)…
      </div>
    );
  }
  // 2) the fetch failed.
  if (pairs.error) {
    return (
      <div style={{ padding: "28px 12px", textAlign: "center", fontSize: 12, color: "var(--red)" }}>
        تعذّر مسح الأزواج: {String(pairs.error).slice(0, 120)}
      </div>
    );
  }
  const sigs = pairs.signals || [];
  // 3) scan completed but no signals — show WHY (the funnel) instead of a fake "scanning…".
  if (sigs.length === 0) {
    const d = pairs.diagnostics || {};
    const g = d.gates || {};
    const hasFunnel = d.eg_tested != null;
    const engineMissing = d.statsmodels_available === false;
    const Row = ({ k, v }) => (
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, padding: "2px 0" }}>
        <span style={{ color: muted }}>{k}</span><span className="mono">{v}</span>
      </div>
    );
    return (
      <div style={{ padding: "20px 12px", fontSize: 12, color: "var(--text-secondary)" }}>
        <div style={{ textAlign: "center", fontWeight: 600, marginBottom: 4 }}>
          تم المسح — لا توجد أزواج متكاملة قابلة للتداول الآن
        </div>
        {engineMissing ? (
          <div style={{ color: "var(--red)", fontSize: 11, textAlign: "center", margin: "8px 0", fontWeight: 600 }}>
            ⚠ محرّك الإحصاء (statsmodels) غير متوفر في النشر — لا يمكن اكتشاف أي زوج. هذا عطل يلزم إصلاحه.
          </div>
        ) : hasFunnel ? (
          <div style={{ border: "1px solid var(--border)", borderRadius: 6, padding: "8px 10px", margin: "8px 0" }}>
            <div style={{ fontSize: 9, color: muted, marginBottom: 4, letterSpacing: 0.4 }}>
              SCAN FUNNEL · محرّك الإحصاء {d.statsmodels_available ? "✓" : "✗"}
            </div>
            <Row k="قطاعات" v={d.sectors} />
            <Row k="رموز ضمن قطاعات" v={d.symbols_in_sectors} />
            <Row k="رموز ببيانات" v={d.symbols_with_data} />
            <Row k="أزواج مرشحة (داخل القطاع)" v={d.candidate_pairs} />
            <Row k={`اجتازت فلتر الارتباط ≥${g.correlation_min}`} v={d.passed_correlation} />
            <Row k="اختُبرت إحصائياً (Engle–Granger)" v={d.eg_tested} />
            <Row k={`اجتازت p ≤ ${g.pvalue_max}`} v={d.passed_pvalue} />
            <Row k="أفضل زوج" v={d.best_pair ? `${d.best_pair} · p=${d.best_pvalue}` : "—"} />
          </div>
        ) : null}
        <div style={{ fontSize: 10, color: muted, textAlign: "center", lineHeight: 1.6 }}>
          الصفر نتيجة أمينة: البوابات صارمة (p≤{g.pvalue_max ?? "0.05"} · نصف-عمر≤{g.max_half_life_bars ?? 30} يوماً · ارتباط≥{g.correlation_min ?? 0.7}) لمنع الأزواج الزائفة (مكافحة الـoverfitting — Chan). التكامل المشترك نادر؛ ليس عطلاً.
        </div>
      </div>
    );
  }
  const actionable = sigs.filter(s => s.action === "long_y" || s.action === "long_x" || s.action === "exit");
  const rows = actionable.length ? actionable : sigs.slice(0, 12);
  return (
    <>
      <div style={{ fontSize: 9, color: "var(--text-muted)", marginBottom: 6 }}>
        {pairs.count ?? sigs.length} زوج · دخول |z|≥{pairs.entry_z} · خروج |z|≤{pairs.exit_z} · long-only (لا شورت — حلال)
      </div>
      <div className="s-table-wrap">
        <table className="s-table">
          <thead><tr><th>Pair</th><th>Signal</th><th>Long leg</th><th>z</th><th>Half-life</th></tr></thead>
          <tbody>
            {rows.map((s, i) => {
              const isExit = s.action === "exit";
              const isBuy = s.action === "long_y" || s.action === "long_x";
              return (
                <tr key={s.pair + i}
                    onClick={() => s.long_symbol && window.selectIntelSymbol && window.selectIntelSymbol(s.long_symbol)}
                    style={{ cursor: s.long_symbol ? "pointer" : "default" }} title="اعرض البطاقة">
                  <td style={{ fontWeight: 600 }}>{s.pair}</td>
                  <td><Badge kind={isExit ? "red" : isBuy ? "green" : "muted"}>{isBuy ? "BUY" : isExit ? "EXIT" : "HOLD"}</Badge></td>
                  <td style={{ fontWeight: 600 }}>{s.long_symbol || "—"}</td>
                  <td className="mono">{s.zscore != null ? s.zscore.toFixed(2) : "—"}</td>
                  <td className="mono">{s.half_life_bars != null ? s.half_life_bars.toFixed(0) + "d" : "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div style={{ marginTop: 6, fontSize: 9, color: "var(--text-muted)", textAlign: "center", lineHeight: 1.5 }}>
        نسبية حلال long-only — نشتري الساق الأرخص نسبياً عند تطرّف الفرق، ونخرج عند عودته. تحقّق ورقي فقط (PVP) — لا مال حقيقي قبل التخرّج.
      </div>
    </>
  );
}

function ScanEmpty({ status, mode, scanPct }) {
  const computing = status === "computing";
  const monthly = mode === "monthly";
  return (
    <div style={{
      display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
      gap: 12, padding: "48px 16px", textAlign: "center",
    }}>
      {computing && (
        <div className="spin" style={{
          width: 26, height: 26, border: "3px solid var(--border)",
          borderTopColor: "var(--accent)", borderRadius: "50%",
        }}></div>
      )}
      <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text-secondary)" }}>
        {computing ? (monthly ? `جارٍ مسح الكون الحلال… ${scanPct > 0 ? scanPct + "%" : ""}` : "Scanning the halal universe…") : (monthly ? "No composite picks right now" : "No buy signals right now")}
      </div>
      <div style={{ fontSize: 10, color: "var(--text-muted)", fontFamily: "var(--font-mono)", letterSpacing: 0.4 }}>
        {computing
          ? (monthly && scanPct > 0
              ? `تم المسح ${scanPct}% من ~650 رمزاً`
              : (monthly ? "جارٍ بدء المسح…" : "Screening ~650 symbols across AAOIFI gates · 1–2 min"))
          : (monthly
              ? "Composite screener cache is empty · trigger the smart screener first"
              : "Nothing scored ≥ 55 this cycle · check the watchlist")}
      </div>
      {computing && monthly && scanPct > 0 && (
        <div style={{ width: 200, height: 4, background: "var(--border)", borderRadius: 2, overflow: "hidden" }}>
          <div style={{ width: scanPct + "%", height: "100%", background: "var(--accent)", transition: "width 0.5s" }}></div>
        </div>
      )}
    </div>
  );
}

function ScanColumn(props) {
  const { scanMode, onScanMode, signals, monthlySignals, selectedSymbol, onSelect, market,
          signalsStatus, monthlyStatus, ledgerWeekly, ledgerMonthly, ledgerPairs, pairs, daytrade, watch, monthlyScanPct,
          onRecord, recording } = props;
  const [filterSignal, setFilterSignal] = useState("all");
  const [filterScore, setFilterScore] = useState("0");
  const [halalOnly, setHalalOnly] = useState(true);

  const monthlyMode = scanMode === "monthly";
  const pairsMode   = scanMode === "pairs";
  const daytradeMode = scanMode === "daytrade";
  const list   = monthlyMode ? (monthlySignals || []) : signals;
  const status = monthlyMode ? monthlyStatus : signalsStatus;
  const top3   = list.slice(0, 3);
  const empty  = list.length === 0;

  return (
    <div className="col col-scan">
      <div className="wf-section">
        <div className="wf-head">
          <span className="wf-title">Market</span>
          <span className="wf-sub">VIX · SPY · Breadth · Credit · Liquidity</span>
        </div>
        <MarketCards market={market} />
      </div>
      <div className="wf-section">
        <div className="wf-head">
          <span className="wf-title">SPY Trend</span>
          <span className="wf-sub">price trend · {market.spy_regime}</span>
        </div>
        <RegimeBar regime={market.spy_regime} />
      </div>
      <div className="wf-section">
        <div className="wf-head">
          <span className="wf-title">Scanners</span>
          <span className="wf-sub">{daytradeMode ? "day-trade · ~2500 liquid · technical · research" : pairsMode ? "pairs · cointegration · relative-value" : monthlyMode ? "monthly · ~650 symbols · composite" : "weekly · ~650 symbols · swing"}</span>
        </div>
        <ScanTabs mode={scanMode} onMode={onScanMode} />
        {daytradeMode ? (
          <ExplosionList daytrade={daytrade} selectedSymbol={selectedSymbol} onSelect={onSelect} />
        ) : pairsMode ? (
          <>
            <LedgerBanner ledger={ledgerPairs} kind="Pairs" />
            <PairsList pairs={pairs} />
          </>
        ) : (
          <>
            <LedgerBanner ledger={monthlyMode ? ledgerMonthly : ledgerWeekly} kind={monthlyMode ? "Monthly" : "Weekly"}
                          onRecord={onRecord} recording={recording} />
            {empty ? (
              <ScanEmpty status={status} mode={scanMode} scanPct={monthlyScanPct || 0} />
            ) : (
              <>
                <div className="signals-featured">
                  {top3.map((s) => (
                    <SignalHeroCard key={s.symbol} signal={s} selected={s.symbol === selectedSymbol} onSelect={onSelect} />
                  ))}
                </div>
                {monthlyMode ? (
                  <MonthlyTable signals={list} selectedSymbol={selectedSymbol} onSelect={onSelect} />
                ) : (
                  <SignalTable
                    signals={list}
                    selectedSymbol={selectedSymbol}
                    onSelect={onSelect}
                    filterSignal={filterSignal} setFilterSignal={setFilterSignal}
                    filterScore={filterScore} setFilterScore={setFilterScore}
                    halalOnly={halalOnly} setHalalOnly={setHalalOnly}
                  />
                )}
              </>
            )}
          </>
        )}
      </div>
      <div className="wf-section">
        <div className="wf-head">
          <span className="wf-title">WATCH — قريبون</span>
          <span className="wf-sub">{watch && watch.watch ? watch.watch.length + " stocks" : "—"} · display only</span>
        </div>
        {watch && watch.watch && watch.watch.length > 0 ? (
          <>
            {watch.market_block ? (
              <div className="watch-banner">لماذا ينتظر الجميع الآن: {watch.market_block}</div>
            ) : null}
            <div className="watch-list">
              {watch.watch.map((w) => (
                <div key={w.symbol} className="watch-row"
                     onClick={() => window.selectIntelSymbol && window.selectIntelSymbol(w.symbol)}
                     title="اعرض البطاقة">
                  <div className="watch-top">
                    <span className="watch-sym">{w.symbol}</span>
                    <span className="watch-name">{w.name || "—"}</span>
                    <span className="watch-score">{w.composite_score != null ? w.composite_score : "—"}/100</span>
                    <span className="watch-tech">فني {w.score_tech != null ? w.score_tech : "—"}</span>
                  </div>
                  <div className="watch-reason">🔒 {w.watch_reason || "—"}</div>
                </div>
              ))}
            </div>
            <div className="watch-note">WATCH = إعداد قوي لكن بوّابة تقول انتظر — ليست إشارة شراء.</div>
          </>
        ) : (
          <div className="watch-empty">لا قريبون الآن</div>
        )}
      </div>
    </div>
  );
}

Object.assign(window, { ScanColumn });
