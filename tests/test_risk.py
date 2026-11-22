from iskander_trade.risk import size_position


def _size(**overrides):
    values = {
        "price": 100.0, "capital_usdt": 70.0, "risk_per_trade_usdt": 0.35,
        "stop_loss_pct": 2.0, "max_position_notional_usdt": 20.0,
        "open_positions": 0, "max_open_positions": 3, "daily_pnl_usdt": 0.0,
        "max_daily_loss_usdt": 1.4, "current_exposure_usdt": 0.0,
    }
    values.update(overrides)
    return size_position(**values)


def test_position_size_is_capped_by_risk_and_notional():
    result = _size()
    assert result.allowed
    assert result.notional_usdt == 17.5
    assert result.quantity == 0.175


def test_daily_loss_limit_blocks_new_positions():
    result = _size(daily_pnl_usdt=-1.4)
    assert not result.allowed
    assert "Daily loss" in result.reason


def test_open_position_limit_blocks_new_positions():
    result = _size(open_positions=3)
    assert not result.allowed


def test_position_size_accounts_for_round_trip_costs():
    without_costs = _size()
    with_costs = _size(cost_pct=0.3)
    assert with_costs.allowed
    assert with_costs.notional_usdt < without_costs.notional_usdt
