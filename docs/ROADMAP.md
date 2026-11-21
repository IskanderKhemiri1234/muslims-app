# Roadmap

## Phase 1 — Safe foundation
- [x] Create isolated Python project in the dedicated repository.
- [x] Multi-symbol public Binance Spot market-data scanner.
- [x] Closed-candle EMA crossover baseline.
- [x] Paper position simulation and persistent state.
- [x] Position sizing and daily-loss guard.
- [ ] Add fee, spread, and slippage assumptions to paper fills.
- [ ] Add broader deterministic tests and continuous integration.

## Phase 2 — Research and validation
- [ ] Historical data downloader with caching and data-quality checks.
- [ ] Backtesting engine with realistic costs and no look-ahead bias.
- [ ] Walk-forward and out-of-sample evaluation.
- [ ] Strategy registry and portfolio-level exposure controls.
- [ ] Reports for drawdown, expectancy, profit factor, and risk-adjusted return.

## Phase 3 — Operations
- [ ] API and dashboard with authenticated operator access.
- [ ] Durable database, health checks, structured logs, and alerts.
- [ ] Persistent worker deployment and restart recovery.

## Phase 4 — Optional execution integration
- [ ] Separate exchange execution adapter.
- [ ] Testnet-only lifecycle validation.
- [ ] Independent safety review and explicit authorization before any live capability.
