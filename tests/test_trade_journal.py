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


def test_partial_fill_updates_position_incrementally_without_double_counting(tmp_path):
    journal = TradeJournal(tmp_path / "journal.sqlite3")
    trade = journal.create_trade(
        symbol="BTC/USDT", quantity="0.01", entry_price="60000",
        stop_loss_price="58000", take_profit_price="65000",
    )
    client_id = journal.create_order_intent(
        trade_id=trade["trade_id"], symbol="BTC/USDT", side="buy",
        amount="0.01", price="60000",
    )
    partial = {"id": "exchange-1", "filled": "0.004", "remaining": "0.006",
               "average": "60000", "status": "open"}
    journal.update_order(client_id, partial)
    journal.update_order(client_id, partial)
    position = journal.snapshot()["positions"][0]
    assert position["amount"] == "0.004"
    assert position["average_entry"] == "60000"
    assert journal.pending_orders()[0]["status"] == "partially_filled"


def test_additional_fill_only_applies_the_new_cumulative_delta(tmp_path):
    journal = TradeJournal(tmp_path / "journal.sqlite3")
    trade = journal.create_trade(
        symbol="ETH/USDT", quantity="0.2", entry_price="3000",
        stop_loss_price="2800", take_profit_price="3300",
    )
    client_id = journal.create_order_intent(
        trade_id=trade["trade_id"], symbol="ETH/USDT", side="buy",
        amount="0.2", price="3000",
    )
    journal.update_order(client_id, {
        "id": "exchange-2", "filled": "0.05", "remaining": "0.15",
        "average": "3000", "status": "open",
    })
    journal.update_order(client_id, {
        "id": "exchange-2", "filled": "0.08", "remaining": "0.12",
        "average": "3010", "status": "open",
    })
    assert journal.snapshot()["positions"][0]["amount"] == "0.08"


def test_exchange_fills_and_fees_are_persisted_idempotently(tmp_path):
    journal = TradeJournal(tmp_path / "journal.sqlite3")
    trade = journal.create_trade(
        symbol="BTC/USDT", quantity="0.01", entry_price="60000",
        stop_loss_price="58000", take_profit_price="65000",
    )
    client_id = journal.create_order_intent(
        trade_id=trade["trade_id"], symbol="BTC/USDT", side="buy",
        amount="0.01", price="60000",
    )
    fills = [{
        "id": "trade-101",
        "amount": "0.004",
        "price": "59950",
        "datetime": "2026-11-25T10:00:00Z",
        "fee": {"cost": "0.000004", "currency": "BTC"},
    }]
    assert journal.record_fills(client_id, fills) == 1
    assert journal.record_fills(client_id, fills) == 0
    saved = journal.snapshot()["fills"]
    assert len(saved) == 1
    assert saved[0]["amount"] == "0.004"
    assert saved[0]["price"] == "59950"
    assert saved[0]["fee_cost"] == "0.000004"
    assert saved[0]["fee_currency"] == "BTC"


def test_terminal_canceled_order_is_available_for_restart_reconciliation(tmp_path):
    journal = TradeJournal(tmp_path / "journal.sqlite3")
    trade = journal.create_trade(
        symbol="ETH/USDT", quantity="0.2", entry_price="3000",
        stop_loss_price="2800", take_profit_price="3300",
    )
    client_id = journal.create_order_intent(
        trade_id=trade["trade_id"], symbol="ETH/USDT", side="buy",
        amount="0.2", price="3000",
    )
    journal.update_order(client_id, {
        "id": "exchange-closed-9", "filled": "0", "remaining": "0.2",
        "average": None, "status": "canceled",
    })
    restored = TradeJournal(tmp_path / "journal.sqlite3")
    assert restored.recent_orders()[0]["status"] == "canceled"
    assert restored.pending_orders() == []
