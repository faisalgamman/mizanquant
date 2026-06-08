// AiWidget.jsx — floating MizanAI assistant on the dashboard (agentic: reasons + uses analysis tools)
const { useState, useEffect, useRef } = React;

const AI_QUICK = [
  { label: "أفضل الفرص الحلال", q: "ما أفضل الفرص الحلال اليوم؟ حلّل أقواها." },
  { label: "حلّل سهماً", q: "حلّل AAPL تحليلاً كاملاً: حلال، فني، إجماع، ونقاط دخول/وقف/هدف." },
  { label: "حالة السوق", q: "ما حالة السوق الآن (Regime/VIX/Credit)؟" },
  { label: "محفظتي", q: "اعرض حالة محفظتي والمخاطر." },
];

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
                <div className="aiw-bubble">{m.text}</div>
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
