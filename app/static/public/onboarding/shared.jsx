// shared.jsx — primitives & mock state for the onboarding flow

const { useState, useEffect, useMemo, useRef, useCallback } = React;

// ─── Step definitions ────────────────────────────────────────────────
const STEPS = [
  { id: "nda",     no: "01", lab: "NDA",         sub: "Non-disclosure",        eyebrow: "Step 01 of 04 · Non-disclosure agreement" },
  { id: "kyc",     no: "02", lab: "KYC",         sub: "Institutional",         eyebrow: "Step 02 of 04 · Institutional KYC" },
  { id: "sharia",  no: "03", lab: "Sharia",      sub: "Board review",          eyebrow: "Step 03 of 04 · Sharia board review" },
  { id: "cred",    no: "04", lab: "Credentials", sub: "Terminal access",       eyebrow: "Step 04 of 04 · Credentials issuance" },
];

// ─── Stepper ─────────────────────────────────────────────────────────
function Stepper({ index }) {
  return (
    <div className="ob-stepper-wrap">
      <div className="ob-stepper">
        {STEPS.map((s, i) => {
          const state = i < index ? "done" : i === index ? "active" : "";
          return (
            <div key={s.id} className={"ob-step " + state}>
              <div className="num">
                {i < index ? <i className="fas fa-check" style={{ fontSize: 11 }}></i> : s.no}
              </div>
              <div className="lab">{s.lab}</div>
              <div className="sub">{s.sub}</div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ─── Top bar / status bar ────────────────────────────────────────────
function TopBar({ refNumber, session }) {
  return (
    <header className="ob-topbar">
      <div className="ob-brand">
        <div className="mark">
          <svg viewBox="0 0 64 64" width="20" height="20" aria-hidden="true">
            <circle cx="18" cy="34" r="9.5" fill="none" stroke="currentColor" strokeWidth="3.2" />
            <path d="M27 40 L36 30 L44 36 L54 22" fill="none" stroke="currentColor" strokeWidth="3.2" strokeLinecap="round" strokeLinejoin="round" />
            <circle cx="54" cy="22" r="3" fill="currentColor" />
          </svg>
        </div>
        <div className="wm">mizan<em>quant</em></div>
        <div className="sep"></div>
        <div className="title">Institutional onboarding</div>
      </div>
      <div className="ob-status">
        <span className="dot"></span>
        <span>Secure channel · TLS 1.3</span>
        <span style={{ color: "var(--text-disabled)" }}>·</span>
        <span className="ref">REF · {refNumber}</span>
      </div>
    </header>
  );
}

// ─── Foot bar ────────────────────────────────────────────────────────
function FootBar({ stepIdx, canContinue, onBack, onContinue, continueLabel, refNumber }) {
  const isLast = stepIdx === STEPS.length - 1;
  return (
    <footer className="ob-footbar">
      <div className="ob-foot-meta">
        <strong>{stepIdx + 1} of {STEPS.length}</strong>  ·  session expires in 18:24  ·  ref {refNumber}
      </div>
      <div className="ob-foot-nav">
        <button className="btn btn-ghost" onClick={onBack} disabled={stepIdx === 0 || isLast}>
          <i className="fas fa-arrow-left" style={{ fontSize: 10 }}></i> Back
        </button>
        <button className="btn btn-primary" onClick={onContinue} disabled={!canContinue}>
          {continueLabel || "Continue"}
          {!isLast && <i className="fas fa-arrow-right" style={{ fontSize: 10 }}></i>}
        </button>
      </div>
    </footer>
  );
}

// ─── Eyebrow + title helpers ─────────────────────────────────────────
function StepHeader({ eyebrow, title, lead, accentTitle }) {
  return (
    <>
      <div className="ob-step-eyebrow">
        <span className="dot"></span>
        {eyebrow}
      </div>
      <h1 className="ob-step-title">
        {title.replace(/<em>.*?<\/em>/g, "")}
        {accentTitle && <em>{accentTitle}</em>}
      </h1>
      {lead && <p className="ob-step-lead">{lead}</p>}
    </>
  );
}

// Reusable section heading
function SectionHead({ num, title, aside }) {
  return (
    <div className="section-head">
      <div className="section-title">
        {num && <span className="num">{num}</span>}{title}
      </div>
      {aside && <div className="section-aside">{aside}</div>}
    </div>
  );
}

// Reusable alert
function Alert({ kind = "accent", icon, children }) {
  return (
    <div className={"alert " + kind}>
      <i className={"fas " + (icon || (kind === "warn" ? "fa-triangle-exclamation" : kind === "ok" ? "fa-circle-check" : "fa-circle-info"))} style={{ fontSize: 14 }}></i>
      <div>{children}</div>
    </div>
  );
}

// ─── Generate a partner ref ──────────────────────────────────────────
function generatePartnerRef() {
  const t = Date.now().toString(36).toUpperCase();
  return "MZN-INST-" + t.slice(-6) + "-2026Q2";
}

Object.assign(window, {
  STEPS, Stepper, TopBar, FootBar, StepHeader, SectionHead, Alert, generatePartnerRef,
});
