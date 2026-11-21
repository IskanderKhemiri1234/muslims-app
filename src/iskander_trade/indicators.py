"""Small dependency-free indicator functions."""

from __future__ import annotations


def ema(values: list[float], period: int) -> list[float]:
    if period < 1 or len(values) < period:
        raise ValueError("EMA requires at least period values and a positive period.")
    alpha = 2.0 / (period + 1.0)
    result = [float(values[0])]
    for value in values[1:]:
        result.append(float(value) * alpha + result[-1] * (1.0 - alpha))
    return result
