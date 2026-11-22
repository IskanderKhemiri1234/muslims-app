from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

sys.modules.setdefault("ccxt", types.ModuleType("ccxt"))

ENGINE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "regime_filter_research_engine.py"
SPEC = importlib.util.spec_from_file_location("regime_filter_engine_test", ENGINE_PATH)
engine = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(engine)


def _synthetic_data():
    base = 1_700_000_000_000
    daily_start = base - 220 * engine.ONE_DAY
    btc_daily = []
    eth_daily = []
    for i in range(220):
        close = 100.0 if i < 200 else 80.0
        btc_daily.append([daily_start + i * engine.ONE_DAY, close, close + 1, close - 1, close, 1000])
        eth_daily.append([daily_start + i * engine.ONE_DAY, 100, 101, 99, 100, 1000])
    btc_4h, eth_4h = [], []
    for i in range(62):
        ts = base + i * engine.FOUR_HOURS
        btc_4h.append([ts, 100, 101, 99, 100, 100])
        if i < 60:
            eth_4h.append([ts, 100, 101, 99, 100, 100])
        elif i == 60:
            eth_4h.append([ts, 100, 121, 99, 120, 100])
        else:
            eth_4h.append([ts, 120, 121, 100, 120, 100])
    return {
        "BTC/USDT": {"4h": btc_4h, "1d": btc_daily},
        "ETH/USDT": {"4h": eth_4h, "1d": eth_daily},
    }, base


def test_only_two_frozen_strategies_are_enabled():
    assert engine.STRATEGIES == ("donchian", "momentum")


def test_missing_candles_are_preserved_and_indicators_reset():
    data, _base = _synthetic_data()
    rows = data["ETH/USDT"]["4h"]
    # Remove a real candle to create a gap; no synthetic row is inserted.
    rows.pop(10)
    prepared = engine.indicators(data)
    idx = prepared["ETH/USDT"]["index"][int(rows[11][0])]
    assert prepared["ETH/USDT"]["ema200"][idx] != prepared["ETH/USDT"]["ema200"][idx]


def test_btc_filter_blocks_new_entry_but_unfiltered_baseline_enters():
    data, base = _synthetic_data()
    prepared = engine.indicators(data)
    start = base + 60 * engine.FOUR_HOURS
    end = base + 62 * engine.FOUR_HOURS
    baseline = engine.simulate(prepared, "donchian", 0.005, 0.10, start, end,
                               btc_filter=False, liquidate_at_end=False)
    filtered = engine.simulate(prepared, "donchian", 0.005, 0.10, start, end,
                               btc_filter=True, liquidate_at_end=False)
    assert baseline["trades"]
    assert baseline["trades"][0]["entry_btc_regime"] == "bear"
    assert not filtered["trades"]
    assert not filtered["open_positions"]


def test_trade_net_pnl_reconciles_gross_minus_all_costs():
    data, base = _synthetic_data()
    prepared = engine.indicators(data)
    start = base + 60 * engine.FOUR_HOURS
    result = engine.simulate(prepared, "donchian", 0.005, 0.10, start,
                             base + 62 * engine.FOUR_HOURS,
                             btc_filter=False, liquidate_at_end=False)
    trade = result["trades"][0]
    assert trade["gross_pnl_usdt"] - trade["total_costs_usdt"] == pytest.approx(
        trade["net_pnl_usdt"], abs=1e-9
    )
    assert trade["fees_usdt"] > 0
    assert trade["entry_execution_cost_usdt"] > 0
    assert trade["exit_execution_cost_usdt"] > 0
