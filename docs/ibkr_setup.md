# Interactive Brokers — deployment runbook (Phase B)

This document describes how to wire the bot up to IBKR through a
sidecar IB Gateway service on Railway. Strategy A (HANA / Momentum)
is the only strategy approved to run on IB initially, per the
Out-of-Scope register: B and C stay on Alpaca.

## Architecture

```
┌──────────────────┐        TCP socket :4002        ┌─────────────────────┐
│   openbb-trading │ ─────────────────────────────▶ │  ib-gateway service │
│   (main service) │                                │  gnzsnz/ib-gateway  │
│                  │ ◀───── orders / fills ───────  │  + IBC (auto-login) │
└──────────────────┘                                └──────┬──────────────┘
                                                           │
                                                           ▼
                                                    Interactive Brokers
                                                    (paper: DUP607506)
```

The bot calls `app.services.broker.IBBroker` (resolved via the Phase A
factory when `STRATEGY_BROKER_A=ibkr`). The adapter speaks ib_insync to
the gateway over the Railway private network.

## One-time setup on Railway

### 1. Add a second service to the same project

In the openbb-trading project on Railway:

1. **+ New** → **Empty Service** (name it `ib-gateway`)
2. **Source** → **Docker Image** → `ghcr.io/gnzsnz/ib-gateway:stable`
   - إذا لم يعمل، جرب: `docker.io/gnzsnz/ib-gateway:latest`
3. **Settings → Networking → Private Networking → Enabled**
4. **Variables**:

   | Variable | Value | Notes |
   |----------|-------|-------|
   | `TWS_USERID` | *your IB paper username* | NOT the live one |
   | `TWS_PASSWORD` | *your IB paper password* | |
   | `TRADING_MODE` | `paper` | Hardcoded paper for now |
   | `READ_ONLY_API` | `no` | Must be `no` to place orders |
   | `TWOFA_TIMEOUT_ACTION` | `restart` | IBC handles weekly forced logout |
   | `BYPASS_WARNING` | `yes` | |
   | `AUTO_RESTART_TIME` | `11:55 PM` | IB requires daily restart |
   | `AUTO_LOGOFF_MINUTES` | `0` | Disable auto-logoff |

5. **No public domain needed** — leave the service private. The
    private networking hostname will be something like
    `ib-gateway.railway.internal`.  
    **ملاحظة:** بسبب وجود مسافة في اسم الخدمة (` ib-gateway`)، الاستضافة الفعلية هي
    `content-miracle.railway.internal`.

### 2. Wire the main bot to the gateway

In the openbb-trading main service, add these variables:

| Variable | Value |
|----------|-------|
| `IBKR_HOST` | `content-miracle.railway.internal` |
| `IBKR_PORT` | `4002` |
| `IBKR_CLIENT_ID` | `1` |
| `IBKR_CLIENT_ID_A` | `11` |
| `IBKR_CLIENT_ID_B` | `12` |
| `IBKR_CLIENT_ID_C` | `13` |

Per-strategy client IDs prevent collision if more than one strategy
talks to the gateway at the same time.

### 3. Activate Strategy A on IB (DO NOT FLIP YET)

Only after the gateway service is up and the main bot is healthy:

```
STRATEGY_BROKER_A=ibkr
```

Strategy B and C continue on Alpaca because their per-strategy
overrides are absent → factory falls back to `BROKER_TYPE=alpaca`.

## Verification

After deploy, on the main service:

```bash
curl -H "X-API-Key: $KEY" \
  https://openbb-trading-production.up.railway.app/admin/config
```

Logs should show:

```
INFO:screener:IBKR connected: host=ib-gateway.railway.internal port=4002 client_id=11 strategy=A
```

Then check the IB account:

```bash
curl -H "X-API-Key: $KEY" \
  "https://openbb-trading-production.up.railway.app/admin/config?strategy=A"
```

If the gateway is reachable, account values will be returned. If the
gateway is down, the adapter returns None and the engine logs an error
instead of crashing.

## Operational notes

- **Daily restart**: IB forces a logout once per 24 hours. IBC handles
  the re-login automatically; expect a 1–2 minute window each night
  where orders cannot be placed. The bot's `is_market_open()` guard
  already prevents trades outside market hours, so this overlap is
  invisible in practice if `AUTO_RESTART_TIME` is set to ~midnight ET.
- **Weekly Sunday restart**: IB also forces a full restart on Sundays.
  IBC handles this too.
- **2FA**: IBKR Pro requires 2FA on login. With paper accounts and IBC,
  the second factor is bypassed automatically. Live accounts need
  IB Key (mobile push). Live trading therefore requires either a
  dedicated phone-as-server or moving to IBKR Lite (no 2FA on API).
- **Cost**: ~$5/month for the always-on gateway service.

## Reverting to Alpaca

In one click:

```
STRATEGY_BROKER_A=alpaca   # or simply unset the variable
```

No code change. The factory re-resolves on the next call.

## Known limitations

- **ib_insync requires Python 3.11 or 3.12** — the production Dockerfile
  is on 3.11 so this is fine in deploy. Local dev on Python 3.14 will
  show import errors; the test suite skips IB tests in that case.
- **Bracket orders on IB**: structurally identical to Alpaca's
  (parent + TP + SL), but IB returns the child orders as separate
  trades rather than `legs` on the parent. The adapter handles this in
  `_trade_to_dict` by emitting `order_class="bracket"` whenever
  `parentId != 0`.
- **Partial fills**: ib_insync's `Trade.orderStatus.filled` is
  authoritative. The fill watcher polls `get_orders(status="open")` on
  the same interval it does for Alpaca.
