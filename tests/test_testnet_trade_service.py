from decimal import Decimal

from iskander_trade.testnet_trade_service import TestnetTradeService


class FakeTestnetAdapter:
    def __init__(self, price):
        self.settings = type("Settings", (), {"testnet": True, "live_enabled": False})()
        self.price = Decimal(price)
        self.orders = {}
        self.closed = False
        self.market_sides = []

    def fetch_order_by_client_id(self, client_order_id, symbol):
        return self.orders[client_order_id]

    def fetch_my_trades_for_order(self, symbol, exchange_order_id):
        return [{
            "id": "fill-" + exchange_order_id,
            "amount": "0.004",
            "price": "60000",
            "fee": {"cost": "0.000004", "currency": "BTC"},
        }]

    def fetch_last_price(self, symbol):
        return self.price

    def create_limit_order(self, symbol, side, amount, price, client_order_id):
        self.market_sides.append(side)
        order = {
            "id": "buy-order", "filled": "0.004", "remaining": "0.006",
            "average": "60000", "status": "open",
        }
        self.orders[client_order_id] = order
        return order

    def create_market_order(self, symbol, side, amount, client_order_id):
        self.market_sides.append(side)
        order = {
            "id": "sell-order", "filled": str(amount), "remaining": "0",
            "average": str(self.price), "status": "closed",
        }
        self.orders[client_order_id] = order
        return order

    def fetch_balance(self):
        return {
            "BTC": {"free": "0.004", "used": "0", "total": "0.004"},
            "USDT": {"free": "1000", "used": "0", "total": "1000"},
        }

    def close(self):
        self.closed = True


def _armed_service(tmp_path, monkeypatch, price):
    service = TestnetTradeService(tmp_path / "testnet.sqlite3")
    trade = service.create_trade(
        symbol="BTC/USDT", quantity="0.01", entry_price="60000",
        stop_loss_price="58000", take_profit_price="65000",
    )
    service.release_emergency_stop()
    service.set_enabled(trade["trade_id"], True)
    adapter = FakeTestnetAdapter(price)
    monkeypatch.setattr(TestnetTradeService, "_execution", staticmethod(lambda: adapter))
    return service, trade, adapter


def test_partial_buy_then_stop_loss_sell_survives_service_restart(tmp_path, monkeypatch):
    service, trade, adapter = _armed_service(tmp_path, monkeypatch, "60000")
    first = service.cycle()
    assert first["actions"][0]["action"] == "entry_limit"
    assert first["positions"][0]["amount"] == "0.004"

    restarted = TestnetTradeService(tmp_path / "testnet.sqlite3")
    adapter.price = Decimal("57900")
    second = restarted.cycle()
    assert second["actions"][0]["action"] == "stop_loss"
    assert second["positions"][0]["amount"] == "0"
    assert adapter.market_sides == ["buy", "sell"]
    assert second["fills"]
    assert second["balances"]


def test_partial_buy_then_take_profit_sell(tmp_path, monkeypatch):
    service, trade, adapter = _armed_service(tmp_path, monkeypatch, "60000")
    service.cycle()
    adapter.price = Decimal("65100")
    result = service.cycle()
    assert result["actions"][0]["action"] == "take_profit"
    assert result["positions"][0]["amount"] == "0"
    assert result["realizations"]
