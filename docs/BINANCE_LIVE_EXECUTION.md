# Binance Spot execution — isolated implementation branch

Branch: `feature/binance-live-execution-v1`

## Current state

- The existing `deploy/infrlo-paper-v1` branch remains unchanged and Paper-only.
- This branch introduces a guarded Spot execution adapter, but does not yet expose it through the dashboard/API.
- The adapter requires server-side API credentials, an explicit symbol allowlist, a per-order quote-notional cap, and a unique client order ID.
- Withdrawals/transfers are not implemented. Only Spot limit orders are exposed by this adapter.
- Testnet is the default. Live mode remains disabled unless `ENABLE_LIVE_TRADING=true`; conflicting Testnet/live flags fail closed.

## Required server environment (Testnet phase)

```env
BINANCE_API_KEY=<testnet-key>
BINANCE_API_SECRET=<testnet-secret>
BINANCE_TESTNET=true
ENABLE_LIVE_TRADING=false
BINANCE_ALLOWED_SYMBOLS=BTC/USDT,ETH/USDT
MAX_ORDER_QUOTE_USDT=10
```

Never place API credentials in the Android app, dashboard JavaScript, repository, logs, or chat. Use Spot-only permissions and keep withdrawals disabled.

## Not ready for live funds

Before enabling any real-money path, this branch still needs:
1. Unit and integration tests against Binance Spot Testnet.
2. Durable order-intent storage and idempotency across process restarts.
3. Startup reconciliation of open orders, fills, balances, and partial fills.
4. Server-side user-configurable sizing and stop/target state machine.
5. Daily-loss, exposure, open-position, stale-price, clock-drift, and kill-switch enforcement.
6. Authenticated API routes and dashboard controls with audit logging.
7. Deployment verification and failure/recovery tests on the actual host.

No live orders are submitted by this commit.