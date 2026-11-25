"""Guarded Binance Spot execution adapter.

This module is deliberately not wired to HTTP routes yet. It provides a narrow,
validated boundary for the later order-management service. Live execution is
fail-closed unless explicitly enabled in server environment.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

import ccxt


class ExecutionDisabled(RuntimeError):
    """Raised when the configured execution environment is not enabled."""


class OrderValidationError(ValueError):
    """Raised when an order violates local safety checks."""


def _decimal(value: str | float | Decimal, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise OrderValidationError(f"{field} must be a valid decimal.") from exc
    if not result.is_finite() or result <= 0:
        raise OrderValidationError(f"{field} must be greater than zero.")
    return result


@dataclass(frozen=True)
class ExecutionSettings:
    api_key: str
    api_secret: str
    testnet: bool
    live_enabled: bool
    max_order_quote: Decimal
    allowed_symbols: frozenset[str]

    @classmethod
    def from_env(cls) -> ExecutionSettings:
        symbols = frozenset(
            item.strip().upper()
            for item in os.getenv("BINANCE_ALLOWED_SYMBOLS", "").split(",")
            if item.strip()
        )
        maximum = _decimal(os.getenv("MAX_ORDER_QUOTE_USDT", "10"), "MAX_ORDER_QUOTE_USDT")
        return cls(
            api_key=os.getenv("BINANCE_API_KEY", "").strip(),
            api_secret=os.getenv("BINANCE_API_SECRET", "").strip(),
            testnet=os.getenv("BINANCE_TESTNET", "true").strip().lower() in {"1", "true", "yes"},
            live_enabled=os.getenv("ENABLE_LIVE_TRADING", "false").strip().lower()
            in {"1", "true", "yes"},
            max_order_quote=maximum,
            allowed_symbols=symbols,
        )


class BinanceSpotExecution:
    """Small Spot-only order adapter; never handles withdrawals or transfers."""

    def __init__(self, settings: ExecutionSettings | None = None) -> None:
        self.settings = settings or ExecutionSettings.from_env()
        if not self.settings.api_key or not self.settings.api_secret:
            raise ExecutionDisabled("Binance API credentials are not configured on the server.")
        if not self.settings.testnet and not self.settings.live_enabled:
            raise ExecutionDisabled("Live trading is disabled. Testnet is required.")
        if self.settings.testnet and self.settings.live_enabled:
            raise ExecutionDisabled("Conflicting flags: disable live trading while using Testnet.")

        self.exchange = ccxt.binance({
            "apiKey": self.settings.api_key,
            "secret": self.settings.api_secret,
            "enableRateLimit": True,
            "timeout": 15000,
            "options": {"defaultType": "spot", "adjustForTimeDifference": True},
        })
        self.exchange.set_sandbox_mode(self.settings.testnet)
        self.exchange.load_markets()

    def _validate(self, symbol: str, side: str, amount: str | float | Decimal,
                  price: str | float | Decimal) -> tuple[str, Decimal, Decimal]:
        normalized = symbol.strip().upper()
        if normalized not in self.settings.allowed_symbols:
            raise OrderValidationError("Symbol is not in BINANCE_ALLOWED_SYMBOLS.")
        if side not in {"buy", "sell"}:
            raise OrderValidationError("Only Spot buy and sell orders are supported.")
        qty = _decimal(amount, "amount")
        limit_price = _decimal(price, "price")
        if normalized not in self.exchange.markets:
            raise OrderValidationError("Unknown Binance Spot symbol.")
        market = self.exchange.market(normalized)
        if not market.get("spot") or market.get("active") is False:
            raise OrderValidationError("Market is not an active Spot market.")
        notional = qty * limit_price
        if notional > self.settings.max_order_quote:
            raise OrderValidationError("Order exceeds MAX_ORDER_QUOTE_USDT.")
        formatted_amount = Decimal(self.exchange.amount_to_precision(normalized, float(qty)))
        formatted_price = Decimal(self.exchange.price_to_precision(normalized, float(limit_price)))
        if formatted_amount <= 0 or formatted_price <= 0:
            raise OrderValidationError("Order rounds to zero at Binance precision.")
        limits = market.get("limits") or {}
        amount_min = (limits.get("amount") or {}).get("min")
        cost_min = (limits.get("cost") or {}).get("min")
        if amount_min is not None and formatted_amount < Decimal(str(amount_min)):
            raise OrderValidationError("Order amount is below Binance minimum.")
        if cost_min is not None and formatted_amount * formatted_price < Decimal(str(cost_min)):
            raise OrderValidationError("Order notional is below Binance minimum.")
        return normalized, formatted_amount, formatted_price

    def create_limit_order(self, symbol: str, side: str,
                           amount: str | float | Decimal,
                           price: str | float | Decimal,
                           client_order_id: str) -> dict[str, Any]:
        if not client_order_id or len(client_order_id) > 36:
            raise OrderValidationError("A unique client_order_id of 1–36 characters is required.")
        normalized, qty, limit_price = self._validate(symbol, side, amount, price)
        params = {"newClientOrderId": client_order_id}
        return self.exchange.create_order(
            normalized, "limit", side, float(qty), float(limit_price), params
        )

    def fetch_order(self, order_id: str, symbol: str) -> dict[str, Any]:
        normalized = symbol.strip().upper()
        if normalized not in self.settings.allowed_symbols:
            raise OrderValidationError("Symbol is not in BINANCE_ALLOWED_SYMBOLS.")
        return self.exchange.fetch_order(order_id, normalized)

    def create_market_order(self, symbol: str, side: str,
                            amount: str | float | Decimal,
                            client_order_id: str) -> dict[str, Any]:
        if not client_order_id or len(client_order_id) > 36:
            raise OrderValidationError("A unique client_order_id of 1–36 characters is required.")
        normalized = symbol.strip().upper()
        if normalized not in self.settings.allowed_symbols:
            raise OrderValidationError("Symbol is not in BINANCE_ALLOWED_SYMBOLS.")
        if side not in {"buy", "sell"}:
            raise OrderValidationError("Only Spot buy and sell orders are supported.")
        qty = _decimal(amount, "amount")
        if normalized not in self.exchange.markets:
            raise OrderValidationError("Unknown Binance Spot symbol.")
        market = self.exchange.market(normalized)
        if not market.get("spot") or market.get("active") is False:
            raise OrderValidationError("Market is not an active Spot market.")
        formatted = Decimal(self.exchange.amount_to_precision(normalized, float(qty)))
        if formatted <= 0:
            raise OrderValidationError("Order rounds to zero at Binance precision.")
        limits = market.get("limits") or {}
        minimum = (limits.get("amount") or {}).get("min")
        if minimum is not None and formatted < Decimal(str(minimum)):
            raise OrderValidationError("Order amount is below Binance minimum.")
        return self.exchange.create_order(
            normalized, "market", side, float(formatted), None,
            {"newClientOrderId": client_order_id},
        )

    def fetch_order_by_client_id(self, client_order_id: str, symbol: str) -> dict[str, Any]:
        normalized = symbol.strip().upper()
        if normalized not in self.settings.allowed_symbols:
            raise OrderValidationError("Symbol is not in BINANCE_ALLOWED_SYMBOLS.")
        return self.exchange.fetch_order(
            None, normalized, {"origClientOrderId": client_order_id}
        )

    def fetch_my_trades_for_order(
        self, symbol: str, exchange_order_id: str, since: int | None = None
    ) -> list[dict[str, Any]]:
        normalized = symbol.strip().upper()
        if normalized not in self.settings.allowed_symbols:
            raise OrderValidationError("Symbol is not in BINANCE_ALLOWED_SYMBOLS.")
        if not exchange_order_id:
            return []
        return self.exchange.fetch_my_trades(
            normalized, since, 1000, {"orderId": exchange_order_id}
        )

    def fetch_balance(self) -> dict[str, Any]:
        return self.exchange.fetch_balance()

    def fetch_last_price(self, symbol: str) -> Decimal:
        normalized = symbol.strip().upper()
        if normalized not in self.settings.allowed_symbols:
            raise OrderValidationError("Symbol is not in BINANCE_ALLOWED_SYMBOLS.")
        ticker = self.exchange.fetch_ticker(normalized)
        last = ticker.get("last")
        if last is None:
            raise RuntimeError("Binance did not return a current ticker price.")
        return _decimal(last, "ticker.last")

    def close(self) -> None:
        self.exchange.close()
