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
