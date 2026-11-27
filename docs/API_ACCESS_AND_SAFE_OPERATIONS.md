# Iskander AI Trade — API access and safe operations

## Swagger sign-in

1. Open the API documentation at `/docs`.
2. Select **Authorize** in the top-right corner.
3. Paste the value of `DASHBOARD_TOKEN` only. Do not type the word `Bearer`; Swagger adds the scheme automatically.
4. Confirm **Authorize**, close the dialog, then run a protected GET endpoint such as `/api/v1/status`.
5. The generated cURL command must contain an `Authorization: Bearer …` header. If it does not, do not proceed with any POST operation.

All dashboard, Paper, Testnet, and manual Live API operations use the same bearer-token dependency. The public `/health` endpoint does not require a token.

## Server token configuration

- Prefer the hosting provider's private Environment Variables panel for `DASHBOARD_TOKEN`.
- If using a `.env` file, place it in the application root. The application also checks its working directory and parent directory.
- Provider environment variables take precedence over `.env` values. If both locations define `DASHBOARD_TOKEN`, make them identical or remove the duplicate.
- Rotate any token that has appeared in a screenshot, chat, terminal history, or public log. Generate a long random value and never share it in support messages.
- Restart/redeploy the server after changing the token.

## Safe execution sequence

1. Confirm `/health` reports the expected service and Paper mode.
2. Authenticate with **Authorize**.
3. Use `GET /api/v1/status` and `GET /api/v1/accounting` to inspect the Paper state.
4. Use `GET /api/v1/testnet/preflight` only after Testnet credentials are configured. This checks authenticated Testnet access and market prices; it does not submit an order.
5. Testnet trade creation and arming are separate operator actions. A Testnet cycle can submit Testnet orders for trades that are enabled, so do not run it until the preflight is successful and the Testnet wallet/risk limits have been checked.
6. Keep the persistent emergency stop available. Do not remove or bypass it.

## Safe baseline flags

```env
MODE=paper
BINANCE_TESTNET=true
ENABLE_LIVE_TRADING=false
RUN_PAPER_WORKER=false
RUN_BINANCE_TESTNET_WORKER=false
```

Paper strategy execution, Testnet execution, and manual Live execution are separate controls. Live trading uses real funds and must remain disabled during authentication and Testnet verification. A visible API route does not mean its exchange connection is configured or that an order has been placed.

## Current release note

API version 0.6.1 introduces a shared OpenAPI HTTP Bearer security scheme. Swagger should show an **Authorize** control and automatically attach the token to protected operations. The same authentication dependency protects the Testnet and manual Live routes.
