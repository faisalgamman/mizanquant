// LowerRow.jsx — Models leaderboard · Sectors heatmap · AI Consensus

function ModelLeaderboard({ models }) {
  return (
    <div className="ml-grid">
      {models.map((m) => (
        <div key={m.name} className={"ml-card " + m.family}>
          <div className="ml-top">
            <span className="ml-name">{m.name}</span>
            <span className={"ml-status " + m.status}>{m.status}</span>
          </div>
          <div className="ml-metrics">
            <div className="ml-metric"><span className="ml-mval">{m.sharpe.toFixed(2)}</span><span className="ml-mlab">Sharpe</span></div>
            <div className="ml-metric"><span className="ml-mval txt-positive">+{m.return.toFixed(1)}%</span><span className="ml-mlab">Return</span></div>
            <div className="ml-metric"><span className="ml-mval" style={{ color: "var(--text-muted)", fontSize: 11 }}>{m.family}</span><span className="ml-mlab">Family</span></div>
          </div>
        </div>
      ))}
    </div>
  );
}

function SectorHeatmap({ sectors }) {
  const tile = (s) => {
    const abs = Math.abs(s.chg);
    const alpha = Math.min(0.45, 0.15 + abs / 5 * 0.30).toFixed(2);
    const bg = s.chg >= 0
      ? `rgba(74, 222, 128, ${alpha})`
      : `rgba(248, 113, 113, ${alpha})`;
    const color = s.chg >= 0 ? "var(--positive)" : "var(--negative)";
    return (
      <div key={s.name} className={"sec-tile" + (s.halal ? "" : " haram")} style={{ background: bg }}>
        <div className="sec-name">{s.name}</div>
        <div className="sec-chg" style={{ color }}>{fmtPct(s.chg, 1)}</div>
      </div>
    );
  };
  return (
    <>
      <div className="sec-grid">{sectors.map(tile)}</div>
      <div className="sec-legend">
        <span className="sw" style={{ background: "rgba(248,113,113,0.45)" }}></span>−2.5%
        <span className="sw" style={{ background: "rgba(248,113,113,0.20)" }}></span>−1%
        <span className="sw" style={{ background: "var(--bg-raised)" }}></span>0
        <span className="sw" style={{ background: "rgba(74,222,128,0.20)" }}></span>+1%
        <span className="sw" style={{ background: "rgba(74,222,128,0.45)" }}></span>+2.5%
        <span>· dashed = haram</span>
      </div>
    </>
  );
}

function ConsensusPanel({ signal, votes }) {
  if (!signal) {
    return (
      <div className="analyze-empty">
        <i className="fas fa-vote-yea"></i>
        Select a signal for AI consensus
      </div>
    );
  }
  const counts = { BUY: 0, WAIT: 0, SELL: 0 };
  votes.forEach((v) => { counts[v.v] = (counts[v.v] || 0) + 1; });
  const total = votes.length;
  const max = Math.max(counts.BUY, counts.WAIT, counts.SELL);
  const verdict = max === counts.BUY ? "BUY" : max === counts.SELL ? "SELL" : "HOLD";
  const conf = Math.round((max / total) * 100);
  const headKind = verdict === "BUY" ? "buy" : verdict === "SELL" ? "sell" : "hold";
  return (
    <div>
      <div className={"cn-head " + headKind}>
        <div>
          <div className="cn-sym">{signal.symbol}</div>
          <div className="cn-sub">14-model consensus</div>
        </div>
        <div>
          <div className="cn-conf">{conf}%</div>
          <div className="cn-conflab">{verdict}</div>
        </div>
      </div>
      <div className="cn-sum">
        <div className="cn-sum-cell buy"><div className="cn-sum-val">{counts.BUY}</div><div className="cn-sum-lab">Buy</div></div>
        <div className="cn-sum-cell hold"><div className="cn-sum-val">{counts.WAIT}</div><div className="cn-sum-lab">Wait</div></div>
        <div className="cn-sum-cell sell"><div className="cn-sum-val">{counts.SELL}</div><div className="cn-sum-lab">Sell</div></div>
      </div>
      {votes.slice(0, 8).map((v, i) => (
        <div key={i} className="cn-vote">
          <span className="model">{v.model}</span>
          <span className={"v-pill " + (v.v === "BUY" ? "b-green" : v.v === "WAIT" ? "b-amber" : "b-red")}
                style={{ background: v.v === "BUY" ? "var(--positive-dim)" : v.v === "WAIT" ? "var(--warning-dim)" : "var(--negative-dim)",
                         color:      v.v === "BUY" ? "var(--positive)"     : v.v === "WAIT" ? "var(--warning)"     : "var(--negative)" }}>
            {v.v}
          </span>
        </div>
      ))}
      <div style={{ fontSize: 9, color: "var(--text-muted)", textAlign: "center", marginTop: 6 }}>+ {votes.length - 8} more</div>
    </div>
  );
}

function LowerRow({ models, sectors, signal, votes }) {
  return (
    <div className="lower">
      <div className="col">
        <div className="wf-section">
          <div className="wf-head"><span className="wf-title">Models</span><span className="wf-sub">Forecast leaderboard</span></div>
          <ModelLeaderboard models={models} />
        </div>
      </div>
      <div className="col">
        <div className="wf-section">
          <div className="wf-head"><span className="wf-title">Sectors</span><span className="wf-sub">11 sector ETFs · halal-flagged</span></div>
          <SectorHeatmap sectors={sectors} />
        </div>
      </div>
      <div className="col">
        <div className="wf-section">
          <div className="wf-head"><span className="wf-title">Consensus</span><span className="wf-sub">{signal ? signal.symbol : "Select a signal"}</span></div>
          <ConsensusPanel signal={signal} votes={votes} />
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { LowerRow });
