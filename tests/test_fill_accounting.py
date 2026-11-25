from decimal import Decimal

import pytest

from iskander_trade.fill_accounting import AccountingError, replay_spot_fills


def test_buy_base_fee_reduces_inventory_without_losing_quote_cost():
    ledger = replay_spot_fills("BTC/USDT", [{
        "id": "b1", "side": "buy", "amount": "0.01", "price": "60000",
        "fee_cost": "0.00001", "fee_currency": "BTC", "timestamp": "1",
    }])
    assert ledger.base_quantity == Decimal("0.00999")
    assert ledger.quote_cost == Decimal("600")
    assert ledger.average_entry == Decimal("600") / Decimal("0.00999")


def test_buy_quote_fee_is_added_to_cost_basis():
    ledger = replay_spot_fills("ETH/USDT", [{
        "id": "b1", "side": "buy", "amount": "0.1", "price": "3000",
        "fee_cost": "0.3", "fee_currency": "USDT", "timestamp": "1",
    }])
    assert ledger.base_quantity == Decimal("0.1")
    assert ledger.quote_cost == Decimal("300.3")


def test_sell_base_fee_is_included_in_inventory_consumption():
    ledger = replay_spot_fills("BTC/USDT", [
        {"id": "b1", "side": "buy", "amount": "0.01", "price": "60000",
         "fee_cost": "0", "fee_currency": "", "timestamp": "1"},
        {"id": "s1", "side": "sell", "amount": "0.005", "price": "65000",
         "fee_cost": "0.00001", "fee_currency": "BTC", "timestamp": "2"},
    ])
    assert ledger.base_quantity == Decimal("0.00499")
    assert ledger.realized_quote_pnl == Decimal("325") - Decimal("300.3")


def test_sell_quote_fee_reduces_realized_proceeds():
    ledger = replay_spot_fills("BTC/USDT", [
        {"id": "b1", "side": "buy", "amount": "0.01", "price": "60000",
         "fee_cost": "0", "fee_currency": "", "timestamp": "1"},
        {"id": "s1", "side": "sell", "amount": "0.01", "price": "65000",
         "fee_cost": "0.65", "fee_currency": "USDT", "timestamp": "2"},
    ])
    assert ledger.base_quantity == 0
    assert ledger.realized_quote_pnl == Decimal("49.35")


def test_third_asset_fee_is_reported_without_fake_fx_conversion():
    ledger = replay_spot_fills("BTC/USDT", [{
        "id": "b1", "side": "buy", "amount": "0.01", "price": "60000",
        "fee_cost": "0.02", "fee_currency": "BNB", "timestamp": "1",
    }])
    assert ledger.base_quantity == Decimal("0.01")
    assert ledger.quote_cost == Decimal("600")
    assert ledger.fees_by_asset == {"BNB": Decimal("0.02")}


def test_duplicate_exchange_fill_is_not_double_counted():
    fill = {"id": "same", "side": "buy", "amount": "1", "price": "10",
            "fee_cost": "0", "fee_currency": "", "timestamp": "1"}
    ledger = replay_spot_fills("ABC/USDT", [fill, fill])
    assert ledger.base_quantity == Decimal("1")


def test_sell_cannot_make_spot_inventory_negative():
    with pytest.raises(AccountingError, match="available inventory"):
        replay_spot_fills("BTC/USDT", [{
            "id": "s1", "side": "sell", "amount": "1", "price": "10",
            "fee_cost": "0", "fee_currency": "", "timestamp": "1",
        }])
