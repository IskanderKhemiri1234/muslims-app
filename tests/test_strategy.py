from iskander_trade.strategy import ema_crossover


def _candles(closes):
    return [[index * 60_000, value, value, value, value, 1.0]
            for index, value in enumerate(closes)]


def test_ema_crossover_uses_only_closed_candles():
    candles = _candles([5, 4, 3, 2, 3, 4, 100])
    signal = ema_crossover("BTC/USDT", candles, fast_period=2, slow_period=3)
    assert signal is not None
    assert signal.action == "buy"
    assert signal.candle_timestamp == 5 * 60_000


def test_strategy_returns_no_signal_when_history_is_too_short():
    assert ema_crossover("BTC/USDT", _candles([1, 2, 3]), 2, 3) is None
