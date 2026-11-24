"""Public market-data adapter. No authenticated exchange endpoints are used."""

from __future__ import annotations

import math

import ccxt


class BinanceMarketData:
    def __init__(self) -> None:
        self.exchange = ccxt.binance(
            {
                "enableRateLimit": True,
                "timeout": 20000,
                "options": {"defaultType": "spot"},
            }
        )

    def fetch_candles(self, symbol: str, timeframe: str, limit: int) -> list[list[float]]:
        rows = self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        if not rows or len(rows) < 3:
            raise RuntimeError(f"Insufficient OHLCV data for {symbol}.")
        validated: list[list[float]] = []
        for index, row in enumerate(rows):
            if len(row) < 6:
                raise ValueError(f"Malformed OHLCV row {index} for {symbol}.")
            try:
                values = [float(value) for value in row[:6]]
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Non-numeric OHLCV row {index} for {symbol}.") from exc
            if not all(math.isfinite(value) for value in values):
                raise ValueError(f"Non-finite OHLCV value in row {index} for {symbol}.")
            timestamp, open_price, high, low, close, volume = values
            if min(open_price, high, low, close) <= 0 or volume < 0:
                raise ValueError(f"Non-positive OHLCV price or negative volume in row {index} for {symbol}.")
            if high < max(open_price, close, low) or low > min(open_price, close, high):
                raise ValueError(f"Inconsistent OHLCV high/low in row {index} for {symbol}.")
            validated.append([timestamp, open_price, high, low, close, volume])
        return validated

    def fetch_last_price(self, symbol: str) -> float:
        ticker = self.exchange.fetch_ticker(symbol)
        raw_price = ticker.get("last")
        try:
            price = float(raw_price)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid last price received for {symbol}.") from exc
        if not math.isfinite(price) or price <= 0:
            raise ValueError(f"Invalid last price received for {symbol}.")
        return price

    def close(self) -> None:
        self.exchange.close()
