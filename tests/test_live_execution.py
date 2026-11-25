from decimal import Decimal

import pytest

from iskander_trade.live_execution import (
    BinanceSpotExecution,
    ExecutionDisabled,
    ExecutionSettings,
    OrderValidationError,
    _decimal,
)


def test_decimal_rejects_non_positive_and_non_finite_values():
    for value in ("0", "-1", "NaN", "Infinity"):
        with pytest.raises(OrderValidationError):
            _decimal(value, "amount")


def test_execution_requires_server_side_credentials(monkeypatch):
    monkeypatch.delenv("BINANCE_API_KEY", raising=False)
    monkeypatch.delenv("BINANCE_API_SECRET", raising=False)
    monkeypatch.setenv("BINANCE_TESTNET", "true")
    monkeypatch.setenv("ENABLE_LIVE_TRADING", "false")
    with pytest.raises(ExecutionDisabled, match="credentials"):
        BinanceSpotExecution()


def test_live_and_testnet_flags_cannot_be_enabled_together():
    settings = ExecutionSettings(
        api_key="test-key",
        api_secret="test-secret",
        testnet=True,
        live_enabled=True,
        max_order_quote=Decimal(10),
        allowed_symbols=frozenset({"BTC/USDT"}),
    )
    with pytest.raises(ExecutionDisabled, match="Conflicting flags"):
        BinanceSpotExecution(settings)


def test_live_mode_fails_closed_without_explicit_enable_flag():
    settings = ExecutionSettings(
        api_key="test-key",
        api_secret="test-secret",
        testnet=False,
        live_enabled=False,
        max_order_quote=Decimal(10),
        allowed_symbols=frozenset({"BTC/USDT"}),
    )
    with pytest.raises(ExecutionDisabled, match="not available in this Testnet release"):
        BinanceSpotExecution(settings)


def test_production_live_mode_is_unavailable_even_when_flag_is_enabled():
    settings = ExecutionSettings(
        api_key="test-key",
        api_secret="test-secret",
        testnet=False,
        live_enabled=True,
        max_order_quote=Decimal(10),
        allowed_symbols=frozenset({"BTC/USDT"}),
    )
    with pytest.raises(ExecutionDisabled, match="not available in this Testnet release"):
        BinanceSpotExecution(settings)
