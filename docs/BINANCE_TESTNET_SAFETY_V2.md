# Binance Spot Testnet Safety v2

This branch is an isolated safety implementation. It is not a Live trading release.

## Current verified baseline

- Parent branch: `feature/binance-live-execution-v1`
- Parent commit: `9dec8d5b9a678b4328d1e6b9dda7b753e8743e35`
- Main/foundation are not modified.
- Live execution remains disabled.

## Accounting rule

`fill_accounting.py` replays exchange fills rather than cumulative order status:
- Base commission on buys reduces received base inventory.
- Base commission on sells increases base inventory consumed.
- Quote commission increases buy cost basis or reduces sell proceeds.
- Third-asset commission remains a separate asset-denominated fee. It is not converted to USDT without a trusted timestamped conversion price.
- Duplicate exchange trade IDs are ignored.
- Sells that exceed available inventory are rejected.

This is a deterministic accounting primitive. It is not yet the production journal integration or proof of exchange reconciliation.

## Release gates

1. Integrate fill replay into SQLite journal and migrate existing rows safely.
2. Reconcile every known order and compare all exchange open orders against journal intents. Unknown orders must halt new entries and be reported; never auto-cancel them.
3. On cancel timeout/uncertain result, keep the trade locked and reconcile late fills before any exit order.
4. Verify free balances, symbol filters, quantity/price precision and min-notional immediately before submission.
5. Run authenticated Binance Spot Testnet preflight, then one bounded buy/sell round trip, and verify order IDs, trade IDs, commissions and balances from exchange history.
6. Restart against the same persistent SQLite path and prove no duplicate submission.
7. Keep Live behind a separate disabled-by-default gate and independent tests. Never place Live orders as part of Testnet validation.

## Deployment

Do not deploy this branch to SillyDev until CI is green and its deployment source/branch is explicitly set to this branch. Do not replace the existing Paper folder or its data in-place.
