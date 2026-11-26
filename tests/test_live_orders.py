from iskander_trade.live_orders import LiveManualOrderService, LiveOrderError


def test_manual_live_service_defaults_disarmed_and_stopped(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVE_CONTROL_DB", str(tmp_path / "control.sqlite3"))
    monkeypatch.setenv("LIVE_ORDERS_DB", str(tmp_path / "orders.sqlite3"))
    monkeypatch.delenv("ENABLE_LIVE_TRADING", raising=False)
    monkeypatch.delenv("LIVE_MANUAL_ORDERS_ENABLED", raising=False)
    service = LiveManualOrderService()
    state = service.status()
    assert state["manual_armed"] is False
    assert state["emergency_stop"] is True
    assert state["orders_enabled"] is False


def test_manual_live_arm_requires_both_server_flags_and_released_stop(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVE_CONTROL_DB", str(tmp_path / "control.sqlite3"))
    monkeypatch.setenv("LIVE_ORDERS_DB", str(tmp_path / "orders.sqlite3"))
    monkeypatch.setenv("ENABLE_LIVE_TRADING", "false")
    monkeypatch.setenv("LIVE_MANUAL_ORDERS_ENABLED", "false")
    service = LiveManualOrderService()
    service.control.set_stopped(False)
    try:
        service.arm()
        assert False, "arm should be refused while server flags are disabled"
    except LiveOrderError:
        pass
    monkeypatch.setenv("ENABLE_LIVE_TRADING", "true")
    monkeypatch.setenv("LIVE_MANUAL_ORDERS_ENABLED", "true")
    assert service.arm()["manual_armed"] is True
    assert service.disarm()["manual_armed"] is False


def test_emergency_stop_disarms_and_persists(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVE_CONTROL_DB", str(tmp_path / "control.sqlite3"))
    monkeypatch.setenv("LIVE_ORDERS_DB", str(tmp_path / "orders.sqlite3"))
    service = LiveManualOrderService()
    service.emergency_stop()
    reopened = LiveManualOrderService()
    assert reopened.status()["emergency_stop"] is True
    assert reopened.status()["manual_armed"] is False



def _armed_service(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVE_CONTROL_DB", str(tmp_path / "control-risk.sqlite3"))
    monkeypatch.setenv("LIVE_ORDERS_DB", str(tmp_path / "orders-risk.sqlite3"))
    monkeypatch.setenv("ENABLE_LIVE_TRADING", "true")
    monkeypatch.setenv("LIVE_MANUAL_ORDERS_ENABLED", "true")
    service = LiveManualOrderService()
    service.control.set_stopped(False)
    service.arm()
    return service


def test_position_limit_counts_pending_buy_reservations(tmp_path, monkeypatch):
    from decimal import Decimal

    service = _armed_service(tmp_path, monkeypatch)
    monkeypatch.setenv("MAX_POSITION_QUOTE_USDT", "15")
    with service._connect() as db:
        db.execute(
            """INSERT INTO live_orders(client_order_id,request_id,symbol,side,order_type,
               amount,price,status,created_at,updated_at)
               VALUES('pending-one','request-one','BTC/USDT','buy','limit','0.1','50',
               'open','2026-11-25T00:00:00+00:00','2026-11-25T00:00:00+00:00')"""
        )
    try:
        service._risk_gate("BTC/USDT", "buy", Decimal("0.21"), Decimal(50))
        assert False, "pending buy reservation must count toward position cap"
    except LiveOrderError:
        pass


def test_daily_loss_blocks_buys_but_does_not_block_manual_sell_exit(tmp_path, monkeypatch):
    from decimal import Decimal

    service = _armed_service(tmp_path, monkeypatch)
    monkeypatch.setenv("MAX_DAILY_LOSS_QUOTE", "5")
    with service._connect() as db:
        db.execute(
            """INSERT INTO live_positions(symbol,amount,average_entry,updated_at)
               VALUES('BTC/USDT','0.2','100','2026-11-25T00:00:00+00:00')"""
        )
        db.execute(
            """INSERT INTO live_fills(fill_key,client_order_id,symbol,side,amount,price,
               fee_cost,fee_currency,realized_pnl_quote,occurred_at)
               VALUES('loss-fill','order-loss','BTC/USDT','sell','0.1','40','0','USDT',
               '-6','2026-11-25T12:00:00+00:00')"""
        )
    try:
        service._risk_gate("BTC/USDT", "buy", Decimal("0.01"), Decimal(100))
        assert False, "daily loss gate must block new buys"
    except LiveOrderError:
        pass
    service._risk_gate("BTC/USDT", "sell", Decimal("0.1"), Decimal(100))


def test_repeated_client_request_id_is_not_submitted_again(tmp_path, monkeypatch):
    service = _armed_service(tmp_path, monkeypatch)
    with service._connect() as db:
        db.execute(
            """INSERT INTO live_orders(client_order_id,request_id,symbol,side,order_type,
               amount,price,status,created_at,updated_at)
               VALUES('isk012345678901234567890123456789','same-request','BTC/USDT',
               'buy','limit','0.001','50000','intent','2026-11-25T00:00:00+00:00',
               '2026-11-25T00:00:00+00:00')"""
        )
    result = service.submit(
        request_id="same-request", symbol="BTC/USDT", side="buy",
        order_type="limit", amount="0.001", price="50000",
    )
    assert len(result["orders"]) == 1


def test_daily_loss_gate_includes_adverse_unrealized_position_loss(tmp_path, monkeypatch):
    from decimal import Decimal

    service = _armed_service(tmp_path, monkeypatch)
    monkeypatch.setenv("MAX_DAILY_LOSS_QUOTE", "5")
    with service._connect() as db:
        db.execute(
            """INSERT INTO live_positions(symbol,amount,average_entry,updated_at)
               VALUES('BTC/USDT','0.2','100','2026-11-26T00:00:00+00:00')"""
        )

    class PriceAdapter:
        def fetch_last_price(self, symbol):
            assert symbol == "BTC/USDT"
            return Decimal("70")

    try:
        service._risk_gate("BTC/USDT", "buy", Decimal("0.01"), Decimal("70"), PriceAdapter())
        assert False, "adverse unrealized loss must count toward the daily risk gate"
    except LiveOrderError:
        pass
    # Risk controls must not prevent a manual sell that reduces exposure.
    service._risk_gate("BTC/USDT", "sell", Decimal("0.1"), Decimal("70"), PriceAdapter())


def test_unvalued_commission_blocks_new_buys(tmp_path, monkeypatch):
    from datetime import UTC, datetime
    from decimal import Decimal

    service = _armed_service(tmp_path, monkeypatch)
    with service._connect() as db:
        db.execute(
            """INSERT INTO live_fills(fill_key,client_order_id,symbol,side,amount,price,
               fee_cost,fee_currency,fee_conversion_complete,realized_pnl_quote,occurred_at)
               VALUES('unknown-fee','order-unknown-fee','BTC/USDT','sell','0.01','100',
               '0.02','BNB',0,'0',?)""",
            (datetime.now(UTC).isoformat(),),
        )
    try:
        service._risk_gate("BTC/USDT", "buy", Decimal("0.001"), Decimal("100"))
        assert False, "unvalued commission must block new buys"
    except LiveOrderError:
        pass


def test_live_order_service_refuses_testnet_adapter(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import pytest
    from iskander_trade import live_orders as live_orders_module
    from iskander_trade.live_execution import ExecutionDisabled

    monkeypatch.setenv("LIVE_CONTROL_DB", str(tmp_path / "control-testnet.sqlite3"))
    monkeypatch.setenv("LIVE_ORDERS_DB", str(tmp_path / "orders-testnet.sqlite3"))
    monkeypatch.setenv("ENABLE_LIVE_TRADING", "true")
    monkeypatch.setenv("LIVE_MANUAL_ORDERS_ENABLED", "true")
    service = LiveManualOrderService()
    service.control.set_stopped(False)
    service.arm()

    class TestnetAdapter:
        settings = SimpleNamespace(testnet=True, live_enabled=False)

        def close(self):
            pass

    monkeypatch.setattr(live_orders_module, "BinanceSpotExecution", TestnetAdapter)
    with pytest.raises(ExecutionDisabled):
        service.submit(
            request_id="testnet-request-0001",
            symbol="BTC/USDT",
            side="buy",
            order_type="limit",
            amount="0.001",
            price="50000",
        )
