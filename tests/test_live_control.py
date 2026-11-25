from iskander_trade.live_control import LiveControlService


def test_live_latch_defaults_to_stopped_and_persists(tmp_path):
    path = tmp_path / "live.sqlite3"
    first = LiveControlService(path)
    assert first.stopped() is True
    first.set_stopped(False)
    assert LiveControlService(path).stopped() is False
    LiveControlService(path).set_stopped(True)
    assert LiveControlService(path).stopped() is True


def test_live_status_is_disabled_by_default(tmp_path, monkeypatch):
    for name in ("BINANCE_TESTNET", "ENABLE_LIVE_TRADING", "BINANCE_API_KEY",
                 "BINANCE_API_SECRET", "BINANCE_ALLOWED_SYMBOLS"):
        monkeypatch.delenv(name, raising=False)
    status = LiveControlService(tmp_path / "live.sqlite3").status()
    assert status["configured"] is False
    assert status["orders_enabled"] is False
    assert status["emergency_stop"] is True


def test_live_status_requires_explicit_live_flags_and_allowlist(tmp_path, monkeypatch):
    monkeypatch.setenv("BINANCE_TESTNET", "false")
    monkeypatch.setenv("ENABLE_LIVE_TRADING", "true")
    monkeypatch.setenv("BINANCE_API_KEY", "server-key")
    monkeypatch.setenv("BINANCE_API_SECRET", "server-secret")
    monkeypatch.setenv("BINANCE_ALLOWED_SYMBOLS", "BTC/USDT,ETH/USDT")
    service = LiveControlService(tmp_path / "live.sqlite3")
    status = service.status()
    assert status["configured"] is True
    assert status["orders_enabled"] is False
    assert status["emergency_stop"] is True
