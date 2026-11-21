# Roadmap

## Phase 1 — Safe foundation
- [x] Dedicated Python project, public multi-symbol Binance Spot data, EMA baseline.
- [x] Paper position simulation, local atomic state, risk sizing and daily-loss guard.
- [x] Initial unit tests and CI workflow.
- [ ] Add fee, spread, slippage, partial-fill and exchange-filter assumptions.
- [ ] Expand deterministic engine and recovery tests.

## Phase 2 — Research and validation
- [x] First offline Random Forest research module with chronological validation.
- [x] Dashboard endpoint for per-market research metrics and baseline comparison.
- [ ] Historical data downloader with caching and data-quality checks.
- [ ] Cost-aware backtesting engine with no look-ahead bias.
- [ ] Walk-forward and untouched holdout evaluation.
- [ ] Strategy registry and portfolio-level exposure controls.
- [ ] Reports for drawdown, expectancy, profit factor, and risk-adjusted return.

## Phase 3 — Operations
- [x] FastAPI dashboard and token-protected operator endpoints.
- [x] Combined API and paper worker process for a single Render instance.
- [x] Render Blueprint with persistent disk configuration (not yet deployed).
- [x] Android WebView client and debug APK CI workflow.
- [ ] Move paper state to transactional Postgres before multi-instance deployment.
- [ ] Add structured logs, alerts, backups, and restart/recovery tests.
- [ ] Deploy only after the required always-on compute and disk costs are approved.

## Phase 4 — Optional execution integration
- [ ] Separate exchange execution adapter.
- [ ] Testnet-only lifecycle validation.
- [ ] Independent safety review and explicit authorization before any live capability.
