import pytest

from iskander_trade.data_integrity import (
    MarketDataIntegrityError,
    validate_ohlcv_candles,
)


def _candles(now_ms, count=4):
    latest = now_ms - (now_ms % 900_000)
    start = latest - (count - 1) * 900_000
    return [
        [start + index * 900_000, 100.0, 102.0, 99.0, 101.0, 12.0]
        for index in range(count)
    ]


def test_accepts_well_formed_fresh_15m_candles():
    now_ms = 1_800_000_000_000
    rows = _candles(now_ms)
    assert validate_ohlcv_candles(rows, "15m", now_ms=now_ms) == rows


def test_rejects_duplicate_timestamps():
    now_ms = 1_800_000_000_000
    rows = _candles(now_ms)
    rows[2][0] = rows[1][0]
    with pytest.raises(MarketDataIntegrityError, match="duplicate_timestamp"):
        validate_ohlcv_candles(rows, "15m", now_ms=now_ms)


def test_rejects_out_of_order_timestamps():
    now_ms = 1_800_000_000_000
    rows = _candles(now_ms)
    rows[2][0], rows[1][0] = rows[1][0], rows[2][0]
    with pytest.raises(MarketDataIntegrityError, match="out_of_order_timestamp"):
        validate_ohlcv_candles(rows, "15m", now_ms=now_ms)


def test_rejects_invalid_ohlc_relationship():
    now_ms = 1_800_000_000_000
    rows = _candles(now_ms)
    rows[1][2] = 99.5
    with pytest.raises(MarketDataIntegrityError, match="invalid_ohlc_relationship"):
        validate_ohlcv_candles(rows, "15m", now_ms=now_ms)


def test_rejects_non_finite_values():
    now_ms = 1_800_000_000_000
    rows = _candles(now_ms)
    rows[1][4] = float("nan")
    with pytest.raises(MarketDataIntegrityError, match="non_finite_candle"):
        validate_ohlcv_candles(rows, "15m", now_ms=now_ms)


def test_rejects_stale_market_data():
    now_ms = 1_800_000_000_000
    rows = _candles(now_ms)
    for row in rows:
        row[0] -= 3 * 900_000
    with pytest.raises(MarketDataIntegrityError, match="stale_market_data"):
        validate_ohlcv_candles(rows, "15m", now_ms=now_ms)


def test_rejects_insufficient_rows_and_unaligned_timestamps():
    now_ms = 1_800_000_000_000
    with pytest.raises(MarketDataIntegrityError, match="insufficient_candles"):
        validate_ohlcv_candles(_candles(now_ms, count=2), "15m", now_ms=now_ms)
    rows = _candles(now_ms)
    rows[1][0] += 1
    with pytest.raises(MarketDataIntegrityError, match="unaligned_timestamp"):
        validate_ohlcv_candles(rows, "15m", now_ms=now_ms)


def test_rejects_future_candles_and_negative_volume():
    now_ms = 1_800_000_000_000
    future_rows = _candles(now_ms)
    future_rows[-1][0] += 900_000
    with pytest.raises(MarketDataIntegrityError, match="future_candle"):
        validate_ohlcv_candles(future_rows, "15m", now_ms=now_ms)

    negative_volume_rows = _candles(now_ms)
    negative_volume_rows[1][5] = -0.1
    with pytest.raises(MarketDataIntegrityError, match="invalid_price_or_volume"):
        validate_ohlcv_candles(negative_volume_rows, "15m", now_ms=now_ms)


def test_rejects_malformed_and_unsupported_timeframe():
    now_ms = 1_800_000_000_000
    malformed_rows = _candles(now_ms)
    malformed_rows[1] = malformed_rows[1][:4]
    with pytest.raises(MarketDataIntegrityError, match="malformed_candle"):
        validate_ohlcv_candles(malformed_rows, "15m", now_ms=now_ms)

    with pytest.raises(MarketDataIntegrityError, match="unsupported_timeframe"):
        validate_ohlcv_candles(_candles(now_ms), "7m", now_ms=now_ms)
