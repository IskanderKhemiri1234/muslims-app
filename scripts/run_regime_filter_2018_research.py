"""Frozen 2018+ Donchian/Momentum regime-filter experiment. Research only."""
from __future__ import annotations

import json
import time
from collections import defaultdict
from pathlib import Path

import ccxt

import regime_filter_research_engine as engine

FREEZE_SPEC = {
    "start_date_utc": "2018-01-01",
    "holdout_days": 90,
    "fold_days": 30,
    "capital_usdt": 70.0,
    "risk_fraction": 0.005,
    "spread_round_trip_pct": [0.05, 0.10, 0.15],
    "fee_each_side_pct": 0.10,
    "slippage_each_side_pct": 0.05,
    "btc_sideways_band_pct": 1.0,
    "strategies": ["donchian", "momentum"],
    "filter_variants": ["without_btc_sma200", "with_btc_sma200"],
    "markets": engine.SYMBOLS,
}


def summarize(trades: list[dict], points: list[dict], start: int, end: int) -> dict:
    selected = [t for t in trades if start <= t["closed_at"] < end]
    curve = [p for p in points if start <= p["timestamp"] < end]
    start_eq = curve[0]["equity"] if curve else engine.INITIAL_CAPITAL
    end_eq = curve[-1]["equity"] if curve else start_eq
    peak = start_eq
    max_dd = 0.0
    for point in curve:
        peak = max(peak, point["equity"])
        max_dd = max(max_dd, peak - point["adverse_equity"])
    return engine.metric_summary(selected, max_dd, start_eq, end_eq)


def grouped(trades: list[dict], key_fn) -> dict:
    groups = defaultdict(list)
    for trade in trades:
        groups[str(key_fn(trade))].append(trade)
    return {
        key: {
            **engine.metric_summary(items, 0.0, engine.INITIAL_CAPITAL,
                                    engine.INITIAL_CAPITAL + sum(t["net_pnl_usdt"] for t in items)),
            "commission_usdt": sum(t["fees_usdt"] for t in items),
            "spread_slippage_usdt": sum(t["entry_execution_cost_usdt"] +
                                          t["exit_execution_cost_usdt"] for t in items),
            "exit_reasons": dict(sorted(_counts(t["exit_reason"] for t in items).items())),
        }
        for key, items in sorted(groups.items())
    }


def _counts(values):
    result = defaultdict(int)
    for value in values:
        result[value] += 1
    return result


def concentration(trades: list[dict], key_fn) -> float | None:
    buckets = defaultdict(float)
    for trade in trades:
        buckets[key_fn(trade)] += trade["net_pnl_usdt"]
    positive_total = sum(max(0.0, value) for value in buckets.values())
    if positive_total <= 0:
        return None
    return max((max(0.0, value) for value in buckets.values()), default=0.0) / positive_total


def yearly_report(trades: list[dict], points: list[dict]) -> dict:
    years = sorted({time.strftime("%Y", time.gmtime(t["closed_at"] / 1000)) for t in trades})
    output = {}
    for year in years:
        subset = [t for t in trades if time.strftime("%Y", time.gmtime(t["closed_at"] / 1000)) == year]
        year_points = [p for p in points if time.strftime("%Y", time.gmtime(p["timestamp"] / 1000)) == year]
        peak = float("-inf")
        local_dd = 0.0
        for point in year_points:
            peak = max(peak, point["equity"])
            local_dd = max(local_dd, peak - point["adverse_equity"])
        output[year] = {
            **engine.metric_summary(subset, local_dd, engine.INITIAL_CAPITAL,
                                    engine.INITIAL_CAPITAL + sum(t["net_pnl_usdt"] for t in subset)),
            "btc_regime_at_entry": grouped(subset, lambda t: t.get("entry_btc_regime", "unknown")),
            "by_symbol": grouped(subset, lambda t: t["symbol"]),
            "by_exit_reason": grouped(subset, lambda t: t["exit_reason"]),
        }
    return output


def run() -> dict:
    exchange = ccxt.okx({"enableRateLimit": True, "timeout": 30000})
    since = exchange.parse8601("2018-01-01T00:00:00Z")
    data = {}
    for symbol in engine.SYMBOLS:
        print(f"Fetching {symbol} from 2018-01-01 or first available listing candle", flush=True)
        data[symbol] = {
            "4h": engine.fetch_history(exchange, symbol, "4h", since),
            "1d": engine.fetch_history(exchange, symbol, "1d", since),
        }

    prepared = engine.indicators(data)
    btc = data["BTC/USDT"]["4h"]
    last_ts = min(int(data[s]["4h"][-1][0]) for s in engine.SYMBOLS)
    holdout_start = last_ts - 90 * engine.ONE_DAY
    wf_start = since + 220 * engine.ONE_DAY
    scenarios = {}
    for strategy in ("donchian", "momentum"):
        for use_filter in (False, True):
            for spread in (0.05, 0.10, 0.15):
                sim = engine.simulate(
                    prepared, strategy, 0.005, spread, wf_start, last_ts,
                    btc_filter=use_filter, liquidate_at_end=False,
                )
                trades, points = sim["trades"], sim["equity_curve"]
                wf = summarize(trades, points, wf_start, holdout_start)
                ho = summarize(trades, points, holdout_start, last_ts + engine.FOUR_HOURS)
                wf_trades = [t for t in trades if wf_start <= t["closed_at"] < holdout_start]
                ho_trades = [t for t in trades if holdout_start <= t["closed_at"] < last_ts + engine.FOUR_HOURS]
                year_detail = yearly_report(wf_trades + ho_trades, points)
                max_year_share = concentration(trades, lambda t: time.strftime("%Y", time.gmtime(t["closed_at"] / 1000)))
                max_symbol_share = concentration(trades, lambda t: t["symbol"])
                key = f"{strategy}|btc_sma200_filter={str(use_filter).lower()}|spread={spread:.2f}%"
                scenarios[key] = {
                    "strategy": strategy, "btc_sma200_filter": use_filter,
                    "round_trip_spread_pct": spread,
                    "walk_forward": wf, "holdout": ho,
                    "continuous_30_day_folds": sim["folds"],
                    "walk_forward_diagnostics": {
                        "by_symbol": grouped(wf_trades, lambda t: t["symbol"]),
                        "by_exit_reason": grouped(wf_trades, lambda t: t["exit_reason"]),
                        "by_btc_regime_at_entry": grouped(wf_trades, lambda t: t.get("entry_btc_regime", "unknown")),
                    },
                    "holdout_diagnostics": {
                        "by_symbol": grouped(ho_trades, lambda t: t["symbol"]),
                        "by_exit_reason": grouped(ho_trades, lambda t: t["exit_reason"]),
                        "by_btc_regime_at_entry": grouped(ho_trades, lambda t: t.get("entry_btc_regime", "unknown")),
                    },
                    "yearly": year_detail,
                    "concentration": {
                        "largest_year_share_of_positive_net_pnl": max_year_share,
                        "largest_symbol_share_of_positive_net_pnl": max_symbol_share,
                    },
                    "open_positions_at_report_end": sim["open_positions"],
                }

    decisions = {}
    for key, result in scenarios.items():
        if result["round_trip_spread_pct"] != 0.10:
            continue
        wf, ho = result["walk_forward"], result["holdout"]
        year_share = result["concentration"]["largest_year_share_of_positive_trade_pnl"]
        symbol_share = result["concentration"]["largest_symbol_share_of_positive_trade_pnl"]
        passed = {
            "walk_forward_at_least_100_trades": wf["trades"] >= 100,
            "positive_expectancy_walk_forward": wf["expectancy_usdt_per_trade"] > 0,
            "positive_expectancy_holdout": ho["expectancy_usdt_per_trade"] > 0,
            "holdout_profit_factor_above_1_2": ho["profit_factor"] is None or ho["profit_factor"] > 1.2,
            "no_year_over_50_percent_of_positive_pnl": year_share is not None and year_share <= 0.50,
            "no_symbol_over_50_percent_of_positive_pnl": symbol_share is not None and symbol_share <= 0.50,
            "walk_forward_drawdown_below_25pct_initial_capital": wf["max_drawdown_usdt"] < 17.5,
            "holdout_drawdown_below_25pct_initial_capital": ho["max_drawdown_usdt"] < 17.5,
        }
        decisions[key] = {"checks": passed, "accepted": all(passed.values())}

    return {
        "protocol": "frozen_2018_donchian_momentum_btc_sma200_filter_once",
        "mode": "historical_research_only_no_live_orders",
        "freeze_specification": FREEZE_SPEC,
        "data": {
            "provider": "OKX public OHLCV",
            "requested_start_utc": "2018-01-01",
            "latest_common_completed_4h_open_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(last_ts / 1000)),
            "holdout_start_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(holdout_start / 1000)),
            "per_market_first_4h_candle_utc": {
                s: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(data[s]["4h"][0][0] / 1000))
                for s in engine.SYMBOLS
            },
            "per_market_missing_1d_intervals": {
                s: sum(1 for i in range(1, len(data[s]["1d"]))
                       if int(data[s]["1d"][i][0]) - int(data[s]["1d"][i-1][0]) != engine.ONE_DAY)
                for s in engine.SYMBOLS
            },
            "per_market_missing_4h_intervals": {
                s: sum(1 for i in range(1, len(data[s]["4h"]))
                       if int(data[s]["4h"][i][0]) - int(data[s]["4h"][i-1][0]) != engine.FOUR_HOURS)
                for s in engine.SYMBOLS
            },
            "no_synthetic_candles": True,
            "gaps_policy": "Indicator windows reset at gaps; positions are exited at the first observed open after a gap; no candles are forward-filled.",
        },
        "walk_forward": {
            "start_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(wf_start / 1000)),
            "fold_days": 30,
            "method": "single uninterrupted portfolio per scenario; 30-day windows are attribution only; open positions and equity carry across all windows and into holdout",
        },
        "holdout": {"days": 90, "method": "final 90 days of the same continuous simulation; no reset or parameter selection"},
        "cost_model": {
            "taker_fee_each_side_pct": 0.10,
            "slippage_each_side_pct": 0.05,
            "round_trip_spread_scenarios_pct": [0.05, 0.10, 0.15],
            "net_pnl_formula": "raw gross PnL minus entry/exit commissions, spread and slippage",
            "fee_note": "0.10% per-side public spot taker assumption; account-specific OKX fee tier is not accessible without account credentials and is not queried.",
        },
        "strategies": {
            "donchian": "55 completed 4h-bar high breakout; exit below prior 20 completed 4h-bar low; 3x ATR(14) initial stop. No per-asset SMA filter.",
            "momentum": "Top 2 of the six markets by trailing 90-day return, daily rebalance, 3x ATR(14) initial stop. No per-asset SMA filter.",
        },
        "regime_filter": "Entry-only global gate: a new position may open only when the last completed BTC daily close is above its daily SMA200. Existing positions exit only under their strategy's ordinary exit rules. SMA200 and all other thresholds are frozen.",
        "annual_and_regime_definition": "Trade-year is the UTC close year. Entry regime is bull when BTC close is >1% above SMA200, bear when >1% below, and sideways within the +/-1% band. This fixed neutral band is descriptive only; the actual entry filter remains strictly close>SMA200.",
        "comparison_table": [
            {
                "strategy": value["strategy"],
                "btc_sma200_filter": value["btc_sma200_filter"],
                "spread_round_trip_pct": value["round_trip_spread_pct"],
                "walk_forward_trades": value["walk_forward"]["trades"],
                "walk_forward_expectancy_usdt": value["walk_forward"]["expectancy_usdt_per_trade"],
                "walk_forward_profit_factor": value["walk_forward"]["profit_factor"],
                "walk_forward_max_drawdown_usdt": value["walk_forward"]["max_drawdown_usdt"],
                "holdout_trades": value["holdout"]["trades"],
                "holdout_expectancy_usdt": value["holdout"]["expectancy_usdt_per_trade"],
                "holdout_profit_factor": value["holdout"]["profit_factor"],
                "holdout_max_drawdown_usdt": value["holdout"]["max_drawdown_usdt"],
            }
            for _, value in sorted(scenarios.items())
        ],
        "scenarios": scenarios,
        "acceptance_decisions_at_0_10_spread_only": decisions,
        "overall_result": "PENDING_REPORT_GENERATION",
    }


def main() -> None:
    report = run()
    accepted = sum(1 for value in report["acceptance_decisions_at_0_10_spread_only"].values()
                   if value["accepted"])
    report["overall_result"] = "PASS" if accepted else "NO_GO"
    Path("reports").mkdir(exist_ok=True)
    Path("reports/regime_filter_2018_research.json").write_text(
        json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({
        "overall_result": report["overall_result"],
        "accepted_scenarios": accepted,
        "decisions": report["acceptance_decisions_at_0_10_spread_only"],
    }, indent=2))


if __name__ == "__main__":
    main()
