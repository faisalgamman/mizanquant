// PriceChart.jsx — accurate price + volume chart (REAL OHLCV via /api/stock/chart). No deps.
const { useState, useEffect, useRef } = React;

const PC_RANGES = [
  { k: "5D", r: "5d" }, { k: "1M", r: "1mo" }, { k: "3M", r: "3mo" },
  { k: "6M", r: "6mo" }, { k: "YTD", r: "ytd" }, { k: "1Y", r: "1y" }, { k: "5Y", r: "5y" },
];

function _pcFmt(v, d = 2) {
  return (v == null || isNaN(v)) ? "—"
    : Number(v).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
}
function _pcVol(v) {
  if (v == null) return "—";
  if (v >= 1e9) return (v / 1e9).toFixed(1) + "B";
  if (v >= 1e6) return (v / 1e6).toFixed(1) + "M";
  if (v >= 1e3) return (v / 1e3).toFixed(0) + "K";
  return String(v);
}

function PriceChart({ symbol }) {
  const [range, setRange] = useState("6mo");
  const [bars, setBars] = useState(null);   // null=loading, []=no data
  const [hover, setHover] = useState(null);  // bar index under cursor
  const svgRef = useRef(null);

  useEffect(() => {
    if (!symbol) { setBars([]); return; }
    let alive = true;
    setBars(null); setHover(null);
    const fetchRange = range === "ytd" ? "1y" : range;   // YTD = 1y then client-filter to this year
    fetch(`/api/stock/chart?symbol=${encodeURIComponent(symbol)}&range=${fetchRange}`)
      .then(r => r.json())
      .then(j => {
        if (!alive) return;
        let b = (j && Array.isArray(j.bars)) ? j.bars : [];
        if (range === "ytd") {
          const y = new Date().getFullYear();
          b = b.filter(x => x.date && Number(String(x.date).slice(0, 4)) === y);
        }
        setBars(b);
      })
      .catch(() => { if (alive) setBars([]); });
    return () => { alive = false; };
  }, [symbol, range]);

  const W = 600, H = 200, padT = 8, padB = 36, padR = 52, padL = 4;
  const plotH = H - padT - padB;            // price plot height
  const volH = 26;                          // volume band height (sits in the padB region)
  const data = bars || [];
  const n = data.length;
  const closes = data.map(b => b.close);
  const vols = data.map(b => b.volume || 0);
  const lo = n ? Math.min(...closes) : 0;
  const hi = n ? Math.max(...closes) : 1;
  const padv = (hi - lo) * 0.06 || 1;
  const yMin = lo - padv, yMax = hi + padv;
  const volMax = n ? Math.max(...vols) : 1;
  const plotW = W - padL - padR;
  const x = (i) => padL + (n <= 1 ? plotW / 2 : (i / (n - 1)) * plotW);
  const y = (v) => padT + (1 - (v - yMin) / ((yMax - yMin) || 1)) * plotH;
  const volBase = (H - padB) + volH;        // volume baseline (zero)
  const vy = (v) => volBase - (v / (volMax || 1)) * volH;

  const first = closes[0], last = closes[n - 1];
  const up = (last ?? 0) >= (first ?? 0);
  const lineColor = up ? "var(--positive)" : "var(--negative)";
  const chg = (first && last) ? ((last / first - 1) * 100) : null;

  let linePath = "", areaPath = "";
  if (n > 0) {
    linePath = data.map((b, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(2)},${y(b.close).toFixed(2)}`).join(" ");
    areaPath = linePath + ` L${x(n - 1).toFixed(2)},${(padT + plotH).toFixed(2)} L${x(0).toFixed(2)},${(padT + plotH).toFixed(2)} Z`;
  }

  const onMove = (e) => {
    if (!svgRef.current || n === 0) return;
    const rect = svgRef.current.getBoundingClientRect();
    const px = (e.clientX - rect.left) / rect.width * W;
    let idx = Math.round((px - padL) / (plotW / Math.max(n - 1, 1)));
    setHover(Math.max(0, Math.min(n - 1, idx)));
  };

  const hb = (hover != null && data[hover]) ? data[hover] : null;
  const gid = "pcg-" + (up ? "u" : "d");
  const ticks = n ? [0, Math.floor(n * 0.25), Math.floor(n * 0.5), Math.floor(n * 0.75), n - 1]
    .filter((v, i, a) => a.indexOf(v) === i && data[v]) : [];

  return (
    <div className="wf-section sc-chart">
      <div className="wf-head">
        <span className="wf-title">
          <i className="fas fa-chart-area" style={{ marginRight: 6, color: "var(--accent)" }}></i>
          {symbol || "—"} · Price
        </span>
        <span className="wf-sub" style={{ display: "flex", gap: 10, alignItems: "center" }}>
          {last != null ? <b className="mono">{_pcFmt(last)}</b> : "—"}
          {chg != null ? (
            <span style={{ color: up ? "var(--positive)" : "var(--negative)" }}>
              {(chg >= 0 ? "+" : "") + chg.toFixed(2) + "%"}
            </span>
          ) : null}
        </span>
      </div>

      <div className="sc-range">
        {PC_RANGES.map(rr => (
          <button key={rr.k} className={"sc-range-chip" + (range === rr.r ? " active" : "")}
                  onClick={() => setRange(rr.r)}>{rr.k}</button>
        ))}
      </div>

      {bars === null ? (
        <div className="sc-chart-empty">جارٍ تحميل الشارت…</div>
      ) : n === 0 ? (
        <div className="sc-chart-empty">لا بيانات سعرية — "—"</div>
      ) : (
        <div className="sc-chart-wrap">
          <svg ref={svgRef} className="sc-chart-svg" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none"
               onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
            <defs>
              <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={up ? "rgba(74,222,128,0.22)" : "rgba(248,113,113,0.22)"} />
                <stop offset="100%" stopColor="rgba(0,0,0,0)" />
              </linearGradient>
            </defs>
            {data.map((b, i) => (
              <rect key={i} x={x(i) - 1} y={vy(b.volume || 0)} width={2}
                    height={Math.max(0, volBase - vy(b.volume || 0))} fill="var(--border-strong)" />
            ))}
            <path d={areaPath} fill={`url(#${gid})`} stroke="none" />
            <path d={linePath} fill="none" stroke={lineColor} strokeWidth={1.5} />
            {last != null ? (
              <line x1={padL} y1={y(last)} x2={W - padR} y2={y(last)} stroke={lineColor}
                    strokeWidth={0.8} strokeDasharray="3 3" opacity={0.7} />
            ) : null}
            {hb ? (
              <g>
                <line x1={x(hover)} y1={padT} x2={x(hover)} y2={padT + plotH}
                      stroke="var(--text-muted)" strokeWidth={0.7} />
                <circle cx={x(hover)} cy={y(hb.close)} r={2.5} fill={lineColor} />
              </g>
            ) : null}
            {ticks.map(i => (
              <text key={"t" + i} x={x(i)} y={H - 2} fill="var(--text-muted)" fontSize="7"
                    textAnchor="middle">{String(data[i].date).slice(5)}</text>
            ))}
          </svg>
          {last != null ? (
            <div className="sc-chart-last"
                 style={{ top: (y(last) / H * 100) + "%", color: lineColor, borderColor: lineColor }}>
              {_pcFmt(last)}
            </div>
          ) : null}
          {hb ? (
            <div className="sc-chart-tip">
              <b>{hb.date}</b> · O {_pcFmt(hb.open)} · H {_pcFmt(hb.high)} · L {_pcFmt(hb.low)} ·
              C <b>{_pcFmt(hb.close)}</b> · Vol {_pcVol(hb.volume)}
            </div>
          ) : null}
        </div>
      )}
      <div className="sc-chart-disc">بيانات يومية حقيقية من السوق — لا رقم إلا حقيقي.</div>
    </div>
  );
}

Object.assign(window, { PriceChart });
