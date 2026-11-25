from decimal import Decimal

import pytest

from iskander_trade.trade_journal import TradeJournal


def test_trade_settings_and_emergency_stop_survive_restart(tmp_path):
    path = tmp_path / "journal.sqlite3"
    journal = TradeJournal(path)
    trade = journal.create_trade(
        symbol="BTC/USDT",
        quantity=Decimal("0.001"),
        entry_price="60000",
        stop_loss_price="58000",
        take_profit_price="65000",
    )
    journal.set_trade_enabled(trade["trade_id"], True)
    journal.set_emergency_stop(False, "test")
    restored = TradeJournal(path)
    assert restored.get_trade(trade["trade_id"])["enabled"] == 1
    assert restored.emergency_stopped() is False


def test_order_intent_is_durable_and_pending_after_restart(tmp_path):
    path = tmp_path / "journal.sqlite3"
    journal = TradeJournal(path)
    trade = journal.create_trade(
        symbol="ETH/USDT", quantity="0.02", entry_price="3000",
        stop_loss_price="2850", take_profit_price="3300",
    )
    client_id = journal.create_order_intent(
        trade_id=trade["trade_id"], symbol="ETH/USDT", side="buy",
        amount="0.02", price="3000",
    )
    restored = TradeJournal(path)
    pending = restored.pending_orders()
    assert len(pending) == 1
    assert pending[0]["client_order_id"] == client_id
    assert pending[0]["status"] == "intent"


def test_trade_risk_prices_must_bracket_long_entry(tmp_path):
    journal = TradeJournal(tmp_path / "journal.sqlite3")
    with pytest.raises(ValueError):
        journal.create_trade(
            symbol="BTC/USDT", quantity="0.01", entry_price="100",
            stop_loss_price="101", take_profit_price="110",
        )


def test_invalid_quantities_are_rejected(tmp_path):
    journal = TradeJournal(tmp_path / "journal.sqlite3")
    with pytest.raises(ValueError):
        journal.create_trade(
            symbol="BTC/USDT", quantity="NaN", entry_price="100",
            stop_loss_price="90", take_profit_price="110",
        )
