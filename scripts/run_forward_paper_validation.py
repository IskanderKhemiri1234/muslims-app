"""Prospective, locked-rule paper validation. Replays only candles after the freeze date.

The full history download is used solely for indicator warm-up. No pre-freeze
candle is eligible for a signal, fill, trade, or performance measurement.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import ccxt

import run_continuous_portfolio_research as research

FREEZE_UTC = "2026-11-22T00:00:00Z"
WARMUP_SINCE = "2023-01-01"
RISK_FRACTION = 0.005
ROUND_TRIP_SPREAD_PCT = 0.10
HISTORICAL_REFERENCE = {
    "donchian": {"walk_forward_expectancy_usdt": 0.246, "holdout_expectancy_usdt": -0.018,
                 "walk_forward_trades": 128, "holdout_trades": 16},
    "momentum": {"walk_forward_expectancy_usdt": 0.278, "holdout_expectancy_usdt": -0.131,
                 "walk_forward_trades": 150, "holdout_trades": 31},
    "pullback": {"walk_forward_expectancy_usdt": -0.005, "holdout_expectancy_usdt": -0.066,
                 "walk_forward_trades": 385, "holdout_trades": 56},
}


def main() -> None:
    exchange = ccxt.okx({"enableRateLimit": True, "timeout": 30000})
    warmup_ms = exchange.parse8601(WARMUP_SINCE + "T00:00:00Z")
    freeze_ms = exchange.parse8601(FREEZE_UTC)
    data = {}
    for symbol in research.SYMBOLS:
        print(f"Downloading public OKX candles: {symbol}", flush=True)
        data[symbol] = {
            "4h": research.fetch_history(exchange, symbol, "4h", warmup_ms),
            "1d": research.fetch_history(exchange, symbol, "1d", warmup_ms),
        }

    last_completed = min(int(series["4h"][-1][0]) for series in data.values())
    if last_completed <= freeze_ms:
        raise RuntimeError("No completed post-freeze 4h candle is available yet")

    prepared = research.indicators(data)
    results = {}
    for strategy in research.STRATEGIES:
        result = research.simulate(
            prepared, strategy, RISK_FRACTION, ROUND_TRIP_SPREAD_PCT,
            freeze_ms, last_completed, liquidate_at_end=False,
        )
        summary = result["summary"]
        results[strategy] = {
            "frozen_rules": True,
            "risk_fraction": RISK_FRACTION,
            "round_trip_spread_pct": ROUND_TRIP_SPREAD_PCT,
            "summary_closed_trades": summary,
            "trade_diagnostics": research.diagnostics(result["trades"]),
            "open_positions": result["open_positions"],
            "open_position_count": len(result["open_positions"]),
            "historical_reference": HISTORICAL_REFERENCE[strategy],
            "historical_expectancy_drift_vs_walk_forward_usdt":
                summary["expectancy_usdt_per_trade"]
                - HISTORICAL_REFERENCE[strategy]["walk_forward_expectancy_usdt"],
            "historical_expectancy_drift_vs_holdout_usdt":
                summary["expectancy_usdt_per_trade"]
                - HISTORICAL_REFERENCE[strategy]["holdout_expectancy_usdt"],
            "minimum_100_closed_trades_reached": summary["trades"] >= 100,
        }

    report = {
        "mode": "prospective_locked_rules_paper_only",
        "freeze_utc": FREEZE_UTC,
        "last_completed_4h_candle_utc": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(last_completed / 1000)),
        "provider": "OKX public OHLCV",
        "markets": research.SYMBOLS,
        "signal_timeframe": "4h",
        "capital_usdt": research.INITIAL_CAPITAL,
        "spot_only": True,
        "leverage": False,
        "mainnet": False,
        "warmup_policy": "Pre-freeze candles are used only to calculate indicators; no pre-freeze trades or PnL are included.",
        "replay_policy": "Cumulative replay from the fixed freeze timestamp on every run; open positions remain open and are not force-liquidated at report boundaries.",
        "execution_assumptions": {
            "entry": "next 4h open",
            "fee_each_side": research.FEE_RATE,
            "slippage_each_side": research.SLIPPAGE_RATE,
            "spread_round_trip_pct": ROUND_TRIP_SPREAD_PCT,
            "risk_fraction": RISK_FRACTION,
            "max_positions": research.MAX_POSITIONS,
            "max_notional_per_position_usdt": research.MAX_NOTIONAL,
        },
        "decision_gate": {
            "minimum_closed_trades_per_strategy": 100,
            "require_positive_net_expectancy_after_costs": True,
            "require_profit_factor_above_1": True,
            "require_drawdown_review": True,
            "no_parameter_changes_before_gate": True,
            "no_live_trading_or_mainnet": True,
        },
        "strategies": results,
    }
    Path("reports").mkdir(exist_ok=True)
    Path("reports/forward_paper_validation.json").write_text(
        json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({
        name: {"closed_trades": value["summary_closed_trades"]["trades"],
               "expectancy": value["summary_closed_trades"]["expectancy_usdt_per_trade"],
               "open_positions": value["open_position_count"],
               "minimum_100_reached": value["minimum_100_closed_trades_reached"]}
        for name, value in results.items()
    }, indent=2))


if __name__ == "__main__":
    main()
