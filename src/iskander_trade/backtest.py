"""Chronological, cost-aware EMA baseline vs screened EMA portfolio backtest.

Historical candles are expected to be closed OHLCV rows in exchange format:
[timestamp_ms, open, high, low, close, volume].
The final holdout must be selected before inspecting results and must never be
used to tune strategy thresholds.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from typing import Any

from .indicators import ema
from .selection import evaluate_long_candidate


@dataclass(frozen=True)
class BacktestConfig:
    fast_period: int = 20
    slow_period: int = 50
    initial_capital_usdt: float = 70.0
    risk_per_trade_usdt: float = 0.35
    max_daily_loss_usdt: float = 1.40
    max_open_positions: int = 3
    max_position_notional_usdt: float = 20.0
    stop_loss_pct: float = 2.0
    take_profit_pct: float = 4.0
    taker_fee_rate: float = 0.001
    slippage_rate: float = 0.0005
    assumed_spread_pct: float = 0.05
    max_spread_pct: float = 0.15
    timeframe_ms: int = 900_000
    context_timeframe_ms: int = 3_600_000

    def validate(self) -> None:
        if self.fast_period < 2 or self.slow_period <= self.fast_period:
            raise ValueError("EMA periods are invalid.")
        if min(
            self.initial_capital_usdt, self.risk_per_trade_usdt,
            self.max_daily_loss_usdt, self.max_position_notional_usdt, self.stop_loss_pct,
            self.take_profit_pct,
        ) <= 0:
            raise ValueError("Capital, risk, sizing, stop and target must be positive.")
        if self.max_open_positions < 1:
            raise ValueError("max_open_positions must be positive.")
        if not 0 <= self.taker_fee_rate < 0.02:
            raise ValueError("taker_fee_rate is outside the supported range.")
        if not 0 <= self.slippage_rate < 0.02:
            raise ValueError("slippage_rate is outside the supported range.")
        if not 0 <= self.assumed_spread_pct <= self.max_spread_pct <= 2:
            raise ValueError("Spread assumptions/limit are invalid.")


def _validate_rows(rows: list[list[float]], duration_ms: int, symbol: str) -> None:
    if len(rows) < 3:
        raise ValueError(f"{symbol}: insufficient historical candles")
    previous = -1
    for index, row in enumerate(rows):
        if len(row) < 6:
            raise ValueError(f"{symbol}: malformed candle at {index}")
        timestamp, open_, high, low, close, volume = map(float, row[:6])
        values = (timestamp, open_, high, low, close, volume)
        if any(not math.isfinite(value) for value in values):
            raise ValueError(f"{symbol}: non-finite candle at {index}")
        if int(timestamp) != timestamp or int(timestamp) % duration_ms:
            raise ValueError(f"{symbol}: invalid candle timestamp at {index}")
        if int(timestamp) <= previous:
            raise ValueError(f"{symbol}: duplicate/out-of-order candle at {index}")
        if previous >= 0 and int(timestamp) - previous != duration_ms:
            raise ValueError(f"{symbol}: missing_candle_gap at {index}")
        if min(open_, high, low, close) <= 0 or volume < 0:
            raise ValueError(f"{symbol}: invalid price/volume at {index}")
        if high < max(open_, close, low) or low > min(open_, close):
            raise ValueError(f"{symbol}: inconsistent OHLC at {index}")
        previous = int(timestamp)


def _metrics(
    trades: list[dict[str, Any]],
    *,
    initial_capital: float,
    max_drawdown_usdt: float,
) -> dict[str, float | int | None]:
    pnl = [float(trade["net_pnl_usdt"]) for trade in trades]
    wins = [value for value in pnl if value > 0]
    losses = [value for value in pnl if value < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    return {
        "trades": len(pnl),
        "win_rate_pct": (len(wins) / len(pnl) * 100.0) if pnl else 0.0,
        "average_win_usdt": (sum(wins) / len(wins)) if wins else 0.0,
        "average_loss_usdt": (sum(losses) / len(losses)) if losses else 0.0,
        "expectancy_usdt_per_trade": (sum(pnl) / len(pnl)) if pnl else 0.0,
        "net_pnl_usdt": sum(pnl),
        "return_pct": (sum(pnl) / initial_capital * 100.0),
        "profit_factor": (gross_profit / gross_loss) if gross_loss else (
            None if gross_profit else 0.0
        ),
        "max_drawdown_usdt": max_drawdown_usdt,
        "max_drawdown_pct_of_initial": max_drawdown_usdt / initial_capital * 100.0,
    }


def run_ema_backtest(
    datasets: dict[str, dict[str, list[list[float]]]],
    *,
    screened: bool,
    config: BacktestConfig | None = None,
    evaluation_start_ms: int | None = None,
    evaluation_end_ms: int | None = None,
) -> dict[str, Any]:
    """Run a synchronized multi-symbol, long-only Spot portfolio simulation.

    Signals are evaluated at a closed 15m candle and filled at the next candle
    open. If stop and target are both touched in one candle, stop is assumed first.
    This pessimistic tie-break avoids optimistic intrabar path assumptions.
    """
    cfg = config or BacktestConfig()
    cfg.validate()
    if not datasets:
        raise ValueError("No market datasets supplied.")

    prepared: dict[str, dict[str, Any]] = {}
    timeline: set[int] = set()
    for symbol, series in datasets.items():
        fifteen = series.get("15m", [])
        hourly = series.get("1h", [])
        _validate_rows(fifteen, cfg.timeframe_ms, symbol + " 15m")
        _validate_rows(hourly, cfg.context_timeframe_ms, symbol + " 1h")
        closes = [float(row[4]) for row in fifteen]
        prepared[symbol] = {
            "fifteen": fifteen,
            "hourly": hourly,
            "fast": ema(closes, cfg.fast_period),
            "slow": ema(closes, cfg.slow_period),
            "index": {int(row[0]): i for i, row in enumerate(fifteen)},
            "pending": None,
        }
        timeline.update(int(row[0]) for row in fifteen)

    positions: dict[str, dict[str, Any]] = {}
    trades: list[dict[str, Any]] = []
    rejected: Counter[str] = Counter()
    capacity_rejections = 0
    daily_loss_rejections = 0
    rejected_candidates = 0
    daily_pnl_usdt = 0.0
    daily_utc_day: int | None = None
    realized_pnl = 0.0
    peak_equity = cfg.initial_capital_usdt
    max_drawdown = 0.0
    total_fees = total_slippage = total_spread = 0.0
    last_active_rows: dict[str, list[float]] = {}

    def close_position(symbol: str, raw_exit: float, timestamp: int, reason: str) -> None:
        nonlocal realized_pnl, total_fees, total_slippage, total_spread
        nonlocal daily_pnl_usdt, daily_utc_day
        position = positions.pop(symbol)
        quantity = position["quantity"]
        spread_fraction = cfg.assumed_spread_pct / 100.0
        exit_fill = raw_exit * (1.0 - cfg.slippage_rate - spread_fraction / 2.0)
        exit_fee = exit_fill * quantity * cfg.taker_fee_rate
        gross = (exit_fill - position["entry_fill"]) * quantity
        fees = position["entry_fee"] + exit_fee
        net = gross - fees
        slip_cost = quantity * (
            position["raw_entry"] * cfg.slippage_rate + raw_exit * cfg.slippage_rate
        )
        spread_cost = quantity * (
            position["raw_entry"] * spread_fraction / 2.0
            + raw_exit * spread_fraction / 2.0
        )
        trade_day = timestamp // 86_400_000
        if daily_utc_day != trade_day:
            daily_utc_day = trade_day
            daily_pnl_usdt = 0.0
        daily_pnl_usdt += net
        realized_pnl += net
        total_fees += fees
        total_slippage += slip_cost
        total_spread += spread_cost
        trades.append({
            "symbol": symbol,
            "opened_at": position["opened_at"],
            "closed_at": timestamp,
            "entry_price": position["entry_fill"],
            "exit_price": exit_fill,
            "quantity": quantity,
            "notional_usdt": position["notional"],
            "gross_pnl_usdt": gross,
            "fees_usdt": fees,
            "slippage_cost_usdt": slip_cost,
            "spread_cost_usdt": spread_cost,
            "net_pnl_usdt": net,
            "return_pct": net / position["notional"] * 100.0,
            "exit_reason": reason,
        })

    for timestamp in sorted(timeline):
        if evaluation_end_ms is not None and timestamp > evaluation_end_ms:
            break
        current_rows: dict[str, tuple[int, list[float]]] = {}
        for symbol, item in prepared.items():
            index = item["index"].get(timestamp)
            if index is not None:
                current_rows[symbol] = (index, item["fifteen"][index])

        current_day = timestamp // 86_400_000
        if daily_utc_day != current_day:
            daily_utc_day = current_day
            daily_pnl_usdt = 0.0

        # Execute orders generated at the previous candle close, at this candle open.
        for symbol, (index, candle) in current_rows.items():
            if evaluation_start_ms is not None and timestamp < evaluation_start_ms:
                continue
            item = prepared[symbol]
            pending = item["pending"]
            item["pending"] = None
            if pending == "sell" and symbol in positions:
                close_position(symbol, float(candle[1]), timestamp, "ema_cross_down")
            elif pending == "buy" and symbol not in positions:
                if daily_pnl_usdt <= -cfg.max_daily_loss_usdt:
                    daily_loss_rejections += 1
                    continue
                if len(positions) >= cfg.max_open_positions:
                    capacity_rejections += 1
                    continue
                raw_entry = float(candle[1])
                if screened:
                    # Re-check the gates at the actual next-open fill price. The
                    # feature window contains only candles closed before this open.
                    closed_15m = item["fifteen"][:index]
                    closed_1h = [
                        row for row in item["hourly"]
                        if int(row[0]) + cfg.context_timeframe_ms <= timestamp
                    ]
                    if not closed_15m or not closed_1h:
                        rejected_candidates += 1
                        rejected["insufficient_multi_timeframe_history"] += 1
                        continue
                    decision = evaluate_long_candidate(
                        closed_15m + [closed_15m[-1]],
                        closed_1h + [closed_1h[-1]],
                        entry_price=raw_entry,
                        stop_loss_pct=cfg.stop_loss_pct,
                        take_profit_pct=cfg.take_profit_pct,
                        taker_fee_rate=cfg.taker_fee_rate,
                        slippage_rate=cfg.slippage_rate,
                        spread_pct=cfg.assumed_spread_pct,
                        max_spread_pct=cfg.max_spread_pct,
                    )
                    if not decision.accepted:
                        rejected_candidates += 1
                        rejected.update(decision.reasons)
                        continue
                spread_fraction = cfg.assumed_spread_pct / 100.0
                entry_fill = raw_entry * (
                    1.0 + cfg.slippage_rate + spread_fraction / 2.0
                )
                open_notional = sum(p["notional"] for p in positions.values())
                open_fees = sum(p["entry_fee"] for p in positions.values())
                available = max(
                    0.0, cfg.initial_capital_usdt + realized_pnl - open_notional - open_fees
                )
                round_trip_cost_pct = (
                    2.0 * (cfg.taker_fee_rate + cfg.slippage_rate) * 100.0
                    + cfg.assumed_spread_pct
                )
                effective_risk_pct = cfg.stop_loss_pct + round_trip_cost_pct
                risk_notional = cfg.risk_per_trade_usdt / (effective_risk_pct / 100.0)
                notional = min(
                    cfg.max_position_notional_usdt, available, risk_notional
                )
                if notional <= 0:
                    capacity_rejections += 1
                    continue
                quantity = notional / entry_fill
                actual_notional = quantity * entry_fill
                entry_fee = actual_notional * cfg.taker_fee_rate
                positions[symbol] = {
                    "raw_entry": raw_entry,
                    "entry_fill": entry_fill,
                    "quantity": quantity,
                    "notional": actual_notional,
                    "entry_fee": entry_fee,
                    "opened_at": timestamp,
                    "stop": entry_fill * (1.0 - cfg.stop_loss_pct / 100.0),
                    "target": entry_fill * (1.0 + cfg.take_profit_pct / 100.0),
                }

        # Conservative intrabar stop/target simulation; gaps fill at the open.
        for symbol, (_, candle) in current_rows.items():
            position = positions.get(symbol)
            if position is None:
                continue
            open_, high, low = float(candle[1]), float(candle[2]), float(candle[3])
            stop, target = position["stop"], position["target"]
            if open_ <= stop:
                close_position(symbol, open_, timestamp, "stop_loss_gap")
            elif open_ >= target:
                close_position(symbol, open_, timestamp, "take_profit_gap")
            elif low <= stop:
                close_position(symbol, stop, timestamp, "stop_loss")
            elif high >= target:
                close_position(symbol, target, timestamp, "take_profit")

        # Evaluate closed-candle crossovers. Entry/exit is deferred to next candle open.
        for symbol, (index, candle) in current_rows.items():
            item = prepared[symbol]
            if index < cfg.slow_period + 1:
                continue
            fast, slow = item["fast"], item["slow"]
            buy_cross = fast[index - 1] <= slow[index - 1] and fast[index] > slow[index]
            sell_cross = fast[index - 1] >= slow[index - 1] and fast[index] < slow[index]
            if sell_cross and symbol in positions:
                if evaluation_start_ms is None or timestamp >= evaluation_start_ms:
                    item["pending"] = "sell"
                continue
            if not buy_cross or symbol in positions:
                continue
            if evaluation_start_ms is not None and timestamp + cfg.timeframe_ms < evaluation_start_ms:
                continue

            # Selection is evaluated at the next candle open using its actual price.
            item["pending"] = "buy"

        if evaluation_start_ms is not None and timestamp < evaluation_start_ms:
            continue
        last_active_rows.update({symbol: candle for symbol, (_, candle) in current_rows.items()})

        # Mark-to-market drawdown includes estimated liquidation costs on open positions.
        unrealized = 0.0
        for symbol, position in positions.items():
            row_info = current_rows.get(symbol)
            if row_info is None:
                continue
            mark = float(row_info[1][4])
            estimated_exit = mark * (
                1.0 - cfg.slippage_rate - cfg.assumed_spread_pct / 200.0
            )
            estimated_exit_fee = estimated_exit * position["quantity"] * cfg.taker_fee_rate
            unrealized += (
                (estimated_exit - position["entry_fill"]) * position["quantity"]
                - position["entry_fee"] - estimated_exit_fee
            )
        equity = cfg.initial_capital_usdt + realized_pnl + unrealized
        peak_equity = max(peak_equity, equity)
        max_drawdown = max(max_drawdown, peak_equity - equity)

    # Liquidate remaining positions at the final close inside the evaluation window.
    for symbol in list(positions):
        last_row = last_active_rows.get(symbol, prepared[symbol]["fifteen"][-1])
        close_position(symbol, float(last_row[4]), int(last_row[0]), "end_of_evaluation")

    summary = _metrics(
        trades, initial_capital=cfg.initial_capital_usdt,
        max_drawdown_usdt=max_drawdown,
    )
    summary.update({
        "rejected_candidates": rejected_candidates,
        "rejected_candidate_reasons": dict(rejected),
        "capacity_rejections": capacity_rejections,
        "daily_loss_rejections": daily_loss_rejections,
        "fees_usdt": total_fees,
        "slippage_cost_usdt": total_slippage,
        "assumed_spread_cost_usdt": total_spread,
        "assumed_spread_pct": cfg.assumed_spread_pct,
    })
    by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trade in trades:
        by_symbol[trade["symbol"]].append(trade)
    per_symbol = {}
    for symbol, symbol_trades in sorted(by_symbol.items()):
        symbol_equity = peak = cfg.initial_capital_usdt
        symbol_drawdown = 0.0
        for trade in sorted(symbol_trades, key=lambda item: item["closed_at"]):
            symbol_equity += float(trade["net_pnl_usdt"])
            peak = max(peak, symbol_equity)
            symbol_drawdown = max(symbol_drawdown, peak - symbol_equity)
        per_symbol[symbol] = _metrics(
            symbol_trades,
            initial_capital=cfg.initial_capital_usdt,
            max_drawdown_usdt=symbol_drawdown,
        )
    return {
        "mode": "ema_with_selection" if screened else "ema_baseline",
        "config": asdict(cfg),
        "summary": summary,
        "per_symbol": per_symbol,
        "trades": trades,
    }


def compare_strategies(
    datasets: dict[str, dict[str, list[list[float]]]],
    *,
    config: BacktestConfig | None = None,
    evaluation_start_ms: int | None = None,
    evaluation_end_ms: int | None = None,
) -> dict[str, Any]:
    cfg = config or BacktestConfig()
    return {
        "baseline": run_ema_backtest(
            datasets, screened=False, config=cfg,
            evaluation_start_ms=evaluation_start_ms, evaluation_end_ms=evaluation_end_ms,
        ),
        "screened": run_ema_backtest(
            datasets, screened=True, config=cfg,
            evaluation_start_ms=evaluation_start_ms, evaluation_end_ms=evaluation_end_ms,
        ),
        "methodology": {
            "signal": "EMA fast/slow crossover on closed 15m candles",
            "entry": "next 15m candle open",
            "exit": "next open after EMA cross-down, or intrabar stop/target",
            "intrabar_ambiguity": "stop-loss is assumed first when stop and target both touch",
            "costs": "taker fees + configurable slippage + explicitly assumed spread",
            "lookahead": "none; 1h context includes only candles fully closed at signal time",
            "holdout": "must be reserved chronologically before inspecting results",
        },
    }
