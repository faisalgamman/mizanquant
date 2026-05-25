// LowerRow.jsx — Models leaderboard · Sectors heatmap · AI Consensus

function ModelLeaderboard({ models }) {
  if (!models || models.length === 0) {
    return (
      <div className="analyze-empty">
        <i className="fas fa-flask"></i>
        No validated models yet · run a backtest to populate
      </div>
    );
  }
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

function ConsensusPanel({ signal, consensus }) {
  if (!signal) {
    return (
      <div className="analyze-empty">
        <i className="fas fa-vote-yea"></i>
        Select a signal for AI consensus
      </div>
    );
  }
  // Real consensus only — no fabricated votes. Honest empty state if none recorded.
  if (!consensus || consensus.available === false) {
    return (
      <div className="analyze-empty">
        <i className="fas fa-vote-yea"></i>
        No consensus recorded for {signal.symbol} yet · runs each pipeline cycle
      </div>
    );
  }
  const buy  = consensus.votes_buy  || 0;
  const wait = consensus.votes_hold || 0;
  const sell = consensus.votes_sell || 0;
  const total = buy + wait + sell;
  const verdict = (consensus.verdict ||
    (buy >= sell && buy >= wait ? "BUY" : sell >= buy && sell >= wait ? "SELL" : "HOLD")).toUpperCase();
  const conf = consensus.confidence != null
    ? Math.round(consensus.confidence)
    : (total ? Math.round((Math.max(buy, wait, sell) / total) * 100) : 0);
  const headKind = /buy/.test(verdict.toLowerCase()) ? "buy"
                 : /sell/.test(verdict.toLowerCase()) ? "sell" : "hold";

  // Surface a per-model breakdown only if the details JSON actually carries one.
  let modelVotes = [];
  const d = consensus.details;
  if (Array.isArray(d)) modelVotes = d;
  else if (d && Array.isArray(d.models)) modelVotes = d.models;
  else if (d && Array.isArray(d.votes)) modelVotes = d.votes;
  const normVote = (v) => {
    const name = v.model || v.name || v.id || "model";
    const vote = (v.vote || v.v || v.verdict || "").toString().toUpperCase();
    return { name, vote };
  };
  const rows = modelVotes.map(normVote).filter(r => r.vote);

  return (
    <div>
      <div className={"cn-head " + headKind}>
        <div>
          <div className="cn-sym">{signal.symbol}</div>
          <div className="cn-sub">{total > 0 ? total + "-vote consensus" : "consensus"}</div>
        </div>
        <div>
          <div className="cn-conf">{conf}%</div>
          <div className="cn-conflab">{verdict}</div>
        </div>
      </div>
      <div className="cn-sum">
        <div className="cn-sum-cell buy"><div className="cn-sum-val">{buy}</div><div className="cn-sum-lab">Buy</div></div>
        <div className="cn-sum-cell hold"><div className="cn-sum-val">{wait}</div><div className="cn-sum-lab">Wait</div></div>
        <div className="cn-sum-cell sell"><div className="cn-sum-val">{sell}</div><div className="cn-sum-lab">Sell</div></div>
      </div>
      {rows.length > 0 ? (
        <>
          {rows.slice(0, 8).map((v, i) => (
            <div key={i} className="cn-vote">
              <span className="model">{v.name}</span>
              <span className={"v-pill " + (v.vote === "BUY" ? "b-green" : v.vote === "WAIT" || v.vote === "HOLD" ? "b-amber" : "b-red")}
                    style={{ background: v.vote === "BUY" ? "var(--positive-dim)" : (v.vote === "WAIT" || v.vote === "HOLD") ? "var(--warning-dim)" : "var(--negative-dim)",
                             color:      v.vote === "BUY" ? "var(--positive)"     : (v.vote === "WAIT" || v.vote === "HOLD") ? "var(--warning)"     : "var(--negative)" }}>
                {v.vote}
              </span>
            </div>
          ))}
          {rows.length > 8 && (
            <div style={{ fontSize: 9, color: "var(--text-muted)", textAlign: "center", marginTop: 6 }}>+ {rows.length - 8} more</div>
          )}
        </>
      ) : (
        <div style={{ fontSize: 9, color: "var(--text-muted)", textAlign: "center", marginTop: 8 }}>
          Aggregate of technical + AI ensemble votes
          {consensus.ts ? " · " + new Date(consensus.ts).toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : ""}
        </div>
      )}
    </div>
  );
}

function LowerRow({ models, sectors, signal, consensus }) {
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
          <ConsensusPanel signal={signal} consensus={consensus} />
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { LowerRow });
