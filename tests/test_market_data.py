import pytest

import iskander_trade.market_data as market_data_module


class FakeExchange:
    def __init__(self, ticker=None, candles=None):
        self.ticker = ticker or {"last": 10}
        self.candles = candles or [[1, 10, 11, 9, 10, 100]]

    def fetch_ticker(self, symbol):
        return self.ticker

    def fetch_ohlcv(self, symbol, timeframe, limit):
        return self.candles

    def close(self):
        pass


@pytest.mark.parametrize("bad_price", [None, 0, -1, "not-a-price", "NaN", "Infinity"])
def test_last_price_rejects_missing_nonpositive_and_nonfinite_values(monkeypatch, bad_price):
    monkeypatch.setattr(market_data_module.ccxt, "binance",
                        lambda config: FakeExchange({"last": bad_price}))
    client = market_data_module.BinanceMarketData()
    with pytest.raises(ValueError, match="Invalid last price"):
        client.fetch_last_price("BTC/USDT")


def test_last_price_accepts_valid_numeric_string(monkeypatch):
    monkeypatch.setattr(market_data_module.ccxt, "binance",
                        lambda config: FakeExchange({"last": "123.45"}))
    assert market_data_module.BinanceMarketData().fetch_last_price("BTC/USDT") == 123.45


@pytest.mark.parametrize("bad_row", [
    [1, 10, 11, 9, float("nan"), 1],
    [1, 10, float("inf"), 9, 10, 1],
    [1, 10, 8, 9, 10, 1],
    [1, 10, 11, 9, 10, -1],
    [1, 10, 11],
])
def test_ohlcv_rejects_nonfinite_inconsistent_or_malformed_rows(monkeypatch, bad_row):
    monkeypatch.setattr(market_data_module.ccxt, "binance",
                        lambda config: FakeExchange(candles=[bad_row, bad_row, bad_row]))
    with pytest.raises(ValueError):
        market_data_module.BinanceMarketData().fetch_candles("BTC/USDT", "15m", 10)


def test_ohlcv_requires_minimum_history(monkeypatch):
    monkeypatch.setattr(market_data_module.ccxt, "binance",
                        lambda config: FakeExchange(candles=[[1, 10, 11, 9, 10, 1]]))
    with pytest.raises(RuntimeError, match="Insufficient OHLCV"):
        market_data_module.BinanceMarketData().fetch_candles("BTC/USDT", "15m", 10)
