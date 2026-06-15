// AnalyzeColumn.jsx — Column 2: REAL scoring + trade plan for the selected signal.
//
// Every number comes from the live APIs (passed in via the `analyze` prop):
//   score breakdown → GET /api/v1/scoring/weighted   (.total, .components)
//   trade plan       → GET /api/v1/trade/plan         (entry/stop/tp/shares/rr)
// There are NO synthesized sub-scores and NO mock trade plan. Anything the API
// does not provide renders as "—" rather than an invented value.
//
// v1.1 accuracy + polish: points/max bars, measured values, plan tooltips,
// signal⇄forecast agreement chip, WAIT-confirm guard, stale-entry guard.

const _AN_MAX = { rs: 25, trend: 15, regime: 15, macd: 15, volume: 10, rsi: 8, adx: 7, bb: 5, vwap: 5, gap: 4 };
const _AN_LAB = { rs: "RS vs SPY", trend: "Trend", regime: "Regime", macd: "MACD", volume: "Volume", rsi: "RSI", adx: "ADX", bb: "Bollinger", vwap: "VWAP", gap: "Gap" };
const _AN_TOOLTIP = {
  rs: "قوة السهم النسبية مقابل SPY — كلما ارتفعت زاد التفوق", trend: "اتجاه السهم (فوق/تحت المتوسطات الرئيسية)",
  regime: "حالة السوق العامة (صاعد/محايد/هابط)", macd: "تقاطع MACD الأخير — إشارة زخم",
  volume: "تأكيد الحجم — ارتفاع الحجم يدعم الحركة", rsi: "مؤشر القوة النسبية RSI — ذروة شراء/بيع",
  adx: "قوة الاتجاه ADX — فوق 20 = اتجاه قوي", bb: "Bollinger Bands — موقع السعر داخل النطاق",
  vwap: "متوسط السعر المرجح بالحجم VWAP — دعم/مقاومة", gap: "فجوة سعرية — قياس الانحراف عن الإغلاق السابق"
};
const _anFx = (n, p = 2) => (n == null || isNaN(Number(n))) ? "—" : "$" + Number(n).toFixed(p);

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

function AnalyzeColumn({ signal, analyze, forecast, horizon, onHorizon, onTrade, brokerHealth, backtest, onBacktest }) {
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

  // HEADLINE = the SAME score + verdict as the scanner row the user clicked, so the
  // Analyze panel NEVER contradicts the scanner. Previously it recomputed a different
  // score (smart_score) with a different threshold → a scanner BUY (e.g. composite 61)
  // showed here as AVOID (smart_score 49). The weighted-score bars below are a TECHNICAL
  // sub-view shown with their OWN total — not the headline number.
  const score    = Math.round(Number(signal.score) || (scoring && (scoring.smart_score ?? scoring.total)) || 0);
  const verdict  = signal.verdict || verdictFromScore(score);
  const chgColor = signal.chg >= 0 ? "var(--positive)" : "var(--negative)";

  // Raw measured values from momentum_details (if present in signal payload)
  const mom = signal.momentum_details || {};

  // Real per-factor score components (points). Show points/max.
  const comps = (scoring && scoring.components) || {};
  const compRows = Object.keys(comps).filter(k => _AN_MAX[k]).map(k => {
    const v = Number(comps[k]) || 0;
    const max = _AN_MAX[k];
    const pct = Math.max(0, Math.min(100, (v / max) * 100));
    // Measured value: ADAPT to real keys from signal/scoring payload
    const rawVal = mom[k + "_raw"] ?? mom[k] ?? null;
    return { lab: _AN_LAB[k] || k, k, v, max, pct, rawVal };
  });
  // Technical sub-total (the bars' own scale, distinct from the headline verdict).
  const techSum = compRows.reduce((a, c) => a + (Number(c.v) || 0), 0);
  const techMax = compRows.reduce((a, c) => a + (Number(c.max) || 0), 0);
  const techStrong = techMax > 0 && techSum / techMax >= 0.65;     // technically strong
  const verdictBuy = /(?:BUY|STRONG)/i.test(verdict);
  // Honest reconciliation note: the headline (composite/smart) weighs fundamentals the
  // technical bars don't, so the two lenses can disagree — say so plainly.
  const techNote = (compRows.length && scoring && !scErr)
    ? (techStrong && !verdictBuy
        ? "قوي فنياً، لكن الأساسيات/النظام تكبح المحصّلة"
        : (!techStrong && verdictBuy
            ? "المحصّلة شراء بفضل الأساسيات رغم ضعف الفنّي"
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
  const canTrade = signal.halal && shares != null && Number(shares) > 0;
  const isWait   = verdict === "WAIT" || !/(?:BUY|STRONG)/i.test(verdict);

  // Stale-entry: entry price vs current price divergence > 1%
  const priceNow = Number(signal.price) || 0;
  const entryNow = Number(entry) || 0;
  const stalePct = priceNow && entryNow ? Math.abs(entryNow - priceNow) / priceNow * 100 : 0;
  const isStale = stalePct > 1;

  // Risk as % of equity
  const equity = brokerHealth && brokerHealth.account ? brokerHealth.account.equity : null;
  const riskPctEq = equity && riskAmt ? (Number(riskAmt) / Number(equity) * 100).toFixed(2) : null;

  // H3: Signal⇄Forecast agreement
  const fcForChip = (forecast && forecast.data && !forecast.data.error) ? forecast.data : null;
  const fcDir = fcForChip ? (fcForChip.expected_change_pct >= 0 ? "up" : "down") : null;
  const sigDir = verdict.includes("SELL") ? "down" : verdict.includes("BUY") ? "up" : null;
  const agreeChip = fcDir && sigDir ? (fcDir === sigDir ? "agree" : "disagree") : null;

  // H4: WAIT-confirm guard
  const handleTrade = (sig) => {
    if (isWait && !window.confirm("الخطة تقول WAIT — متأكد من الإرسال؟")) return;
    if (onTrade) onTrade(sig);
  };

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
              <span title={verdict === "WAIT" ? "التحليل لا يعطي إشارة شراء واضحة — انتظر تأكيداً أقوى" : verdict.includes("SELL") ? "إشارة بيع — دقة تاريخية منخفضة" : "إشارة شراء بناءً على نقاط السكور"}>
                <Badge kind={badgeClassFor(verdict).replace("b-", "")}>{verdict}</Badge>
              </span>
              {score > 0 ? <span style={{ fontSize: 11, fontWeight: 700, marginLeft: 6, color: "var(--text-secondary)" }} title="درجة الماسح — نفس الرقم في جدول الفاحص">{score}/100</span> : null}
              {(verdict || "").includes("SELL") && (
                <div style={{ fontSize: 8, color: "var(--text-muted)", marginTop: 3, lineHeight: 1.3 }}>
                  ⚠ إشارات البيع تاريخياً معكوسة (دقة ~38%) — لا تُعتمد
                </div>
              )}
              <div style={{ marginTop: 4, display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
                {(() => {
                  // Three-state AAOIFI verdict; fall back to the boolean when verdict absent.
                  const v = signal.halalVerdict || (signal.halal ? "halal" : "non_compliant");
                  const reasons = (signal.halalReasons || []).join(" · ");
                  if (v === "halal") return <Badge kind="accent">HALAL · AAOIFI</Badge>;
                  if (v === "doubtful") return (
                    <span title={reasons || "نشاط يحتاج مراجعة شرعية"}
                          style={{ fontSize: 10, fontWeight: 700, padding: "2px 7px", borderRadius: 5,
                                   color: "var(--amber, #d9a441)", border: "1px solid var(--amber, #d9a441)" }}>
                      ⚠ مشكوك — مراجعة{reasons ? ` · ${reasons}` : ""}
                    </span>
                  );
                  return (
                    <span title={reasons || "غير متوافق مع AAOIFI"}
                          style={{ fontSize: 10, fontWeight: 700, padding: "2px 7px", borderRadius: 5,
                                   color: "var(--red)", border: "1px solid var(--red)" }}>
                      NON-COMPLIANT · AAOIFI{reasons ? ` · ${reasons}` : ""}
                    </span>
                  );
                })()}
                {signal.halalStaleDays != null && (
                  <span title="تعذّر تحديث الأساسيات (Yahoo محجوب) — النتيجة من كاش قديم؛ تُصحَّح في إعادة الفحص الشهرية"
                        style={{ fontSize: 9, fontWeight: 700, padding: "2px 6px", borderRadius: 5,
                                 color: "var(--amber, #d9a441)", border: "1px solid var(--amber, #d9a441)" }}>
                    ⚠ بيانات حلال قديمة · {signal.halalStaleDays} يوم
                  </span>
                )}
              </div>
            </div>
          </div>
          <div className="an-price">
            <span className="p">${Number(signal.price).toFixed(2)}</span>
            <span className="c" style={{ color: chgColor }}>{fmtPct(signal.chg)} <span style={{ fontSize: 9, color: "var(--text-muted)" }}>1w</span></span>
          </div>
          <Sparkline points={signal.spark} color={chgColor} height={36} />

          {/* H1: Score bars with points/max */}
          <div className="an-sect-title">
            Technical factors{compRows.length ? " · " + techSum + "/" + techMax : ""}
          </div>
          {loading ? (
            <div className="an-bar-row"><div className="an-skel-bar" style={{width:"100%",height:4,borderRadius:2,background:"var(--bg-raised)",animation:"pulse 1.5s ease-in-out infinite"}}></div></div>
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

          {techNote ? (
            <div style={{ marginTop: 4, fontSize: 9, color: "var(--text-muted)", lineHeight: 1.4 }}>
              ⓘ {techNote}
            </div>
          ) : null}

          {/* H3: Signal⇄Forecast agreement chip */}
          {agreeChip ? (
            <div style={{ marginTop: 6, display: "flex", alignItems: "center", gap: 6 }}>
              <span className={"an-agree-chip" + (agreeChip === "agree" ? " an-agree-ok" : " an-agree-warn")}
                    title="قياس أوّلي (n=902): الصفقات الموافقة كانت أفضل — عيّنة صغيرة، ليس ضماناً">
                {agreeChip === "agree" ? "✓ التوقّع يوافق الإشارة" : "⚠ التوقّع يخالف الإشارة"}
              </span>
            </div>
          ) : (fcForChip && !agreeChip) ? (
            <div style={{ marginTop: 6, fontSize: 9, color: "var(--text-muted)" }}>—</div>
          ) : null}

          {/* H2: Trade plan accuracy */}
          <div className="an-sect-title">Trade plan{strat ? " · " + strat : ""}</div>
          <div className="an-grid">
            <div className="row"><span className="l">Entry</span><span className="v">{_anFx(entry)}</span></div>
            <div className="row"><span className="l">Size</span><span className="v">{shares != null ? shares + " sh" : "—"}</span></div>
            <div className="row"><span className="l">Stop</span><span className="v txt-negative">{_anFx(stop)}</span></div>
            <div className="row"><span className="l">Target 1</span><span className="v txt-positive">{_anFx(tp1)}</span></div>
            <div className="row"><span className="l">Target 2</span><span className="v txt-positive">{_anFx(tp2)}</span></div>
            <div className="row"><span className="l">Target 3</span><span className="v txt-positive">{_anFx(tp3)}</span></div>
            <div className="row">
              <span className="l">R / R</span>
              <span className="v" title="1:1.7 = نسبة مرجّحة لخطة خروج متدرّجة (50% عند TP1، 30% TP2، 20% TP3) — ليست نسبة TP1 وحده">
                {rr != null ? "1 : " + Number(rr).toFixed(1) : "—"}
              </span>
            </div>
            <div className="row">
              <span className="l">Risk</span>
              <span className="v txt-negative">
                {riskAmt != null ? "$" + Number(riskAmt).toLocaleString("en-US", { maximumFractionDigits: 0 }) : "—"}
                {riskPctEq != null && <span style={{ fontSize: 8, color: "var(--text-muted)", marginLeft: 4 }}>({riskPctEq}% equity)</span>}
              </span>
            </div>
          </div>

          {/* H2: Stale-entry guard */}
          {isStale && (
            <div style={{ fontSize: 8.5, color: "var(--amber)", marginTop: 6, lineHeight: 1.4 }}>
              ⚠ الخطة محسوبة على ${Number(entry).toFixed(2)} — السعر الآن ${Number(signal.price).toFixed(2)}
            </div>
          )}

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
                  <div className="an-bar-row"><div className="an-skel-bar" style={{width:"100%",height:4,borderRadius:2,background:"var(--bg-raised)",animation:"pulse 1.5s ease-in-out infinite"}}></div></div>
                ) : fc ? (
                  <>
                    {_forecastFanSvg(fc)}
                    {/* H5: P5-P95 range bar mini-visual */}
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

          {/* On-demand backtest (Chan Ch.2) — historical OOS evidence, run only on click */}
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
                          title="backtest سيري سنتين — بلا look-ahead، بتكاليف، Deflated Sharpe"
                          style={{ width: "100%", padding: "6px 10px", fontSize: 11, fontWeight: 700, cursor: "pointer",
                                   borderRadius: 6, border: "1px solid var(--accent)", color: "var(--accent)", background: "var(--accent-dim)" }}>
                    🔬 شغّل Backtest (سنتان)
                  </button>
                ) : bt.loading ? (
                  <div className="an-bar-row"><div className="an-skel-bar" style={{ width: "100%", height: 4, borderRadius: 2, background: "var(--bg-raised)", animation: "pulse 1.5s ease-in-out infinite" }}></div></div>
                ) : err ? (
                  <div style={{ fontSize: 9, color: "var(--text-muted)" }}>تعذّر الـbacktest: {String(err).slice(0, 80)}</div>
                ) : s0 ? (
                  <>
                    <div className="an-grid">
                      <div className="row"><span className="l">Win rate</span><span className="v">{_n(s0["Win Rate %"], 1)}%</span></div>
                      <div className="row"><span className="l">Profit factor</span><span className="v">{_n(s0["Profit Factor"])}</span></div>
                      <div className="row"><span className="l">Trades</span><span className="v">{s0["Total Trades"] != null ? s0["Total Trades"] : "—"}</span></div>
                      <div className="row"><span className="l">Return</span><span className="v">{_n(s0["Return %"], 1)}%</span></div>
                      <div className="row"><span className="l">Max drawdown</span><span className="v txt-negative">{_n(s0["Max Drawdown %"], 1)}%</span></div>
                      <div className="row"><span className="l" title="Deflated Sharpe — يعاقب تعدّد المحاولات (Bailey & López de Prado)">Deflated Sharpe</span><span className="v">{_n(s0["Deflated Sharpe"])}</span></div>
                      <div className="row"><span className="l">Permutation p</span><span className="v">{_n(s0["Permutation p-value"], 3)}</span></div>
                    </div>
                    <div style={{ fontSize: 8.5, color: "var(--text-muted)", marginTop: 5, lineHeight: 1.4 }}>
                      تاريخي OOS · سنتان · بلا look-ahead · بتكاليف — ليس وعداً. الحَكَم النهائي هو الدفتر الورقي الأمامي.
                    </div>
                  </>
                ) : (
                  <div style={{ fontSize: 9, color: "var(--text-muted)" }}>لا نتائج</div>
                )}
              </div>
            );
          })()}

          {/* H4: WAIT-confirm guard on buy button */}
          <button className="an-trade" onClick={() => handleTrade(signal)} disabled={!canTrade}>
            <i className="fas fa-paper-plane" style={{ marginRight: 6 }}></i>
            {!signal.halal ? "Blocked — halal fail" : canTrade ? "Send to paper trade" : loading ? <span className="pulse" style={{opacity:0.5}}>Loading plan…</span> : "Sizing unavailable"}
          </button>
          {brokerHealth ? (
            <div style={{ marginTop: 6, fontSize: 9, textAlign: "center", color: brokerHealth.connected ? "var(--positive)" : "var(--text-muted)" }}>
              IBKR paper {brokerHealth.connected ? "✓" : "offline"}
              {brokerHealth.account && brokerHealth.account.equity ? ` · $${Number(brokerHealth.account.equity).toLocaleString()}` : ""}
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { AnalyzeColumn });
