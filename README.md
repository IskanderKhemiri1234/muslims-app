# Iskander AI Trade

A modular cryptocurrency trading research project, separated from the Muslims-app Flutter application. The first implementation is a multi-symbol Binance Spot market scanner with a transparent EMA baseline and a local paper-trading engine.

> **Safety boundary:** this build is paper-only. It cannot submit real exchange orders, does not request API keys, and rejects any mode other than `paper`. It is not a production-ready or profitable trading system.

## Current foundation

- Scans a configurable list of USDT spot markets (six example symbols by default).
- Uses closed-candle EMA crossover signals (20/50 by default, 15m timeframe).
- Sizes simulated positions from a 0.35 USDT risk budget and 2% stop reference.
- Uses 70 USDT virtual capital, a 1.40 USDT daily realized-loss limit, up to three concurrent positions, and a 20 USDT per-position notional cap.
- Persists simulated positions and trade history to local JSON.
- Includes unit tests and a GitHub Actions test/lint workflow.

The strategy is currently deterministic technical analysis, **not a trained AI/ML model**. The project name is the product identity; genuine ML research, historical validation, and strategy evaluation are later milestones.

## Repository layout

```text
src/iskander_trade/
  config.py       validated configuration; paper-only mode guard
  market_data.py  public Binance Spot OHLCV/ticker adapter
  indicators.py   EMA calculation
  strategy.py     closed-candle EMA crossover signal
  risk.py         risk-based position sizing and daily-loss guard
  state.py        atomic local paper-state persistence
  engine.py       multi-symbol paper execution loop
  main.py         command-line process entry point
tests/            deterministic unit tests
docs/             safety policy and development roadmap
```

## Run locally

Python 3.11 or newer is required.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
pytest -q
iskander-trade
```

On Windows, activate the environment with `.venv\\Scripts\\activate`.

The process needs public network access to read Binance market data. No account connection is required. Stop it with Ctrl+C.

## Configuration

Edit `.env` to change the watchlist, timeframe, virtual capital, risk budget, daily-loss limit, and strategy periods. Keep `MODE=paper`. The program intentionally refuses `testnet` and `live` until a separate execution adapter and safety review exist.

## Important limitations

The current paper simulator does not yet model exchange fees, spread, slippage, partial fills, minimum order sizes, or exchange precision. It is not a validated backtester and does not establish a trading edge. See [docs/SAFETY.md](docs/SAFETY.md) and [docs/ROADMAP.md](docs/ROADMAP.md).

No trading system can guarantee profit. Do not connect real funds based on paper results.
