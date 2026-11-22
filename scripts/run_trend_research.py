"""4h trend research: Donchian breakout and cross-sectional momentum.

Research only. Uses public OHLCV, a completed-daily 200 SMA regime gate,
ATR-based position sizing/stops, and next-4h-open execution.
"""
from __future__ import annotations

import argparse
import json
import math
import time
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
    result = [rows[key] for key in sorted(rows) if key + duration <= now_ms]
    if len(result) < 220:
        raise ValueError(f"{symbol} {timeframe}: insufficient history ({len(result)})")
    previous = None
    for row in result:
        ts, o, h, low, close, volume = row
        if int(ts) % duration or any(not math.isfinite(v) for v in row):
            raise ValueError(f"{symbol} {timeframe}: invalid timestamp/value")
        if min(o, h, low, close) <= 0 or volume < 0 or h < max(o, close, low) or low > min(o, close):
            raise ValueError(f"{symbol} {timeframe}: invalid OHLCV")
        if previous is not None and int(ts) - previous != duration:
            raise ValueError(f"{symbol} {timeframe}: historical gap at {int(ts)}")
        previous = int(ts)
    return result


def atr_values(rows: list[list[float]], period: int = 14) -> list[float]:
    tr = []
    for i, row in enumerate(rows):
        high, low, close = row[2], row[3], row[4]
        prev = rows[i - 1][4] if i else close
        tr.append(max(high - low, abs(high - prev), abs(low - prev)))
    out = [float("nan")] * len(rows)
    for i in range(period - 1, len(rows)):
        out[i] = sum(tr[i - period + 1:i + 1]) / period
    return out


def daily_regime(rows: list[list[float]], timestamp: int) -> bool:
    completed = [r[4] for r in rows if int(r[0]) + ONE_DAY <= timestamp]
    return len(completed) >= 200 and completed[-1] > sum(completed[-200:]) / 200


def metrics(trades: list[dict[str, Any]], max_dd: float) -> dict[str, Any]:
    pnls = [float(t["net_pnl_usdt"]) for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gp, gl = sum(wins), abs(sum(losses))
    return {
        "trades": len(pnls),
        "win_rate_pct": len(wins) / len(pnls) * 100 if pnls else 0.0,
        "average_win_usdt": sum(wins) / len(wins) if wins else 0.0,
        "average_loss_usdt": sum(losses) / len(losses) if losses else 0.0,
        "expectancy_usdt_per_trade": sum(pnls) / len(pnls) if pnls else 0.0,
        "net_pnl_usdt": sum(pnls),
        "profit_factor": gp / gl if gl else (None if gp else 0.0),
        "max_drawdown_usdt": max_dd,
    }


def simulate(
    data: dict[str, dict[str, list[list[float]]]],
    strategy: str,
    risk_fraction: float,
    spread_pct: float,
    start_ms: int,
    end_ms: int,
) -> dict[str, Any]:
    prepared = {}
    timeline: set[int] = set()
    for symbol, series in data.items():
        bars, daily = series["4h"], series["1d"]
        atr = atr_values(bars)
        prepared[symbol] = {"bars": bars, "daily": daily, "atr": atr,
                            "index": {int(r[0]): i for i, r in enumerate(bars)},
                            "pending": None}
        timeline.update(int(r[0]) for r in bars)

    positions: dict[str, dict[str, Any]] = {}
    trades: list[dict[str, Any]] = []
    realized = 0.0
    peak = INITIAL_CAPITAL
    max_dd = 0.0
    last_rows: dict[str, list[float]] = {}

    def close(symbol: str, raw_exit: float, ts: int, reason: str) -> None:
        nonlocal realized
        p = positions.pop(symbol)
        half_spread = spread_pct / 200.0
        exit_fill = raw_exit * (1 - SLIPPAGE_RATE - half_spread)
        exit_fee = exit_fill * p["qty"] * FEE_RATE
        gross = (exit_fill - p["entry_fill"]) * p["qty"]
        fees = p["entry_fee"] + exit_fee
        net = gross - fees
        realized += net
        trades.append({"symbol": symbol, "opened_at": p["opened_at"], "closed_at": ts,
                       "entry_price": p["entry_fill"], "exit_price": exit_fill,
                       "quantity": p["qty"], "notional_usdt": p["notional"],
                       "gross_pnl_usdt": gross, "fees_usdt": fees,
                       "net_pnl_usdt": net, "exit_reason": reason})

    for ts in sorted(timeline):
        if ts > end_ms:
            break
        current = {}
        for symbol, item in prepared.items():
            idx = item["index"].get(ts)
            if idx is not None:
                current[symbol] = (idx, item["bars"][idx])

        # Execute exits/entries generated at the preceding completed 4h close.
        for symbol, (idx, bar) in current.items():
            item = prepared[symbol]
            action = item["pending"]
            item["pending"] = None
            if action == "sell" and symbol in positions:
                close(symbol, bar[1], ts, "strategy_or_regime_exit")
            elif action == "buy" and symbol not in positions and ts >= start_ms:
                if len(positions) >= MAX_POSITIONS:
                    continue
                a = item["atr"][idx - 1] if idx else float("nan")
                if not math.isfinite(a) or a <= 0:
                    continue
                equity = max(0.0, INITIAL_CAPITAL + realized)
                stop_distance = 3.0 * a
                risk_budget = equity * risk_fraction
                effective_risk_fraction = (
                    stop_distance / bar[1]
                    + 2.0 * (FEE_RATE + SLIPPAGE_RATE)
                    + spread_pct / 100.0
                )
                risk_notional = risk_budget / effective_risk_fraction
                available = max(0.0, equity - sum(p["notional"] for p in positions.values()))
                notional = min(MAX_NOTIONAL, available, risk_notional)
                if notional < 1.0:
                    continue
                half_spread = spread_pct / 200.0
                entry_fill = bar[1] * (1 + SLIPPAGE_RATE + half_spread)
                qty = notional / entry_fill
                entry_fee = notional * FEE_RATE
                positions[symbol] = {"qty": qty, "notional": notional,
                    "entry_fill": entry_fill, "entry_fee": entry_fee,
                    "opened_at": ts, "stop": bar[1] - stop_distance,
                    "entry_atr": a}

        # Intrabar ATR stop, with gap-first conservative fills.
        for symbol, (_, bar) in current.items():
            p = positions.get(symbol)
            if not p:
                continue
            if bar[1] <= p["stop"]:
                close(symbol, bar[1], ts, "atr_stop_gap")
            elif bar[3] <= p["stop"]:
                close(symbol, p["stop"], ts, "atr_stop")

        # Evaluate only closed bars; all actions execute at next 4h open.
        if ts < start_ms:
            continue
        last_rows.update({s: b for s, (_, b) in current.items()})
        if strategy == "donchian":
            for symbol, (idx, bar) in current.items():
                item = prepared[symbol]
                if idx < 55 or not math.isfinite(item["atr"][idx]):
                    continue
                regime = daily_regime(item["daily"], ts + FOUR_HOURS)
                prior_high = max(r[2] for r in item["bars"][idx - 55:idx])
                prior_low = min(r[3] for r in item["bars"][idx - 20:idx])
                if symbol in positions and (not regime or bar[4] < prior_low):
                    item["pending"] = "sell"
                elif symbol not in positions and regime and bar[4] > prior_high:
                    item["pending"] = "buy"
        elif strategy == "momentum":
            # Rebalance once per UTC day; 90-day return ranks are cross-sectional.
            if (ts // ONE_DAY) != ((ts - FOUR_HOURS) // ONE_DAY):
                scores = {}
                for symbol, (idx, bar) in current.items():
                    item = prepared[symbol]
                    if idx < 540 or not daily_regime(item["daily"], ts + FOUR_HOURS):
                        continue
                    old = item["bars"][idx - 540][4]
                    if old > 0:
                        scores[symbol] = bar[4] / old - 1.0
                leaders = set(sorted(scores, key=scores.get, reverse=True)[:2])
                for symbol in prepared:
                    if symbol in positions and symbol not in leaders:
                        prepared[symbol]["pending"] = "sell"
                    elif symbol not in positions and symbol in leaders:
                        prepared[symbol]["pending"] = "buy"
        else:
            raise ValueError(f"Unknown strategy: {strategy}")

        unrealized = 0.0
        for symbol, position in positions.items():
            row = current.get(symbol)
            if row is None:
                continue
            mark = row[1][4] * (1 - SLIPPAGE_RATE - spread_pct / 200.0)
            exit_fee = mark * position["qty"] * FEE_RATE
            unrealized += (mark - position["entry_fill"]) * position["qty"] - position["entry_fee"] - exit_fee
        equity = INITIAL_CAPITAL + realized + unrealized
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)

    for symbol in list(positions):
        row = last_rows.get(symbol)
        if row:
            close(symbol, row[4], int(row[0]), "end_of_window")
    final_equity = INITIAL_CAPITAL + realized
    peak = max(peak, final_equity)
    max_dd = max(max_dd, peak - final_equity)
    return {"summary": metrics(trades, max_dd), "trades": trades}


def run_report(data: dict[str, dict[str, list[list[float]]]], since_ms: int,
               spread_values: list[float]) -> dict[str, Any]:
    last_ts = min(int(v["4h"][-1][0]) for v in data.values())
    holdout_start = last_ts - 90 * ONE_DAY
    wf_start = since_ms + 200 * ONE_DAY
    strategies = ["donchian", "momentum"]
    scenarios = {}
    for strategy in strategies:
        for risk in (0.005, 0.01):
            for spread in spread_values:
                folds = []
                cursor = wf_start
                while cursor < holdout_start:
                    end = min(cursor + 30 * ONE_DAY, holdout_start)
                    folds.append(simulate(data, strategy, risk, spread, cursor, end))
                    cursor = end
                wf_trades = [t for fold in folds for t in fold["trades"]]
                wf_max_dd = max((fold["summary"]["max_drawdown_usdt"] for fold in folds), default=0.0)
                holdout = simulate(data, strategy, risk, spread, holdout_start, last_ts)
                scenarios[f"{strategy}|risk={risk:.3f}|spread={spread:.2f}"] = {
                    "strategy": strategy, "risk_fraction": risk, "spread_round_trip_pct": spread,
                    "walk_forward": metrics(wf_trades, wf_max_dd),
                    "walk_forward_folds": [fold["summary"] for fold in folds],
                    "holdout": holdout["summary"],
                    "acceptance": {
                        "walk_forward_min_100_trades": len(wf_trades) >= 100,
                        "holdout_min_100_trades": len(holdout["trades"]) >= 100,
                        "walk_forward_positive_expectancy": metrics(wf_trades, wf_max_dd)["expectancy_usdt_per_trade"] > 0,
                        "holdout_positive_expectancy": holdout["summary"]["expectancy_usdt_per_trade"] > 0,
                    },
                }
    return {
        "provider": "OKX public OHLCV",
        "markets": SYMBOLS,
        "timeframe": "4h",
        "regime_filter": "last completed daily close > SMA(200) of completed daily closes",
        "strategies": {
            "donchian": "55-bar 4h breakout; 20-bar low exit; 3x ATR(14) initial stop",
            "momentum": "top 2 of 6 by 90-day 4h momentum; daily rebalance; daily SMA200 regime gate; 3x ATR(14) initial stop",
        },
        "risk_scenarios": [0.005, 0.01],
        "spread_scenarios_round_trip_pct": spread_values,
        "execution": {"entry": "next 4h open", "fee_each_side": FEE_RATE,
                      "slippage_each_side": SLIPPAGE_RATE, "initial_capital_usdt": INITIAL_CAPITAL,
                      "max_positions": MAX_POSITIONS, "max_notional_usdt": MAX_NOTIONAL},
        "walk_forward": "sequential 30-day folds after 200-day warmup; frozen rules",
        "holdout": "last 90 days, untouched",
        "scenarios": scenarios,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exchange", default="okx")
    parser.add_argument("--since", default="2024-01-01")
    parser.add_argument("--spread-values", default="0.05,0.10,0.15")
    args = parser.parse_args()
    exchange_class = getattr(ccxt, args.exchange)
    exchange = exchange_class({"enableRateLimit": True, "timeout": 30000})
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
    Path("reports/trend_4h_research.json").write_text(
        json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({k: v["acceptance"] for k, v in report["scenarios"].items()}, indent=2))


if __name__ == "__main__":
    main()
