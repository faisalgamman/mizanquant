// ScreenerHeader.jsx — title + scan button + last-scanned timestamp

function ScreenerHeader({ scanning, lastScanned, onScan, onExport }) {
  return (
    <header className="hs-header">
      <div className="hs-title">
        <div className="hs-mark">
          <i className="fas fa-mosque" style={{ fontSize: 16 }}></i>
        </div>
        <div>
          <h1>Halal screener</h1>
          <div className="ar" dir="rtl" lang="ar">فحص التوافق الشرعي</div>
        </div>
      </div>
      <div className="hs-actions">
        <span className="hs-ts">{lastScanned ? "Last scan · " + lastScanned : "Not yet scanned"}</span>
        <button className="hs-export-btn" onClick={onExport}>
          <i className="fas fa-download" style={{ fontSize: 10 }}></i>
          Export CSV
        </button>
        <button className="hs-scan-btn" onClick={onScan} disabled={scanning}>
          <i className={"fas " + (scanning ? "fa-spinner" : "fa-play")} style={{ fontSize: 10, animation: scanning ? "spin 0.8s linear infinite" : "none" }}></i>
          {scanning ? "Scanning…" : "Run smart scan"}
        </button>
      </div>
    </header>
  );
}

Object.assign(window, { ScreenerHeader });
