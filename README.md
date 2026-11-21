# Iskander AI Trade

A modular, risk-first cryptocurrency trading system. The initial release is paper-trading only; live execution remains disabled until separately reviewed and explicitly enabled.

## Status

Foundation scaffold in progress. The supplied EMA bot is treated as a prototype, not as a production-ready trading system.

## Safety defaults

- Spot markets only; no margin, futures, or leverage.
- Paper mode by default.
- No exchange credentials required for market-data and paper mode.
- Explicit daily-loss and position-size limits.
- No guarantee of profit.

See `.env.example` for configuration and `docs/SAFETY.md` for the execution policy.
