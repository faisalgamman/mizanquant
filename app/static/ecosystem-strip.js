/* MizanQuant — Ecosystem status strip.
 * A self-contained floating pill (top-right) shared across pages that surfaces
 * the unified MarketContextBundle: regime · posture · VIX · 10Y-2Y · top sector.
 * Polls /api/context/bundle every 60s and pulses on a risk_posture flip.
 * Non-intrusive: fixed position, no layout changes, DESIGN.md tokens with fallbacks.
 */
(function () {
  if (window.__ecoStripLoaded) return;
  window.__ecoStripLoaded = true;
  var POLL_MS = 60000;
  var lastVersion = null;

  function postureColor(p) {
    return p === 'risk_off' ? 'var(--negative,#f87171)'
         : p === 'risk_on'  ? 'var(--positive,#4ade80)'
         : 'var(--warning,#fbbf24)';
  }
  function lbl(t) { return '<span style="color:var(--text-muted,#5c5c6a);">' + t + '</span> '; }
  function val(t) { return '<span style="color:var(--text-primary,#e4e4ec);">' + t + '</span>'; }
  function sep() { return ' <span style="color:var(--border-strong,#333);">·</span> '; }

  function ensure() {
    var strip = document.getElementById('ecoStrip');
    if (strip) return strip;
    strip = document.createElement('div');
    strip.id = 'ecoStrip';
    strip.title = 'MizanQuant ecosystem context — regime, risk posture, VIX, yield spread, top sector';
    strip.style.cssText = [
      'position:fixed', 'top:8px', 'right:12px', 'z-index:9999',
      'display:flex', 'align-items:center', 'gap:6px',
      'padding:6px 12px', 'border-radius:6px',
      'background:var(--bg-surface,#14141a)',
      'border:1px solid var(--border-default,rgba(255,255,255,0.08))',
      'font-family:var(--mono,ui-monospace,monospace)', 'font-size:11px',
      'color:var(--text-secondary,#9494a0)', 'box-shadow:0 4px 16px rgba(0,0,0,0.3)',
      'user-select:none', 'transition:border-color 300ms ease'
    ].join(';');
    document.body.appendChild(strip);
    return strip;
  }

  function render(b) {
    if (!b || typeof b !== 'object') return;
    var strip = ensure();
    var posture = b.risk_posture || '-';
    var regime = b.regime || '-';
    var vix = (b.vix != null) ? Number(b.vix).toFixed(1) : '-';
    var ys = (b.macro && b.macro.t10y2y != null) ? Number(b.macro.t10y2y).toFixed(2) + '%' : '-';
    var top = (b.top_sectors && b.top_sectors[0]) ? b.top_sectors[0].sector : '-';
    var flipped = (lastVersion != null && b.version !== lastVersion);
    lastVersion = b.version;

    strip.innerHTML =
      '<span style="color:var(--accent,#c8963e);font-weight:700;letter-spacing:0.5px;">MIZAN</span>'
      + sep() + lbl('REGIME') + val(regime)
      + sep() + lbl('POSTURE') + '<span style="color:' + postureColor(posture) + ';font-weight:600;">' + String(posture).toUpperCase() + '</span>'
      + sep() + lbl('VIX') + val(vix)
      + sep() + lbl('10Y-2Y') + val(ys)
      + sep() + lbl('TOP') + val(top);

    if (flipped) {
      strip.style.borderColor = postureColor(posture);
      if (strip.animate) {
        strip.animate([{ opacity: 0.45 }, { opacity: 1 }], { duration: 700 });
      }
    }
  }

  async function tick() {
    try {
      var ctrl = (typeof AbortSignal !== 'undefined' && AbortSignal.timeout)
        ? { signal: AbortSignal.timeout(12000) } : {};
      var r = await fetch('/api/context/bundle', ctrl);
      if (r.ok) render(await r.json());
    } catch (e) { /* silent — strip is best-effort */ }
  }

  function start() { tick(); setInterval(tick, POLL_MS); }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }
})();
