# Frozen 2018+ BTC regime-filter experiment

**Status:** isolated historical research only  
**Branch:** `research/regime-filter-2018-v1`  
**Main:** untouched; no engine deployment or live orders.

## Freeze specification

- Strategies: Donchian Breakout and Cross-sectional Momentum only. Pullback is excluded from the runner and research engine.
- Markets: BTC/USDT, ETH/USDT, BNB/USDT, SOL/USDT, XRP/USDT, ADA/USDT.
- Signal bars: 4h completed candles; regime source: completed BTC 1d candles.
- Requested history start: 2018-01-01 UTC; each symbol begins at the first public candle returned by OKX. No synthetic or forward-filled candles.
- Initial virtual capital: 70 USDT; risk: 0.5% only.
- Existing research-engine portfolio limits remain fixed and identical in all paired runs: maximum three positions, 20 USDT maximum notional per position.
- Walk-forward reporting folds: 30 days, one uninterrupted portfolio per strategy/filter/spread scenario. Equity, positions, stops and drawdown carry across every fold and into Holdout.
- Holdout: final 90 days of that same simulation. It is not reset to fresh capital and no position is force-liquidated at the boundary or report endpoint.

## Immutable strategy rules

1. **Donchian Breakout:** enter at next 4h open after a completed 4h close breaks above the previous 55 completed 4h-bar highs; exit at next open after a completed close breaks below the previous 20 completed 4h-bar lows; initial stop 3 × ATR(14).
2. **Cross-sectional Momentum:** rank available symbols by trailing 90-day return (540 consecutive 4h candles); hold the top two, rebalance daily; exit when a symbol leaves the top two; initial stop 3 × ATR(14).

No per-asset SMA200 gate is applied to either baseline strategy. The only filter experiment is the global BTC regime gate below.

## One-time BTC filter comparison

Each strategy is run in paired versions with identical sizing, signals, costs and risk:

- **Without filter:** no BTC SMA gate.
- **With filter:** a new position may be opened only if the last completed BTC daily close is strictly above its daily SMA200. If BTC falls below the SMA200, no new positions are opened. Existing positions remain open and exit only under their ordinary strategy exit or stop rules.

SMA200 is fixed. No threshold, period, market, stop, ranking horizon or other parameter may be adjusted after viewing these results.

For descriptive regime attribution only, classify BTC as bull when its completed daily close is more than 1% above SMA200, bear when more than 1% below, and sideways within the ±1% band. This descriptive band does not alter the actual binary entry gate.

## Costs and execution

- OKX public spot taker fee assumption: 0.10% per side. Account-specific fee tiers are not accessed because no private credentials are used.
- Slippage: 0.05% per side.
- Round-trip spread scenarios: 0.05%, 0.10%, 0.15%; half the round-trip spread is charged on each fill.
- All entries execute at the next 4h open; all exits execute at the next 4h open after a completed signal, or at the stop price/gap open as appropriate.
- Net PnL reconciles as raw gross PnL minus entry/exit commissions, spread and slippage.
- No leverage, private account, API key, exchange order or real capital.

## Missing-history policy

The data loader preserves timestamp gaps. Indicator windows restart after a gap; Donchian and momentum lookbacks require fully consecutive candles. No candle is forward-filled or synthesized. If a position spans a missing 4h interval, it is closed at the first subsequently observed open with a clearly tagged `data_gap_exit`; pending orders are discarded across the gap. Each symbol's first returned candle and missing-interval counts are recorded.

## Pre-registered acceptance criteria

Evaluated at the 0.10% round-trip spread scenario:

1. At least 100 closed trades in Walk-forward for each strategy × filter scenario.
2. Positive net expectancy after all costs in both Walk-forward and final 90-day Holdout.
3. Holdout Profit Factor > 1.2.
4. No single UTC close year and no single symbol contributes more than 50% of positive net group PnL.
5. Maximum mark-to-market drawdown below 25% of initial capital (17.50 USDT) in both Walk-forward and Holdout.

The report includes all 12 combinations (2 strategies × 2 filter states × 3 spreads), annual results, entry-regime attribution, symbol and exit-reason breakdowns, fold continuity, costs, open positions, and explicit acceptance decisions.

If no scenario passes every pre-registered criterion, the final outcome is NO-GO. No tuning against this Holdout is permitted. Any future hypothesis requires a new branch, a new frozen protocol and a new unseen evaluation period.
