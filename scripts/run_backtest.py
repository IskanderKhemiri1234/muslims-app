"""Download public Binance OHLCV and run EMA baseline/screened research validation.

Examples:
  python scripts/run_backtest.py --since 2024-01-01
  python scripts/run_backtest.py --offline
  python scripts/run_backtest.py --assumed-spread-pct 0.10

The spread is an explicit assumption: historical OHLCV does not contain historical
best-bid/best-ask quotes. This script never places orders or reads account keys.
"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from iskander_trade.backtest import BacktestConfig, compare_strategies

ROOT = Path(__file__).resolve().parents[1]


DEFAULT_SYMBOLS = (
    "BTC/USDT", "ETH/USDT", "BNB/USDT",
    "SOL/USDT", "XRP/USDT", "ADA/USDT",
)
TIMEFRAMES = {"15m": 900_000, "1h": 3_600_000}
DAY_MS = 86_400_000


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", default="2024-01-01", help="UTC start date (YYYY-MM-DD)")
    parser.add_argument("--exchange", default="binance", help="CCXT public OHLCV provider, e.g. binance or okx")
    parser.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--holdout-days", type=int, default=90)
    parser.add_argument("--walk-forward-days", type=int, default=30)
    parser.add_argument("--warmup-days", type=int, default=180)
    parser.add_argument("--fee-rate", type=float, default=0.001)
    parser.add_argument("--slippage-rate", type=float, default=0.0005)
    parser.add_argument("--assumed-spread-pct", type=float, default=0.05)
    parser.add_argument("--max-spread-pct", type=float, default=0.15)
    parser.add_argument("--cache-dir", type=Path, default=ROOT / "data" / "research")
    parser.add_argument("--output", type=Path, default=ROOT / "reports" / "ema_backtest_report.json")
    parser.add_argument("--offline", action="store_true", help="Use existing CSV cache only")
    parser.add_argument("--refresh", action="store_true", help="Redownload even if cache exists")
    return parser.parse_args()


def _read_csv(path: Path) -> list[list[float]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return [[float(value) for value in row[:6]] for row in csv.reader(handle) if row]


def _write_csv(path: Path, rows: list[list[float]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerows(rows)


def _fetch_history(
    exchange: Any,
    symbol: str,
    timeframe: str,
    since_ms: int,
    end_exclusive_ms: int,
    cache_dir: Path,
    *,
    provider: str,
    offline: bool,
    refresh: bool,
) -> list[list[float]]:
    safe_symbol = symbol.replace("/", "_")
    path = cache_dir / f"{provider}_{safe_symbol}_{timeframe}.csv"
    if path.exists() and not refresh:
        cached = _read_csv(path)
        if cached and int(cached[0][0]) <= since_ms and int(cached[-1][0]) >= (
            end_exclusive_ms - TIMEFRAMES[timeframe]
        ):
            return cached
        if offline:
            raise RuntimeError(f"Cache does not cover requested interval: {path}")
    elif offline:
        raise FileNotFoundError(f"Offline cache missing: {path}")

    rows: dict[int, list[float]] = {}
    cursor = since_ms
    page_limit = 300 if provider == "okx" else 1000
    while cursor < end_exclusive_ms:
        batch = exchange.fetch_ohlcv(
            symbol, timeframe=timeframe, since=cursor, limit=page_limit
        )
        if not batch:
            break
        for raw in batch:
            timestamp = int(raw[0])
            if since_ms <= timestamp < end_exclusive_ms:
                rows[timestamp] = [float(value) for value in raw[:6]]
        next_cursor = int(batch[-1][0]) + TIMEFRAMES[timeframe]
        if next_cursor <= cursor:
            raise RuntimeError(f"Exchange pagination did not advance for {symbol} {timeframe}")
        cursor = next_cursor
        if len(batch) < page_limit:
            break

    result = [rows[key] for key in sorted(rows)]
    if len(result) < 100:
        raise RuntimeError(
            f"Insufficient downloaded history for {symbol} {timeframe}: {len(result)} rows"
        )
    _write_csv(path, result)
    return result


def _iso(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC).isoformat()


def _summarize_fold_results(folds: list[dict[str, Any]], key: str) -> dict[str, Any]:
    trades = [
        trade
        for fold in folds
        for trade in fold[key]["trades"]
    ]
    wins = [float(trade["net_pnl_usdt"]) for trade in trades if trade["net_pnl_usdt"] > 0]
    losses = [float(trade["net_pnl_usdt"]) for trade in trades if trade["net_pnl_usdt"] < 0]
    pnl = [float(trade["net_pnl_usdt"]) for trade in trades]
    return {
        "trades": len(trades),
        "win_rate_pct": len(wins) / len(trades) * 100 if trades else 0.0,
        "average_win_usdt": sum(wins) / len(wins) if wins else 0.0,
        "average_loss_usdt": sum(losses) / len(losses) if losses else 0.0,
        "expectancy_usdt_per_trade": sum(pnl) / len(pnl) if pnl else 0.0,
        "net_pnl_usdt_sum_of_independent_folds": sum(pnl),
        "max_drawdown_usdt_worst_fold": max(
            (float(fold[key]["summary"]["max_drawdown_usdt"]) for fold in folds),
            default=0.0,
        ),
        "rejected_candidates": sum(
            int(fold[key]["summary"]["rejected_candidates"]) for fold in folds
        ),
    }


def main() -> None:
    args = _parse_args()
    since = datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=UTC)
    since_ms = int(since.timestamp() * 1000)
    if min(args.holdout_days, args.walk_forward_days, args.warmup_days) <= 0:
        raise SystemExit("Validation window lengths must be positive.")

    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    datasets: dict[str, dict[str, list[list[float]]]] = {}
    if not args.offline:
        try:
            import ccxt
        except ImportError as exc:
            raise SystemExit('Install project dependencies first: pip install -e ".[dev]"') from exc
        exchange_factory = getattr(ccxt, args.exchange, None)
        if exchange_factory is None:
            raise SystemExit(f"Unsupported CCXT exchange id: {args.exchange}")
        exchange = exchange_factory({"enableRateLimit": True})
        exchange.load_markets()
    else:
        exchange = None

    symbols = tuple(item.strip() for item in args.symbols.split(",") if item.strip())
    for symbol in symbols:
        if exchange is not None and symbol not in exchange.markets:
            raise SystemExit(f"{args.exchange} Spot market is unavailable: {symbol}")
        datasets[symbol] = {}
        for timeframe, duration in TIMEFRAMES.items():
            end_exclusive = (now_ms // duration) * duration
            rows = _fetch_history(
                exchange, symbol, timeframe, since_ms, end_exclusive,
                args.cache_dir, provider=args.exchange,
                offline=args.offline, refresh=args.refresh,
            )
            datasets[symbol][timeframe] = rows
            print(f"Loaded {symbol} {timeframe}: {len(rows):,} candles")

    first_timestamp = max(
        int(series["15m"][0][0]) for series in datasets.values()
    )
    last_timestamp = min(
        int(series["15m"][-1][0]) for series in datasets.values()
    )
    holdout_start = last_timestamp - args.holdout_days * DAY_MS
    walk_start = first_timestamp + args.warmup_days * DAY_MS
    if holdout_start <= walk_start:
        raise SystemExit(
            "Not enough common history for warmup, walk-forward and untouched holdout. "
            "Choose an earlier --since or shorter windows."
        )

    config = BacktestConfig(
        taker_fee_rate=args.fee_rate,
        slippage_rate=args.slippage_rate,
        assumed_spread_pct=args.assumed_spread_pct,
        max_spread_pct=args.max_spread_pct,
    )
    config.validate()

    folds: list[dict[str, Any]] = []
    fold_start = walk_start
    fold_number = 1
    while fold_start < holdout_start:
        fold_end = min(
            fold_start + args.walk_forward_days * DAY_MS - config.timeframe_ms,
            holdout_start - config.timeframe_ms,
        )
        if fold_end < fold_start:
            break
        comparison = compare_strategies(
            datasets,
            config=config,
            evaluation_start_ms=fold_start,
            evaluation_end_ms=fold_end,
        )
        folds.append({
            "fold": fold_number,
            "start_utc": _iso(fold_start),
            "end_utc": _iso(fold_end),
            "baseline": comparison["baseline"],
            "screened": comparison["screened"],
        })
        print(
            f"Walk-forward fold {fold_number}: "
            f"{_iso(fold_start)} -> {_iso(fold_end)}"
        )
        fold_number += 1
        fold_start = fold_end + config.timeframe_ms

    holdout = compare_strategies(
        datasets,
        config=config,
        evaluation_start_ms=holdout_start,
        evaluation_end_ms=last_timestamp,
    )
    report = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "markets": list(symbols),
        "historical_data_provider": args.exchange,
        "data_start_utc": _iso(first_timestamp),
        "data_end_utc": _iso(last_timestamp),
        "validation_design": {
            "warmup_days": args.warmup_days,
            "walk_forward_test_days": args.walk_forward_days,
            "holdout_days": args.holdout_days,
            "walk_forward_folds": len(folds),
            "holdout_start_utc": _iso(holdout_start),
            "parameters_frozen": True,
            "training_note": (
                "Warmup is used only to initialize causal indicators and selection context. "
                "No parameters are fitted or tuned on walk-forward or holdout results."
            ),
        },
        "execution_cost_assumptions": {
            "taker_fee_rate_per_side": args.fee_rate,
            "slippage_rate_per_side": args.slippage_rate,
            "assumed_spread_pct_round_trip": args.assumed_spread_pct,
            "spread_warning": (
                "Spread is a user-configured assumption, not measured historical bid/ask data. "
                "Run multiple spread scenarios before drawing conclusions."
            ),
        },
        "walk_forward": {
            "folds": folds,
            "pooled_oos_baseline": _summarize_fold_results(folds, "baseline"),
            "pooled_oos_screened": _summarize_fold_results(folds, "screened"),
        },
        "untouched_holdout": {
            "baseline": holdout["baseline"],
            "screened": holdout["screened"],
        },
        "methodology": holdout["methodology"],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    print(f"Research report written to {args.output}")
    print(json.dumps({
        "walk_forward_baseline": report["walk_forward"]["pooled_oos_baseline"],
        "walk_forward_screened": report["walk_forward"]["pooled_oos_screened"],
        "holdout_baseline": holdout["baseline"]["summary"],
        "holdout_screened": holdout["screened"]["summary"],
    }, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
