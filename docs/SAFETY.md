# Trading Safety Policy

## Current release boundary

This repository currently implements public Binance market-data access and simulated paper positions only. The application deliberately rejects every mode other than `paper`. It does not load API keys, sign requests, or submit exchange orders.

Do not interpret a paper-trading result as evidence of profitability or as authorization to trade real funds.

## Risk defaults

- Virtual starting capital: 70 USDT.
- Planned risk budget per position: 0.35 USDT.
- Stop-loss reference: 2%.
- Take-profit reference: 4%.
- Maximum daily realized loss: 1.40 USDT.
- Maximum concurrent paper positions: 3.
- Maximum notional per position: 20 USDT.
- Spot symbols quoted in USDT; no margin, futures, or leverage.

Position notional is bounded by the risk budget divided by the stop distance, the per-position notional cap, and remaining virtual capital.

## Known limitations before any live-trading discussion

- Fees, spread, slippage, partial fills, exchange filters, and minimum-notional rules are not yet modeled.
- The baseline EMA crossover is a transparent technical rule, not machine learning or an AI prediction.
- There is no validated backtest, walk-forward evaluation, or out-of-sample report yet.
- Paper positions are local simulations and do not represent exchange balances or actual fills.
- Daily accounting currently uses the host machine's calendar date.
- The initial state file is local JSON and is not suitable for multi-instance production deployment.

## Release gates

A future execution adapter must remain a separate module and must not be enabled by changing an environment variable alone. Before considering any real-money integration, require independent code review, exchange testnet integration, deterministic tests for order lifecycle and recovery, fee/slippage modeling, durable transactional storage, monitoring/alerts, explicit user-controlled enablement, and a documented rollback procedure.
