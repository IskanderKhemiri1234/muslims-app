from iskander_trade.indicators import ema


def test_ema_returns_same_length_and_expected_initial_value():
    values = [10.0, 11.0, 12.0, 13.0]
    result = ema(values, 3)
    assert len(result) == len(values)
    assert result[0] == 10.0


def test_ema_rejects_insufficient_values():
    try:
        ema([1.0, 2.0], 3)
    except ValueError:
        pass
    else:
        raise AssertionError("Expected ValueError")
