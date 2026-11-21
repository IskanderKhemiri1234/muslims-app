# Trading Safety Policy

## Current release boundary

The application uses public Binance Spot market data and a paper-only simulator. Configuration validation rejects every mode other than `paper`. The API does not provide order-submission routes. No API key is requested or stored, and no real exchange order can be submitted.

The dashboard's Random Forest model is an offline research utility. Its metrics are not trade recommendations, predictions of guaranteed outcomes, or authorization for the strategy engine to act.

## Risk defaults

- Virtual starting capital: 70 USDT.
- Planned risk budget per position: 0.35 USDT.
- Stop-loss reference: 2%.
- Take-profit reference: 4%.
- Maximum daily realized loss: 1.40 USDT.
- Maximum concurrent paper positions: 3.
- Maximum notional per position: 20 USDT.
- USDT-quoted Spot markets only; no margin, futures, or leverage.

Position notional is bounded by the risk budget divided by the stop distance, the per-position notional cap, and remaining virtual capital. These controls do not guarantee the simulated loss will remain within the planned budget.

## Known limitations

- Fees, spread, slippage, partial fills, exchange filters, and minimum-notional rules are not yet modeled.
- Stop execution assumes a simple ticker-price fill and does not model gaps or liquidity.
- The EMA crossover is a transparent technical baseline, not a validated strategy.
- ML evaluation is preliminary and lacks cost-aware backtesting, walk-forward testing, and an untouched holdout.
- Paper state is local JSON on an ephemeral free-host filesystem; it may be lost on sleep, restart, or redeploy. It is not suitable for unattended trading or durable portfolio accounting.
- The free-host API runs paper cycles only when the operator explicitly requests them; it does not run continuously while the service is asleep.
- Daily accounting uses the host machine's calendar date.
- The Render Blueprint uses paid always-on compute and a persistent disk; the free web tier is unsuitable for a continuous worker and durable local state.

## Release gates

Any future execution adapter must remain separate and must not be enabled by an environment variable alone. Before considering real-money integration, require independent code review, exchange testnet integration, deterministic order-lifecycle and recovery tests, fee/slippage modeling, durable transactional storage, monitoring and alerts, explicit user-controlled enablement, and a documented rollback procedure.
