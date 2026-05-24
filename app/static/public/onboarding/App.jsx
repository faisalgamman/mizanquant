// App.jsx — orchestrates the 4-step onboarding state machine

function App() {
  const [stepIdx, setStepIdx] = useState(0);

  // Per-step data lives on a single object keyed by step id
  const [refNumber] = useState(() => generatePartnerRef());
  const [ndaData, setNdaData]       = useState({ refNumber });
  const [kycData, setKycData]       = useState({});
  const [shariaData, setShariaData] = useState({ refNumber });

  // Bridge refNumber into the step data so step components don't need globals
  useEffect(() => {
    setShariaData((d) => ({ ...d, refNumber }));
  }, [refNumber]);

  // ── Per-step "can advance" predicates ──
  const canAdvance = useMemo(() => {
    if (stepIdx === 0) {
      return !!(ndaData.signatoryName && ndaData.signatoryTitle && ndaData.signature
                && ndaData.ackTerms && ndaData.ackJurisdiction && ndaData.ackElectronic);
    }
    if (stepIdx === 1) {
      const requiredDocs = ["incorp", "passport", "address", "auth"];
      const docs = kycData.docs || {};
      const docsOk = requiredDocs.every((d) => docs[d]);
      return !!(kycData.entType && kycData.entName && kycData.juris && kycData.year
                && kycData.aum && kycData.deploy && kycData.compEmail && kycData.shariaBoard
                && docsOk);
    }
    if (stepIdx === 2) {
      return shariaData.phase === "approved";
    }
    return true;
  }, [stepIdx, ndaData, kycData, shariaData]);

  const onBack = () => setStepIdx((i) => Math.max(0, i - 1));
  const onContinue = () => {
    if (!canAdvance) return;
    if (stepIdx < STEPS.length - 1) {
      setStepIdx((i) => i + 1);
      window.scrollTo({ top: 0, behavior: "smooth" });
    }
  };

  // Persist the partner ref onto each step's data
  useEffect(() => {
    setNdaData((d) => ({ ...d, refNumber }));
  }, [refNumber]);

  const continueLabel = stepIdx === 0 ? "Execute NDA"
                       : stepIdx === 1 ? "Submit for review"
                       : stepIdx === 2 ? "Issue credentials"
                       : null;

  return (
    <div className="ob-shell">
      <div className="ob-bg-glow"></div>
      <TopBar refNumber={refNumber} />
      <Stepper index={stepIdx} />

      <div className="ob-stage">
        <div className="ob-card" key={stepIdx}>
          {stepIdx === 0 && <Step1NDA       data={ndaData}    setData={setNdaData} />}
          {stepIdx === 1 && <Step2KYC       data={kycData}    setData={setKycData} />}
          {stepIdx === 2 && <Step3Sharia    data={shariaData} setData={setShariaData} />}
          {stepIdx === 3 && <Step4Credentials data={{ ...ndaData, ...kycData, ...shariaData }} />}
        </div>
      </div>

      <FootBar
        stepIdx={stepIdx}
        canContinue={canAdvance}
        onBack={onBack}
        onContinue={onContinue}
        continueLabel={continueLabel}
        refNumber={refNumber}
      />
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
