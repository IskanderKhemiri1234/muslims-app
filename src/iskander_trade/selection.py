"""Regime-aware, explainable screening for long paper entries.

This is a deterministic research gate, not a predictive or profitability guarantee.
All thresholds are explicit screening heuristics and must be evaluated out of sample.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .indicators import ema


@dataclass(frozen=True)
class SelectionDecision:
    accepted: bool
    regime: str
    reasons: tuple[str, ...]
    metrics: dict[str, float]


def _true_ranges(candles: list[list[float]]) -> list[float]:
    ranges: list[float] = []
    previous_close: float | None = None
    for row in candles:
        high, low, close = float(row[2]), float(row[3]), float(row[4])
        if previous_close is None:
            ranges.append(high - low)
        else:
            ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
        previous_close = close
    return ranges


def _efficiency_ratio(closes: list[float], period: int = 20) -> float:
    window = closes[-(period + 1):]
    path = sum(abs(right - left) for left, right in zip(window, window[1:]))
    if path <= 0:
        return 0.0
    return abs(window[-1] - window[0]) / path


def _regime(candles: list[list[float]]) -> tuple[str, dict[str, float]]:
    closed = candles[:-1]
    if len(closed) < 60:
        return "insufficient_history", {}
    closes = [float(row[4]) for row in closed]
    ranges = _true_ranges(closed)
    atr_values = [
        sum(ranges[index - 13:index + 1]) / 14
        for index in range(13, len(ranges))
    ]
    atr = atr_values[-1]
    median_atr = sorted(atr_values[-30:])[len(atr_values[-30:]) // 2]
    efficiency = _efficiency_ratio(closes)
    fast = ema(closes, 20)
    slow = ema(closes, 50)
    atr_pct = atr / closes[-1] * 100 if closes[-1] else 0.0
    volatility_ratio = atr / median_atr if median_atr > 0 else 0.0
    metrics = {
        "efficiency_ratio": efficiency,
        "atr_pct": atr_pct,
        "volatility_ratio": volatility_ratio,
        "ema20_ema50_gap_pct": (fast[-1] / slow[-1] - 1.0) * 100,
    }
    if volatility_ratio >= 2.2:
        return "volatility_shock", metrics
    if efficiency < 0.22:
        return "range", metrics
    if fast[-1] > slow[-1] and fast[-1] > fast[-4] and efficiency >= 0.30:
        return "trend_up", metrics
    if fast[-1] < slow[-1] and fast[-1] < fast[-4] and efficiency >= 0.30:
        return "trend_down", metrics
    return "transition", metrics


def evaluate_long_candidate(
    candles_15m: list[list[float]],
    candles_1h: list[list[float]],
    *,
    entry_price: float,
    stop_loss_pct: float,
    take_profit_pct: float,
    taker_fee_rate: float,
    slippage_rate: float,
    spread_pct: float,
    max_spread_pct: float,
) -> SelectionDecision:
    """Fail closed unless regime, higher timeframe, quality and cost gates pass."""
    reasons: list[str] = []
    regime, metrics = _regime(candles_15m)
    if regime != "trend_up":
        reasons.append(f"regime_rejected:{regime}")

    closed_15m = candles_15m[:-1]
    closed_1h = candles_1h[:-1]
    if len(closed_15m) < 60 or len(closed_1h) < 55:
        reasons.append("insufficient_multi_timeframe_history")
        return SelectionDecision(False, regime, tuple(reasons), metrics)

    closes = [float(row[4]) for row in closed_15m]
    volumes = [float(row[5]) for row in closed_15m]
    highs = [float(row[2]) for row in closed_15m]
    lows = [float(row[3]) for row in closed_15m]
    ranges = _true_ranges(closed_15m)
    atr = sum(ranges[-14:]) / 14
    fast_15m = ema(closes, 20)
    fast_1h = ema([float(row[4]) for row in closed_1h], 20)
    slow_1h = ema([float(row[4]) for row in closed_1h], 50)
    volume_baseline = sum(volumes[-21:-1]) / 20
    volume_ratio = volumes[-1] / volume_baseline if volume_baseline > 0 else 0.0
    momentum_pct = (closes[-1] / closes[-4] - 1.0) * 100 if closes[-4] else 0.0
    chase_atr = abs(entry_price - fast_15m[-1]) / atr if atr > 0 else 999.0
    candle_range = highs[-1] - lows[-1]
    close_location = (closes[-1] - lows[-1]) / candle_range if candle_range > 0 else 0.0
    round_trip_cost_pct = 2.0 * (taker_fee_rate + slippage_rate) * 100.0
    net_reward_pct = take_profit_pct - round_trip_cost_pct
    net_risk_pct = stop_loss_pct + round_trip_cost_pct
    net_reward_risk = net_reward_pct / net_risk_pct if net_risk_pct > 0 else 0.0

    metrics.update({
        "volume_ratio": volume_ratio,
        "momentum_3bar_pct": momentum_pct,
        "entry_distance_atr": chase_atr,
        "close_location": close_location,
        "round_trip_cost_pct": round_trip_cost_pct,
        "spread_pct": spread_pct,
        "net_reward_risk": net_reward_risk,
    })

    if fast_1h[-1] <= slow_1h[-1] or fast_1h[-1] <= fast_1h[-4]:
        reasons.append("higher_timeframe_trend_not_confirmed")
    if closes[-1] <= fast_15m[-1] or momentum_pct <= 0:
        reasons.append("momentum_not_confirmed")
    if volume_ratio < 0.90:
        reasons.append("volume_below_relative_liquidity_floor")
    if not math.isfinite(spread_pct) or spread_pct < 0 or spread_pct > max_spread_pct:
        reasons.append("bid_ask_spread_exceeds_liquidity_limit")
    if close_location < 0.55:
        reasons.append("weak_close_possible_false_breakout")
    if chase_atr > 1.5:
        reasons.append("entry_too_far_from_ema20_atr")
    if net_reward_pct <= 0 or net_reward_risk < 1.20:
        reasons.append("net_reward_risk_insufficient_after_costs")
    if entry_price <= 0 or atr <= 0:
        reasons.append("invalid_entry_or_atr")

    return SelectionDecision(not reasons, regime, tuple(reasons) or ("all_selection_gates_passed",), metrics)
