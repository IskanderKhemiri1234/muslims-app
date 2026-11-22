from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

sys.modules.setdefault("ccxt", types.ModuleType("ccxt"))

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
ENGINE_SPEC = importlib.util.spec_from_file_location(
    "binance_research_engine_test", ROOT / "scripts" / "regime_filter_research_engine.py"
)
engine = importlib.util.module_from_spec(ENGINE_SPEC)
assert ENGINE_SPEC and ENGINE_SPEC.loader
ENGINE_SPEC.loader.exec_module(engine)

DATA_SPEC = importlib.util.spec_from_file_location(
    "binance_vision_data_test", ROOT / "scripts" / "binance_vision_data.py"
)
vision = importlib.util.module_from_spec(DATA_SPEC)
assert DATA_SPEC and DATA_SPEC.loader
DATA_SPEC.loader.exec_module(vision)


def _momentum_fixture():
    base = 1_600_000_000_000
    days = 232
    rates = {"BTC/USDT": 0.04, "ETH/USDT": 0.08, "BNB/USDT": 0.12}
    data = {}
    for symbol, rate in rates.items():
        daily = []
        four_hour = []
        for day in range(days):
            daily_close = 100.0 + day * rate
            daily.append([
                base + day * engine.ONE_DAY, daily_close, daily_close + 1,
                daily_close - 1, daily_close, 1000,
            ])
            for slot in range(6):
                ts = base + day * engine.ONE_DAY + slot * engine.FOUR_HOURS
                close = daily_close + slot * rate / 6
                four_hour.append([ts, close, close + 0.5, close - 0.5, close, 100])
        data[symbol] = {"4h": four_hour, "1d": daily}
    return data, base


def test_binance_archive_timestamp_microseconds_are_normalized():
    import io
    import zipfile

    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr(
            "BTCUSDT-4h-2025-01.csv",
            "1735689600000000,100,101,99,100,10,1735703999999999,1000,10,5,500,0\n",
        )
    rows = vision._parse_zip(payload.getvalue(), "4h", "fixture.zip")
    assert rows[0][0] == 1_735_689_600_000


def test_missing_candles_are_counted_not_filled():
    rows = [
        [0, 1, 1, 1, 1, 1],
        [engine.FOUR_HOURS, 1, 1, 1, 1, 1],
        [3 * engine.FOUR_HOURS, 1, 1, 1, 1, 1],
    ]
    assert vision.gap_count(rows, engine.FOUR_HOURS) == 1
    assert len(rows) == 3


def test_daily_btc_filter_uses_only_last_completed_daily_candle():
    timestamps = [i * engine.ONE_DAY for i in range(205)]
    closes = [100.0] * 199 + [101.0] * 6
    averages = engine.sma(closes, 200)
    # At day 200 + 4h, the day-200 candle is still in progress.
    evaluation_time = 200 * engine.ONE_DAY + engine.FOUR_HOURS
    before = engine.daily_regime(timestamps, closes, averages, evaluation_time)
    mutated_future_closes = list(closes)
    mutated_future_closes[200:] = [10_000.0] * 5
    after = engine.daily_regime(timestamps, mutated_future_closes, averages, evaluation_time)
    assert before is True
    assert after is before


def test_momentum_ranking_has_no_future_bar_leakage():
    data, base = _momentum_fixture()
    start = base + 220 * engine.ONE_DAY
    end = base + 223 * engine.ONE_DAY
    prepared = engine.indicators(data)
    original = engine.simulate(
        prepared, "momentum", 0.005, 0.10, start, end,
        btc_filter=False, liquidate_at_end=False,
    )

    changed_future = {
        symbol: {
            "4h": [list(row) for row in series["4h"]],
            "1d": [list(row) for row in series["1d"]],
        }
        for symbol, series in data.items()
    }
    for series in changed_future.values():
        for row in series["4h"]:
            if int(row[0]) > end:
                row[1] *= 100
                row[2] *= 100
                row[3] *= 100
                row[4] *= 100
        for row in series["1d"]:
            if int(row[0]) > end:
                row[1] *= 100
                row[2] *= 100
                row[3] *= 100
                row[4] *= 100
    altered = engine.simulate(
        engine.indicators(changed_future), "momentum", 0.005, 0.10, start, end,
        btc_filter=False, liquidate_at_end=False,
    )
    assert original["open_positions"]
    assert original["trades"] == altered["trades"]
    assert original["open_positions"] == altered["open_positions"]


def test_buy_and_hold_entry_costs_are_charged_once():
    import importlib.util

    runner_spec = importlib.util.spec_from_file_location(
        "binance_runner_test", ROOT / "scripts" / "run_binance_2018_buyhold_research.py"
    )
    runner = importlib.util.module_from_spec(runner_spec)
    assert runner_spec and runner_spec.loader
    runner_spec.loader.exec_module(runner)
    start = 0
    end = 2 * engine.FOUR_HOURS
    rows = [
        [0, 100, 101, 99, 100, 100],
        [engine.FOUR_HOURS, 100, 101, 99, 101, 100],
        [2 * engine.FOUR_HOURS, 101, 102, 100, 102, 100],
    ]
    data = {symbol: {"4h": rows, "1d": []} for symbol in vision.SYMBOLS}
    result = runner.buy_and_hold(data, start, end, 0.10, btc_only=True)
    assert len(result["entries"]) == 1
    assert result["entries"]["BTC/USDT"]["commission_usdt"] > 0
    assert result["entry_cost_model"]["no_exit_cost_charged"] is True
