"""Public market-data adapter. No authenticated exchange endpoints are used."""

from __future__ import annotations

import math

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
        self._ticker_cache = getattr(self, "_ticker_cache", {})
        self._ticker_cache[symbol] = ticker
        price = ticker.get("last")
        if price is None or float(price) <= 0:
            raise RuntimeError(f"Invalid last price received for {symbol}.")
        return float(price)

    def fetch_spread_pct(self, symbol: str) -> float:
        ticker = getattr(self, "_ticker_cache", {}).get(symbol)
        if ticker is None:
            ticker = self.exchange.fetch_ticker(symbol)
        bid, ask = ticker.get("bid"), ticker.get("ask")
        if bid is None or ask is None:
            raise RuntimeError(f"Bid/ask data unavailable for {symbol}.")
        bid, ask = float(bid), float(ask)
        if not math.isfinite(bid) or not math.isfinite(ask) or bid <= 0 or ask < bid:
            raise RuntimeError(f"Invalid bid/ask data for {symbol}.")
        midpoint = (bid + ask) / 2.0
        return (ask - bid) / midpoint * 100.0

    def close(self) -> None:
        self.exchange.close()
