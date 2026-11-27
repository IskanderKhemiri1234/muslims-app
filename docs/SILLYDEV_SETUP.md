# SillyDev deployment setup

Use the repository root as the application directory. Do not point the app file at an old nested folder such as `iskander-ai-trade-testnet-v2/launch.py`.

## SillyDev application settings

- Repository: `IskanderKhemiri1234/muslims-app`
- Branch: `feature/manual-live-safety-v2`
- Source directory: `/`
- Python version: `3.11`
- App Python file: `launch.py`
- Requirements file: `requirements.txt`
- Port: use the provider's assigned `PORT`; the launcher falls back to `6007`.

The root launcher changes to the repository root and adds `src/` to Python's import path before starting Uvicorn. Keep the `.env` file beside `launch.py`, never inside the old nested application directory.

## Required environment

Set `DASHBOARD_TOKEN` to a newly generated, unique secret in the provider's private environment settings. Do not commit it or share it in screenshots.

Keep these safety values while validating the deployment:

```env
MODE=paper
BINANCE_TESTNET=true
ENABLE_LIVE_TRADING=false
LIVE_MANUAL_ORDERS_ENABLED=false
RUN_PAPER_WORKER=false
RUN_BINANCE_TESTNET_WORKER=false
```

Keep Binance API credentials restricted to Spot Testnet keys. Do not use mainnet keys.

## Deployment verification

1. Open `/health`. It must report API version `0.6.2`.
2. Open `/docs`. Protected endpoints must use the Swagger **Authorize** dialog with HTTP Bearer authentication; they must not show a plain `authorization` header parameter.
3. Authorize with the dashboard token. Swagger sends the header as `Authorization: Bearer <token>`.
4. Call only `GET /api/v1/testnet/preflight`. A successful response reports Testnet connectivity and `orders_submitted: false`. Do not run a Testnet cycle or enable trades during the initial connectivity check.

A healthy API process does not prove exchange connectivity, and a successful Testnet preflight does not enable real-money trading. Keep all Live execution gates disabled.
