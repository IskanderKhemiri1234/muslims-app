from iskander_trade.selection import evaluate_long_candidate


def _candles(count, *, start, step, spread, volume=10.0):
    rows = []
    for index in range(count):
        close = start + index * step
        rows.append([
            index * 900_000, close - spread * 0.2, close + spread * 0.5,
            close - spread, close, volume,
        ])
    return rows


def _candidate_inputs():
    fifteen = _candles(82, start=100.0, step=0.1, spread=0.75)
    fifteen[-2][5] = 12.0
    hourly = _candles(72, start=90.0, step=0.25, spread=1.0)
    return fifteen, hourly


def _evaluate(fifteen, hourly, **overrides):
    values = {
        "entry_price": fifteen[-2][4],
        "stop_loss_pct": 2.0,
        "take_profit_pct": 4.0,
        "taker_fee_rate": 0.001,
        "slippage_rate": 0.0005,
    }
    values.update(overrides)
    return evaluate_long_candidate(fifteen, hourly, **values)


def test_accepts_aligned_uptrend_with_cost_aware_confirmation():
    fifteen, hourly = _candidate_inputs()
    result = _evaluate(fifteen, hourly)
    assert result.accepted
    assert result.regime == "trend_up"
    assert result.reasons == ("all_selection_gates_passed",)
    assert result.metrics["net_reward_risk"] > 1.2


def test_rejects_range_market():
    fifteen, hourly = _candidate_inputs()
    for row in fifteen[:-1]:
        row[1] = 99.25
        row[2] = 100.75
        row[3] = 99.25
        row[4] = 100.0
    result = _evaluate(fifteen, hourly, entry_price=100.0)
    assert not result.accepted
    assert result.regime == "range"
    assert "regime_rejected:range" in result.reasons


def test_rejects_chasing_price_far_above_entry_area():
    fifteen, hourly = _candidate_inputs()
    result = _evaluate(fifteen, hourly, entry_price=150.0)
    assert not result.accepted
    assert "entry_too_far_from_ema20_atr" in result.reasons


def test_rejects_costs_that_destroy_net_reward_risk():
    fifteen, hourly = _candidate_inputs()
    result = _evaluate(
        fifteen, hourly, taker_fee_rate=0.02, slippage_rate=0.02
    )
    assert not result.accepted
    assert "net_reward_risk_insufficient_after_costs" in result.reasons


def test_rejects_unconfirmed_higher_timeframe():
    fifteen, hourly = _candidate_inputs()
    bearish = _candles(72, start=120.0, step=-0.2, spread=1.0)
    result = _evaluate(fifteen, bearish)
    assert not result.accepted
    assert "higher_timeframe_trend_not_confirmed" in result.reasons
