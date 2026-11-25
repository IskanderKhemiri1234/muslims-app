"""Testnet-only trade lifecycle coordinator.

No real-money mode is exposed here. Orders are created only when the Binance
adapter confirms Testnet mode and the durable emergency stop is released.
"""
from __future__ import annotations

import logging
import os
import threading
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

import ccxt

from .live_execution import BinanceSpotExecution, ExecutionDisabled
from .trade_journal import TradeJournal

log = logging.getLogger(__name__)


class TestnetTradeService:
    def __init__(self, journal_path: str | Path | None = None) -> None:
        self.journal = TradeJournal(
            journal_path or os.getenv("TRADE_JOURNAL_PATH", "data/trades.sqlite3")
        )
        self._cycle_lock = threading.Lock()

    @staticmethod
    def _execution() -> BinanceSpotExecution:
        adapter = BinanceSpotExecution()
        if not adapter.settings.testnet or adapter.settings.live_enabled:
            adapter.close()
            raise ExecutionDisabled("Trade lifecycle service is restricted to Binance Spot Testnet.")
        return adapter

    def create_trade(self, **values: Any) -> dict[str, Any]:
        return self.journal.create_trade(**values)

    def set_enabled(self, trade_id: str, enabled: bool) -> dict[str, Any]:
        if enabled and self.journal.emergency_stopped():
            raise RuntimeError("Emergency stop is active. Release it before arming a Testnet trade.")
        self.journal.set_trade_enabled(trade_id, enabled)
        return self.journal.get_trade(trade_id)

    def emergency_stop(self, reason: str = "operator") -> dict[str, Any]:
        self.journal.set_emergency_stop(True, reason[:160])
        return self.journal.snapshot()

    def release_emergency_stop(self) -> dict[str, Any]:
        # Releasing the latch does not arm any trade.
        self.journal.set_emergency_stop(False, "explicit dashboard release")
        return self.journal.snapshot()

    def snapshot(self) -> dict[str, Any]:
        return self.journal.snapshot()

    def preflight(self) -> dict[str, Any]:
        """Verify authenticated Spot Testnet connectivity without placing an order."""
        adapter = self._execution()
        try:
            balances = adapter.fetch_balance()
            assets = {
                asset: {
                    "free": str(values.get("free", 0)),
                    "used": str(values.get("used", 0)),
                    "total": str(values.get("total", 0)),
                }
                for asset, values in balances.items()
                if isinstance(values, dict)
                and any(values.get(field) is not None for field in ("free", "used", "total"))
                and Decimal(str(values.get("total") or "0")) > 0
            }
            prices = {
                symbol: str(adapter.fetch_last_price(symbol))
                for symbol in sorted(adapter.settings.allowed_symbols)
            }
            return {
                "status": "connected",
                "mode": "binance_spot_testnet",
                "orders_submitted": False,
                "allowed_symbols": sorted(adapter.settings.allowed_symbols),
                "prices": prices,
                "balances": assets,
                "emergency_stop": self.journal.emergency_stopped(),
            }
        finally:
            adapter.close()

    def cycle(self) -> dict[str, Any]:
        if not self._cycle_lock.acquire(blocking=False):
            raise RuntimeError("A Testnet monitor cycle is already running.")
        owner_id = uuid.uuid4().hex
        lease_acquired = False
        try:
            lease_acquired = self.journal.acquire_cycle_lease(owner_id)
            if not lease_acquired:
                raise RuntimeError(
                    "Another Testnet monitor process owns the durable cycle lease."
                )
            return self._run_cycle()
        finally:
            if lease_acquired:
                self.journal.release_cycle_lease(owner_id)
            self._cycle_lock.release()

    def _run_cycle(self) -> dict[str, Any]:
        stopped = self.journal.emergency_stopped()
        adapter = self._execution()
        actions: list[dict[str, Any]] = []
        try:
            # Reconcile every known order, including terminal/canceled orders,
            # so late fills and commissions are not lost after an app/server restart.
            for known in self.journal.recent_orders():
                try:
                    order = adapter.fetch_order_by_client_id(
                        known["client_order_id"], known["symbol"]
                    )
                    self.journal.update_order(known["client_order_id"], order)
                    exchange_order_id = str(order.get("id") or known.get("exchange_order_id") or "")
                    if exchange_order_id:
                        fills = adapter.fetch_my_trades_for_order(
                            known["symbol"], exchange_order_id
                        )
                        self.journal.record_fills(known["client_order_id"], fills)
                        if (
                            order.get("status") in {"closed", "canceled", "rejected", "expired"}
                            and self.journal.order_fills_complete(known["client_order_id"])
                        ):
                            self.journal.mark_fills_synced(known["client_order_id"])
                except ccxt.BaseError as exc:
                    # Missing/temporarily unavailable exchange state remains unresolved.
                    # Never blindly re-submit an intent with an uncertain outcome.
                    log.warning(
                        "Could not reconcile Testnet order %s: %s",
                        known["client_order_id"],
                        type(exc).__name__,
                    )
                    continue

            # Compare every configured market's exchange-open orders with our durable
            # intents. Unknown orders are never canceled automatically; they freeze
            # this cycle so an external/manual order cannot be mistaken for free funds.
            known_ids = {item["client_order_id"] for item in self.journal.recent_orders()}
            unknown_open_orders: list[dict[str, str]] = []
            for symbol in sorted(adapter.settings.allowed_symbols):
                for exchange_order in adapter.fetch_open_orders(symbol):
                    info = exchange_order.get("info") if isinstance(exchange_order.get("info"), dict) else {}
                    client_id = str(
                        exchange_order.get("clientOrderId")
                        or info.get("clientOrderId")
                        or info.get("origClientOrderId")
                        or ""
                    )
                    if client_id not in known_ids:
                        unknown_open_orders.append({
                            "symbol": symbol,
                            "exchange_order_id": str(exchange_order.get("id") or ""),
                            "client_order_id": client_id,
                            "status": str(exchange_order.get("status") or "open"),
                        })
            if unknown_open_orders:
                balances = adapter.fetch_balance()
                normalized = {
                    key: {
                        "free": str(value.get("free", 0)),
                        "used": str(value.get("used", 0)),
                        "total": str(value.get("total", 0)),
                    }
                    for key, value in balances.items()
                    if isinstance(value, dict) and any(
                        value.get(field) is not None for field in ("free", "used", "total")
                    )
                }
                self.journal.save_balance_snapshot(normalized)
                snapshot = self.journal.snapshot()
                return {
                    "status": "halted",
                    "reason": "unknown_exchange_open_orders",
                    "unknown_open_orders": unknown_open_orders,
                    "actions": [],
                    "mode": "binance_spot_testnet",
                    **snapshot,
                }

            state = self.journal.snapshot()
            if state.get("accounting_issues"):
                return {
                    "status": "halted",
                    "reason": "fill_history_unreconciled",
                    "accounting_issues": state["accounting_issues"],
                    "actions": [],
                    "mode": "binance_spot_testnet",
                    **state,
                }
            pending_by_trade: dict[str, list[dict[str, Any]]] = {}
            for item in self.journal.pending_orders():
                pending_by_trade.setdefault(item["trade_id"], []).append(item)
            for trade in state["trades"] if not stopped else ():
                if not trade["enabled"]:
                    continue
                pending = pending_by_trade.get(trade["trade_id"], [])
                if any(item["side"] == "sell" for item in pending):
                    continue
                symbol = trade["symbol"]
                price = adapter.fetch_last_price(symbol)
                position = next(
                    (item for item in state["positions"] if item["trade_id"] == trade["trade_id"]),
                    None,
                )
                if position and Decimal(position["amount"]) > 0:
                    stop = Decimal(trade["stop_loss_price"])
                    target = Decimal(trade["take_profit_price"])
                    if price <= stop or price >= target:
                        # Cancel any unfilled entry remainder before exiting the
                        # partial position, otherwise a late buy could reopen risk.
                        for pending_buy in pending:
                            if pending_buy["side"] == "buy":
                                adapter.cancel_order_by_client_id(
                                    pending_buy["client_order_id"], symbol
                                )
                                # A cancel acknowledgement alone is not enough: fetch
                                # final state again to include fills racing with cancel.
                                canceled = adapter.fetch_order_by_client_id(
                                    pending_buy["client_order_id"], symbol
                                )
                                if canceled.get("status") not in {
                                    "canceled", "closed", "rejected", "expired"
                                }:
                                    raise RuntimeError(
                                        "Entry cancellation is unconfirmed; exit is blocked "
                                        "until Binance reports a terminal order state."
                                    )
                                self.journal.update_order(
                                    pending_buy["client_order_id"], canceled
                                )
                                canceled_id = str(
                                    canceled.get("id") or pending_buy.get("exchange_order_id") or ""
                                )
                                if canceled_id:
                                    late_fills = adapter.fetch_my_trades_for_order(
                                        symbol, canceled_id
                                    )
                                    self.journal.record_fills(
                                        pending_buy["client_order_id"], late_fills
                                    )
                                    if not self.journal.order_fills_complete(
                                        pending_buy["client_order_id"]
                                    ):
                                        raise RuntimeError(
                                            "Final fill history is incomplete after cancel; "
                                            "exit is blocked until reconciliation completes."
                                        )
                                    self.journal.mark_fills_synced(
                                        pending_buy["client_order_id"]
                                    )
                        # Spot sells are capped by both the journaled position and
                        # Binance's currently free base-asset balance; never short.
                        balances_now = adapter.fetch_balance()
                        base_asset = symbol.split("/", maxsplit=1)[0]
                        base_balance = balances_now.get(base_asset) or {}
                        free_base = Decimal(str(base_balance.get("free") or "0"))
                        amount = min(Decimal(position["amount"]), free_base)
                        if amount <= 0:
                            log.warning(
                                "Exit signal for %s but no free %s balance is available.",
                                symbol,
                                base_asset,
                            )
                            continue
                        client_id = self.journal.create_order_intent(
                            trade_id=trade["trade_id"], symbol=symbol, side="sell",
                            amount=amount, price=None, order_type="market",
                        )
                        # Intent is durable before the exchange call.
                        order = adapter.create_market_order(symbol, "sell", amount, client_id)
                        self.journal.update_order(client_id, order)
                        exchange_order_id = str(order.get("id") or "")
                        if exchange_order_id:
                            self.journal.record_fills(
                                client_id,
                                adapter.fetch_my_trades_for_order(symbol, exchange_order_id),
                            )
                        actions.append({
                            "trade_id": trade["trade_id"],
                            "action": "stop_loss" if price <= stop else "take_profit",
                            "client_order_id": client_id,
                        })
                elif not any(item["side"] == "buy" for item in pending) and price <= Decimal(trade["entry_price"]):
                    client_id = self.journal.create_order_intent(
                        trade_id=trade["trade_id"], symbol=symbol, side="buy",
                        amount=trade["quantity"], price=trade["entry_price"],
                    )
                    order = adapter.create_limit_order(
                        symbol, "buy", trade["quantity"], trade["entry_price"], client_id
                    )
                    self.journal.update_order(client_id, order)
                    exchange_order_id = str(order.get("id") or "")
                    if exchange_order_id:
                        self.journal.record_fills(
                            client_id,
                            adapter.fetch_my_trades_for_order(symbol, exchange_order_id),
                        )
                    actions.append({
                        "trade_id": trade["trade_id"],
                        "action": "entry_limit",
                        "client_order_id": client_id,
                    })

            balances = adapter.fetch_balance()
            normalized_balances = {
                key: {
                    "free": str(value.get("free", 0)),
                    "used": str(value.get("used", 0)),
                    "total": str(value.get("total", 0)),
                }
                for key, value in balances.items()
                if isinstance(value, dict) and any(
                    value.get(field) is not None for field in ("free", "used", "total")
                )
            }
            self.journal.save_balance_snapshot(normalized_balances)
            snapshot = self.journal.snapshot()
            return {
                "status": "halted" if stopped else "completed",
                "reason": "emergency_stop" if stopped else None,
                "mode": "binance_spot_testnet",
                "actions": actions,
                "balances": snapshot["balances"],
                **snapshot,
            }
        finally:
            adapter.close()
