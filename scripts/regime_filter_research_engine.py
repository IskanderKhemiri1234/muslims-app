"""Continuous-portfolio walk-forward and locked-rule strategy research.

Research only. The walk-forward portfolio is simulated once across all folds:
equity, open positions, trailing stops, and drawdown carry across fold boundaries.
The final locked-rule period is evaluated separately with fresh virtual capital.
"""
from __future__ import annotations

import argparse
import bisect
import json
import math
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import ccxt

SYMBOLS = ["BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT", "XRP/USDT", "ADA/USDT"]
FOUR_HOURS = 14_400_000
ONE_DAY = 86_400_000
INITIAL_CAPITAL = 70.0
MAX_NOTIONAL = 20.0
MAX_POSITIONS = 3
FEE_RATE = 0.001
SLIPPAGE_RATE = 0.0005
STRATEGIES = ("donchian", "momentum")


def fetch_history(exchange: Any, symbol: str, timeframe: str, since_ms: int) -> list[list[float]]:
    duration = exchange.parse_timeframe(timeframe) * 1000
    cursor = since_ms - since_ms % duration
    rows: dict[int, list[float]] = {}
    while True:
        batch = exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=cursor, limit=300)
        if not batch:
            break
        for row in batch:
            ts = int(row[0])
            if ts >= since_ms:
                rows[ts] = [float(value) for value in row[:6]]
        last = int(batch[-1][0])
        next_cursor = last + duration
        if next_cursor <= cursor or len(batch) < 300:
            break
        cursor = next_cursor
        time.sleep(exchange.rateLimit / 1000)
    now_ms = int(time.time() * 1000)
    result = [rows[k] for k in sorted(rows) if k + duration <= now_ms]
    if len(result) < 2:
        raise ValueError(f"{symbol} {timeframe}: no usable public history ({len(result)})")
    previous = None
    for row in result:
        ts, o, h, low, close, volume = row
        if int(ts) % duration or any(not math.isfinite(v) for v in row):
            raise ValueError(f"{symbol} {timeframe}: invalid timestamp/value")
        if min(o, h, low, close) <= 0 or volume < 0 or h < max(o, close, low) or low > min(o, close):
            raise ValueError(f"{symbol} {timeframe}: invalid OHLCV")
        previous = int(ts)
    return result


def sma(values: list[float], period: int) -> list[float]:
    out = [float("nan")] * len(values)
    running = 0.0
    for i, value in enumerate(values):
        running += value
        if i >= period:
            running -= values[i - period]
        if i >= period - 1:
            out[i] = running / period
    return out


def ema(values: list[float], period: int) -> list[float]:
    out = [float("nan")] * len(values)
    if len(values) < period:
        return out
    alpha = 2.0 / (period + 1)
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    for i in range(period, len(values)):
        out[i] = alpha * values[i] + (1 - alpha) * out[i - 1]
    return out


def atr_values(rows: list[list[float]], period: int = 14) -> list[float]:
    tr = []
    for i, row in enumerate(rows):
        prev = rows[i - 1][4] if i else row[4]
        tr.append(max(row[2] - row[3], abs(row[2] - prev), abs(row[3] - prev)))
    return sma(tr, period)


def rsi_values(values: list[float], period: int = 14) -> list[float]:
    gains = [0.0]
    losses = [0.0]
    for i in range(1, len(values)):
        delta = values[i] - values[i - 1]
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))
    avg_gain = sma(gains, period)
    avg_loss = sma(losses, period)
    out = [float("nan")] * len(values)
    for i in range(period, len(values)):
        if avg_loss[i] == 0:
            out[i] = 100.0 if avg_gain[i] > 0 else 50.0
        else:
            rs = avg_gain[i] / avg_loss[i]
            out[i] = 100.0 - 100.0 / (1.0 + rs)
    return out


def daily_regime(daily_timestamps: list[int], daily_closes: list[float],
                 daily_sma200: list[float], timestamp: int) -> bool:
    idx = bisect.bisect_right(daily_timestamps, timestamp - ONE_DAY) - 1
    return idx >= 199 and math.isfinite(daily_sma200[idx]) and daily_closes[idx] > daily_sma200[idx]


def indicators(data: dict[str, dict[str, list[list[float]]]]) -> dict[str, dict[str, Any]]:
    prepared = {}
    for symbol, series in data.items():
        bars, daily = series["4h"], series["1d"]
        closes = [r[4] for r in bars]
        daily_sma = [float("nan")] * len(daily)
        ds = 0
        for i in range(1, len(daily) + 1):
            if i == len(daily) or int(daily[i][0]) - int(daily[i-1][0]) != ONE_DAY:
                daily_sma[ds:i] = sma([r[4] for r in daily[ds:i]], 200)
                ds = i
        arrays = {k: [float("nan")] * len(bars) for k in ("atr","ema20","ema50","ema200","rsi")}
        start = 0
        for i in range(1, len(bars) + 1):
            if i == len(bars) or int(bars[i][0]) - int(bars[i-1][0]) != FOUR_HOURS:
                chunk, values = bars[start:i], closes[start:i]
                arrays["atr"][start:i] = atr_values(chunk)
                arrays["ema20"][start:i] = ema(values, 20)
                arrays["ema50"][start:i] = ema(values, 50)
                arrays["ema200"][start:i] = ema(values, 200)
                arrays["rsi"][start:i] = rsi_values(values, 14)
                start = i
        prepared[symbol] = {
            "bars": bars, "daily": daily,
            "daily_timestamps": [int(r[0]) for r in daily],
            "daily_closes": [r[4] for r in daily], "daily_sma200": daily_sma,
            "index": {int(r[0]): i for i, r in enumerate(bars)},
            **arrays, "pending": None,
        }
    return prepared

def metric_summary(trades: list[dict[str, Any]], max_dd: float, start_equity: float,
                   end_equity: float) -> dict[str, Any]:
    pnls = [float(t["net_pnl_usdt"]) for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gp, gl = sum(wins), abs(sum(losses))
    return {
        "trades": len(pnls),
        "win_rate_pct": 100 * len(wins) / len(pnls) if pnls else 0.0,
        "average_win_usdt": sum(wins) / len(wins) if wins else 0.0,
        "average_loss_usdt": sum(losses) / len(losses) if losses else 0.0,
        "expectancy_usdt_per_trade": sum(pnls) / len(pnls) if pnls else 0.0,
        "net_closed_trade_pnl_usdt": sum(pnls),
        "profit_factor": gp / gl if gl else (None if gp else 0.0),
        "max_drawdown_usdt": max_dd,
        "start_equity_usdt": start_equity,
        "end_equity_usdt": end_equity,
        "equity_return_pct": (end_equity / start_equity - 1) * 100 if start_equity else 0.0,
    }


def simulate(
    prepared: dict[str, dict[str, Any]], strategy: str, risk_fraction: float,
    spread_pct: float, start_ms: int, end_ms: int, fold_days: int = 30,
    btc_filter: bool = False, liquidate_at_end: bool = False,
) -> dict[str, Any]:
    """Run one continuous portfolio from start_ms to end_ms; never reset at folds."""
    timeline = sorted({int(r[0]) for x in prepared.values() for r in x["bars"]
                       if start_ms - 200 * ONE_DAY <= int(r[0]) <= end_ms})
    positions: dict[str, dict[str, Any]] = {}
    trades: list[dict[str, Any]] = []
    realized = 0.0
    peak = INITIAL_CAPITAL
    max_dd = 0.0
    equity_curve: list[dict[str, float]] = []
    fold_points: dict[int, list[float]] = defaultdict(list)
    fold_trades: dict[int, list[dict[str, Any]]] = defaultdict(list)
    last_bar: dict[str, list[float]] = {}
    last_seen: dict[str, int] = {}
    fold_ms = fold_days * ONE_DAY
    first_equity = INITIAL_CAPITAL

    def fold_id(ts: int) -> int:
        return max(0, (ts - start_ms) // fold_ms)

    def close(symbol: str, raw_exit: float, ts: int, reason: str) -> None:
        nonlocal realized
        p = positions.pop(symbol)
        half_spread = spread_pct / 200.0
        exit_fill = raw_exit * (1 - SLIPPAGE_RATE - half_spread)
        exit_fee = exit_fill * p["qty"] * FEE_RATE
        execution_adjusted_gross = (exit_fill - p["entry_fill"]) * p["qty"]
        gross = (raw_exit - p["raw_entry_price"]) * p["qty"]
        fees = p["entry_fee"] + exit_fee
        net = execution_adjusted_gross - fees
        realized += net
        exit_execution_cost = abs(raw_exit - exit_fill) * p["qty"]
        trade = {
            "symbol": symbol, "opened_at": p["opened_at"], "closed_at": ts,
            "entry_price": p["entry_fill"], "exit_price": exit_fill,
            "quantity": p["qty"], "notional_usdt": p["notional"],
            "gross_pnl_usdt": gross, "execution_adjusted_gross_pnl_usdt": execution_adjusted_gross,
            "fees_usdt": fees,
            "entry_execution_cost_usdt": p["entry_execution_cost"],
            "exit_execution_cost_usdt": exit_execution_cost,
            "total_costs_usdt": fees + p["entry_execution_cost"] + exit_execution_cost,
            "net_pnl_usdt": net,
            "exit_reason": reason, "bars_held": max(1, (ts - p["opened_at"]) // FOUR_HOURS),
            "entry_atr_pct": p["entry_atr"] / p["entry_fill"] * 100,
            "entry_btc_regime": p.get("entry_regime", "unknown"),
            "entry_btc_regime": p.get("entry_regime", "unknown"),
        }
        trades.append(trade)
        if ts >= start_ms:
            fold_trades[fold_id(ts)].append(trade)

    def marked_equity(current_rows: dict[str, tuple[int, list[float]]],
                      use_open: bool = False, use_low: bool = False) -> float:
        unrealized = 0.0
        for sym, pos in positions.items():
            row = current_rows.get(sym)
            mark_bar = row[1] if row else last_bar.get(sym)
            if mark_bar is None:
                continue
            raw_mark = mark_bar[1] if use_open and row else (
                mark_bar[3] if use_low and row else mark_bar[4]
            )
            mark = raw_mark * (1 - SLIPPAGE_RATE - spread_pct / 200)
            unrealized += ((mark - pos["entry_fill"]) * pos["qty"] -
                           pos["entry_fee"] - mark * pos["qty"] * FEE_RATE)
        return INITIAL_CAPITAL + realized + unrealized

    for ts in timeline:
        current = {}
        for symbol, item in prepared.items():
            idx = item["index"].get(ts)
            if idx is not None:
                current[symbol] = (idx, item["bars"][idx])
                last_bar[symbol] = item["bars"][idx]

        # Missing intervals are never filled. Exit carried positions at the first observed open.
        for symbol, (idx, bar) in current.items():
            prior = last_seen.get(symbol)
            if prior is not None and ts - prior != FOUR_HOURS and symbol in positions:
                close(symbol, bar[1], ts, "data_gap_exit")
            last_seen[symbol] = ts
        btc_item = prepared.get("BTC/USDT")
        btc_on = bool(btc_item and daily_regime(
            btc_item["daily_timestamps"], btc_item["daily_closes"],
            btc_item["daily_sma200"], ts + FOUR_HOURS))
        btc_idx = bisect.bisect_right(btc_item["daily_timestamps"], ts + FOUR_HOURS - ONE_DAY) - 1 if btc_item else -1
        btc_state = "unknown"
        if btc_idx >= 0 and math.isfinite(btc_item["daily_sma200"][btc_idx]):
            ratio = btc_item["daily_closes"][btc_idx] / btc_item["daily_sma200"][btc_idx]
            btc_state = "bull" if ratio > 1.01 else ("bear" if ratio < 0.99 else "sideways")

        # Previously generated orders execute at this bar's open.
        for symbol, (idx, bar) in current.items():
            item = prepared[symbol]
            action = item["pending"]
            item["pending"] = None
            if action == "sell" and symbol in positions:
                close(symbol, bar[1], ts, "signal_exit")
            elif action == "buy" and symbol not in positions and ts >= start_ms:
                if len(positions) >= MAX_POSITIONS:
                    continue
                atr = item["atr"][idx - 1] if idx else float("nan")
                if not math.isfinite(atr) or atr <= 0:
                    continue
                equity = max(0.0, marked_equity(current, use_open=True))
                stop_distance = 3.0 * atr
                risk_budget = equity * risk_fraction
                cost_fraction = (stop_distance / bar[1] +
                                 2 * (FEE_RATE + SLIPPAGE_RATE) + spread_pct / 100)
                risk_notional = risk_budget / cost_fraction if cost_fraction > 0 else 0.0
                committed = sum(p["notional"] for p in positions.values())
                available = max(0.0, equity - committed)
                notional = min(MAX_NOTIONAL, available, risk_notional)
                if notional < 1.0:
                    continue
                fill = bar[1] * (1 + SLIPPAGE_RATE + spread_pct / 200)
                qty = notional / fill
                positions[symbol] = {
                    "qty": qty, "notional": notional, "entry_fill": fill,
                    "entry_fee": notional * FEE_RATE,
                    "entry_execution_cost": abs(fill - bar[1]) * qty,
                    "raw_entry_price": bar[1], "opened_at": ts, "stop": bar[1] - stop_distance, "entry_atr": atr,
                    "highest_close": bar[1], "entry_regime": btc_state, "entry_regime": btc_state,
                }

        # Stops are evaluated before updating trailing highs: no same-bar lookahead.
        for symbol, (_, bar) in current.items():
            p = positions.get(symbol)
            if p is None:
                continue
            if bar[1] <= p["stop"]:
                close(symbol, bar[1], ts, "atr_stop_gap")
            elif bar[3] <= p["stop"]:
                close(symbol, p["stop"], ts, "atr_stop")

        # Signals are calculated from this completed candle and fill next candle.
        if ts >= start_ms:
            if strategy == "donchian":
                for symbol, (idx, bar) in current.items():
                    x = prepared[symbol]
                    if idx < 55 or not math.isfinite(x["atr"][idx]):
                        continue
                    if int(bar[0]) - int(x["bars"][idx-55][0]) != 55 * FOUR_HOURS:
                        continue
                    if int(bar[0]) - int(x["bars"][idx-20][0]) != 20 * FOUR_HOURS:
                        continue
                    high = max(r[2] for r in x["bars"][idx-55:idx])
                    low = min(r[3] for r in x["bars"][idx-20:idx])
                    if symbol in positions and bar[4] < low:
                        x["pending"] = "sell"
                    elif symbol not in positions and bar[4] > high and (not btc_filter or btc_on):
                        x["pending"] = "buy"
            elif strategy == "momentum":
                if ts // ONE_DAY != (ts - FOUR_HOURS) // ONE_DAY:
                    scores = {}
                    for symbol, (idx, bar) in current.items():
                        x = prepared[symbol]
                        if idx < 540 or int(bar[0]) - int(x["bars"][idx-540][0]) != 540 * FOUR_HOURS:
                            continue
                        old = x["bars"][idx - 540][4]
                        if old > 0:
                            scores[symbol] = bar[4] / old - 1.0
                    leaders = set(sorted(scores, key=scores.get, reverse=True)[:2])
                    for symbol, x in prepared.items():
                        if symbol in positions and symbol not in leaders:
                            x["pending"] = "sell"
                        elif symbol not in positions and symbol in leaders and (not btc_filter or btc_on):
                            x["pending"] = "buy"
            else:
                raise ValueError(f"Unknown strategy: {strategy}")

        # Update mark-to-market portfolio equity and global drawdown continuously.
        equity = marked_equity(current)
        adverse_equity = marked_equity(current, use_low=True)
        peak = max(peak, equity)
        drawdown = peak - min(equity, adverse_equity)
        max_dd = max(max_dd, drawdown)
        if ts >= start_ms:
            equity_curve.append({"timestamp": ts, "equity": equity, "adverse_equity": adverse_equity, "drawdown": drawdown})
            fold_points[fold_id(ts)].append(equity)

    # Historical batch reports liquidate at the endpoint; forward replay keeps positions open
    # so each scheduled replay does not manufacture an artificial close every four hours.
    if liquidate_at_end:
        for symbol in list(positions):
            bar = last_bar.get(symbol)
            if bar is not None:
                close(symbol, bar[4], int(bar[0]), "period_end_liquidation")

    end_equity = INITIAL_CAPITAL + realized
    fold_reports = []
    total_folds = max(fold_points.keys(), default=-1) + 1
    for fid in range(total_folds):
        points = fold_points.get(fid, [])
        ftrades = fold_trades.get(fid, [])
        fold_reports.append({
            "fold": fid + 1,
            "start_timestamp": start_ms + fid * fold_ms,
            "end_timestamp": min(start_ms + (fid + 1) * fold_ms, end_ms),
            "start_marked_equity_usdt": points[0] if points else None,
            "end_marked_equity_usdt": points[-1] if points else None,
            "closed_trades": len(ftrades),
            "closed_trade_net_pnl_usdt": sum(t["net_pnl_usdt"] for t in ftrades),
            "max_portfolio_drawdown_in_fold_usdt": max(
                (p["drawdown"] for p in equity_curve
                 if fold_id(int(p["timestamp"])) == fid), default=0.0),
            "positions_carried_across_boundary": True,
        })
    return {
        "summary": metric_summary(trades, max_dd, first_equity, end_equity),
        "trades": trades, "equity_curve": equity_curve, "folds": fold_reports,
        "open_positions": [
            {"symbol": symbol, "opened_at": p["opened_at"], "entry_price": p["entry_fill"],
             "quantity": p["qty"], "notional_usdt": p["notional"], "stop_price": p["stop"],
             "unrealized_mark_pnl_usdt": ((last_bar[symbol][4] * (1 - SLIPPAGE_RATE - spread_pct / 200)
                 - p["entry_fill"]) * p["qty"] - p["entry_fee"]
                 - last_bar[symbol][4] * (1 - SLIPPAGE_RATE - spread_pct / 200) * p["qty"] * FEE_RATE)
                 if symbol in last_bar else 0.0}
            for symbol, p in sorted(positions.items())
        ],
    }


def diagnostics(trades: list[dict[str, Any]]) -> dict[str, Any]:
    def group(key: str) -> dict[str, Any]:
        buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for trade in trades:
            buckets[str(trade[key])].append(trade)
        return {name: {
            "trades": len(items),
            "net_pnl_usdt": sum(t["net_pnl_usdt"] for t in items),
            "expectancy_usdt": sum(t["net_pnl_usdt"] for t in items) / len(items),
            "gross_pnl_usdt": sum(t["gross_pnl_usdt"] for t in items),
            "commission_costs_usdt": sum(t["fees_usdt"] for t in items),
            "total_costs_usdt": sum(t["total_costs_usdt"] for t in items),
            "average_bars_held": sum(t["bars_held"] for t in items) / len(items),
        } for name, items in sorted(buckets.items())}
    monthly = []
    for month in sorted({time.strftime("%Y-%m", time.gmtime(t["closed_at"] / 1000))
                         for t in trades}):
        items = [t for t in trades
                 if time.strftime("%Y-%m", time.gmtime(t["closed_at"] / 1000)) == month]
        monthly.append({"month": month, "trades": len(items),
                        "net_pnl_usdt": sum(t["net_pnl_usdt"] for t in items),
                        "expectancy_usdt": sum(t["net_pnl_usdt"] for t in items) / len(items)})
    gross = sum(t["gross_pnl_usdt"] for t in trades)
    costs = sum(t["total_costs_usdt"] for t in trades)
    return {
        "by_symbol": group("symbol"),
        "by_exit_reason": group("exit_reason"),
        "monthly": monthly,
        "gross_pnl_usdt": gross,
        "total_commission_spread_slippage_costs_usdt": costs,
        "cost_drag_pct_of_abs_gross": costs / abs(gross) * 100 if gross else None,
    }


def run_report(data: dict[str, dict[str, list[list[float]]]], since_ms: int,
               spreads: list[float]) -> dict[str, Any]:
    prepared = indicators(data)
    last_ts = min(int(v["4h"][-1][0]) for v in data.values())
    holdout_start = last_ts - 180 * ONE_DAY
    wf_start = since_ms + 220 * ONE_DAY
    scenarios = {}
    for strategy in STRATEGIES:
        for risk in (0.005, 0.01):
            for spread in spreads:
                # A single uninterrupted portfolio across the entire WF interval.
                wf = simulate(prepared, strategy, risk, spread, wf_start, holdout_start)
                # Locked-rule final period, isolated capital and no parameter selection.
                ho = simulate(prepared, strategy, risk, spread, holdout_start, last_ts)
                key = f"{strategy}|risk={risk:.3f}|spread={spread:.2f}"
                scenarios[key] = {
                    "strategy": strategy, "risk_fraction": risk,
                    "spread_round_trip_pct": spread,
                    "walk_forward": wf["summary"], "walk_forward_folds": wf["folds"],
                    "walk_forward_diagnostics": diagnostics(wf["trades"]),
                    "holdout": ho["summary"],
                    "holdout_diagnostics": diagnostics(ho["trades"]),
                    "acceptance": {
                        "walk_forward_min_100_trades": wf["summary"]["trades"] >= 100,
                        "holdout_min_100_trades": ho["summary"]["trades"] >= 100,
                        "walk_forward_positive_expectancy":
                            wf["summary"]["expectancy_usdt_per_trade"] > 0,
                        "holdout_positive_expectancy":
                            ho["summary"]["expectancy_usdt_per_trade"] > 0,
                        "continuous_walk_forward_equity": True,
                    },
                }
    return {
        "provider": "OKX public OHLCV",
        "markets": SYMBOLS, "timeframe": "4h",
        "data_start": time.strftime("%Y-%m-%d", time.gmtime(since_ms / 1000)),
        "last_completed_4h_candle": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                   time.gmtime(last_ts / 1000)),
        "walk_forward": {
            "method": "one continuous portfolio; equity, positions, stops and global drawdown carry across 30-day reporting folds",
            "warmup_days": 220, "fold_days": 30, "capital_usdt": INITIAL_CAPITAL,
        },
        "holdout": {
            "method": "last 180 days; isolated fresh virtual capital; rules frozen before evaluation",
            "caveat": "This is strategy-specific locked-rule evaluation, not globally unseen market data; the prior research already inspected an overlapping shorter period for other strategies.",
            "start": time.strftime("%Y-%m-%d", time.gmtime(holdout_start / 1000)),
        },
        "strategies": {
            "donchian": "55-bar high breakout; 20-bar low exit; 3x ATR(14) stop",
            "momentum": "top 2 of 6 by 90-day return; daily rebalance; daily SMA200 regime gate; 3x ATR(14) stop",
            "pullback": "daily close>SMA200; 4h EMA50>EMA200; enter next open after close reclaims EMA20 from below with RSI14 45..65; exit below EMA50 or daily regime loss; initial 2.5x ATR stop, then 3x ATR trailing stop from highest completed close",
        },
        "execution": {
            "entry": "next 4h open", "fee_each_side": FEE_RATE,
            "slippage_each_side": SLIPPAGE_RATE,
            "spread_scenarios_round_trip_pct": spreads,
            "max_positions": MAX_POSITIONS, "max_notional_usdt": MAX_NOTIONAL,
            "risk_scenarios": [0.005, 0.01],
        },
        "scenarios": scenarios,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exchange", default="okx")
    parser.add_argument("--since", default="2023-01-01")
    parser.add_argument("--spread-values", default="0.05,0.10,0.15")
    args = parser.parse_args()
    exchange = getattr(ccxt, args.exchange)({"enableRateLimit": True, "timeout": 30000})
    since_ms = exchange.parse8601(args.since + "T00:00:00Z")
    data = {}
    for symbol in SYMBOLS:
        print(f"Downloading {symbol} 4h and 1d", flush=True)
        data[symbol] = {
            "4h": fetch_history(exchange, symbol, "4h", since_ms),
            "1d": fetch_history(exchange, symbol, "1d", since_ms),
        }
    spreads = [float(v) for v in args.spread_values.split(",")]
    if spreads != [0.05, 0.10, 0.15]:
        raise ValueError("Required spread scenarios are exactly 0.05,0.10,0.15")
    report = run_report(data, since_ms, spreads)
    Path("reports").mkdir(exist_ok=True)
    Path("reports/continuous_portfolio_research.json").write_text(
        json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({k: v["acceptance"] for k, v in report["scenarios"].items()}, indent=2))


if __name__ == "__main__":
    main()
