import sqlite3
from decimal import Decimal

import pytest

from iskander_trade.paper_ledger import PaperLedger


def test_fifo_partial_sell_fees_and_equity_survive_restart(tmp_path):
    path = tmp_path / "paper.sqlite3"
    ledger = PaperLedger(path, Decimal(70))
    ledger.record_fill(fill_id="buy-btc", symbol="BTC/USDT", side="BUY",
                       quantity=Decimal("0.1"), reference_price=Decimal(50),
                       fill_price=Decimal(50))
    ledger.record_fill(fill_id="buy-eth", symbol="ETH/USDT", side="BUY",
                       quantity=Decimal(1), reference_price=Decimal(10),
                       fill_price=Decimal(10))
    ledger.record_fill(fill_id="sell-btc-part", symbol="BTC/USDT", side="SELL",
                       quantity=Decimal("0.04"), reference_price=Decimal(60),
                       fill_price=Decimal(60))

    restarted = PaperLedger(path, Decimal(70))
    report = restarted.reconcile({"BTC/USDT": Decimal(55), "ETH/USDT": Decimal(11)})

    assert report["status"] == "PASS"
    assert report["checks"].values() and all(report["checks"].values())
    assert Decimal(report["cash"]) == Decimal("57.3826")
    assert Decimal(report["fees_paid"]) == Decimal("0.0174")
    assert Decimal(report["fifo_realized_pnl"]) == Decimal("0.3956")
    assert Decimal(report["open_positions"]["BTC/USDT"]["quantity"]) == Decimal("0.06")
    assert Decimal(report["open_positions"]["ETH/USDT"]["quantity"]) == Decimal(1)
    assert Decimal(report["market_value"]) == Decimal("14.3")
    assert Decimal(report["equity"]) == Decimal("71.6826")
    assert Decimal(report["unrealized_pnl"]) == Decimal("1.287")


def test_fill_idempotency_does_not_double_charge(tmp_path):
    ledger = PaperLedger(tmp_path / "idempotent.sqlite3", Decimal(70))
    args = {"fill_id": "same-fill", "symbol": "SOL/USDT", "side": "BUY",
            "quantity": Decimal(1), "reference_price": Decimal(20),
            "fill_price": Decimal(20)}
    ledger.record_fill(**args)
    ledger.record_fill(**args)
    portfolio = ledger.portfolio()
    assert portfolio.cash == Decimal("49.980")
    assert portfolio.fees_paid == Decimal("0.02000000")
    assert portfolio.open_positions == 1
    assert ledger.reconcile()["status"] == "INCOMPLETE"


def test_sell_cannot_exceed_inventory_and_does_not_mutate_ledger(tmp_path):
    ledger = PaperLedger(tmp_path / "oversell.sqlite3", Decimal(70))
    with pytest.raises(ValueError, match="exceeds available FIFO inventory"):
        ledger.record_fill(fill_id="oversell", symbol="ADA/USDT", side="SELL",
                           quantity=Decimal(1), reference_price=Decimal(1),
                           fill_price=Decimal(1))
    assert ledger.portfolio().cash == Decimal(70)
    assert ledger.reconcile()["status"] == "PASS"


def test_cash_fees_and_position_corruption_are_detected(tmp_path):
    path = tmp_path / "tampered.sqlite3"
    ledger = PaperLedger(path, Decimal(70))
    ledger.record_fill(fill_id="buy-xrp", symbol="XRP/USDT", side="BUY",
                       quantity=Decimal(10), reference_price=Decimal(1),
                       fill_price=Decimal(1))
    with sqlite3.connect(path) as db:
        db.execute("UPDATE ledger_account SET cash='60' WHERE id=1")
        db.execute("UPDATE ledger_account SET fees_paid='0' WHERE id=1")
        db.execute("UPDATE ledger_positions SET quantity='9' WHERE symbol='XRP/USDT'")
    report = ledger.reconcile({"XRP/USDT": Decimal("1.1")})
    assert report["status"] == "FAIL"
    assert report["checks"]["cash_matches_fills"] is False
    assert report["checks"]["fees_match_fills"] is False
    assert report["checks"]["open_positions_match_fifo"] is False
    assert len(report["issues"]) >= 3


def test_initial_capital_is_fixed_for_existing_ledger(tmp_path):
    path = tmp_path / "capital.sqlite3"
    PaperLedger(path, Decimal(70))
    with pytest.raises(ValueError, match="different initial cash"):
        PaperLedger(path, Decimal(71))
