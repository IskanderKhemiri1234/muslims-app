from dataclasses import replace
from decimal import Decimal

import iskander_trade.engine as engine_module
from iskander_trade.config import Settings
from iskander_trade.engine import PaperTradingEngine
from iskander_trade.paper_ledger import PaperLedger
from iskander_trade.state import load_state
from iskander_trade.strategy import Signal


class FakeMarketData:
    def __init__(self, price):
        self.price = price

    def fetch_candles(self, symbol, timeframe, limit):
        return [[i, self.price, self.price, self.price, self.price, 1] for i in range(60)]

    def fetch_last_price(self, symbol):
        return self.price


def test_engine_records_multi_cycle_fills_and_restores_ledger(tmp_path, monkeypatch):
    state_path = tmp_path / "paper_state.json"
    settings = replace(Settings(), state_path=state_path, watchlist=("BTC/USDT",))
    monkeypatch.setattr(
        engine_module, "ema_crossover",
        lambda symbol, candles, fast, slow: Signal("buy", symbol, 1000, "test buy", 50),
    )
    state = load_state(state_path)
    first = PaperTradingEngine(settings, FakeMarketData(50), state)
    first.run_cycle()

    after_buy = PaperLedger(state_path.with_suffix(".ledger.sqlite3"), Decimal("70"))
    buy_report = after_buy.reconcile()
    assert buy_report["status"] == "INCOMPLETE"
    assert Decimal(buy_report["cash"]) == Decimal("52.4825")
    assert Decimal(buy_report["fees_paid"]) == Decimal("0.0175")
    assert Decimal(buy_report["open_positions"]["BTC/USDT"]["quantity"]) == Decimal("0.35")

    monkeypatch.setattr(
        engine_module, "ema_crossover",
        lambda symbol, candles, fast, slow: Signal("sell", symbol, 2000, "test sell", 60),
    )
    restored_state = load_state(state_path)
    second = PaperTradingEngine(settings, FakeMarketData(60), restored_state)
    second.run_cycle()

    final_report = after_buy.reconcile()
    assert final_report["status"] == "PASS"
    assert final_report["open_positions"] == {}
    assert Decimal(final_report["cash"]) == Decimal("73.4615")
    assert Decimal(final_report["fees_paid"]) == Decimal("0.0385")
    assert Decimal(final_report["fifo_realized_pnl"]) == Decimal("3.4615")
    assert restored_state["positions"] == {}
    assert restored_state["total_realized_pnl_usdt"] == 3.4615


def test_engine_blocks_legacy_positions_without_matching_ledger(tmp_path):
    state_path = tmp_path / "legacy.json"
    settings = replace(Settings(), state_path=state_path, watchlist=("BTC/USDT",))
    state = load_state(state_path)
    state["positions"]["BTC/USDT"] = {"quantity": 0.1, "entry_price": 50, "opened_at": 1}
    import pytest
    with pytest.raises(RuntimeError, match="Legacy JSON positions"):
        PaperTradingEngine(settings, FakeMarketData(50), state)
