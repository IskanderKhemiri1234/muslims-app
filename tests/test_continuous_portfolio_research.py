from __future__ import annotations

import importlib.util
import math
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_continuous_portfolio_research.py"
SPEC = importlib.util.spec_from_file_location("continuous_research", SCRIPT)
research = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(research)


def test_sma_and_ema_are_causal_and_seeded_after_full_window() -> None:
    values = [float(i) for i in range(1, 8)]
    simple = research.sma(values, 3)
    exponential = research.ema(values, 3)
    assert all(math.isnan(value) for value in simple[:2])
    assert simple[2] == pytest.approx(2.0)
    assert exponential[1] != exponential[1]
    assert exponential[2] == pytest.approx(2.0)
    assert exponential[3] > exponential[2]


def test_daily_regime_ignores_uncompleted_daily_candle() -> None:
    start = 1_700_000_000_000
    timestamps = [start + i * research.ONE_DAY for i in range(201)]
    closes = [100.0 + i for i in range(200)] + [1.0]
    averages = research.sma(closes, 200)
    before_last_daily_close = timestamps[-1] + research.ONE_DAY - 1
    after_last_daily_close = timestamps[-1] + research.ONE_DAY
    assert research.daily_regime(
        timestamps, closes, averages, before_last_daily_close
    )
    assert not research.daily_regime(
        timestamps, closes, averages, after_last_daily_close
    )


def test_atr_and_rsi_return_finite_values_after_warmup() -> None:
    rows = []
    for i in range(40):
        close = 100.0 + i
        rows.append([i * research.FOUR_HOURS, close, close + 2, close - 2, close, 10.0])
    atr = research.atr_values(rows)
    rsi = research.rsi_values([row[4] for row in rows])
    assert all(math.isnan(value) for value in atr[:13])
    assert atr[13] == pytest.approx(4.0)
    assert atr[-1] == pytest.approx(4.0)
    assert rsi[14] == pytest.approx(100.0)


def test_trade_diagnostics_include_commission_spread_and_slippage() -> None:
    trade = {
        "symbol": "BTC/USDT",
        "opened_at": 1_700_000_000_000,
        "closed_at": 1_700_259_200_000,
        "exit_reason": "atr_stop",
        "net_pnl_usdt": -0.2,
        "gross_pnl_usdt": 0.1,
        "fees_usdt": 0.02,
        "entry_execution_cost_usdt": 0.01,
        "exit_execution_cost_usdt": 0.01,
        "total_costs_usdt": 0.04,
        "bars_held": 3,
    }
    result = research.diagnostics([trade])
    assert result["total_commission_spread_slippage_costs_usdt"] == pytest.approx(0.04)
    assert result["by_symbol"]["BTC/USDT"]["trades"] == 1
    assert result["by_exit_reason"]["atr_stop"]["expectancy_usdt"] == pytest.approx(-0.2)
