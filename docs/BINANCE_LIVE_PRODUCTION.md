# Binance Spot Live production track

This branch is an isolated production-integration track. It does not change
main, Paper, or Testnet.

## Current implementation stage

- Existing Binance Spot adapter is reused; credentials are read only from the
  server environment.
- A separate SQLite Live control latch is durable and defaults to emergency
  stop ON.
- Authenticated Live status, read-only preflight, and emergency-stop endpoints
  are exposed under /api/v1/live.
- The preflight refuses to run unless Testnet is explicitly false, Live is
  explicitly enabled, server credentials exist, and the symbol allowlist is
  non-empty. It performs account/market reads only.
- No Live order endpoint or background Live worker is enabled in this stage.
  This is deliberate: durable intent/reconciliation, exposure reservations,
  daily-loss limits, duplicate-submit recovery, and operator arming must be
  completed and tested before order submission is connected.

## Required server environment

- BINANCE_TESTNET=false
- ENABLE_LIVE_TRADING=true (keep false until all gates are approved)
- BINANCE_API_KEY and BINANCE_API_SECRET (server secret store only)
- BINANCE_ALLOWED_SYMBOLS (explicit comma-separated Spot pairs)
- MAX_ORDER_QUOTE_USDT (strict per-order cap)
- DASHBOARD_TOKEN

Use a Binance API key restricted to Spot trading, with withdrawals and
transfers disabled, IP restrictions enabled where supported, and no secrets in
the Android application, repository, logs, or chat.

## Release signing

A same-identity Android update requires the original keystore and matching
alias/passwords. The repository workflow must not silently fall back to a
debug key or a newly generated key. If the original keystore is unavailable,
a same-signature update cannot be produced.

## Manual Live order controls (current branch implementation)

The dashboard and Android command center expose explicit manual Spot controls:
arm, disarm, emergency stop, stop release, order submission and reconciliation.
There is no Live strategy loop or automatic entry worker. Every order requires
a user confirmation in the client and a separate server-side arm state.

Additional server settings:
- LIVE_MANUAL_ORDERS_ENABLED=false (must be explicitly enabled)
- MAX_DAILY_LOSS_QUOTE=5
- MAX_POSITION_QUOTE_USDT=25
- MAX_OPEN_LIVE_ORDERS=3
- LIVE_ORDERS_DB=data/live_orders.sqlite3
- LIVE_CONTROL_DB=data/live_control.sqlite3

Manual requests require a client request ID. The server persists the order intent
before contacting Binance and uses the deterministic Binance client order ID to
avoid blind duplicate submissions. Uncertain outcomes are marked unknown and
must be reconciled; they are never automatically resubmitted.

Daily realized P/L is calculated from journaled Spot fills in the quote asset.
Fees in other assets are retained in the fill journal but are not converted into
quote currency; the displayed daily quote P/L therefore must not be treated as a
complete tax/accounting report. Position tracking starts from fills recorded by
this Live journal; pre-existing exchange holdings are not silently treated as
managed positions. Reconcile and compare exchange balances before arming.

The manual Live API and dashboard controls are connected, but this branch is not
a certified production trading system. CI/mock tests do not prove connectivity
to the user's Binance Spot Testnet account. A real Testnet preflight and order
lifecycle run with dedicated Testnet credentials, plus deployment persistence
verification and independent review, are required before considering real-money
credentials. Keep Live flags false and do not place real orders during this stage.
