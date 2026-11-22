import pytest

from iskander_trade.market_data import BinanceMarketData


class TickerExchange:
    def __init__(self, ticker):
        self.ticker = ticker

    def fetch_ticker(self, symbol):
        return self.ticker


def test_spread_measurement_uses_cached_bid_ask():
    client = BinanceMarketData.__new__(BinanceMarketData)
    client.exchange = TickerExchange({"bid": 99.95, "ask": 100.05})
    client._ticker_cache = {"BTC/USDT": {"bid": 99.95, "ask": 100.05}}

    assert client.fetch_spread_pct("BTC/USDT") == pytest.approx(0.1)


def test_spread_measurement_fails_closed_when_bid_ask_missing():
    client = BinanceMarketData.__new__(BinanceMarketData)
    client.exchange = TickerExchange({"last": 100.0})
    client._ticker_cache = {}

    with pytest.raises(RuntimeError, match="Bid/ask data unavailable"):
        client.fetch_spread_pct("BTC/USDT")
