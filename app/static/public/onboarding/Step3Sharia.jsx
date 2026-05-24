// Step3Sharia.jsx — Sharia board review (passive observe state + simulated approval)

const REVIEWERS = [
  { id: "as",  name: "Dr. Ahmad Al-Sayed",      role: "Chair · AAOIFI-accredited",  initials: "AS" },
  { id: "fa",  name: "Sh. Faiz Abdulrahman",    role: "Vice-chair · Hanafi jurist", initials: "FA" },
  { id: "nq",  name: "Dr. Noura Al-Qahtani",    role: "Member · IF/AAOIFI",         initials: "NQ" },
  { id: "ya",  name: "Sh. Yusuf Al-Maliki",     role: "Member · Maliki jurist",     initials: "YA" },
  { id: "kr",  name: "Dr. Khalid Al-Rashid",    role: "Secretary · AAOIFI",         initials: "KR" },
];

function Step3Sharia({ data, setData }) {
  const [progress, setProgress] = useState(data.progress || 8);
  const [phase, setPhase]       = useState(data.phase    || "submitted");  // submitted | reviewing | approved
  const [statuses, setStatuses] = useState(data.reviewerStatuses || {
    as: "reviewing", fa: "pending", nq: "pending", ya: "pending", kr: "pending"
  });
  const intervalRef = useRef(null);

  // Auto-advance the review simulation (1 reviewer ~ every 2.4s)
  useEffect(() => {
    if (phase === "approved") return;
    intervalRef.current = setInterval(() => {
      setProgress((p) => Math.min(96, p + 4 + Math.random() * 5));
      setStatuses((prev) => {
        const order = ["as", "fa", "nq", "ya", "kr"];
        // Find first non-approved
        const next = order.find((id) => prev[id] !== "approved");
        if (!next) return prev;
        const updated = { ...prev };
        // current "reviewing" → approved; next pending → reviewing
        const cur = order.find((id) => prev[id] === "reviewing");
        if (cur) updated[cur] = "approved";
        const after = order.findIndex((id) => id === cur);
        if (after >= 0 && after < order.length - 1) {
          updated[order[after + 1]] = "reviewing";
        }
        return updated;
      });
    }, 2400);
    return () => clearInterval(intervalRef.current);
  }, [phase]);

  // When all 5 approved, switch phase
  useEffect(() => {
    const allApproved = Object.values(statuses).every((s) => s === "approved");
    if (allApproved && phase !== "approved") {
      setPhase("approved");
      setProgress(100);
      setData({ ...data, phase: "approved", progress: 100, reviewerStatuses: statuses });
    } else {
      setData({ ...data, phase, progress, reviewerStatuses: statuses });
    }
    // eslint-disable-next-line
  }, [statuses]);

  const isApproved = phase === "approved";

  return (
    <>
      <StepHeader
        eyebrow="Step 03 of 04 · Sharia board review"
        title="Submitted to the "
        accentTitle="Sharia board."
        lead="Your submission has been routed to MizanQuant's standing Sharia board for review against AAOIFI Standard 21 and the platform's internal compliance framework. The board's verdict is binding."
      />

      <div className="sb-grid">
        <div className={"sb-status" + (isApproved ? " approved" : "")}>
          <div className="sb-status-head">
            <div className={"sb-status-dot" + (isApproved ? " approved" : "")}></div>
            <div className="sb-status-lab">
              {isApproved ? "Approval issued" : phase === "reviewing" ? "Review in progress" : "Submission received"}
            </div>
          </div>
          <div className="sb-status-title">
            {isApproved ? "Sharia compliance approved." : "Awaiting Sharia board verdict…"}
          </div>
          <div className="sb-status-desc">
            {isApproved
              ? "All five board members have signed off. A formal fatwa certificate has been generated and will be attached to your terminal credentials."
              : "The board is reviewing the institutional profile, intended deployment scope, and Sharia governance attestation. Median review time: 18 hours. Live progress below."}
          </div>

          <div className="sb-progress"><div style={{ width: progress + "%" }}></div></div>

          <div className="sb-meta">
            <div>FATWA REF</div>
            <div className="v">{isApproved ? "FW-MZN-2026-" + (data.refNumber || "").split("-").pop() : "—"}</div>

            <div>OPENED</div>
            <div className="v">{new Date().toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" })}</div>

            <div>STANDARD</div>
            <div className="v">AAOIFI Standard 21</div>

            <div>VERDICT</div>
            <div className="v" style={{ color: isApproved ? "var(--positive)" : "var(--accent)" }}>
              {isApproved ? "Approved · unanimous" : "Pending"}
            </div>
          </div>
        </div>

        <div className="sb-reviewers">
          <div className="sb-reviewers-title">Sharia board · {REVIEWERS.length} members</div>
          {REVIEWERS.map((r) => {
            const s = statuses[r.id] || "pending";
            return (
              <div key={r.id} className="sb-reviewer">
                <div className="avatar">{r.initials}</div>
                <div className="info">
                  <div className="name">{r.name}</div>
                  <div className="role">{r.role}</div>
                </div>
                <span className={"stat " + s}>
                  {s === "approved" ? "✓ approved" : s === "reviewing" ? "reviewing" : "pending"}
                </span>
              </div>
            );
          })}
        </div>
      </div>

      {isApproved && (
        <div className="attestation">
          <div className="attestation-head">
            <i className="fas fa-mosque" style={{ marginRight: 8 }}></i>
            Attestation of Sharia compliance
          </div>
          <div className="attestation-body">
            <p dir="rtl" lang="ar" style={{ fontFamily: "var(--font-arabic)", fontSize: 14, lineHeight: 1.7, color: "var(--text-primary)" }}>
              يَشهد مجلس الرقابة الشرعية بأن هيكلية منصة <strong>ميزان كوانت</strong> ومنهجية التداول الكَمّي المتبعة فيها متوافقة مع معايير AAOIFI، ولا تتضمن أي ربا أو غرر فاحش أو نشاط محرّم. هذه الفتوى صادرة بتاريخ تنفيذ هذا التأهيل.
            </p>
            <p>
              <em>The Sharia supervisory board attests that the architecture of the <strong>MizanQuant</strong> platform and the quantitative trading methodology employed are compliant with AAOIFI standards and contain no riba, excessive gharar, or prohibited activity. This fatwa is issued as of the date of onboarding execution.</em>
            </p>
            <div className="attestation-meta">
              <span>Fatwa ref · FW-MZN-2026-{(data.refNumber || "").split("-").pop()}</span>
              <span>Unanimous · 5 of 5</span>
            </div>
          </div>
        </div>
      )}

      {!isApproved && (
        <div style={{ marginTop: 18, textAlign: "center" }}>
          <button
            className="btn btn-ghost"
            onClick={() => {
              // demo: skip wait
              setStatuses({ as: "approved", fa: "approved", nq: "approved", ya: "approved", kr: "approved" });
            }}
            style={{ fontSize: 11 }}
          >
            <i className="fas fa-forward" style={{ fontSize: 10 }}></i>
            Demo · skip review wait
          </button>
        </div>
      )}
    </>
  );
}

window.Step3Sharia = Step3Sharia;
