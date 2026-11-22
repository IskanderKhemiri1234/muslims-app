# Forward paper validation — locked strategy protocol

**Status:** ACTIVE RESEARCH ONLY  
**Branch:** `research/portfolio-walkforward-v2`  
**Freeze timestamp:** `2026-11-22T00:00:00Z` (UTC)  
**First eligible signal:** first completed 4h candle at or after the freeze timestamp  
**Markets:** BTC/USDT, ETH/USDT, BNB/USDT, SOL/USDT, XRP/USDT, ADA/USDT  
**Capital:** 70 USDT virtual; Spot only; no leverage; no exchange account credentials; no live orders.

## Immutable strategy rules

1. **Donchian:** entry above the prior 55 completed 4h-bar highs; exit below the prior 20 completed 4h-bar lows or loss of the completed-daily SMA200 regime; initial stop 3 × ATR(14).
2. **Momentum:** select the top two of the six markets by 90-day return, rebalance daily, require completed-daily close above SMA200, and use a 3 × ATR(14) initial stop.
3. **Pullback:** completed-daily close above SMA200; 4h EMA50 above EMA200; enter on a completed 4h close reclaiming EMA20 from below with RSI(14) between 45 and 65; exit on close below EMA50 or daily regime failure; initial stop 2.5 × ATR(14), then trail at 3 × ATR(14) from the highest completed close.

All signals use completed candles and execute at the next 4h open. These rules, periods, markets, sizing, and execution assumptions are frozen. No optimization, symbol exclusions, threshold changes, or parameter search may use the forward validation period.

## Fixed execution assumptions

- Risk budget: 0.5% of marked-to-market equity per position.
- Maximum three concurrent positions; maximum 20 USDT notional per position.
- Taker fee: 0.10% per side.
- Slippage: 0.05% per side.
- Round-trip spread assumption: 0.10%.
- Costs are included in net trade PnL and expectancy.

## Measurement protocol

The GitHub Actions workflow is manually dispatchable on this isolated research branch. GitHub scheduled workflows run from the repository's default branch; enabling a four-hour schedule would therefore require changing the default branch, which is intentionally out of scope because main must remain untouched. Each manual run replays the complete post-freeze candle history from the fixed start, with pre-freeze candles used only for indicator warm-up. The replay does not force-close open positions at report boundaries; this prevents artificial repeated exits. Reports are cumulative snapshots, not independent samples. The workflow can be run again as new candles arrive.

For each strategy, record closed trades, win rate, average win/loss, net expectancy after fees/spread/slippage, profit factor, drawdown, symbol/exit/month attribution, open positions, and drift against the historical walk-forward and holdout reference figures. A strategy is not eligible for a decision until it has at least 100 closed forward trades.

## Decision gate

No strategy is promoted unless it has at least 100 closed forward trades, positive net expectancy after all modeled costs, profit factor above 1, and a drawdown profile judged acceptable. If it fails, report NO-GO; do not tune it on the same forward sample. Any new strategy or altered rule set requires a new version and a new prospective freeze timestamp.

## Safety and scope

This workflow is a research simulation using public market candles. It cannot place orders. It does not change the running paper engine, production configuration, or `main`. No merge or live trading is authorized by this protocol.
