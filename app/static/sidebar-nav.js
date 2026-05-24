/* MizanQuant — Shared sidebar navigation.
 * Self-contained collapsed sidebar (56 px) that expands on hover (200 px).
 * Injects itself into any page; uses DESIGN.md tokens with hard-coded fallbacks.
 * Include with: <script src="/static/sidebar-nav.js" defer></script>
 */
(function () {
  if (window.__sidebarNavLoaded) return;
  window.__sidebarNavLoaded = true;

  var CURRENT = window.location.pathname;
  function isActive(href) { return href && CURRENT.startsWith(href) && href !== '/'; }

  var NAV = [
    { section: "General" },
    { i: "fa-th-large",     l: "Overview",      href: "/terminal",           badge: "Live" },
    { section: "Forecasting" },
    { i: "fa-brain",        l: "Analysis Lab",  href: "/analysis-lab" },
    { i: "fa-chart-line",   l: "Forecast Lab",  href: "/forecast" },
    { i: "fa-robot",        l: "Trading Lab",   href: "/trading-lab" },
    { i: "fa-history",      l: "Backtest",      href: "/backtest" },
    { section: "Analytics" },
    { i: "fa-shield-alt",   l: "Risk Desk",     href: "/risk-desk-v2" },
    { i: "fa-chart-bar",    l: "Strategies",    href: "/strategy-dashboard" },
    { section: "Research" },
    { i: "fa-mosque",       l: "Halal Screener",href: "/halal-screener-v2" },
    { i: "fa-star",         l: "Insights",      href: "/investors" },
    { i: "fa-bell",         l: "Alerts",        href: "/alerts" },
    { i: "fa-robot",        l: "MizanAI",       href: "/assistant" },
    { section: "Market Data" },
    { i: "fa-landmark",     l: "Macro · FRED",  href: "/macro" },
    { i: "fa-layer-group",  l: "ETF Explorer",  href: "/etf" },
    { i: "fa-sync",         l: "Rotation",      href: "/rotation" },
  ];

  /* ── CSS ──────────────────────────────────────────────────────── */
  var css = `
#__mq-sidebar {
  position: fixed; top: 0; left: 0; bottom: 0;
  width: 56px; z-index: 900;
  display: flex; flex-direction: column;
  background: var(--bg-surface, #14141a);
  border-right: 1px solid var(--border-default, rgba(255,255,255,0.06));
  overflow: hidden;
  transition: width 220ms cubic-bezier(0.4,0,0.2,1);
  font-family: var(--font-display, 'DM Sans', sans-serif);
}
#__mq-sidebar:hover { width: 200px; }

#__mq-sidebar .sb-brand {
  display: flex; align-items: center; gap: 10px;
  padding: 14px 18px; border-bottom: 1px solid var(--border-subtle, rgba(255,255,255,0.04));
  min-height: 52px; flex-shrink: 0;
}
#__mq-sidebar .sb-brand svg { flex-shrink: 0; color: var(--accent, #c8963e); }
#__mq-sidebar .sb-brand-txt { opacity: 0; white-space: nowrap; transition: opacity 180ms ease; overflow: hidden; }
#__mq-sidebar:hover .sb-brand-txt { opacity: 1; }
#__mq-sidebar .sb-brand-txt h2 { font-size: 12px; font-weight: 700; color: var(--text-primary,#e4e4ec); margin: 0; letter-spacing: 0.5px; }
#__mq-sidebar .sb-brand-txt small { font-size: 8px; color: var(--accent,#c8963e); letter-spacing: 1px; font-weight: 600; }

#__mq-sidebar nav { flex: 1; padding: 6px; overflow-y: auto; overflow-x: hidden; }
#__mq-sidebar nav::-webkit-scrollbar { width: 3px; }
#__mq-sidebar nav::-webkit-scrollbar-thumb { background: var(--border-default,rgba(255,255,255,0.06)); border-radius: 2px; }

#__mq-sidebar .sb-section {
  font-size: 9px; font-weight: 600; letter-spacing: 1px;
  color: var(--text-muted,#5c5c6a); text-transform: uppercase;
  padding: 10px 10px 4px; opacity: 0; white-space: nowrap;
  transition: opacity 180ms ease; pointer-events: none;
}
#__mq-sidebar:hover .sb-section { opacity: 1; }

#__mq-sidebar .sb-item {
  display: flex; align-items: center; gap: 10px;
  padding: 7px 10px; border-radius: 6px; text-decoration: none;
  color: var(--text-secondary,#9494a0); font-size: 12px; font-weight: 500;
  transition: background 150ms ease, color 150ms ease;
  white-space: nowrap; min-height: 34px;
}
#__mq-sidebar .sb-item:hover { background: var(--bg-raised,#1c1c26); color: var(--text-primary,#e4e4ec); }
#__mq-sidebar .sb-item.active { background: var(--accent-dim,rgba(200,150,62,0.10)); color: var(--accent,#c8963e); }

#__mq-sidebar .sb-item i { width: 16px; text-align: center; font-size: 13px; flex-shrink: 0; }
#__mq-sidebar .sb-item .sb-label { opacity: 0; transition: opacity 180ms ease; overflow: hidden; flex: 1; }
#__mq-sidebar:hover .sb-item .sb-label { opacity: 1; }
#__mq-sidebar .sb-badge {
  font-size: 9px; font-weight: 700; padding: 1px 5px; border-radius: 10px;
  background: var(--accent-dim,rgba(200,150,62,0.15)); color: var(--accent,#c8963e);
  opacity: 0; transition: opacity 180ms ease; flex-shrink: 0;
}
#__mq-sidebar:hover .sb-badge { opacity: 1; }

#__mq-sidebar .sb-foot {
  padding: 12px 10px; border-top: 1px solid var(--border-subtle,rgba(255,255,255,0.04));
  display: flex; align-items: center; gap: 8px; flex-shrink: 0;
}
.sb-dot-green {
  width: 7px; height: 7px; border-radius: 50%;
  background: var(--positive,#4ade80); flex-shrink: 0;
  animation: sbPulse 2s ease-in-out infinite;
}
@keyframes sbPulse { 0%,100%{opacity:1} 50%{opacity:0.35} }
#__mq-sidebar .sb-foot-txt { opacity: 0; white-space: nowrap; transition: opacity 180ms ease; }
#__mq-sidebar:hover .sb-foot-txt { opacity: 1; }
#__mq-sidebar .sb-foot-txt div:first-child { font-size: 11px; color: var(--text-secondary,#9494a0); }
#__mq-sidebar .sb-foot-txt div:last-child  { font-size: 9px;  color: var(--text-muted,#5c5c6a); }

/* push page content right so sidebar doesn't overlap */
body.__mq-sb-present { margin-left: 56px !important; }
`;

  /* ── Build DOM ─────────────────────────────────────────────────── */
  function build() {
    if (document.getElementById('__mq-sidebar')) return;

    // Inject stylesheet
    var style = document.createElement('style');
    style.textContent = css;
    document.head.appendChild(style);

    // Build sidebar element
    var aside = document.createElement('aside');
    aside.id = '__mq-sidebar';

    // Brand
    aside.innerHTML = `
      <div class="sb-brand">
        <svg viewBox="0 0 64 64" width="20" height="20" aria-hidden="true" style="color:var(--accent,#c8963e)">
          <circle cx="18" cy="34" r="9.5" fill="none" stroke="currentColor" stroke-width="3.2"/>
          <path d="M27 40 L36 30 L44 36 L54 22" fill="none" stroke="currentColor" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round"/>
          <circle cx="54" cy="22" r="3" fill="currentColor"/>
        </svg>
        <div class="sb-brand-txt">
          <h2>mizanquant</h2>
          <small>HALAL TRADING SUITE</small>
        </div>
      </div>
      <nav id="__mq-sidebar-nav"></nav>
      <div class="sb-foot">
        <span class="sb-dot-green"></span>
        <div class="sb-foot-txt">
          <div>Connected</div>
          <div>alpaca · live</div>
        </div>
      </div>
    `;

    var nav = aside.querySelector('#__mq-sidebar-nav');
    NAV.forEach(function(n) {
      if (n.section) {
        var d = document.createElement('div');
        d.className = 'sb-section';
        d.textContent = n.section;
        nav.appendChild(d);
      } else {
        var a = document.createElement('a');
        a.href = n.href || '#';
        a.className = 'sb-item' + (isActive(n.href) ? ' active' : '');
        a.innerHTML =
          '<i class="fas ' + n.i + '"></i>' +
          '<span class="sb-label">' + n.l + '</span>' +
          (n.badge ? '<span class="sb-badge">' + n.badge + '</span>' : '');
        nav.appendChild(a);
      }
    });

    document.body.prepend(aside);
    document.body.classList.add('__mq-sb-present');
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', build);
  } else {
    build();
  }
})();
