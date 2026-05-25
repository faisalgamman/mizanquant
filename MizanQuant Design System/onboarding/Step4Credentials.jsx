// Step4Credentials.jsx — Terminal credentials issuance + QR + downloads

// Tiny faux-QR (deterministic SVG, looks like a QR but is purely cosmetic).
function FauxQR({ size = 110 }) {
  const cells = 21;
  // Seeded pseudo-random pattern (deterministic so it doesn't redraw on rerender)
  const seed = 92847;
  const rng = (i) => {
    const x = Math.sin(seed + i * 13.37) * 10000;
    return x - Math.floor(x);
  };
  const filled = (i) => rng(i) > 0.48;

  // Finder squares helper
  const isFinder = (r, c) => {
    const inBox = (rr, cc) => r >= rr && r < rr + 7 && c >= cc && c < cc + 7;
    return inBox(0, 0) || inBox(0, cells - 7) || inBox(cells - 7, 0);
  };
  const finderPattern = (r, c) => {
    const onBox = (rr, cc) =>
      ((r === rr || r === rr + 6) && c >= cc && c <= cc + 6) ||
      ((c === cc || c === cc + 6) && r >= rr && r <= rr + 6) ||
      (r >= rr + 2 && r <= rr + 4 && c >= cc + 2 && c <= cc + 4);
    return onBox(0, 0) || onBox(0, cells - 7) || onBox(cells - 7, 0);
  };

  const rects = [];
  for (let r = 0; r < cells; r++) {
    for (let c = 0; c < cells; c++) {
      let fill = false;
      if (isFinder(r, c)) fill = finderPattern(r, c);
      else fill = filled(r * cells + c);
      if (fill) rects.push(<rect key={r + "_" + c} x={c} y={r} width="1" height="1" fill="#0c0c10" />);
    }
  }
  return (
    <svg viewBox={`0 0 ${cells} ${cells}`} width={size} height={size} shapeRendering="crispEdges">
      <rect x="0" y="0" width={cells} height={cells} fill="#e4e4ec" />
      {rects}
    </svg>
  );
}

function CopyButton({ value }) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    if (navigator.clipboard) {
      navigator.clipboard.writeText(value).catch(() => {});
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 1400);
  };
  return (
    <button className={"copy" + (copied ? " copied" : "")} onClick={copy}>
      <i className={"fas " + (copied ? "fa-check" : "fa-copy")} style={{ marginRight: 4, fontSize: 9 }}></i>
      {copied ? "Copied" : "Copy"}
    </button>
  );
}

function Step4Credentials({ data }) {
  // Derive deterministic creds from the partner reference
  const credentials = useMemo(() => {
    const ref = data.refNumber || "MZN-INST-XXXXXX-2026Q2";
    const tail = ref.split("-").slice(-2, -1)[0];
    const username = "partner.al-mansouri@" + (data.entName || "alm").toLowerCase().replace(/[^a-z]/g, "").slice(0, 12) + ".com";
    const tempPwd  = "Mizan-" + tail + "-" + Math.floor(1000 + Math.random() * 9000);
    const apiKey   = "mzn_live_" + tail.toLowerCase() + "_" + Math.random().toString(36).slice(2, 10) + Math.random().toString(36).slice(2, 8);
    return {
      partnerId: ref,
      username:  username,
      tempPwd:   tempPwd,
      apiKey:    apiKey,
    };
  // eslint-disable-next-line
  }, [data.refNumber]);

  return (
    <>
      <StepHeader
        eyebrow="Step 04 of 04 · Credentials issuance"
        title="Welcome to the "
        accentTitle="terminal."
        lead="Your institution has been provisioned. Credentials below grant immediate access to the MizanQuant terminal, audit log, and the daily report channel. Store them in your institution's privileged credential vault."
      />

      <div className="credentials-card">
        <div className="credentials-head">
          <div>
            <div className="credentials-issued">
              <i className="fas fa-circle-check" style={{ fontSize: 10 }}></i>
              All gates passed
            </div>
            <div className="credentials-title">
              <em>{(data.signatoryName || "Faisal Al-Mansouri").split(" ")[0]}</em>, your partnership is live.
            </div>
            <div className="credentials-ar" dir="rtl" lang="ar">شراكتك مع منصة ميزان كوانت مفعّلة الآن.</div>
          </div>
          <div className="credentials-stamp">
            <div className="seal">
              <i className="fas fa-mosque"></i>
              <div>AAOIFI</div>
              <div style={{ marginTop: 1 }}>VERIFIED</div>
            </div>
            <div className="ref">Issued 2026-05-24 · Q2</div>
          </div>
        </div>

        <div className="cred-grid">
          <div className="cred-tile full">
            <div className="lab">Partner ID</div>
            <div className="val">
              {credentials.partnerId}
              <CopyButton value={credentials.partnerId} />
            </div>
          </div>

          <div className="cred-tile">
            <div className="lab">Username</div>
            <div className="val" style={{ fontSize: 13 }}>
              <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{credentials.username}</span>
              <CopyButton value={credentials.username} />
            </div>
          </div>

          <div className="cred-tile">
            <div className="lab">Temporary password</div>
            <div className="val">
              {credentials.tempPwd}
              <CopyButton value={credentials.tempPwd} />
            </div>
          </div>

          <div className="cred-tile full">
            <div className="lab">API key · live</div>
            <div className="val" style={{ fontSize: 12 }}>
              <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{credentials.apiKey}</span>
              <CopyButton value={credentials.apiKey} />
            </div>
          </div>

          <div className="cred-tile full">
            <div className="lab">Multi-factor enrolment</div>
            <div className="cred-mfa-row" style={{ marginTop: 4 }}>
              <div>
                <div style={{ fontFamily: "var(--font-body)", fontSize: 13, color: "var(--text-secondary)", lineHeight: 1.55 }}>
                  Scan with your TOTP authenticator (Google Authenticator, 1Password, Authy). The QR encodes a TOTP secret bound to your partner ID. You will be prompted to confirm the code on first sign-in.
                </div>
                <div style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--text-muted)", marginTop: 10, letterSpacing: 0.3 }}>
                  Issuer · MizanQuant<br />Account · {credentials.username}<br />Algorithm · SHA-1 · 30s · 6 digits
                </div>
              </div>
              <div className="cred-qr"><FauxQR size={108} /></div>
            </div>
          </div>
        </div>

        <div className="cred-actions">
          <div className="cred-action" onClick={() => alert("Mocked — would download a signed PDF of credentials")}>
            <div className="ico"><i className="fas fa-file-pdf"></i></div>
            <div>
              <div className="name">Download signed credentials (.pdf)</div>
              <div className="sub">For your institution's privileged credential vault</div>
            </div>
          </div>
          <div className="cred-action" onClick={() => alert("Mocked — would download the AAOIFI fatwa certificate")}>
            <div className="ico"><i className="fas fa-mosque"></i></div>
            <div>
              <div className="name">Download fatwa certificate</div>
              <div className="sub">Issued by MizanQuant Sharia board</div>
            </div>
          </div>
          <div className="cred-action" onClick={() => alert("Mocked — would download the audit ZIP")}>
            <div className="ico"><i className="fas fa-folder-tree"></i></div>
            <div>
              <div className="name">Download audit pack (.zip)</div>
              <div className="sub">NDA · KYC · attestation · audit trail</div>
            </div>
          </div>
          <div className="cred-action" onClick={() => alert("Mocked — would open a Calendly slot")}>
            <div className="ico"><i className="fas fa-calendar"></i></div>
            <div>
              <div className="name">Book onboarding call</div>
              <div className="sub">60-min live demo · with the client desk</div>
            </div>
          </div>
        </div>

        <div className="cred-launch">
          <div className="cred-launch-info">
            <div className="name">Terminal access · ready</div>
            <div className="sub">Your session has been pre-provisioned. Single click below.</div>
          </div>
          <a className="btn btn-primary" href="../ui_kits/mizanquant_terminal/index.html">
            Enter terminal
            <i className="fas fa-arrow-right" style={{ fontSize: 10 }}></i>
          </a>
        </div>
      </div>
    </>
  );
}

window.Step4Credentials = Step4Credentials;
