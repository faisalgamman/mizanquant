// AnalyzeColumn.jsx — Column 2: REAL scoring + trade plan for the selected signal.
//
// Every number comes from the live APIs (passed in via the `analyze` prop):
//   score breakdown → GET /api/v1/scoring/weighted   (.total, .components)
//   trade plan       → GET /api/v1/trade/plan         (entry/stop/tp/shares/rr)
// There are NO synthesized sub-scores and NO mock trade plan. Anything the API
// does not provide renders as "—" rather than an invented value.

const _AN_MAX = { rs: 25, trend: 15, regime: 15, macd: 15, volume: 10, rsi: 8, adx: 7, bb: 5, vwap: 5, gap: 4 };
const _AN_LAB = { rs: "RS vs SPY", trend: "Trend", regime: "Regime", macd: "MACD", volume: "Volume", rsi: "RSI", adx: "ADX", bb: "Bollinger", vwap: "VWAP", gap: "Gap" };
const _anFx = (n, p = 2) => (n == null || isNaN(Number(n))) ? "—" : "$" + Number(n).toFixed(p);

function AnalyzeColumn({ signal, analyze, onTrade }) {
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

  const score    = Math.round((scoring && (scoring.total ?? scoring.weighted_score)) ?? signal.score ?? 0);
  const verdict  = verdictFromScore(score);
  const chgColor = signal.chg >= 0 ? "var(--positive)" : "var(--negative)";

  // Real per-factor score components (points). Empty until scoring loads.
  const comps = (scoring && scoring.components) || {};
  const compRows = Object.keys(comps).filter(k => _AN_MAX[k]).map(k => {
    const v = Number(comps[k]) || 0;
    const pct = Math.max(0, Math.min(100, (v / _AN_MAX[k]) * 100));
    return { lab: _AN_LAB[k] || k, v, pct };
  });

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
  const canTrade = signal.halal && shares != null && Number(shares) > 0;

  return (
    <div className="col col-analyze">
      <div className="wf-section">
        <div className="wf-head">
          <span className="wf-title">Analyze</span>
          <span className="wf-sub">{signal.symbol} · {signal.industry}</span>
        </div>
        <div className="an-panel">
          <div className="an-hdr">
            <div>
              <div className="an-sym">{signal.symbol}</div>
              <div className="an-co">{signal.company}</div>
            </div>
            <div style={{ textAlign: "right" }}>
              <Badge kind={badgeClassFor(verdict).replace("b-", "")}>{verdict}</Badge>
              <div style={{ marginTop: 4 }}>
                <Badge kind={signal.halal ? "accent" : "red"}>{signal.halal ? "Halal · pass" : "Halal · fail"}</Badge>
              </div>
            </div>
          </div>
          <div className="an-price">
            <span className="p">${Number(signal.price).toFixed(2)}</span>
            <span className="c" style={{ color: chgColor }}>{fmtPct(signal.chg)} <span style={{ fontSize: 9, color: "var(--text-muted)" }}>1w</span></span>
          </div>
          <Sparkline points={signal.spark} color={chgColor} height={36} />

          <div className="an-sect-title">
            Score breakdown{scoring && scoring.total != null ? " · " + score + "/100" : ""}
          </div>
          {loading ? (
            <div className="an-bar-row"><span className="lab" style={{ color: "var(--text-muted)" }}>Loading…</span></div>
          ) : scErr ? (
            <div className="an-bar-row"><span className="lab" style={{ color: "var(--text-muted)" }}>Scoring unavailable</span></div>
          ) : compRows.length ? compRows.map((c) => (
            <div key={c.lab} className="an-bar-row">
              <span className="lab">{c.lab}</span>
              <div className="bar"><div style={{ width: c.pct + "%", background: scoreColor(c.pct) }}></div></div>
              <span className="num" style={{ color: scoreColor(c.pct) }}>{c.v}</span>
            </div>
          )) : (
            <div className="an-bar-row"><span className="lab" style={{ color: "var(--text-muted)" }}>—</span></div>
          )}

          <div className="an-sect-title">Trade plan{strat ? " · " + strat : ""}</div>
          <div className="an-grid">
            <div className="row"><span className="l">Entry</span><span className="v">{_anFx(entry)}</span></div>
            <div className="row"><span className="l">Size</span><span className="v">{shares != null ? shares + " sh" : "—"}</span></div>
            <div className="row"><span className="l">Stop</span><span className="v txt-negative">{_anFx(stop)}</span></div>
            <div className="row"><span className="l">Target 1</span><span className="v txt-positive">{_anFx(tp1)}</span></div>
            <div className="row"><span className="l">Target 2</span><span className="v txt-positive">{_anFx(tp2)}</span></div>
            <div className="row"><span className="l">Target 3</span><span className="v txt-positive">{_anFx(tp3)}</span></div>
            <div className="row"><span className="l">R / R</span><span className="v">{rr != null ? "1 : " + Number(rr).toFixed(1) : "—"}</span></div>
            <div className="row"><span className="l">Risk $</span><span className="v txt-negative">{riskAmt != null ? "$" + Number(riskAmt).toLocaleString("en-US", { maximumFractionDigits: 0 }) : "—"}</span></div>
          </div>

          <button className="an-trade" onClick={() => onTrade(signal)} disabled={!canTrade}>
            <i className="fas fa-paper-plane" style={{ marginRight: 6 }}></i>
            {!signal.halal ? "Blocked — halal fail" : canTrade ? "Send to paper trade" : loading ? "Loading plan…" : "Sizing unavailable"}
          </button>
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { AnalyzeColumn });
