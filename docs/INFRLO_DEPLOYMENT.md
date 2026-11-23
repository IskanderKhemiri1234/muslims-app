# Infrlo deployment — Iskander AI Trade

This is an isolated deployment path on `deploy/infrlo-paper-v1`. It does not modify `main` or the research branches.

## Scope and safety

- This repository build is **PAPER mode only**. It uses public Binance market data and a virtual 70 USDT balance.
- No exchange account keys are required or read. No authenticated exchange endpoints or real orders exist in this build.
- `RUN_PAPER_WORKER=true` starts the existing paper engine in a background thread inside the same FastAPI process, so the dashboard and paper worker share one process.
- The worker uses the configured six-market watchlist, 15m timeframe, and existing risk limits. It runs one cycle, then waits `POLL_SECONDS` (default 30 seconds).
- A health endpoint is available at `/health`; dashboard/API operations remain protected by `DASHBOARD_TOKEN`.

## Deploy on Infrlo

1. Create an Infrlo account at https://dash.infrlo.com/ (the provider advertises a free plan without a payment card).
2. Connect GitHub and grant the Infrlo GitHub App access only to `IskanderKhemiri1234/muslims-app`.
3. Create one Python application from branch `deploy/infrlo-paper-v1`.
4. Set build command to:

   ```bash
   pip install .
   ```

5. Set start command to:

   ```bash
   uvicorn iskander_trade.api:app --host 0.0.0.0 --port ${PORT:-8000}
   ```

6. Set these environment variables in the provider dashboard (do not commit secrets):

   | Variable | Value |
   |---|---|
   | `RUN_PAPER_WORKER` | `true` |
   | `MODE` | `paper` |
   | `DASHBOARD_TOKEN` | Generate a long, unique random secret |
   | `STATE_PATH` | `data/paper_state.json` |
   | `WATCHLIST` | `BTC/USDT,ETH/USDT,BNB/USDT,SOL/USDT,XRP/USDT,ADA/USDT` |
   | `TIMEFRAME` | `15m` |
   | `POLL_SECONDS` | `30` |
   | `VIRTUAL_CAPITAL_USDT` | `70` |
   | `RISK_PER_TRADE_USDT` | `0.35` |
   | `MAX_DAILY_LOSS_USDT` | `1.40` |
   | `MAX_OPEN_POSITIONS` | `3` |
   | `MAX_POSITION_NOTIONAL_USDT` | `20` |

7. Deploy, wait for a healthy build, and open the HTTPS application URL.
8. Check `/health`. It must report `mode=paper` and `execution=continuous_paper_worker`.
9. Open the dashboard and enter the same `DASHBOARD_TOKEN`.

## Important limitations

- Infrlo's public site advertises a free tier with one application, 512 MB RAM, 0.5 vCPU, 3 GB storage and no card. Its GitHub README advertises 24/7 bot hosting. Provider terms describe a 99.9% uptime SLA, but this is not an independent guarantee of continuous availability for this specific trading workload. Validate the deployed service and monitor it before relying on it.
- The project stores paper state in a local JSON file. Before redeploys or migrations, download/back up `data/paper_state.json` from the app's file manager if available. Do not assume container files survive a rebuild unless the provider confirms persistent volumes.
- 512 MB is a constrained environment, especially because the optional ML endpoint imports NumPy/scikit-learn. If the process is OOM-killed, disable/remove the ML endpoint for this deployment or move to a larger instance; do not raise the memory limit through a paid upgrade without the user's approval.
- A hosted dashboard URL does not make this build suitable for real-money trading. The code remains paper-only and is not a live-trading release.
- Do not use keep-alive pings to evade provider sleep policies. Confirm the host's current free-plan runtime policy.

## Local verification

```bash
pip install -e ".[dev]"
pytest -q
ruff check src tests
```
