# SillyDev deployment — isolated Binance Spot Testnet

This deployment must use a separate directory and must not overwrite the existing Paper deployment or its data.

## 1. Obtain the tested bundle

After the GitHub Actions job is green, open the successful run for branch `feature/binance-testnet-safety-v2`, download artifact `iskander-ai-trade-testnet-v2`, and upload the ZIP through SillyDev Files into the `container` directory. Extract it into a new directory named `iskander-ai-trade-testnet-v2`. Do not replace `muslims-app-deploy-infrlo-paper-v1`.

## 2. Runtime requirements

Use Python 3.11 or newer. Install the lightweight runtime dependencies from `requirements-sillydev.txt`; this avoids installing the optional NumPy/scikit-learn research stack on the 512 MiB free server.

From the extracted project directory:

```sh
python --version
python -m pip install --user -r requirements-sillydev.txt
mkdir -p data
```

Set environment variables in the server's private environment settings, or create a private `.env` file in this new project directory. Never upload a populated `.env` to GitHub.

Required variables for the first manual test:

```dotenv
APP_NAME=Iskander AI Trade
MODE=paper
DASHBOARD_TOKEN=<your-new-long-random-dashboard-token>
BINANCE_API_KEY=<Binance-Spot-Testnet-key>
BINANCE_API_SECRET=<Binance-Spot-Testnet-secret>
BINANCE_TESTNET=true
ENABLE_LIVE_TRADING=false
BINANCE_ALLOWED_SYMBOLS=BTC/USDT
MAX_ORDER_QUOTE_USDT=10
TRADE_JOURNAL_PATH=data/testnet.sqlite3
RUN_BINANCE_TESTNET_WORKER=false
RUN_PAPER_WORKER=false
POLL_SECONDS=30
STATE_PATH=data/paper_state.json
```

Keep the key and secret exclusively in SillyDev private environment settings. They must be Binance Spot Testnet credentials, not production credentials. Do not paste them into a terminal command, dashboard, chat, GitHub issue, or source file. The app must keep `MODE=paper` because the existing research engine is paper-only; Testnet execution is an isolated API/service with its own `BINANCE_TESTNET=true` gate.

## 3. Start and verify manually

Start the app from the new project directory, binding the exact SillyDev app port:

```sh
python -m uvicorn iskander_trade.api:app --host 0.0.0.0 --port 6007
```

Check `http://37.114.41.97:6007/health` and confirm it returns HTTP 200. Connect the dashboard with the dashboard token, then click **Check Binance Testnet connection**. This preflight is read-only and must report `binance_spot_testnet`, `orders_submitted=false`, and the intended symbol.

The Testnet journal must be created at `data/testnet.sqlite3` in this new directory. Confirm the file exists and its size increases after Testnet operations. Back up the SQLite database and its `-wal`/`-shm` companions only after clean shutdown/checkpoint; do not copy a live WAL database as if it were a consistent standalone file.

## 4. Bounded round trip

Only after preflight succeeds:
1. Keep the emergency stop active initially.
2. Configure one Testnet pair, a quantity whose entry notional is at least Binance's current minimum and no more than the configured 10 USDT ceiling, plus entry, stop and target prices.
3. Release the emergency-stop latch, then arm only this one Testnet trade.
4. Run one manual Testnet cycle. Verify Binance Spot Testnet order and trade history show the exact client order ID, exchange order ID, executed quantity, price and commission asset/cost.
5. If the limit buy does not fill, do not force a sell; adjust only the Testnet limit price or cancel safely and reconcile. The service must not treat an unfilled order as a position.
6. Once a buy fill is confirmed, set a Testnet stop/target condition that causes the service to exit, then run the next manual cycle.
7. Verify the sell fill and fee in Binance Testnet history; compare net base inventory, quote balances, fee totals and the journal's `accounting_issues=[]`.
8. Activate the emergency stop again.

Never use production Binance credentials in this procedure.

## 5. Restart persistence test

After a cleanly reconciled round trip, stop the process, confirm the same `data/testnet.sqlite3` remains present, restart the same command, reconnect the dashboard, and verify trades, orders, fills, commissions, balances and emergency-stop state remain. Run one cycle and confirm no duplicate client order ID or second buy is submitted.

## 6. Continuous Testnet monitor

Only after the manual cycle and restart test both pass may `RUN_BINANCE_TESTNET_WORKER=true` be considered. A free host can stop or restart processes and is not a guaranteed always-on trading host. Do not rely on it for unattended monitoring.

## Live boundary

This branch does not expose a Live order route. Keep `ENABLE_LIVE_TRADING=false`; do not add production Binance credentials. Live remains a separate future release requiring separate code review, independent tests, a dedicated explicit enablement step, Spot-only API permissions with withdrawals disabled, and hard risk limits.
