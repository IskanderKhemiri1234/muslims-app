"""Transparent baseline strategy; this is not a trained machine-learning model."""

from __future__ import annotations

from dataclasses import dataclass

from .indicators import ema


@dataclass(frozen=True)
class Signal:
    action: str
    symbol: str
    candle_timestamp: int
    reason: str
    close_price: float


def ema_crossover(
    symbol: str,
    candles: list[list[float]],
    fast_period: int,
    slow_period: int,
) -> Signal | None:
    # Exclude the currently-forming candle to avoid repainting intrabar signals.
    closed = candles[:-1]
    if len(closed) < slow_period + 2:
        return None
    closes = [float(row[4]) for row in closed]
    fast = ema(closes, fast_period)
    slow = ema(closes, slow_period)
    if fast[-2] <= slow[-2] and fast[-1] > slow[-1]:
        action = "buy"
        reason = f"EMA{fast_period} crossed above EMA{slow_period} on a closed candle"
    elif fast[-2] >= slow[-2] and fast[-1] < slow[-1]:
        action = "sell"
        reason = f"EMA{fast_period} crossed below EMA{slow_period} on a closed candle"
    else:
        return None
    return Signal(action, symbol, int(closed[-1][0]), reason, closes[-1])
