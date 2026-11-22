from datetime import UTC, datetime

import pytest

from iskander_trade.market_data import BinanceMarketData


class FakeExchange:
    def __init__(self, rows):
        self.rows = rows

    def fetch_ohlcv(self, symbol, timeframe, limit):
        return self.rows


def _fresh_rows():
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    latest = now_ms - (now_ms % 900_000)
    start = latest - 3 * 900_000
    return [
        [start + index * 900_000, 100.0, 102.0, 99.0, 101.0, 12.0]
        for index in range(4)
    ]


def test_market_data_adapter_returns_validated_candles():
    rows = _fresh_rows()
    client = BinanceMarketData.__new__(BinanceMarketData)
    client.exchange = FakeExchange(rows)

    assert client.fetch_candles("BTC/USDT", "15m", 180) == rows


def test_market_data_adapter_fails_closed_on_stale_candles():
    rows = _fresh_rows()
    for row in rows:
        row[0] -= 3 * 900_000
    client = BinanceMarketData.__new__(BinanceMarketData)
    client.exchange = FakeExchange(rows)

    with pytest.raises(RuntimeError, match="stale_market_data"):
        client.fetch_candles("BTC/USDT", "15m", 180)
