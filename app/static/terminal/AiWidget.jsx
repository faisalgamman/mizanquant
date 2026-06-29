// AiWidget.jsx — floating MizanAI assistant on the dashboard (agentic: reasons + uses analysis tools)
const { useState, useEffect, useRef } = React;

const AI_QUICK = [
  { label: "🚀 مرشّحو الصعود القريب", q:
    "ابحث عن أفضل الأسهم الحلال المرشّحة للصعود خلال مدة قصيرة، بمنهجية صارمة:\n" +
    "1) اجلب المرشّحين الحلال عبر get_deep_picks (وget_buy_signals عند الحاجة) — قائمة بالدرجة المركّبة + الدرجات الفرعية + الحكم الشرعي.\n" +
    "2) لأقوى 4-5 أسماء استدعِ analyze_stock لكلٍّ — فهو يُرجع كل الطبقات دفعةً واحدة: الفني (RS/الاتجاه/MACD/RSI/ADX/VWAP) + الأساسيات (fundamentals: درجة score من 100، نمو الإيرادات revenue_growth، التدفق النقدي fcf_per_share، العائد roe) + الحلال AAOIFI + توافق المحلّلين analyst_consensus + نشاط المطّلعين insider_activity (لاحِظ top_seller.pct_of_stake) + الأرباح earnings + نظام السوق market_regime + سياق السوق market_context (VIX/الائتمان HY-IG/الاتساع/السيولة) + الأخبار news. وتحقّق من توافق التوقّع مع الإشارة عبر get_signal_agreement.\n" +
    "3) قاعدة صارمة: أي سهم به near_term_red_flags (بيع مطّلعين كثيف — خصوصاً نسبة كبيرة من الحصّة، تقلّب شديد ATR>7%، أرباح ضمن فترة الحظر، أو هبوط حادّ) يُستبعَد من المراكز الثلاثة الأولى مهما علت درجته الفنية (لا ترشّح سكّيناً هابطاً في القمّة). اذكره في الاستبعادات مع السبب. ونبّه إذا كان market_regime.spy_bearish=true (السوق العريض هابط — حذرٌ إضافي).\n" +
    "4) للناجين فقط اعرض جدولاً: الرمز | الدرجة | الإشارة | سبب مختصر (من البيانات) | تحذيرات. ثم أفضل 3 مع: الدخول، الوقف، والأهداف الثلاثة TP1/TP2/TP3 (لا هدف واحد). ووضّح أن نسبة المخاطرة/العائد 1.7 مرجّحة عبر الأهداف الثلاثة (TP1 50% / TP2 30% / TP3 20%)، وأن هدفاً واحداً مقابل الوقف ≈ 1:1.\n" +
    "استخدم فقط ما تؤكّده الأدوات؛ قل «غير متاح» لأي حقل ناقص (known=false) ولا تختلق أبداً — خاصةً لا تخترع أخباراً: اذكر الأخبار فقط من حقل news في analyze_stock (وإن كان news_status ≠ ok فقل إن القرار فنّي/كمّي لا خبري). وذكّر أن هذا إرشاديّ لا نصيحة مرخّصة والنتائج ورقية. أجب بالعربية." },
  { label: "🔍 Upside candidates", q:
    "Find the best halal stocks that are candidates for near-term upside, methodically:\n" +
    "1) Pull halal candidates via get_deep_picks (and get_buy_signals if needed) — a shortlist with composite score + sub-scores + halal verdict.\n" +
    "2) For the strongest 4-5, call analyze_stock on each — it returns EVERY layer in one call: technical (RS/Trend/MACD/RSI/ADX/VWAP) + fundamentals (score/100 + revenue_growth + fcf_per_share + roe) + AAOIFI halal + analyst_consensus + insider_activity (note top_seller.pct_of_stake) + earnings + market_regime + market_context (VIX/HY-IG credit/breadth/liquidity) + news. Also check forecast/signal agreement via get_signal_agreement.\n" +
    "3) STRICT rule: any stock with near_term_red_flags (heavy insider selling — especially a large % of stake, extreme volatility ATR>7%, earnings within blackout, or a sharp drop) must be kept OUT of the top 3 regardless of technical score (never rank a falling knife at the top). List it under exclusions with the reason. Also flag if market_regime.spy_bearish is true (broad market downtrend — extra caution).\n" +
    "4) For the survivors only, show a table: Symbol | Score | Signal | Brief reason (from the data) | Warnings. Then the top 3 with: Entry, Stop, and ALL THREE targets TP1/TP2/TP3 (not one). Explain that the 1.7 R/R is BLENDED across the three targets (TP1 50% / TP2 30% / TP3 20%) and that a single target vs the stop is ≈ 1:1.\n" +
    "Use only what the tools confirm; say 'not available' for any missing field (known=false) and never fabricate — especially do NOT invent news: cite news ONLY from analyze_stock's `news` field (if news_status != 'ok', say the call is technical/quant-driven, not news-driven). Reply in English." },
  { label: "Best halal picks", q: "What are the best halal opportunities today? Analyze the strongest ones. Reply in English." },
  { label: "Analyze a stock", q: "Analyze AAPL fully via analyze_stock: halal, technical, fundamentals (score/revenue/FCF/ROE), analysts, insiders (% of stake), earnings, market regime + context (VIX/credit/breadth), recent news, and entry/stop/targets. Reply in English." },
  { label: "Market state", q: "What's the market state now (Regime/VIX/Credit)? Reply in English." },
  { label: "My portfolio", q: "Show my portfolio status and risk. Reply in English." },
];

// Lightweight markdown → JSX so the agent's answers (## headings, **bold**, - bullets,
// | tables |) render readably instead of as raw symbols. Pure function, no deps.
function _aiInline(s) {
  return String(s).split(/(\*\*[^*]+\*\*)/g).map((p, j) =>
    (p.startsWith("**") && p.endsWith("**")) ? <strong key={j}>{p.slice(2, -2)}</strong> : p
  );
}
function renderMarkdown(text) {
  const lines = String(text || "").split("\n");
  const out = [];
  let list = null, k = 0;
  const isSep = (s) => /-/.test(s) && /^[\s|:\-]+$/.test(s);
  const cells = (s) => s.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
  const flush = () => {
    if (list) { out.push(<ul key={"u" + k++}>{list.map((x, j) => <li key={j}>{_aiInline(x)}</li>)}</ul>); list = null; }
  };
  for (let i = 0; i < lines.length; i++) {
    const t = lines[i].trim();
    if (t.startsWith("|") && i + 1 < lines.length && isSep(lines[i + 1].trim())) {
      flush();
      const head = cells(t); i += 2;
      const rows = [];
      while (i < lines.length && lines[i].trim().startsWith("|")) { rows.push(cells(lines[i])); i++; }
      i--;
      out.push(
        <table key={"t" + k++}>
          <thead><tr>{head.map((h, j) => <th key={j}>{_aiInline(h)}</th>)}</tr></thead>
          <tbody>{rows.map((r, ri) => <tr key={ri}>{r.map((c, ci) => <td key={ci}>{_aiInline(c)}</td>)}</tr>)}</tbody>
        </table>
      );
      continue;
    }
    if (/^#{1,6}\s/.test(t)) { flush(); out.push(<h3 key={"h" + k++}>{_aiInline(t.replace(/^#{1,6}\s/, ""))}</h3>); continue; }
    if (/^[-*]\s/.test(t)) { (list = list || []).push(t.replace(/^[-*]\s/, "")); continue; }
    if (t === "") { flush(); continue; }
    flush(); out.push(<p key={"p" + k++}>{_aiInline(t)}</p>);
  }
  flush();
  return <div className="aiw-md">{out}</div>;
}

function AiWidget() {
  const [open, setOpen] = useState(false);
  const [msgs, setMsgs] = useState([
    { role: "ai", text: "السلام عليكم، أنا مساعد ميزان الذكي. أُحلّل الأسهم (حلال + فني + أساسي) وأفكّر — لست مجرد قارئ بيانات. اسألني.", tools: [] },
  ]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const convId = useRef(null);
  const endRef = useRef(null);

  useEffect(() => { if (endRef.current) endRef.current.scrollIntoView({ behavior: "smooth" }); }, [msgs, open]);

  const send = async (text) => {
    const q = (text != null ? text : input).trim();
    if (!q || busy) return;
    setInput("");
    setMsgs((m) => [...m, { role: "user", text: q }]);
    setBusy(true);
    try {
      const r = await fetch("/agent/chat", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: q, conversation_id: convId.current }),
      });
      const j = await r.json();
      if (j && j.error) {
        setMsgs((m) => [...m, { role: "ai", text: "تعذّر: " + j.error, tools: [] }]);
      } else {
        if (j.conversation_id) convId.current = j.conversation_id;
        setMsgs((m) => [...m, { role: "ai", text: j.response || "—", tools: j.tools_used || [], model: j.model }]);
      }
    } catch (e) {
      setMsgs((m) => [...m, { role: "ai", text: "خطأ في الشبكة.", tools: [] }]);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="aiw-root" dir="rtl">
      {/* Floating animated bubble */}
      <button className={"aiw-fab" + (open ? " aiw-fab-open" : "")} onClick={() => setOpen(!open)}
              title="مساعد ميزان الذكي" aria-label="MizanAI">
        <span className="aiw-fab-pulse"></span>
        <i className={"fas " + (open ? "fa-times" : "fa-robot")}></i>
      </button>

      {/* Slide-out chat panel */}
      {open && (
        <div className="aiw-panel">
          <div className="aiw-head">
            <span><i className="fas fa-robot"></i> MizanAI · <small>DeepSeek · يفكّر ويحلّل</small></span>
            <button className="aiw-x" onClick={() => setOpen(false)}>✕</button>
          </div>

          <div className="aiw-body">
            {msgs.map((m, i) => (
              <div key={i} className={"aiw-msg aiw-" + m.role}>
                <div className="aiw-bubble">{m.role === "ai" ? renderMarkdown(m.text) : m.text}</div>
                {m.tools && m.tools.length > 0 && (
                  <div className="aiw-tools">🔧 {m.tools.join(" · ")}</div>
                )}
              </div>
            ))}
            {busy && <div className="aiw-msg aiw-ai"><div className="aiw-bubble aiw-think">يفكّر ويحلّل…</div></div>}
            <div ref={endRef}></div>
          </div>

          <div className="aiw-chips">
            {AI_QUICK.map((c, i) => (
              <button key={i} className="aiw-chip" disabled={busy} onClick={() => send(c.q)}>{c.label}</button>
            ))}
          </div>

          <div className="aiw-input">
            <input value={input} disabled={busy} placeholder="اسأل عن سهم، تحليل، حلال، السوق…"
                   onChange={(e) => setInput(e.target.value)}
                   onKeyDown={(e) => { if (e.key === "Enter") send(); }} />
            <button onClick={() => send()} disabled={busy}><i className="fas fa-paper-plane"></i></button>
          </div>

          <div className="aiw-disc">إرشادي — إشارات كمّية، ليست نصيحة مرخّصة. القرار لك.</div>
        </div>
      )}
    </div>
  );
}

Object.assign(window, { AiWidget });
