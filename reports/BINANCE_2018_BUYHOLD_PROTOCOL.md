# Binance Vision long-history replay + Buy & Hold benchmark

**Branch:** `research/binance-2018-buyhold-v1`  
**Parent:** `research/regime-filter-2018-v1`  
**Scope:** isolated research only. `main`, production engine, account credentials and real orders are out of scope.

## Data-source gate

- Exclusive provider: Binance's public unauthenticated SPOT archive at `https://data.binance.vision/data/spot`.
- Official archive paths: monthly and daily `klines/{SYMBOL}/{4h|1d}/...` ZIPs. The loader checks a known BTC 2017-08 4h and 1d archive before downloading anything.
- No API keys, private account access, exchange order endpoints, or alternate data providers.
- Requested archive history begins 2017-08-01 UTC, covering the first available archive period for the requested assets. Each asset's actual first returned 4h and 1d candle is recorded.
- A 404 is treated as an absent archive period; transport errors, non-404 HTTP errors, malformed ZIP/CSV, or a failed required BTC source probe stop the run. No fallback source is permitted.
- Binance archive timestamps are milliseconds before 2025 and microseconds from 2025-01-01; microseconds are normalized to milliseconds.
- Only completed candles are retained. No missing candle is forward-filled or synthesized. Every adjacent timestamp discontinuity is counted; missing archive periods are also recorded.
- Strategy indicators restart at data gaps and the inherited simulator's gap-exit policy remains unchanged.

## Frozen strategy inheritance

The runner imports the exact engine from `research/regime-filter-2018-v1`; no strategy or filter implementation is changed in this experiment.

- Markets: BTC/USDT, ETH/USDT, BNB/USDT, SOL/USDT, XRP/USDT, ADA/USDT.
- Signal candles: 4h; daily eligibility and BTC regime: completed 1d candles.
- Capital: 70 USDT; risk: 0.5%; existing fixed limits remain max three positions and 20 USDT maximum notional per position.
- Donchian: per-asset completed daily close>SMA200; breakout above prior 55 completed 4h highs; exit below prior 20 completed 4h lows or asset regime failure; initial stop 3×ATR(14).
- Cross-sectional Momentum: eligible assets are per-asset daily close>SMA200; rank by trailing 90-day return over 540 consecutive 4h candles; hold top two; daily rebalance; initial stop 3×ATR(14).
- Paired BTC filter: the existing entry-only global gate, strictly completed BTC daily close>SMA200. No alternative filters or thresholds.
- All 12 combinations: two strategies × two BTC-filter states × three round-trip spread scenarios (0.05%, 0.10%, 0.15%).
- Costs: 0.10% commission per side, 0.05% slippage per side, half of the round-trip spread on each fill.
- One uninterrupted portfolio per scenario. 30-day Walk-forward windows are attribution windows only; equity, open positions and risk carry through folds. Final 90 days are Holdout.

## Buy & Hold references

Both references use the exact same UTC evaluation start/end as the strategy full-period Calmar comparison.

1. **Equal-weight six-market Buy & Hold:** 70 USDT is divided into six equal initial dollar allocations. Each market is bought at its first available 4h open on/after the common evaluation start. If an asset is not yet listed/available, its allocation remains cash until its first available candle. Holdings are never rebalanced or sold.
2. **BTC-only Buy & Hold:** the full 70 USDT is invested in BTC at the first available 4h open on/after the evaluation start and held.

Both benchmarks pay commission, slippage and half the scenario spread only on initial entry. No exit cost is charged because the benchmark is not sold. The report includes total return, max drawdown and Calmar for each spread. Calmar is annualized CAGR divided by maximum drawdown as a fraction of the highest marked equity.

## Reporting

- First candle, last completed candle, candle counts, 4h/1d gap counts, missing archive periods and zero synthetic candles per market.
- All 12 strategy/filter/spread scenarios.
- Walk-forward and Holdout trades, expectancy, Profit Factor and max drawdown.
- Per-year trade metrics, portfolio mark-to-market drawdown, per-symbol results, exit reasons, and binary BTC above-SMA200 / below-or-equal-SMA200 entry regime metrics. The inherited bull/bear/sideways descriptive labels are also reported separately.
- Dedicated 2018 and 2022 year extracts.
- Positive-PnL concentration by UTC close year and symbol.
- Full-period total return, max drawdown and Calmar against both Buy & Hold references.
- Exact cost reconciliation and open positions at report end.

Drawdown definitions are explicit: annual portfolio drawdown is marked-to-market equity drawdown within the UTC close year; per-regime drawdown is cumulative closed-trade PnL drawdown for trades entered in that regime.

## Frozen acceptance at 0.10% round-trip spread

Each strategy × BTC-filter candidate must pass every condition:

1. At least 100 closed Walk-forward trades.
2. Positive net expectancy in both Walk-forward and Holdout.
3. Holdout Profit Factor strictly greater than 1.2.
4. No UTC close year and no symbol contributes more than 50% of positive net PnL.
5. Full-period max drawdown strictly below 17.50 USDT (25% of initial capital).
6. Full-period Calmar strictly greater than the equal-weight six-market Buy & Hold Calmar.
7. Positive expectancy in at least three distinct UTC close years, including positive expectancy in a bearish or sideways BTC regime bucket.

No parameter tuning, alternative filters, universe changes, or Holdout-driven experiments are allowed. If all candidates fail, the final result is **NO-GO**.

## Multiple-testing / Holdout caveat

The project has explored four distinct strategy families so far: **EMA, Donchian, Cross-sectional Momentum and Pullback**. This repeated exploration increases multiple-testing and selection-bias risk and must be treated as a material warning.

The project-level Holdout is **not fully blind**. Previous project experiments exposed overlapping historical periods and Holdout outcomes. The final 90 days in this run are not an untouched project-level blind test.

## Verification

- Unit tests include cost reconciliation, archive timestamp normalization, gap preservation, completed-daily BTC filter look-ahead protection, and a future-bar mutation test for momentum ranking.
- Ruff must pass.
- Full JSON report is uploaded as a GitHub Actions artifact only after successful source download, simulation, tests and lint.
