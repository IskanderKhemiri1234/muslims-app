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

## Market-data integrity gate

Every OHLCV batch is validated before it reaches the EMA strategy. The gate rejects unsupported intervals, insufficient history, malformed/non-numeric/non-finite rows, unaligned or duplicate/out-of-order timestamps, impossible OHLC relationships, negative prices or volume, future-dated candles, and data older than two timeframe intervals. A rejected market is skipped for that cycle and cannot create a paper entry. The strategy continues to exclude the currently-forming candle. These checks establish input integrity; they do not establish predictive edge or profitability.

## Dashboard experience

The responsive Iskander AI Trade dashboard uses a dark emerald terminal layout with a compact mobile bottom navigation, a live six-market universe strip, portfolio performance chart, open paper positions, recent trade activity, and a strategy/risk snapshot populated from the active server configuration. All displayed balances and P/L values are explicitly virtual paper-trading values.

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

## Post-deployment smoke test

After the provider has started the service and the HTTPS URL is active, run the safe smoke test from a trusted terminal:

```bash
BASE_URL=https://your-service-url DASHBOARD_TOKEN="$DASHBOARD_TOKEN" python scripts/smoke_test.py
```

It checks HTTPS health, paper-only mode, scheduler startup, the six configured markets, dashboard authentication, paper capital/risk settings, and market-data API access. It does not trigger a trading cycle and does not submit orders. Do not paste the dashboard token into chat or commit it.

## Deployment status

This branch is being prepared for the free Enzonic Python service. The service has not yet been provisioned: the free request is still waiting for available capacity. No live dashboard URL exists until the provider finishes creating the server and the app is deployed and health-checked. Do not select paid capacity to bypass the queue.

No trading system can guarantee profit. Do not connect real funds based on paper or ML research results.

## Regime-aware trade selection (paper research gate)

A long EMA crossover is only a candidate, not an automatic entry. The paper engine now requires a 15-minute uptrend regime, a rising 1-hour EMA20/EMA50 context, positive short-horizon momentum, relative volume, a validated bid/ask spread within the configured limit, a non-extended entry distance from EMA20 measured in ATR, a strong candle close as a wick/failed-breakout risk proxy, and a minimum net reward/risk after estimated round-trip taker fees, slippage, and bid-ask spread. Range, downtrend, transition, volatility-shock, missing-context, and failed-quality candidates are rejected with reason codes.

The screening thresholds are explicit initial heuristics, not fitted probabilities or claims of an edge. Defaults for execution-cost research are `TAKER_FEE_RATE=0.001`, `SLIPPAGE_RATE=0.0005`, and `MAX_SPREAD_PCT=0.15`; confirm actual venue fees and calibrate assumptions before interpreting results. The engine records up to 500 candidate decisions in persistent state and exposes the latest 100 through the protected status API. Paper entry sizing includes estimated round-trip costs, and paper fills model half-spread plus slippage on each side, and closed-trade P/L deducts entry/exit fees and modeled execution costs.

These controls do not establish profitability. They must be compared with the EMA baseline using the same chronological data, realistic costs, walk-forward validation, and a final untouched holdout before any claim of improved performance.


## Historical EMA comparison, walk-forward and holdout

Run the research-only historical comparison from a trusted Python 3.11 environment:

```bash
pip install -e ".[dev]"
python scripts/run_backtest.py --since 2024-01-01
```

The runner downloads public Binance Spot OHLCV for the six configured markets at 15m and 1h, caches CSV files under `data/research/`, and writes `reports/ema_backtest_report.json`. Reuse a complete local cache with `--offline`. Downloaded candles and reports are intentionally git-ignored.

The report compares the unfiltered EMA 20/50 crossover baseline with the same EMA entry candidates after the regime, 1h context, momentum, relative-volume, spread, candle-close, anti-chase and net reward/risk gates. Both strategies use the same chronological candles, portfolio capital, risk sizing, maximum position count, stop/target rules, next-open fills and modeled execution costs.

Metrics include net P/L, win rate, average winning and losing trade, expectancy per trade, profit factor, mark-to-market maximum drawdown, fees, modeled slippage, assumed spread cost, selection rejections by candidate and reason, capacity rejections, and per-market trade statistics. Signals are evaluated only after a candle closes and filled at the next 15m open. If a candle touches both stop and target, the simulator assumes the stop occurred first. Missing historical candle intervals fail closed.

Walk-forward uses sequential 30-day out-of-sample folds after a 180-day indicator/context warmup. The final 90 days are reserved as an untouched holdout. Parameters are frozen: the warmup period initializes causal indicators and does not fit or optimize thresholds. Each fold and the holdout start with the configured virtual capital; pooled walk-forward P/L is explicitly the sum of independent folds, and the report also shows the worst fold drawdown. Do not tune settings after viewing holdout results.

**Spread limitation:** Binance OHLCV does not contain historical best-bid/ask quotes. The default 0.05% round-trip spread is an explicit assumption, not a measured historical spread. Change it with `--assumed-spread-pct 0.10` and rerun sensitivity scenarios before interpreting the results. Taker fee and slippage defaults are also assumptions and must be replaced with the applicable account/venue fee schedule and defensible execution estimates.

The backtest is a research simulation, not live execution evidence. A positive result is not proof of a durable edge or future profitability.
