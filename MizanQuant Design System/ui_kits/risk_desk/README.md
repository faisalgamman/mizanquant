# Risk Desk — UI Kit

A hi-fi click-thru recreation of the MizanQuant **Risk Desk** surface. Wraps Portfolio VaR, Kelly position-sizing, system-wide guard toggles, position-level risk decomposition, and stress-test scenarios in the unified post-rebrand palette.

## What's here

| File | Component |
| --- | --- |
| `index.html` | Composed entry — open this |
| `App.jsx` | Top-level state + mock data composition |
| `shared.jsx` | Risk primitives: `Gauge`, `BlockToggle`, `mock data` |
| `RiskHeader.jsx` | Page title + global risk-level indicator |
| `VaRMetrics.jsx` | Portfolio VaR (95%), CVaR (95%), Sharpe, Max DD strip |
| `KellySizing.jsx` | Continuous Kelly panel: f\*, fractional setting, per-strategy table |
| `BlockToggles.jsx` | System-wide guard toggles: kill-switch, halt pipeline, auto-trade, dry-run, broker |
| `PositionRisk.jsx` | Position-by-position risk table with VaR contribution bars |
| `StressTests.jsx` | Five canonical scenarios with PnL impact bars |
| `RiskAlerts.jsx` | Recent risk events feed |

## What interacts

- **Toggle a block** → state flips with a 120ms ease-out; risk-level indicator may recompute
- **Run a stress test** → bars animate in
- **Acknowledge an alert** → fades out

## What it mirrors in production

- `mizanquant/app/static/risk-desk.html` — the dashboard structure
- `mizanquant/app/services/kelly.py` — the continuous Kelly math (`f* = mean(r) / var(r)`, fractional + sample-size shrinkage)
- `mizanquant/app/services/risk_manager.py` — Kelly + regime-adaptive position sizing
- `mizanquant/app/services/technical.py` — VaR 95% / CVaR 95% calculation

This is a UI kit. All values are mocked. Real wiring lives in `/api/v1/risk/*` per the FastAPI app.
