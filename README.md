# Iskander AI Trade

A standalone multi-symbol Binance Spot research and paper-trading application. It is separate from the Muslims-app Flutter project.

> **Safety boundary:** paper mode only. No exchange account keys are read, no authenticated exchange endpoint is used, and no real order can be submitted. The EMA strategy is a transparent baseline, not a trained trading AI. ML is offline research only; no profitability is established.

## Runtime and markets

- Python 3.11+; FastAPI dashboard and paper engine run in one Python service.
- Six default USDT Spot markets: BTC, ETH, BNB, SOL, XRP and ADA.
- 15-minute timeframe; EMA 20/50 signals use closed candles only.
- Automatic scheduler wakes 8 seconds after each UTC quarter-hour boundary. It runs one cycle, then waits for the next boundary.
- A process-wide non-blocking lock prevents the scheduled and manual dashboard cycle from overlapping. Run exactly one Uvicorn worker; multiple workers would each start a scheduler.
- Paper settings: 70 USDT virtual capital, 0.35 USDT planned risk per trade, 1.40 USDT daily realized-loss guard, maximum 3 positions, 20 USDT position-notional cap, 2% stop and 4% target.
- No real orders, futures, leverage, or exchange account credentials.

## Low-memory deployment

The web runtime dependencies are intentionally kept small. NumPy and scikit-learn are optional and are not installed by the normal production install. The on-demand ML endpoint requires the `research` extra and may exceed a 512 MB memory limit; do not run it on a 512 MB instance. Research tests install the ML dependencies through the dev extra.

Install and start:

```bash
python -m pip install --upgrade pip
pip install .
```

Configure environment variables (do not commit secrets):

- `MODE=paper`
- `AUTO_PAPER_ENABLED=true`
- `DASHBOARD_TOKEN`: unique random secret, at least 32 characters (64 random characters recommended)
- `STATE_PATH`: path on the host's persistent disk, e.g. `/home/container/data/paper_state.json` only if that path is confirmed persistent by the provider
- `WATCHLIST=BTC/USDT,ETH/USDT,BNB/USDT,SOL/USDT,XRP/USDT,ADA/USDT`
- `TIMEFRAME=15m`

Start one API process:

```bash
uvicorn iskander_trade.api:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1
```

The public health endpoint is `/health`; the dashboard is `/`. Protected API routes require `Authorization: Bearer <DASHBOARD_TOKEN>`. The browser dashboard keeps the token in page memory only and clears it when disconnected; it does not write it to local storage.

## State durability

Paper state is written as JSON using fsync + atomic replacement. The previous valid snapshot is retained as a `.bak` file and can be recovered if the main JSON becomes corrupt. The state file and backup must live on persistent host storage. Atomic writes and backups cannot protect data if the hosting provider deletes or recreates the entire filesystem. Verify the provider's disk-retention policy before relying on trade history.

The scheduler is in-process: it operates continuously only while the Python service process is alive. Provider maintenance, restarts, or free-plan suspension pause it. It catches up at the next quarter-hour after restart; it does not replay missed candles.

## Research and tests

Install research dependencies only on a machine with sufficient memory:

```bash
pip install ".[research]"
```

Run checks:

```bash
pip install ".[dev]"
pytest -q
ruff check src tests
```

ML evaluation is explicitly on-demand, uses chronological TimeSeriesSplit and a majority-class baseline, and is not used by the paper order engine. The Random Forest is limited to one worker and 100 trees to reduce resource use.

## Android client

The Android WebView client accepts the deployed HTTPS dashboard URL. It does not contain exchange credentials. The GitHub Actions workflow builds an installable debug APK; this is not a Play Store production release.

## Deployment status

This branch is being prepared for the free Enzonic Python service. The service has not yet been provisioned: the free request is still waiting for available capacity. No live dashboard URL exists until the provider finishes creating the server and the app is deployed and health-checked. Do not select paid capacity to bypass the queue.

No trading system can guarantee profit. Do not connect real funds based on paper or ML research results.
