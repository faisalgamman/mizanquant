// Step2KYC.jsx — Institutional KYC form

function Step2KYC({ data, setData }) {
  const update = (k, v) => setData({ ...data, [k]: v });
  const toggleDoc = (id) => {
    const docs = { ...(data.docs || {}) };
    docs[id] = !docs[id];
    setData({ ...data, docs });
  };
  const docs = data.docs || {};

  const documents = [
    { id: "incorp",   name: "Certificate of incorporation", desc: "Issued by the home jurisdiction · last 6 months",   req: true },
    { id: "passport", name: "Authorised signatory passport", desc: "Colour scan · machine-readable zone visible",       req: true },
    { id: "address",  name: "Proof of registered address",  desc: "Utility bill · bank statement · last 3 months",     req: true },
    { id: "sharia",   name: "Sharia compliance certificate", desc: "From your existing fatwa committee · if applicable", req: false },
    { id: "auth",     name: "Board resolution / power of attorney", desc: "Authorising signatory to bind the institution", req: true },
  ];

  return (
    <>
      <StepHeader
        eyebrow="Step 02 of 04 · Institutional KYC"
        title="Know-your-counterparty · "
        accentTitle="institutional gate."
        lead="Standard institutional KYC consistent with UAE Central Bank, ADGM, and DIFC requirements. All submissions are encrypted at rest (AES-256) and reviewed by the client desk within two business days."
      />

      <Alert kind="accent" icon="fa-shield-halved">
        <strong>End-to-end encrypted.</strong> Submitted data is encrypted in transit (TLS 1.3) and at rest. Beneficial ownership disclosures are reviewed only by the named compliance officer.
      </Alert>

      <SectionHead num="01 / ENT" title="Entity profile" aside="Bound legal entity" />

      <div className="field">
        <label className="field-label">Institution type <span className="field-required">*</span></label>
        <select className="field-input" value={data.entType || ""} onChange={(e) => update("entType", e.target.value)}>
          <option value="">Select…</option>
          <option>Sovereign wealth fund</option>
          <option>Family office</option>
          <option>Sharia-compliant asset manager</option>
          <option>Pension / endowment</option>
          <option>Multi-strategy hedge fund</option>
          <option>Corporate treasury</option>
          <option>Other (specify in notes)</option>
        </select>
      </div>

      <div className="field-row">
        <div className="field">
          <label className="field-label">Registered legal name <span className="field-required">*</span></label>
          <input className="field-input" type="text" placeholder="e.g. Al Mizan Capital Partners LLC"
                 value={data.entName || ""} onChange={(e) => update("entName", e.target.value)} />
        </div>
        <div className="field">
          <label className="field-label">Trading name / brand</label>
          <input className="field-input" type="text" placeholder="if different"
                 value={data.entTrading || ""} onChange={(e) => update("entTrading", e.target.value)} />
        </div>
      </div>

      <div className="field-row-3">
        <div className="field">
          <label className="field-label">Jurisdiction <span className="field-required">*</span></label>
          <select className="field-input" value={data.juris || ""} onChange={(e) => update("juris", e.target.value)}>
            <option value="">Select…</option>
            <option>United Arab Emirates · DIFC</option>
            <option>United Arab Emirates · ADGM</option>
            <option>Kingdom of Saudi Arabia</option>
            <option>Qatar (QFC)</option>
            <option>Kuwait</option>
            <option>Bahrain</option>
            <option>Oman</option>
            <option>Singapore</option>
            <option>United Kingdom</option>
            <option>Other</option>
          </select>
        </div>
        <div className="field">
          <label className="field-label">Year established <span className="field-required">*</span></label>
          <input className="field-input" type="number" min="1900" max="2026" placeholder="2018"
                 value={data.year || ""} onChange={(e) => update("year", e.target.value)} />
        </div>
        <div className="field">
          <label className="field-label">Regulator</label>
          <input className="field-input" type="text" placeholder="e.g. DFSA / CMA / FCA"
                 value={data.regulator || ""} onChange={(e) => update("regulator", e.target.value)} />
        </div>
      </div>

      <div className="field-row">
        <div className="field">
          <label className="field-label">AUM bracket (USD) <span className="field-required">*</span></label>
          <select className="field-input" value={data.aum || ""} onChange={(e) => update("aum", e.target.value)}>
            <option value="">Select…</option>
            <option>Under $50M</option>
            <option>$50M – $250M</option>
            <option>$250M – $1B</option>
            <option>$1B – $5B</option>
            <option>$5B – $25B</option>
            <option>Over $25B</option>
          </select>
        </div>
        <div className="field">
          <label className="field-label">Target deployment <span className="field-required">*</span></label>
          <select className="field-input" value={data.deploy || ""} onChange={(e) => update("deploy", e.target.value)}>
            <option value="">Select…</option>
            <option>$1M – $5M sleeve</option>
            <option>$5M – $25M sleeve</option>
            <option>$25M – $100M sleeve</option>
            <option>$100M+ mandate</option>
            <option>Evaluation only</option>
          </select>
        </div>
      </div>

      <SectionHead num="02 / CMP" title="Compliance &amp; Sharia governance" aside="Required" />

      <div className="field">
        <label className="field-label">Compliance officer · email <span className="field-required">*</span></label>
        <input className="field-input" type="email" placeholder="compliance@institution.com"
               value={data.compEmail || ""} onChange={(e) => update("compEmail", e.target.value)} />
        <div className="field-hint">Disclosures and the daily audit report will be CC'd here.</div>
      </div>

      <div className="field-row">
        <div className="field">
          <label className="field-label">Sharia board <span className="field-required">*</span></label>
          <select className="field-input" value={data.shariaBoard || ""} onChange={(e) => update("shariaBoard", e.target.value)}>
            <option value="">Select…</option>
            <option>Internal Sharia board</option>
            <option>External fatwa committee</option>
            <option>AAOIFI-accredited consultancy</option>
            <option>Will rely on MizanQuant's Sharia board</option>
            <option>No active board</option>
          </select>
        </div>
        <div className="field">
          <label className="field-label">Fatwa committee chair (name)</label>
          <input className="field-input" type="text" placeholder="optional"
                 value={data.fatwaChair || ""} onChange={(e) => update("fatwaChair", e.target.value)} />
        </div>
      </div>

      <SectionHead num="03 / DOC" title="Document upload" aside="Drag &amp; drop · max 12MB each" />

      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {documents.map((d) => (
          <div
            key={d.id}
            className={"upload" + (docs[d.id] ? " uploaded" : "")}
            onClick={() => toggleDoc(d.id)}
          >
            <div className="ico">
              <i className={"fas " + (docs[d.id] ? "fa-circle-check" : "fa-cloud-arrow-up")} style={{ fontSize: 14 }}></i>
            </div>
            <div className="info">
              <div className="name">
                {d.name}
                {d.req && <span style={{ color: "var(--accent)", marginLeft: 6, fontFamily: "var(--font-mono)", fontSize: 10 }}>*</span>}
              </div>
              <div className="desc">{d.desc}</div>
            </div>
            <span className="state">
              {docs[d.id] ? "Uploaded · 1.2MB · click to remove" : "Click to upload"}
            </span>
          </div>
        ))}
      </div>

      <SectionHead num="04 / NOTE" title="Additional notes" aside="Optional" />

      <div className="field">
        <textarea
          className="field-input"
          rows="3"
          placeholder="Any context the client desk should know in advance — existing mandates, related parties, restricted sectors, etc."
          value={data.notes || ""}
          onChange={(e) => update("notes", e.target.value)}
          style={{ resize: "vertical", fontFamily: "var(--font-body)" }}
        ></textarea>
      </div>
    </>
  );
}

window.Step2KYC = Step2KYC;
