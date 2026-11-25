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
