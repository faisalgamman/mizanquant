# Runbook

## Cold Start Checklist

1. Verify `.env` is present and secrets are current.
2. If `AUTO_TRADE_ENABLED=true`, ensure `API_KEY` is set before boot. Otherwise operator and trading routes fail closed and startup will disarm auto-trading.
3. Start the API and confirm `/health` reports database, market-data, and broker connectivity.
4. Check `/admin/config` with `X-API-Key` for redacted config sanity.
5. Confirm the current regime via `/ops` with `X-API-Key`.
6. Ensure `guard_log` and `regime_log` tables are writable.

## Key Rotation

1. Add new Alpaca/FMP/Telegram/API keys to the environment.
2. Restart the service.
3. Confirm `/admin/config` shows the new redacted tails.
4. Send a test notification and confirm receipt.

Rotation cadence: every 90 days, or immediately after any teammate offboarding.

## Investigating a Stuck Order

1. Query `/portfolio/orders` and broker open orders.
2. Run `reconcile_positions()` from the Python shell if DB state looks stale.
3. Inspect `logs/trades.jsonl` for the order lifecycle.
4. Check `/admin/guards` for any recent blocking guard.
5. Confirm the fill watcher is running.

## Force Regime to BEAR

1. `POST /admin/regime?state=BEAR` with `X-API-Key`.
2. Confirm `/ops` shows `BEAR` on the next refresh.
3. Run a dry trade and verify the correlation / max-position guards tighten.
4. Clear the override with `POST /admin/regime` after the drill.

## Kill-Switch Procedure

1. `POST /admin/killswitch?killed=true` with `X-API-Key`.
2. Confirm Telegram receipt of the kill-switch notification.
3. Confirm `/ops` shows `Kill-switch: True`.
4. Resume only after root cause and paper validation are complete.

## Railway Safe Deploy Checklist

1. Set a strong `API_KEY` in Railway before enabling auto-trading.
2. Verify `/openapi.json` shows security on `/admin/*`, `/portfolio/*`, `/strategy/*`, `/ops`, and `/api/v1/trading/*`.
3. Confirm `/health` reports `"broker": "connected"` before trusting trading status.
4. Confirm `/api/v1/trading/status` returns `"broker_connected": true` with `X-API-Key`.
5. Reject the deploy if `/api/v1/trading/performance` returns `500`, if `/health` is `degraded`, or if protected routes answer without `X-API-Key`.

## Postmortem Template

- Incident date/time:
- Detection path:
- Impacted strategies:
- Customer / capital impact:
- Root cause:
- Why safeguards did or did not catch it:
- Fix shipped:
- Follow-up actions:
