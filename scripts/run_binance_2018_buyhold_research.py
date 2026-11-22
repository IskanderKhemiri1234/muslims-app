"""Frozen Donchian/Momentum replay on Binance Vision with Buy & Hold references."""
from __future__ import annotations

import bisect
import json
import time
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

import regime_filter_research_engine as engine
from binance_vision_data import (
    ArchiveUnavailable, SYMBOLS, fetch_market_history, gap_count, probe_source,
)

START_MS = int(datetime(2018, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
SPREADS = (0.05, 0.10, 0.15)
RISK = 0.005
CAPITAL = 70.0
HOLDOUT_DAYS = 90
FOLD_DAYS = 30


def _year(ts: int) -> str:
    return time.strftime("%Y", time.gmtime(ts / 1000))


def _counts(values):
    result = defaultdict(int)
    for value in values:
        result[str(value)] += 1
    return dict(sorted(result.items()))


def trade_metrics(trades: list[dict]) -> dict:
    ordered = sorted(trades, key=lambda item: item["closed_at"])
    pnl = [float(item["net_pnl_usdt"]) for item in ordered]
    wins = [value for value in pnl if value > 0]
    losses = [value for value in pnl if value < 0]
    gross_profit, gross_loss = sum(wins), abs(sum(losses))
    equity = peak = 0.0
    max_dd = 0.0
    for value in pnl:
        equity += value
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return {
        "trades": len(pnl),
        "expectancy_usdt_per_trade": sum(pnl) / len(pnl) if pnl else 0.0,
        "profit_factor": gross_profit / gross_loss if gross_loss else (None if gross_profit else 0.0),
        "max_drawdown_usdt": max_dd,
        "net_pnl_usdt": sum(pnl),
        "win_rate_pct": 100 * len(wins) / len(pnl) if pnl else 0.0,
    }


def grouped_trades(trades: list[dict], key_fn) -> dict:
    groups = defaultdict(list)
    for trade in trades:
        groups[str(key_fn(trade))].append(trade)
    return {key: trade_metrics(items) for key, items in sorted(groups.items())}


def curve_metrics(points: list[dict], start_ms: int, end_ms: int) -> dict:
    if not points:
        return {"start_equity_usdt": CAPITAL, "end_equity_usdt": CAPITAL,
                "total_return_pct": 0.0, "max_drawdown_usdt": 0.0,
                "max_drawdown_pct": 0.0, "cagr_pct": 0.0, "calmar": None}
    start_equity = CAPITAL
    end_equity = float(points[-1]["equity"])
    peak = start_equity
    max_dd = 0.0
    max_peak = start_equity
    for point in points:
        peak = max(peak, float(point["equity"]))
        max_peak = max(max_peak, peak)
        max_dd = max(max_dd, peak - float(point.get("adverse_equity", point["equity"])))
    days = max(1.0, (end_ms - start_ms) / 86_400_000)
    total_return = end_equity / start_equity - 1.0
    cagr = (end_equity / start_equity) ** (365.25 / days) - 1.0 if end_equity > 0 else -1.0
    dd_pct = max_dd / max_peak if max_peak else 0.0
    return {
        "start_equity_usdt": start_equity, "end_equity_usdt": end_equity,
        "total_return_pct": total_return * 100,
        "max_drawdown_usdt": max_dd, "max_drawdown_pct": dd_pct * 100,
        "cagr_pct": cagr * 100, "calmar": cagr / dd_pct if dd_pct > 0 else None,
    }


def summarize(trades: list[dict], points: list[dict], start: int, end: int) -> dict:
    selected = [t for t in trades if start <= t["closed_at"] < end]
    curve = [p for p in points if start <= p["timestamp"] < end]
    metrics = trade_metrics(selected)
    if curve:
        peak = float(curve[0]["equity"])
        max_dd = 0.0
        for point in curve:
            peak = max(peak, float(point["equity"]))
            max_dd = max(max_dd, peak - float(point["adverse_equity"]))
    else:
        max_dd = 0.0
    metrics.update({
        "max_drawdown_usdt": max_dd,
        "start_marked_equity_usdt": float(curve[0]["equity"]) if curve else CAPITAL,
        "end_marked_equity_usdt": float(curve[-1]["equity"]) if curve else CAPITAL,
    })
    return metrics


def annotate_binary_btc_regime(trades: list[dict], prepared: dict) -> None:
    btc = prepared["BTC/USDT"]
    stamps = btc["daily_timestamps"]
    closes = btc["daily_closes"]
    averages = btc["daily_sma200"]
    for trade in trades:
        opened_at = int(trade["opened_at"])
        idx = bisect.bisect_right(stamps, opened_at - engine.ONE_DAY) - 1
        label = "unknown"
        if idx >= 199 and math.isfinite(averages[idx]):
            age = opened_at - (stamps[idx] + engine.ONE_DAY)
            if 0 <= age < engine.ONE_DAY:
                label = "above_sma200" if closes[idx] > averages[idx] else "below_or_equal_sma200"
        trade["entry_btc_binary_regime"] = label


def yearly_report(trades: list[dict], points: list[dict]) -> dict:
    years = sorted({_year(t["closed_at"]) for t in trades})
    result = {}
    for year in years:
        subset = [t for t in trades if _year(t["closed_at"]) == year]
        year_points = [p for p in points if _year(p["timestamp"]) == year]
        portfolio_peak = float(year_points[0]["equity"]) if year_points else CAPITAL
        portfolio_dd = 0.0
        for point in year_points:
            portfolio_peak = max(portfolio_peak, float(point["equity"]))
            portfolio_dd = max(portfolio_dd, portfolio_peak - float(point["adverse_equity"]))
        result[year] = {
            **trade_metrics(subset),
            "max_portfolio_drawdown_usdt": portfolio_dd,
            "by_btc_binary_regime_at_entry": grouped_trades(
                subset, lambda trade: trade.get("entry_btc_binary_regime", "unknown")),
            "by_btc_descriptive_regime_at_entry": grouped_trades(
                subset, lambda trade: trade.get("entry_btc_regime", "unknown")),
            "by_symbol": grouped_trades(subset, lambda trade: trade["symbol"]),
            "by_exit_reason": grouped_trades(subset, lambda trade: trade["exit_reason"]),
        }
    return result


def positive_concentration(trades: list[dict], key_fn) -> float | None:
    buckets = defaultdict(float)
    for trade in trades:
        buckets[key_fn(trade)] += float(trade["net_pnl_usdt"])
    positive = [max(0.0, value) for value in buckets.values()]
    total = sum(positive)
    return max(positive) / total if total > 0 else None


def buy_and_hold(data: dict, start_ms: int, end_ms: int, spread_pct: float,
                 btc_only: bool = False) -> dict:
    """Equal initial-dollar allocations; each coin is bought at its first available bar."""
    symbols = ["BTC/USDT"] if btc_only else list(SYMBOLS)
    timeline = sorted({
        int(row[0]) for symbol in symbols for row in data[symbol]["4h"]
        if start_ms <= int(row[0]) <= end_ms
    })
    by_symbol = {
        symbol: {int(row[0]): row for row in data[symbol]["4h"]}
        for symbol in symbols
    }
    allocation = CAPITAL / len(symbols)
    fee = engine.FEE_RATE
    half_spread = spread_pct / 200.0
    cash = CAPITAL
    holdings = {}
    last_close = {}
    points = []
    entries = {}
    for ts in timeline:
        current = {}
        for symbol in symbols:
            row = by_symbol[symbol].get(ts)
            if row is not None:
                current[symbol] = row
                last_close[symbol] = float(row[4])
                if symbol not in holdings:
                    target_notional = allocation / (1 + fee)
                    fill = float(row[1]) * (1 + engine.SLIPPAGE_RATE + half_spread)
                    qty = target_notional / fill
                    commission = target_notional * fee
                    cash -= target_notional + commission
                    holdings[symbol] = qty
                    entries[symbol] = {
                        "timestamp": ts, "raw_open": float(row[1]),
                        "fill_price": fill, "notional": target_notional,
                        "commission_usdt": commission,
                    }
        equity = cash + sum(holdings[symbol] * last_close[symbol]
                            for symbol in holdings if symbol in last_close)
        points.append({"timestamp": ts, "equity": equity, "adverse_equity": equity})
    metrics = curve_metrics(points, start_ms, end_ms)
    metrics.update({
        "benchmark": "BTC buy & hold" if btc_only else "equal-weight six-market buy & hold",
        "initial_entry_cost_only": True,
        "entry_cost_model": {
            "commission_each_side_pct": 0.10,
            "slippage_each_side_pct": 0.05,
            "round_trip_spread_scenario_pct": spread_pct,
            "spread_charged_at_entry_pct": spread_pct / 2,
            "no_exit_cost_charged": True,
        },
        "entries": entries,
        "uninvested_cash_at_end_usdt": cash,
    })
    return metrics


def run() -> dict:
    probe_source()
    today = datetime.now(timezone.utc).date()
    data = {}
    data_quality = {}
    for symbol in engine.SYMBOLS:
        print(f"Downloading Binance Vision SPOT klines: {symbol}", flush=True)
        loaded = fetch_market_history(symbol, today)
        data[symbol] = {"4h": loaded["4h"], "1d": loaded["1d"]}
        data_quality[symbol] = {
            "first_4h_candle_utc": datetime.fromtimestamp(
                loaded["4h"][0][0] / 1000, timezone.utc).isoformat(),
            "first_1d_candle_utc": datetime.fromtimestamp(
                loaded["1d"][0][0] / 1000, timezone.utc).isoformat(),
            "last_completed_4h_candle_utc": datetime.fromtimestamp(
                loaded["4h"][-1][0] / 1000, timezone.utc).isoformat(),
            "last_completed_1d_candle_utc": datetime.fromtimestamp(
                loaded["1d"][-1][0] / 1000, timezone.utc).isoformat(),
            "4h_candle_count": len(loaded["4h"]),
            "1d_candle_count": len(loaded["1d"]),
            "4h_gap_count": gap_count(loaded["4h"], engine.FOUR_HOURS),
            "1d_gap_count": gap_count(loaded["1d"], engine.ONE_DAY),
            "missing_archive_periods": loaded["_missing_archive_periods"],
            "synthetic_candles_created": 0,
        }

    prepared = engine.indicators(data)
    last_ts = min(int(data[s]["4h"][-1][0]) for s in engine.SYMBOLS)
    holdout_start = last_ts - HOLDOUT_DAYS * engine.ONE_DAY
    wf_start = START_MS + 200 * engine.ONE_DAY
    if holdout_start <= wf_start:
        raise ArchiveUnavailable("Insufficient Binance history for a 90-day Holdout after warm-up")
    scenarios = {}
    benchmark_by_spread = {}
    for spread in SPREADS:
        benchmark_by_spread[f"{spread:.2f}"] = {
            "equal_weight": buy_and_hold(data, wf_start, last_ts, spread, False),
            "btc_only": buy_and_hold(data, wf_start, last_ts, spread, True),
        }
    for strategy in ("donchian", "momentum"):
        for use_filter in (False, True):
            for spread in SPREADS:
                sim = engine.simulate(
                    prepared, strategy, RISK, spread, wf_start, last_ts,
                    fold_days=FOLD_DAYS, btc_filter=use_filter, liquidate_at_end=False,
                )
                trades, points = sim["trades"], sim["equity_curve"]
                annotate_binary_btc_regime(trades, prepared)
                wf = summarize(trades, points, wf_start, holdout_start)
                ho = summarize(trades, points, holdout_start, last_ts + engine.FOUR_HOURS)
                all_metrics = trade_metrics(trades)
                full_curve = curve_metrics(points, wf_start, last_ts + engine.FOUR_HOURS)
                all_metrics.update(full_curve)
                wf_trades = [t for t in trades if wf_start <= t["closed_at"] < holdout_start]
                ho_trades = [t for t in trades if holdout_start <= t["closed_at"] < last_ts + engine.FOUR_HOURS]
                annual = yearly_report(wf_trades + ho_trades, points)
                year_share = positive_concentration(
                    trades, lambda t: _year(t["closed_at"]))
                symbol_share = positive_concentration(trades, lambda t: t["symbol"])
                regime_all = grouped_trades(
                    trades, lambda t: t.get("entry_btc_binary_regime", "unknown"))
                descriptive_regime_all = grouped_trades(
                    trades, lambda t: t.get("entry_btc_regime", "unknown"))
                key = f"{strategy}|btc_sma200_filter={str(use_filter).lower()}|spread={spread:.2f}%"
                benchmark = benchmark_by_spread[f"{spread:.2f}"]["equal_weight"]
                scenarios[key] = {
                    "strategy": strategy,
                    "btc_sma200_filter": use_filter,
                    "round_trip_spread_pct": spread,
                    "walk_forward": wf,
                    "holdout": ho,
                    "full_period": all_metrics,
                    "full_period_btc_regime_at_entry": regime_all,\n                    "full_period_btc_descriptive_regime_at_entry": descriptive_regime_all,
                    "continuous_30_day_folds": sim["folds"],
                    "walk_forward_diagnostics": {
                        "by_symbol": grouped_trades(wf_trades, lambda t: t["symbol"]),
                        "by_exit_reason": grouped_trades(wf_trades, lambda t: t["exit_reason"]),
                        "by_btc_binary_regime_at_entry": grouped_trades(
                            wf_trades, lambda t: t.get("entry_btc_binary_regime", "unknown")),
                        "by_btc_descriptive_regime_at_entry": grouped_trades(
                            wf_trades, lambda t: t.get("entry_btc_regime", "unknown")),
                    },
                    "holdout_diagnostics": {
                        "by_symbol": grouped_trades(ho_trades, lambda t: t["symbol"]),
                        "by_exit_reason": grouped_trades(ho_trades, lambda t: t["exit_reason"]),
                        "by_btc_binary_regime_at_entry": grouped_trades(
                            ho_trades, lambda t: t.get("entry_btc_binary_regime", "unknown")),
                        "by_btc_descriptive_regime_at_entry": grouped_trades(
                            ho_trades, lambda t: t.get("entry_btc_regime", "unknown")),
                    },
                    "yearly": annual,
                    "open_positions_at_report_end": sim["open_positions"],
                    "concentration": {
                        "largest_year_share_of_positive_pnl": year_share,
                        "largest_symbol_share_of_positive_pnl": symbol_share,
                    },
                    "calmar_comparison": {
                        "strategy_calmar": all_metrics["calmar"],
                        "equal_weight_buy_hold_calmar": benchmark["calmar"],
                        "beats_equal_weight_buy_hold": (
                            all_metrics["calmar"] is not None
                            and benchmark["calmar"] is not None
                            and all_metrics["calmar"] > benchmark["calmar"]
                        ),
                        "btc_buy_hold_calmar": benchmark_by_spread[f"{spread:.2f}"]["btc_only"]["calmar"],
                    },
                }

    decisions = {}
    for key, result in scenarios.items():
        if result["round_trip_spread_pct"] != 0.10:
            continue
        wf, ho = result["walk_forward"], result["holdout"]
        positive_years = [
            year for year, metrics in result["yearly"].items()
            if metrics["expectancy_usdt_per_trade"] > 0
        ]
        positive_bear_or_sideways_year = any(
            metrics["expectancy_usdt_per_trade"] > 0
            for year in result["yearly"].values()
            for regime, metrics in year["by_btc_descriptive_regime_at_entry"].items()
            if regime in {"bear", "sideways"}
        )
        checks = {
            "walk_forward_at_least_100_trades": wf["trades"] >= 100,
            "positive_expectancy_walk_forward": wf["expectancy_usdt_per_trade"] > 0,
            "positive_expectancy_holdout": ho["expectancy_usdt_per_trade"] > 0,
            "holdout_profit_factor_above_1_2": (
                ho["profit_factor"] is not None and ho["profit_factor"] > 1.2),
            "no_year_over_50_percent_of_positive_pnl": (
                result["concentration"]["largest_year_share_of_positive_pnl"] is not None
                and result["concentration"]["largest_year_share_of_positive_pnl"] <= 0.50),
            "no_symbol_over_50_percent_of_positive_pnl": (
                result["concentration"]["largest_symbol_share_of_positive_pnl"] is not None
                and result["concentration"]["largest_symbol_share_of_positive_pnl"] <= 0.50),
            "max_drawdown_below_25pct_initial_capital": (
                result["full_period"]["max_drawdown_usdt"] < CAPITAL * 0.25),
            "calmar_above_equal_weight_buy_hold": (
                result["calmar_comparison"]["beats_equal_weight_buy_hold"]),
            "positive_expectancy_in_at_least_3_years": len(positive_years) >= 3,
            "positive_expectancy_in_bear_or_sideways_year": positive_bear_or_sideways_year,
        }
        decisions[key] = {
            "checks": checks, "accepted": all(checks.values()),
            "positive_expectancy_years": positive_years,
        }

    accepted = sum(item["accepted"] for item in decisions.values())
    return {
        "protocol": "binance_vision_frozen_donchian_momentum_buyhold_v1",
        "mode": "research_only_no_live_orders_no_production_engine_changes",
        "branch": "research/binance-2018-buyhold-v1",
        "source": {
            "provider": "Binance Public Data Vision SPOT",
            "base_url": "https://data.binance.vision/data/spot",
            "access": "public unauthenticated archive; no API keys",
            "intervals": ["4h", "1d"],
            "requested_start_utc": "2017-08-01",
            "latest_common_completed_4h_open_utc": datetime.fromtimestamp(
                last_ts / 1000, timezone.utc).isoformat(),
            "holdout_start_utc": datetime.fromtimestamp(
                holdout_start / 1000, timezone.utc).isoformat(),
            "per_market": data_quality,
            "no_synthetic_candles": True,
            "gap_policy": "No candles are inserted or forward-filled. Indicators reset at gaps; the frozen simulator exits carried positions at the first observed open after a 4h gap.",
        },
        "freeze_specification": {
            "inherited_from": "research/regime-filter-2018-v1",
            "strategies": ["Donchian Breakout", "Cross-sectional Momentum"],
            "markets": list(engine.SYMBOLS),
            "capital_usdt": CAPITAL,
            "risk_fraction": RISK,
            "fee_each_side_pct": 0.10,
            "slippage_each_side_pct": 0.05,
            "round_trip_spread_scenarios_pct": list(SPREADS),
            "btc_sma200_filter": "entry-only, completed BTC daily close strictly above SMA200; no alternative filters",
            "donchian": "unchanged: per-asset daily close>SMA200; 55 completed 4h-bar breakout; exit below prior 20 completed 4h-bar low or asset regime failure; initial stop 3x ATR(14)",
            "momentum": "unchanged: rank eligible assets by trailing 90-day return (540 consecutive 4h bars); hold top two; daily rebalance; initial stop 3x ATR(14)",
            "portfolio_limits": {"max_positions": engine.MAX_POSITIONS, "max_notional_per_position_usdt": engine.MAX_NOTIONAL},
            "walk_forward": {"fold_days": FOLD_DAYS, "continuous_portfolio": True, "equity_and_positions_carry": True},
            "holdout": {"days": HOLDOUT_DAYS, "final_period": True, "reset": False},
        },
        "benchmarks": {
            "definition": "70 USDT initially allocated equally in dollar terms; each market is bought at its first available 4h open on/after the common evaluation start, so a not-yet-listed market's allocation remains cash until its first candle. Holdings are not rebalanced or sold.",
            "costs": "Entry only: 0.10% commission, 0.05% slippage and half of the scenario round-trip spread. No exit cost is charged because the benchmark remains Buy & Hold.",
            "same_evaluation_period": {
                "start_utc": datetime.fromtimestamp(wf_start / 1000, timezone.utc).isoformat(),
                "end_utc": datetime.fromtimestamp((last_ts + engine.FOUR_HOURS) / 1000, timezone.utc).isoformat(),
            },
            "by_spread": benchmark_by_spread,
        },
        "acceptance_criteria_at_0_10_spread": {
            "minimum_walk_forward_trades": 100,
            "positive_expectancy_walk_forward_and_holdout": True,
            "holdout_profit_factor_gt": 1.2,
            "year_and_symbol_positive_profit_share_max": 0.50,
            "max_drawdown_usdt_lt": CAPITAL * 0.25,
            "calmar_gt_equal_weight_buy_hold": True,
            "positive_expectancy_years_min": 3,
            "at_least_one_positive_bear_or_sideways_regime_year": True,\n            "regime_metrics": "binary BTC above-SMA200 versus below-or-equal-SMA200 at entry; descriptive bull/bear/sideways attribution is additional",
        },
        "strategy_family_overfitting_warning": {
            "distinct_strategy_families_tested_in_project": 4,
            "families": ["EMA", "Donchian", "Cross-sectional Momentum", "Pullback"],
            "warning": "Repeated exploration across four strategy families raises multiple-testing/selection-bias risk. Results are research evidence, not independent confirmation.",
            "holdout_blindness": "The project-level Holdout is not fully blind: prior experiments exposed the project to overlapping historical periods and Holdout outcomes. This run's final 90 days are not an untouched project-level blind test.",
        },
        "drawdown_definitions": {\n            "annual_portfolio_drawdown": "mark-to-market portfolio equity drawdown within each UTC close year",\n            "regime_drawdown": "closed-trade cumulative PnL drawdown for trades entered in that BTC regime",\n        },\n        "annual_results": {
            key: value["yearly"] for key, value in scenarios.items()
        },
        "bear_market_years_2018_2022": {
            key: {year: value["yearly"].get(year) for year in ("2018", "2022")}
            for key, value in scenarios.items()
        },
        "comparison_table": [
            {
                "strategy": value["strategy"],
                "btc_filter": value["btc_sma200_filter"],
                "spread_pct": value["round_trip_spread_pct"],
                "wf_trades": value["walk_forward"]["trades"],
                "wf_expectancy_usdt": value["walk_forward"]["expectancy_usdt_per_trade"],
                "holdout_expectancy_usdt": value["holdout"]["expectancy_usdt_per_trade"],
                "holdout_pf": value["holdout"]["profit_factor"],
                "full_return_pct": value["full_period"]["total_return_pct"],
                "full_max_dd_usdt": value["full_period"]["max_drawdown_usdt"],
                "strategy_calmar": value["full_period"]["calmar"],
                "equal_weight_buy_hold_calmar": value["calmar_comparison"]["equal_weight_buy_hold_calmar"],
                "beats_equal_weight_buy_hold": value["calmar_comparison"]["beats_equal_weight_buy_hold"],
            }
            for value in scenarios.values()
        ],
        "scenarios": scenarios,
        "acceptance_decisions_at_0_10_spread": decisions,
        "strategy_families_tested": 4,
        "holdout_is_project_level_blind": False,
        "overall_result": "PASS" if accepted == len(decisions) else ("PARTIAL_PASS" if accepted else "NO_GO"),
    }


def main() -> None:
    try:
        report = run()
    except ArchiveUnavailable as exc:
        raise SystemExit(f"STOP: Binance data source unavailable; no fallback provider used. {exc}") from exc
    Path("reports").mkdir(exist_ok=True)
    output = Path("reports/binance_2018_buyhold_research.json")
    output.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({
        "overall_result": report["overall_result"],
        "accepted_scenarios": sum(v["accepted"] for v in report["acceptance_decisions_at_0_10_spread"].values()),
        "decisions": report["acceptance_decisions_at_0_10_spread"],
        "report": str(output),
    }, indent=2))


if __name__ == "__main__":
    main()
