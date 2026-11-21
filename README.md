# Iskander AI Trade

A standalone multi-symbol Binance Spot research and paper-trading application. It is separate from the Muslims-app Flutter project.

> **Safety boundary:** paper mode only. No exchange account keys are read, no authenticated exchange endpoint is used, and no real order can be submitted. The ML module is research-only and does not authorize trades. This is not a production-validated or profitable trading system.

## Current implementation

- Configurable multi-symbol USDT Spot watchlist; six symbols are configured by default.
- 15m closed-candle EMA crossover baseline (20/50 defaults).
- Paper engine with a 70 USDT virtual balance, 0.35 USDT planned risk budget, 1.40 USDT daily realized-loss guard, three-position cap, and 20 USDT position-notional cap.
- Atomic local JSON state persistence.
- FastAPI dashboard/API with token-protected status and market routes.
- Dashboard includes paper portfolio, market watch, recent trades, and an offline ML evaluation panel.
- Offline Random Forest research with chronological TimeSeriesSplit validation and a majority-class baseline.
- Android WebView client and GitHub Actions workflow that builds an installable debug APK.
- Pytest and Ruff CI.

The ML report is a diagnostic metric only. It is not a reliable prediction, does not model trading costs, and is not connected to the order engine.

## Local setup

Python 3.11+:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

Set a long random `DASHBOARD_TOKEN` in `.env`, then run the API and paper worker together:

```bash
uvicorn iskander_trade.api:app --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000`, enter the dashboard token, and connect. The protected API is under `/api/v1`; `/health` is public. To run only the CLI worker instead, use `iskander-trade`.

Run checks:

```bash
pytest -q
ruff check src tests
```

## Android APK

The Android client asks for the deployed HTTPS dashboard URL, then opens the dashboard in a WebView. It does not contain exchange credentials. The workflow `.github/workflows/android-apk.yml` builds a debug APK and uploads it as the `iskander-ai-trade-debug-apk` Actions artifact. This is a debug-signed installable build, not a Play Store release or a separately managed production signing key.

## Render free deployment

`render.yaml` is configured for a single **free** Render web service, with no paid disk, worker, or database. The API does not run a background trading loop: the operator explicitly requests one paper cycle from the dashboard. The service sleeps after 15 minutes without inbound traffic and may restart at any time.

**Important free-tier limitations:** the service filesystem is ephemeral, so paper positions and trade history can be lost after sleep, restart, or redeploy. The free Render Postgres option is not a durable solution because it expires after 30 days and has no backups. This deployment is for evaluation and dashboard access only; it is not an always-on bot and must not be relied on to monitor markets continuously. Review [Render free-instance limits](https://render.com/docs/free).

## Known limitations

The simulator still needs explicit fee, spread, slippage, partial-fill, exchange-precision, minimum-notional, and gap-risk modeling. The ML dataset and validation are preliminary; no historical backtest, walk-forward study, untouched holdout report, or profitability evidence is available. Local JSON is single-instance storage, not a multi-replica database.

No trading system can guarantee profit. Do not connect real funds based on paper or ML research results.
