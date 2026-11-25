from decimal import Decimal

from iskander_trade.testnet_trade_service import TestnetTradeService as TradeService


class FakeTestnetAdapter:
    def __init__(self, price):
        self.settings = type("Settings", (), {
            "testnet": True, "live_enabled": False,
            "allowed_symbols": frozenset({"BTC/USDT"}),
        })()
        self.price = Decimal(price)
        self.orders = {}
        self.closed = False
        self.market_sides = []
        self.cancel_status = "canceled"

    def fetch_order_by_client_id(self, client_order_id, symbol):
        return self.orders[client_order_id]

    def cancel_order_by_client_id(self, client_order_id, symbol):
        current = dict(self.orders[client_order_id])
        current["status"] = self.cancel_status
        self.orders[client_order_id] = current
        return current

    def fetch_my_trades_for_order(self, symbol, exchange_order_id):
        order = next(
            (item for item in self.orders.values() if item.get("id") == exchange_order_id),
            None,
        )
        if not order or Decimal(str(order.get("filled") or "0")) <= 0:
            return []
        is_buy = order.get("side") == "buy"
        return [{
            "id": "fill-" + exchange_order_id,
            "amount": str(order["filled"]),
            "price": str(order.get("average") or "60000"),
            "fee": {
                "cost": "0.000004" if is_buy else "0",
                "currency": "BTC" if is_buy else None,
            },
        }]

    def fetch_last_price(self, symbol):
        return self.price

    def create_limit_order(self, symbol, side, amount, price, client_order_id):
        self.market_sides.append(side)
        order = {
            "id": "buy-order", "side": side, "filled": "0.004", "remaining": "0.006",
            "average": "60000", "status": "open",
        }
        self.orders[client_order_id] = order
        return order

    def create_market_order(self, symbol, side, amount, client_order_id):
        self.market_sides.append(side)
        order = {
            "id": "sell-order", "side": side, "filled": str(amount), "remaining": "0",
            "average": str(self.price), "status": "closed",
        }
        self.orders[client_order_id] = order
        return order

    def fetch_open_orders(self, symbol):
        return [dict(order, clientOrderId=client_id) for client_id, order in self.orders.items()
                if order.get("status") in {"open", "partially_filled"}]

    def fetch_balance(self):
        return {
            "BTC": {"free": "0.004", "used": "0", "total": "0.004"},
            "USDT": {"free": "1000", "used": "0", "total": "1000"},
        }

    def close(self):
        self.closed = True


def _armed_service(tmp_path, monkeypatch, price):
    service = TradeService(tmp_path / "testnet.sqlite3")
    trade = service.create_trade(
        symbol="BTC/USDT", quantity="0.01", entry_price="60000",
        stop_loss_price="58000", take_profit_price="65000",
    )
    service.release_emergency_stop()
    service.set_enabled(trade["trade_id"], True)
    adapter = FakeTestnetAdapter(price)
    monkeypatch.setattr(TradeService, "_execution", staticmethod(lambda: adapter))
    return service, trade, adapter


def test_partial_buy_then_stop_loss_sell_survives_service_restart(tmp_path, monkeypatch):
    service, _trade, adapter = _armed_service(tmp_path, monkeypatch, "60000")
    first = service.cycle()
    assert first["actions"][0]["action"] == "entry_limit"
    assert first["positions"][0]["amount"] == "0.003996"

    restarted = TradeService(tmp_path / "testnet.sqlite3")
    adapter.price = Decimal(57900)
    second = restarted.cycle()
    assert second["actions"][0]["action"] == "stop_loss"
    assert Decimal(second["positions"][0]["amount"]) == Decimal(0)
    assert adapter.market_sides == ["buy", "sell"]
    assert second["fills"]
    assert second["balances"]


def test_partial_buy_then_take_profit_sell(tmp_path, monkeypatch):
    service, _trade, adapter = _armed_service(tmp_path, monkeypatch, "60000")
    service.cycle()
    adapter.price = Decimal(65100)
    result = service.cycle()
    assert result["actions"][0]["action"] == "take_profit"
    assert Decimal(result["positions"][0]["amount"]) == 0
    assert result["realizations"]


def test_emergency_stop_blocks_new_entries_but_keeps_balance_sync(tmp_path, monkeypatch):
    service, _trade, adapter = _armed_service(tmp_path, monkeypatch, "59000")
    service.emergency_stop("test emergency")
    result = service.cycle()
    assert result["status"] == "halted"
    assert result["actions"] == []
    assert adapter.market_sides == []
    assert result["balances"]
    assert service.journal.emergency_stopped() is True


def test_testnet_preflight_is_read_only_and_reports_connection(tmp_path, monkeypatch):
    service, _trade, adapter = _armed_service(tmp_path, monkeypatch, "60000")
    result = service.preflight()
    assert result["status"] == "connected"
    assert result["mode"] == "binance_spot_testnet"
    assert result["orders_submitted"] is False
    assert result["prices"] == {"BTC/USDT": "60000"}
    assert adapter.market_sides == []
    assert adapter.closed is True

def test_pending_unfilled_buy_is_not_duplicated_on_next_cycle(tmp_path, monkeypatch):
    service, _trade, adapter = _armed_service(tmp_path, monkeypatch, "60000")

    def create_unfilled_limit(symbol, side, amount, price, client_order_id):
        adapter.market_sides.append(side)
        order = {
            "id": "buy-order-pending", "filled": "0", "remaining": str(amount),
            "average": None, "status": "open",
        }
        adapter.orders[client_order_id] = order
        return order

    adapter.create_limit_order = create_unfilled_limit
    first = service.cycle()
    assert first["actions"][0]["action"] == "entry_limit"

    adapter.price = Decimal(59900)
    second = service.cycle()

    assert second["actions"] == []
    assert adapter.market_sides == ["buy"]
    assert len(second["orders"]) == 1



def test_unconfirmed_entry_cancel_blocks_stop_loss_sell(tmp_path, monkeypatch):
    service, _trade, adapter = _armed_service(tmp_path, monkeypatch, "60000")
    service.cycle()
    adapter.price = Decimal(57900)
    adapter.cancel_status = "open"

    restarted = TradeService(tmp_path / "testnet.sqlite3")
    try:
        restarted.cycle()
    except RuntimeError as exc:
        assert "cancellation is unconfirmed" in str(exc)
    else:
        raise AssertionError("Unconfirmed cancel must block exit order")

    assert adapter.market_sides == ["buy"]


def test_unknown_exchange_open_order_halts_cycle_without_auto_cancel(tmp_path, monkeypatch):
    service, _trade, adapter = _armed_service(tmp_path, monkeypatch, "60000")
    adapter.orders["external-manual-order"] = {
        "id": "external-1", "filled": "0", "remaining": "0.01",
        "average": None, "status": "open",
    }

    result = service.cycle()

    assert result["status"] == "halted"
    assert result["reason"] == "unknown_exchange_open_orders"
    assert result["actions"] == []
    assert result["unknown_open_orders"][0]["client_order_id"] == "external-manual-order"
    assert adapter.market_sides == []
    assert adapter.orders["external-manual-order"]["status"] == "open"


def test_insufficient_free_quote_balance_skips_buy(tmp_path, monkeypatch):
    service, _trade, adapter = _armed_service(tmp_path, monkeypatch, "59000")
    adapter.fetch_balance = lambda: {
        "BTC": {"free": "0", "used": "0", "total": "0"},
        "USDT": {"free": "1", "used": "0", "total": "1"},
    }

    result = service.cycle()

    assert result["actions"] == []
    assert adapter.market_sides == []
    assert result["status"] == "completed"
