from __future__ import annotations

import pytest

from iskander_trade.backtest import BacktestConfig, compare_strategies, run_ema_backtest


def _rows(closes: list[float], duration_ms: int) -> list[list[float]]:
    return [
        [
            index * duration_ms,
            close,
            close + 0.1,
            close - 0.1,
            close,
            100.0,
        ]
        for index, close in enumerate(closes)
    ]


def _datasets() -> dict[str, dict[str, list[list[float]]]]:
    closes = [10, 9, 8, 9, 10, 11, 12, 11, 10, 9, 8, 9, 10, 11]
    return {
        "BTC/USDT": {
            "15m": _rows(closes, 900_000),
            "1h": _rows([10, 10.5, 11, 11.5], 3_600_000),
        }
    }


def _config() -> BacktestConfig:
    return BacktestConfig(
        fast_period=2,
        slow_period=3,
        stop_loss_pct=30.0,
        take_profit_pct=50.0,
        risk_per_trade_usdt=0.35,
        assumed_spread_pct=0.05,
        max_spread_pct=0.15,
    )


def test_baseline_ema_uses_next_open_and_deducts_all_costs():
    result = run_ema_backtest(_datasets(), screened=False, config=_config())
    summary = result["summary"]

    assert summary["trades"] == 2
    assert result["trades"][0]["opened_at"] == 5 * 900_000
    assert result["trades"][0]["closed_at"] == 9 * 900_000
    assert summary["fees_usdt"] > 0
    assert summary["slippage_cost_usdt"] > 0
    assert summary["assumed_spread_cost_usdt"] > 0
    assert "win_rate_pct" in summary
    assert "average_win_usdt" in summary
    assert "average_loss_usdt" in summary
    assert "expectancy_usdt_per_trade" in summary
    assert "max_drawdown_usdt" in summary


def test_screened_strategy_counts_rejected_candidates_not_reason_codes():
    result = run_ema_backtest(_datasets(), screened=True, config=_config())
    summary = result["summary"]

    assert summary["trades"] == 0
    assert summary["rejected_candidates"] == 2
    assert summary["rejected_candidate_reasons"]["insufficient_multi_timeframe_history"] == 2


def test_evaluation_window_uses_pre_window_signal_as_warmup_only():
    result = run_ema_backtest(
        _datasets(),
        screened=False,
        config=_config(),
        evaluation_start_ms=5 * 900_000,
        evaluation_end_ms=9 * 900_000,
    )

    assert result["summary"]["trades"] == 1
    assert result["trades"][0]["opened_at"] == 5 * 900_000
    assert result["trades"][0]["closed_at"] == 9 * 900_000


def test_backtest_rejects_duplicate_historical_timestamps():
    datasets = _datasets()
    datasets["BTC/USDT"]["15m"][2][0] = datasets["BTC/USDT"]["15m"][1][0]

    with pytest.raises(ValueError, match="duplicate/out-of-order"):
        run_ema_backtest(datasets, screened=False, config=_config())


def test_compare_runs_baseline_and_screened_on_identical_inputs():
    result = compare_strategies(_datasets(), config=_config())

    assert result["baseline"]["mode"] == "ema_baseline"
    assert result["screened"]["mode"] == "ema_with_selection"
    assert result["methodology"]["entry"] == "next 15m candle open"



def test_shared_daily_loss_guard_blocks_new_entries_after_realized_limit():
    config = BacktestConfig(
        fast_period=2,
        slow_period=3,
        stop_loss_pct=30.0,
        take_profit_pct=50.0,
        risk_per_trade_usdt=0.35,
        max_daily_loss_usdt=0.01,
        assumed_spread_pct=0.05,
        max_spread_pct=0.15,
    )
    result = run_ema_backtest(_datasets(), screened=False, config=config)

    assert result["summary"]["trades"] == 1
    assert result["summary"]["daily_loss_rejections"] == 1
