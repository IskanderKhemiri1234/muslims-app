"""Fail-closed validation for public OHLCV candles."""

from __future__ import annotations

import math
import time

from collections.abc import Sequence


_TIMEFRAME_MS = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}


class MarketDataIntegrityError(ValueError):
    """Raised when market data is malformed, stale, or internally inconsistent."""


def validate_ohlcv_candles(
    candles: Sequence[Sequence[float]],
    timeframe: str,
    *,
    now_ms: int | None = None,
    minimum_rows: int = 3,
) -> list[list[float]]:
    """Validate OHLCV structure and freshness before any strategy can consume it.

    The final row may be the exchange's still-forming candle; strategy code remains
    responsible for excluding it from signal calculations.
    """
    duration_ms = _TIMEFRAME_MS.get(timeframe)
    if duration_ms is None:
        raise MarketDataIntegrityError(f"unsupported_timeframe:{timeframe}")
    if not isinstance(candles, Sequence) or len(candles) < minimum_rows:
        raise MarketDataIntegrityError("insufficient_candles")

    current_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    normalized: list[list[float]] = []
    previous_timestamp: int | None = None

    for index, row in enumerate(candles):
        if not isinstance(row, Sequence) or isinstance(row, (str, bytes)) or len(row) < 6:
            raise MarketDataIntegrityError(f"malformed_candle:{index}")
        try:
            values = [float(row[column]) for column in range(6)]
        except (TypeError, ValueError, OverflowError) as exc:
            raise MarketDataIntegrityError(f"non_numeric_candle:{index}") from exc

        if not all(math.isfinite(value) for value in values):
            raise MarketDataIntegrityError(f"non_finite_candle:{index}")

        timestamp_value, open_price, high, low, close, volume = values
        timestamp = int(timestamp_value)
        if timestamp != timestamp_value or timestamp < 0:
            raise MarketDataIntegrityError(f"invalid_timestamp:{index}")
        if timestamp % duration_ms != 0:
            raise MarketDataIntegrityError(f"unaligned_timestamp:{index}")
        if previous_timestamp is not None and timestamp <= previous_timestamp:
            code = "duplicate_timestamp" if timestamp == previous_timestamp else "out_of_order_timestamp"
            raise MarketDataIntegrityError(f"{code}:{index}")
        if min(open_price, high, low, close) <= 0 or volume < 0:
            raise MarketDataIntegrityError(f"invalid_price_or_volume:{index}")
        if high < max(open_price, close, low) or low > min(open_price, close):
            raise MarketDataIntegrityError(f"invalid_ohlc_relationship:{index}")
        if timestamp > current_ms + 60_000:
            raise MarketDataIntegrityError(f"future_candle:{index}")

        normalized.append([timestamp, open_price, high, low, close, volume])
        previous_timestamp = timestamp

    latest_timestamp = int(normalized[-1][0])
    if current_ms - latest_timestamp > duration_ms * 2:
        raise MarketDataIntegrityError("stale_market_data")

    return normalized
