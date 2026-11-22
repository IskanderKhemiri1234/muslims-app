from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_trend_research.py"
_SPEC = importlib.util.spec_from_file_location("trend_research", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)

ONE_DAY = _MODULE.ONE_DAY
atr_values = _MODULE.atr_values
daily_regime = _MODULE.daily_regime
metrics = _MODULE.metrics


def _daily(count: int = 200) -> list[list[float]]:
    return [
        [float(i * ONE_DAY), float(100 + i), float(102 + i),
         float(99 + i), float(101 + i), 1000.0]
        for i in range(count)
    ]


def test_daily_sma200_uses_only_completed_daily_candles():
    rows = _daily()
    final_open = int(rows[-1][0])

    assert not daily_regime(rows, final_open + ONE_DAY - 1)
    assert daily_regime(rows, final_open + ONE_DAY)


def test_daily_regime_rejects_price_below_sma200():
    rows = _daily()
    rows[-1][4] = 1.0

    assert not daily_regime(rows, int(rows[-1][0]) + ONE_DAY)


def test_atr_is_unavailable_until_full_lookback_then_positive():
    rows = _daily(20)
    values = atr_values(rows, period=14)

    assert all(value != value for value in values[:13])
    assert all(value > 0 for value in values[13:])


def test_metrics_reports_net_expectancy_and_trade_count():
    result = metrics(
        [{"net_pnl_usdt": 2.0}, {"net_pnl_usdt": -1.0}, {"net_pnl_usdt": 1.0}],
        3.0,
    )

    assert result["trades"] == 3
    assert result["expectancy_usdt_per_trade"] == 2.0 / 3.0
    assert result["net_pnl_usdt"] == 2.0
    assert result["max_drawdown_usdt"] == 3.0
