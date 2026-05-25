// Step1NDA.jsx — NDA review + canvas signature

function Step1NDA({ data, setData, onContinue }) {
  const canvasRef = useRef(null);
  const [hasInk, setHasInk] = useState(!!data.signature);
  const [drawing, setDrawing] = useState(false);

  // Hand-roll a tiny canvas signature pad
  useEffect(() => {
    const c = canvasRef.current;
    if (!c) return;
    // Pixel-density crisp setup
    const dpr = window.devicePixelRatio || 1;
    const rect = c.getBoundingClientRect();
    c.width = rect.width * dpr;
    c.height = rect.height * dpr;
    const ctx = c.getContext("2d");
    ctx.scale(dpr, dpr);
    ctx.lineWidth = 2;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    ctx.strokeStyle = "#c8963e";

    // If we already have data, restore
    if (data.signature) {
      const img = new Image();
      img.onload = () => ctx.drawImage(img, 0, 0, rect.width, rect.height);
      img.src = data.signature;
    }
  }, []);

  const getPos = (e) => {
    const c = canvasRef.current;
    const rect = c.getBoundingClientRect();
    const x = (e.touches ? e.touches[0].clientX : e.clientX) - rect.left;
    const y = (e.touches ? e.touches[0].clientY : e.clientY) - rect.top;
    return { x, y };
  };
  const onDown = (e) => {
    e.preventDefault();
    const ctx = canvasRef.current.getContext("2d");
    const { x, y } = getPos(e);
    ctx.beginPath();
    ctx.moveTo(x, y);
    setDrawing(true);
  };
  const onMove = (e) => {
    if (!drawing) return;
    e.preventDefault();
    const ctx = canvasRef.current.getContext("2d");
    const { x, y } = getPos(e);
    ctx.lineTo(x, y);
    ctx.stroke();
    if (!hasInk) setHasInk(true);
  };
  const onUp = () => {
    if (!drawing) return;
    setDrawing(false);
    // Persist as data URL
    const url = canvasRef.current.toDataURL("image/png");
    setData({ ...data, signature: url });
  };
  const clear = () => {
    const c = canvasRef.current;
    const ctx = c.getContext("2d");
    ctx.clearRect(0, 0, c.width, c.height);
    setHasInk(false);
    setData({ ...data, signature: null });
  };

  const toggleAck = (key) => {
    setData({ ...data, [key]: !data[key] });
  };

  const allAcked = data.ackTerms && data.ackJurisdiction && data.ackElectronic;
  const ready = allAcked && data.signature && data.signatoryName && data.signatoryTitle;

  return (
    <>
      <StepHeader
        eyebrow="Step 01 of 04 · Non-disclosure agreement"
        title="Mutual NDA · execute to "
        accentTitle="proceed."
        lead="A standard mutual non-disclosure agreement between MizanQuant and your institution. The full counterparty draft has been emailed to your nominated counsel. The text below is binding once signed below."
      />

      <div className="nda-doc">
        <h4>1 · Parties &amp; recital</h4>
        <p>
          <span className="number">1.1</span>
          This Mutual Non-Disclosure Agreement ("Agreement") is entered into between <strong>MizanQuant FZE</strong> (DIFC, Dubai, United Arab Emirates) and the <strong>Institutional Partner</strong> identified at execution below ("Counterparty"). Each party may be referred to individually as a "Party" and collectively as the "Parties".
        </p>
        <p>
          <span className="number">1.2</span>
          The Parties wish to enter into discussions concerning a potential business relationship relating to the MizanQuant platform — a Sharia-compliant quantitative trading suite operated under AAOIFI Standard 21 (the "Purpose"). In the course of such discussions, each Party may disclose Confidential Information to the other.
        </p>

        <h4>2 · Confidential information</h4>
        <p>
          <span className="number">2.1</span>
          "Confidential Information" shall mean any information disclosed by one Party to the other, in any form, that is marked as confidential or that a reasonable person would understand to be confidential. This includes, without limitation: model architectures, AAOIFI screening logic, AI consensus weights, Kelly position-sizing parameters, risk-desk thresholds, backtest results, client lists, and pricing.
        </p>
        <p>
          <span className="number">2.2</span>
          The receiving Party shall hold Confidential Information in strict confidence and shall not disclose it to any third party except to its directors, officers, employees, and professional advisors (collectively "Representatives") who have a need to know for the Purpose, and who are themselves bound by obligations of confidentiality at least as protective.
        </p>

        <h4>3 · Term &amp; survival</h4>
        <p>
          <span className="number">3.1</span>
          This Agreement shall remain in effect for a period of <strong>three (3) years</strong> from the date of last signature, after which the confidentiality obligations herein shall continue to apply to Confidential Information disclosed prior to expiration for a further period of five (5) years.
        </p>

        <h4>4 · Sharia carve-out</h4>
        <p>
          <span className="number">4.1</span>
          Notwithstanding any other clause, the receiving Party may disclose the Confidential Information to a Sharia board, fatwa committee, or AAOIFI auditor reviewing the receiving Party's own activities, provided that such recipient is bound by professional confidentiality.
        </p>

        <h4>5 · Governing law</h4>
        <p>
          <span className="number">5.1</span>
          This Agreement shall be governed by the laws of the Dubai International Financial Centre (DIFC). Any dispute arising out of or in connection with this Agreement shall be referred to the DIFC Courts for exclusive jurisdiction.
        </p>
      </div>

      <SectionHead num="01 / SIG" title="Authorised signatory" aside="Bound officer of the Counterparty" />

      <div className="field-row">
        <div className="field">
          <label className="field-label">Full legal name <span className="field-required">*</span></label>
          <input
            className="field-input"
            type="text"
            placeholder="e.g. Faisal Al-Mansouri"
            value={data.signatoryName || ""}
            onChange={(e) => setData({ ...data, signatoryName: e.target.value })}
          />
        </div>
        <div className="field">
          <label className="field-label">Title &amp; capacity <span className="field-required">*</span></label>
          <input
            className="field-input"
            type="text"
            placeholder="e.g. Chief Investment Officer"
            value={data.signatoryTitle || ""}
            onChange={(e) => setData({ ...data, signatoryTitle: e.target.value })}
          />
        </div>
      </div>

      <div className="sig-row">
        <div>
          <div className="field-label" style={{ marginBottom: 8 }}>Wet signature <span className="field-required">*</span></div>
          <div className={"sig-pad" + (hasInk ? " has-ink" : "")}>
            <canvas
              ref={canvasRef}
              onMouseDown={onDown} onMouseMove={onMove}
              onMouseUp={onUp}     onMouseLeave={onUp}
              onTouchStart={onDown} onTouchMove={onMove}
              onTouchEnd={onUp}
            ></canvas>
            <div className="placeholder">Sign here</div>
            <div className="baseline"></div>
            <div className="label">Drawn signature · binding</div>
            {hasInk && (
              <button className="clear" onClick={clear}>Clear</button>
            )}
          </div>
        </div>
        <div className="sig-info">
          <div>
            <div className="lab">Date of execution</div>
            <div className="val">{new Date().toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" })}</div>
          </div>
          <div>
            <div className="lab">Counterparty reference</div>
            <div className="val">{data.refNumber}</div>
          </div>
          <div>
            <div className="lab">Audit trail</div>
            <div className="val" style={{ fontSize: 11, color: "var(--text-secondary)", letterSpacing: 0.2 }}>
              IP · 185.83.144.•••<br />
              Browser fingerprint logged<br />
              Stored 7 years (DIFC)
            </div>
          </div>
        </div>
      </div>

      <div className="sig-checks">
        <label className={"sig-check" + (data.ackTerms ? " checked" : "")}>
          <input type="checkbox" checked={!!data.ackTerms} onChange={() => toggleAck("ackTerms")} />
          <span>I confirm I have read and agree to all five clauses of the NDA above, including the 3-year confidentiality term and 5-year survival period.</span>
        </label>
        <label className={"sig-check" + (data.ackJurisdiction ? " checked" : "")}>
          <input type="checkbox" checked={!!data.ackJurisdiction} onChange={() => toggleAck("ackJurisdiction")} />
          <span>I accept the exclusive jurisdiction of the DIFC Courts and the governing law as stated in clause 5.1.</span>
        </label>
        <label className={"sig-check" + (data.ackElectronic ? " checked" : "")}>
          <input type="checkbox" checked={!!data.ackElectronic} onChange={() => toggleAck("ackElectronic")} />
          <span>I consent to electronic execution under the UAE Electronic Transactions Law (Federal Decree-Law No. 46 of 2021) and confirm I am duly authorised to bind my institution.</span>
        </label>
      </div>
    </>
  );
}

window.Step1NDA = Step1NDA;
