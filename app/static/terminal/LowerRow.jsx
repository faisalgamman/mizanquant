// LowerRow.jsx — Predicted Risers (Monte Carlo) · Market Indicators · Market News

function IndicStrip({ indicators }) {
  if (!indicators || indicators.length === 0) {
    return <div className="lr2-empty">لا مؤشرات الآن</div>;
  }
  return (
    <div className="lr2-indic-strip">
      {indicators.map((ind) => {
        const chg = ind.change_pct;
        const chgColor = chg != null ? (chg >= 0 ? "var(--positive)" : "var(--negative)") : "var(--text-muted)";
        const chgStr = chg != null ? ((chg >= 0 ? "+" : "") + chg.toFixed(2) + "%") : "—";
        const isVIX = ind.symbol === "VIX";
        const isBTC = ind.symbol === "BTC/USD";
        const priceStr = ind.price != null
          ? (isVIX ? ind.price.toFixed(2) : (isBTC ? "$" + Number(ind.price).toLocaleString("en-US", {minimumFractionDigits: 0, maximumFractionDigits: 0}) : "$" + (ind.price >= 1 ? Number(ind.price).toFixed(0) : Number(ind.price).toFixed(2))))
          : "—";
        return (
          <div key={ind.label} className="lr2-ind-tile">
            <div className="lr2-ind-lab">{ind.label}</div>
            <div className="lr2-ind-price">{priceStr}</div>
            <div className="lr2-ind-chg" style={{ color: chgColor }}>{chgStr}</div>
            {ind.proxy && ind.proxy !== ind.symbol ? (
              <div className="lr2-ind-proxy">عبر {ind.proxy}</div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

function NewsList({ marketNews }) {
  if (!marketNews || marketNews.length === 0) {
    return <div className="lr2-empty">لا أخبار الآن</div>;
  }
  return (
    <div className="lr2-news-list">
      {marketNews.slice(0, 8).map((n, i) => (
        <div key={i} className="lr2-news-row">
          <a href={n.link || "#"} target="_blank" rel="noopener" className="lr2-news-title">{n.title || "—"}</a>
          <span className="lr2-news-meta">
            {n.publisher || "—"}{n.published ? " · " + new Date(n.published).toLocaleDateString("en-US", {month:"short", day:"numeric"}) : ""}
          </span>
        </div>
      ))}
    </div>
  );
}

function RisersList({ risers, marketSoft }) {
  if (!risers || risers.length === 0) {
    return <div className="lr2-empty">لا بيانات الآن</div>;
  }
  return (
    <div className="lr2-risers-list">
      {risers.map((r) => {
        const ec = r.expected_change_pct;
        const ecColor = ec != null ? (ec >= 0 ? "var(--positive)" : "var(--negative)") : "var(--text-muted)";
        return (
          <div key={r.symbol} className="lr2-riser-row"
               onClick={() => window.selectIntelSymbol && window.selectIntelSymbol(r.symbol)}
               title="اعرض البطاقة">
            <span className="lr2-riser-sym">{r.symbol}</span>
            <span className="lr2-riser-name">{r.name || "—"}</span>
            <span className="lr2-riser-price">{r.price != null ? "$" + Number(r.price).toFixed(2) : "—"}</span>
            <span className="lr2-riser-ec" style={{ color: ecColor }}>
              {ec != null ? (ec >= 0 ? "▲ " : "▼ ") + Math.abs(ec).toFixed(1) + "%" : "—"}
            </span>
            <span className="lr2-riser-prob">
              P(ربح) {r.prob_profit_pct != null ? r.prob_profit_pct.toFixed(0) + "%" : "—"}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function LowerRow({ risers, marketNews, indicators, risersMeta }) {
  const soft = risersMeta && risersMeta.market_soft;
  return (
    <div className="lower lr2">
      {/* Part A — Predicted Risers */}
      <div className="col lr2-col-a">
        <div className="wf-section">
          <div className="wf-head">
            <span className="wf-title">{soft ? "أعلى توقّع (الانحراف ضعيف الآن)" : "أسهم متوقّع صعودها · Monte Carlo"}</span>
            <span className="wf-sub">{(risers || []).length} stocks · expected upside</span>
          </div>
          <RisersList risers={risers} marketSoft={soft} />
          <div className="lr2-disc">نطاق احتمالي من تذبذب السهم — ليس وعداً ولا نصيحة.</div>
        </div>
      </div>
      {/* Part B — Indicators + News */}
      <div className="col lr2-col-b">
        <div className="wf-section">
          <div className="wf-head">
            <span className="wf-title">المؤشرات</span>
            <span className="wf-sub">SPY · QQQ · DIA · IWM · VIX · GLD · BNO · BTC</span>
          </div>
          <IndicStrip indicators={indicators} />
        </div>
        <div className="wf-section">
          <div className="wf-head">
            <span className="wf-title">أخبار السوق</span>
            <span className="wf-sub">{(marketNews || []).length} headlines</span>
          </div>
          <NewsList marketNews={marketNews} />
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { LowerRow });
