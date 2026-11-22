"""Public market-data adapter. No authenticated exchange endpoints are used."""

from __future__ import annotations

import ccxt

from .data_integrity import validate_ohlcv_candles


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
        try:
            return validate_ohlcv_candles(rows, timeframe)
        except ValueError as exc:
            raise RuntimeError(f"Market data rejected for {symbol}: {exc}") from exc

    def fetch_last_price(self, symbol: str) -> float:
        ticker = self.exchange.fetch_ticker(symbol)
        price = ticker.get("last")
        if price is None or float(price) <= 0:
            raise RuntimeError(f"Invalid last price received for {symbol}.")
        return float(price)

    def close(self) -> None:
        self.exchange.close()
