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
    position = journal.snapshot()["positions"][0]
    assert position["amount"] == "0.08"
    assert Decimal(position["average_entry"]) == Decimal(3010)


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


def test_fill_journal_survives_restart_and_keeps_distinct_commissions(tmp_path):
    path = tmp_path / "journal.sqlite3"
    journal = TradeJournal(path)
    trade = journal.create_trade(
        symbol="BTC/USDT", quantity="0.01", entry_price="60000",
        stop_loss_price="58000", take_profit_price="65000",
    )
    client_id = journal.create_order_intent(
        trade_id=trade["trade_id"], symbol="BTC/USDT", side="buy",
        amount="0.01", price="60000",
    )
    journal.record_fills(client_id, [
        {"id": "t1", "amount": "0.003", "price": "60000",
         "fee": {"cost": "0.000003", "currency": "BTC"}},
        {"id": "t2", "amount": "0.002", "price": "60010",
         "fee": {"cost": "0.000002", "currency": "BTC"}},
    ])
    restored = TradeJournal(path)
    fills = restored.snapshot()["fills"]
    assert {item["fill_key"] for item in fills} == {f"{client_id}:t1", f"{client_id}:t2"}
    assert sum(Decimal(item["fee_cost"]) for item in fills) == Decimal("0.000005")
    assert restored.snapshot()["fee_totals"] == [
        {"currency": "BTC", "total_fee": "0.000005"}
    ]


def test_sell_fill_records_gross_realized_pnl_and_closes_position(tmp_path):
    journal = TradeJournal(tmp_path / "journal.sqlite3")
    trade = journal.create_trade(
        symbol="BTC/USDT", quantity="0.01", entry_price="60000",
        stop_loss_price="58000", take_profit_price="65000",
    )
    buy_id = journal.create_order_intent(
        trade_id=trade["trade_id"], symbol="BTC/USDT", side="buy",
        amount="0.01", price="60000",
    )
    journal.update_order(buy_id, {
        "id": "buy-1", "filled": "0.01", "remaining": "0",
        "average": "60000", "status": "closed",
    })
    sell_id = journal.create_order_intent(
        trade_id=trade["trade_id"], symbol="BTC/USDT", side="sell",
        amount="0.01", price=None, order_type="market",
    )
    journal.update_order(sell_id, {
        "id": "sell-1", "filled": "0.01", "remaining": "0",
        "average": "65000", "status": "closed",
    })
    snapshot = journal.snapshot()
    assert Decimal(snapshot["positions"][0]["amount"]) == 0
    assert Decimal(snapshot["realizations"][0]["gross_pnl"]) == Decimal(50)


def test_sell_fill_cannot_create_short_spot_position(tmp_path):
    from iskander_trade.trade_journal import JournalError

    journal = TradeJournal(tmp_path / "journal.sqlite3")
    trade = journal.create_trade(
        symbol="BTC/USDT", quantity="0.01", entry_price="60000",
        stop_loss_price="58000", take_profit_price="65000",
    )
    sell_id = journal.create_order_intent(
        trade_id=trade["trade_id"], symbol="BTC/USDT", side="sell",
        amount="0.01", price=None, order_type="market",
    )
    with pytest.raises(JournalError, match="position"):
        journal.update_order(sell_id, {
            "id": "sell-early", "filled": "0.01", "remaining": "0",
            "average": "61000", "status": "closed",
        })


def test_monitor_cycle_lease_is_shared_across_journal_instances(tmp_path):
    path = tmp_path / "journal.sqlite3"
    first = TradeJournal(path)
    second = TradeJournal(path)
    assert first.acquire_cycle_lease("process-one") is True
    assert second.acquire_cycle_lease("process-two") is False
    second.release_cycle_lease("process-two")
    assert second.acquire_cycle_lease("process-two") is False
    first.release_cycle_lease("process-one")
    assert second.acquire_cycle_lease("process-two") is True
    second.release_cycle_lease("process-two")


def test_cycle_lease_can_be_renewed_and_excludes_other_owner(tmp_path):
    journal = TradeJournal(tmp_path / "journal.sqlite3")
    assert journal.acquire_cycle_lease("owner-a", ttl_seconds=60) is True
    assert journal.renew_cycle_lease("owner-a", ttl_seconds=60) is True
    assert journal.acquire_cycle_lease("owner-b", ttl_seconds=60) is False
    journal.release_cycle_lease("owner-a")
    assert journal.acquire_cycle_lease("owner-b", ttl_seconds=60) is True
