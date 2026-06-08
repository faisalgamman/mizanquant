// StockCard.jsx — professional single-symbol ID card
// (fundamental · technical · sharia DJIM · forecast · trade plan)
const { useState, useEffect } = React;

function _n(v, d = 2) { return (v == null || isNaN(v)) ? "—" : Number(v).toFixed(d); }
function _pct(v, d = 1) { return (v == null || isNaN(v)) ? "—" : (v >= 0 ? "+" : "") + Number(v).toFixed(d) + "%"; }
function _capLabel(mc) {
  if (mc == null) return "—";
  if (mc >= 200e9) return "Mega Cap"; if (mc >= 10e9) return "Large Cap";
  if (mc >= 2e9) return "Mid Cap"; if (mc >= 300e6) return "Small Cap"; return "Micro Cap";
}
function _verdictColor(v) {
  v = String(v || "").toUpperCase();
  if (v === "BUY") return "var(--positive)";
  if (v === "AVOID" || v === "SELL") return "var(--negative)";
  return "var(--warning)"; // WAIT / —
}

function CardStat({ label, value, sub }) {
  return (
    <div className="sc-stat">
      <span className="sc-stat-l">{label}</span>
      <span className="sc-stat-v">{value}</span>
      {sub ? <span className="sc-stat-s">{sub}</span> : null}
    </div>
  );
}

function StockCard({ symbol, account, onClose }) {
  const [card, setCard] = useState(null);
  const [plan, setPlan] = useState(null);
  const [fc, setFc] = useState(null);
  const [news, setNews] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!symbol) return;
    let alive = true;
    setLoading(true); setCard(null); setPlan(null); setFc(null); setNews(null);
    const acct = account || 10000;
    Promise.allSettled([
      fetch(`/api/stock/card?symbol=${symbol}`).then(r => r.json()),
      fetch(`/api/v1/trade/plan?symbol=${symbol}&portfolio=${acct}`).then(r => r.json()),
      fetch(`/api/v1/forecast/${symbol}?horizon=20`).then(r => r.json()),
      fetch(`/api/stock/news?symbol=${symbol}&limit=6`).then(r => r.json()),
    ]).then(([c, p, f, n]) => {
      if (!alive) return;
      if (c.status === "fulfilled") setCard(c.value);
      if (p.status === "fulfilled") setPlan(p.value);
      if (f.status === "fulfilled") setFc(f.value);
      if (n.status === "fulfilled") setNews(n.value);
      setLoading(false);
    });
    return () => { alive = false; };
  }, [symbol, account]);

  if (!symbol) return null;

  const prof = (card && card.profile) || {};
  const sig = (card && card.signal) || {};
  const fund = (card && card.fundamental) || {};
  const tech = (card && card.technical) || {};
  const td = tech.details || {};
  const sent = (card && card.sentiment) || {};
  const halal = (card && card.halal) || {};
  const isHalal = halal.is_halal;

  return (
    <div className="wf-section sc-card">
      <div className="wf-head">
        <span className="wf-title">
          <i className="fas fa-id-card"></i> {symbol} · {prof.name || "—"}
        </span>
        <span className="wf-sub" style={{ display: "flex", gap: 12, alignItems: "center" }}>
          <b style={{ fontFamily: "var(--font-mono)" }}>${_n(prof.price)}</b>
          <span style={{ color: (prof.change_pct >= 0) ? "var(--positive)" : "var(--negative)" }}>{_pct(prof.change_pct)}</span>
          <button className="sc-close" onClick={onClose} title="Close">✕</button>
        </span>
      </div>

      {loading ? <div className="sc-loading">Loading {symbol} …</div> : (
      <div className="sc-body">
        {/* Verdict banner */}
        <div className="sc-verdict" style={{ borderColor: _verdictColor(sig.verdict) }}>
          <span className="sc-verdict-tag" style={{ color: _verdictColor(sig.verdict) }}>● {sig.verdict || "—"}</span>
          <span className="sc-verdict-meta">
            smart {sig.smart_score != null ? sig.smart_score : "—"}/100 ·
            RS {sig.rs_vs_spy || "—"} ·
            gate {sig.hard_gates_passed === false ? "FAIL" : "PASS"}
          </span>
          <span className="sc-verdict-co">{prof.sector || "—"} · {_capLabel(prof.market_cap)}</span>
        </div>

        {/* 3-column analysis grid */}
        <div className="sc-grid">
          {/* Fundamental */}
          <div className="sc-block">
            <div className="sc-block-h">FUNDAMENTAL <b>{fund.score != null ? fund.score : "—"}/{fund.max || 40}</b></div>
            <CardStat label="ROE" value={(fund.profitability || {}).roe || "—"} sub={_pct(((fund.profitability || {}).roe_val || 0) * 100)} />
            <CardStat label="Margin" value={(fund.profitability || {}).margin || "—"} sub={_pct(((fund.profitability || {}).margin_val || 0) * 100)} />
            <CardStat label="Rev growth" value={(fund.profitability || {}).rev_growth || "—"} />
            <CardStat label="P/E" value={_n((fund.valuation || {}).pe_val)} sub={(fund.valuation || {}).pe_comment} />
            <CardStat label="PEG" value={_n((fund.valuation || {}).peg_val)} sub={(fund.valuation || {}).peg_comment} />
          </div>
          {/* Technical */}
          <div className="sc-block">
            <div className="sc-block-h">TECHNICAL <b>{tech.score != null ? tech.score : "—"}/{tech.max || 30}</b></div>
            <CardStat label="RSI" value={_n(td.rsi, 0)} sub={td.rsi_label} />
            <CardStat label="MACD" value={td.macd_label || "—"} />
            <CardStat label="Bollinger" value={td.bb_squeeze ? "Squeeze ●" : (td.bb_label || "—")} />
            <CardStat label="VWAP" value={td.vwap_label || "—"} />
            <CardStat label="Sentiment" value={sent.label || "—"} sub={sent.score != null ? sent.score + "/20" : ""} />
          </div>
          {/* Sharia DJIM */}
          <div className="sc-block">
            <div className="sc-block-h">
              SHARIA · <b style={{ color: isHalal ? "var(--positive)" : "var(--negative)" }}>{isHalal ? "HALAL" : "NON-COMPLIANT"}</b>
            </div>
            <CardStat label="Standard" value={halal.standard || "DJIM"} />
            <CardStat label="Debt" value={_n(halal.debt_ratio, 1) + "%"} sub={halal.debt_pass === false ? "> 33% ✗" : "< 33% ✓"} />
            <CardStat label="Liquidity" value={_n(halal.liquidity_ratio, 1) + "%"} sub={halal.liquidity_pass === false ? "> 33% ✗" : "< 33% ✓"} />
            <CardStat label="Receivables" value={_n(halal.receivable_ratio, 1) + "%"} sub={halal.receivable_pass === false ? "> 33% ✗" : "< 33% ✓"} />
            <CardStat label="Interest" value={_n(halal.interest_ratio, 1) + "%"} sub={"basis: " + (halal.mcap_basis || "—")} />
          </div>
        </div>

        {/* Forecast strip */}
        <div className="sc-strip">
          <span className="sc-strip-h">FORECAST · 20d · probabilistic</span>
          {fc && !fc.error ? (
            <span className="sc-strip-body">
              Expected <b>${_n(fc.expected_price)}</b> ({_pct(fc.expected_change_pct)}) ·
              P(profit) <b>{_n(fc.prob_profit_pct, 0)}%</b> ·
              vol {_n(fc.annual_vol_pct, 0)}%/yr
            </span>
          ) : <span className="sc-strip-body" style={{ color: "var(--text-muted)" }}>—</span>}
        </div>

        {/* Trade plan strip */}
        <div className="sc-strip">
          <span className="sc-strip-h">TRADE PLAN · Option-A</span>
          {plan && !plan.error ? (
            <span className="sc-strip-body">
              Entry <b>${_n(plan.entry)}</b> · Stop <b>${_n(plan.stop_loss)}</b> ·
              TP1 ${_n(plan.tp1)} TP2 ${_n(plan.tp2)} TP3 ${_n(plan.tp3)} ·
              R:R <b>1:{_n(plan.rr_ratio, 1)}</b> · {plan.shares != null ? plan.shares : "—"} sh ·
              risk ${_n(plan.risk_amount, 0)} ({_n(plan.portfolio_pct, 1)}%)
            </span>
          ) : <span className="sc-strip-body" style={{ color: "var(--text-muted)" }}>—</span>}
        </div>

        {/* News block */}
        <div className="sc-strip sc-news">
          <span className="sc-strip-h">NEWS · {news && news.source ? news.source.toUpperCase() : "—"}</span>
          {news && news.news && news.news.length > 0 ? (
            <div className="sc-news-list">
              {news.news.slice(0, 6).map((n, i) => (
                <div key={i} className="sc-news-row" title={n.summary || ""}>
                  <a href={n.link || "#"} target="_blank" rel="noopener" className="sc-news-title">{n.title || "—"}</a>
                  <span className="sc-news-meta">
                    {n.publisher || "—"}{n.published ? " · " + new Date(n.published).toLocaleDateString("en-US", {month:"short", day:"numeric"}) : ""}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <span className="sc-strip-body" style={{ color: "var(--text-muted)" }}>لا أخبار حديثة</span>
          )}
        </div>

        <div className="sc-disc">
          {(card && card.disclaimers ? card.disclaimers : []).map((d, i) => <div key={i}>· {d}</div>)}
          <div>· Forecast is a probabilistic range, not a point prediction.</div>
        </div>
      </div>
      )}
    </div>
  );
}

Object.assign(window, { StockCard });
