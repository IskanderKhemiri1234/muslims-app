"""Manually submitted Binance Spot Live orders with fail-closed controls."""
from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import ccxt

from .live_control import LiveControlService
from .live_execution import BinanceSpotExecution, ExecutionDisabled


class LiveOrderError(RuntimeError):
    pass


class LiveManualOrderService:
    """Manual-only Spot order service; never runs a strategy or background trader."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path or os.getenv("LIVE_ORDERS_DB", "data/live_orders.sqlite3"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.control = LiveControlService()
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS live_orders(
                    client_order_id TEXT PRIMARY KEY, request_id TEXT UNIQUE, exchange_order_id TEXT,
                    symbol TEXT NOT NULL, side TEXT NOT NULL, order_type TEXT NOT NULL,
                    amount TEXT NOT NULL, price TEXT, filled TEXT NOT NULL DEFAULT '0',
                    average TEXT, status TEXT NOT NULL, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL, raw_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS live_fills(
                    fill_key TEXT PRIMARY KEY, client_order_id TEXT NOT NULL,
                    symbol TEXT NOT NULL, side TEXT NOT NULL, amount TEXT NOT NULL,
                    price TEXT NOT NULL, fee_cost TEXT NOT NULL, fee_currency TEXT,
                    realized_pnl_quote TEXT NOT NULL DEFAULT '0', occurred_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS live_positions(
                    symbol TEXT PRIMARY KEY, amount TEXT NOT NULL,
                    average_entry TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS live_flags(
                    name TEXT PRIMARY KEY, value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS live_balances(
                    asset TEXT PRIMARY KEY, free TEXT NOT NULL, used TEXT NOT NULL,
                    total TEXT NOT NULL, observed_at TEXT NOT NULL
                );
                INSERT OR IGNORE INTO live_flags(name,value) VALUES('manual_armed','0');
            """)
            cols = {row["name"] for row in db.execute("PRAGMA table_info(live_orders)")}
            if "request_id" not in cols:
                db.execute("ALTER TABLE live_orders ADD COLUMN request_id TEXT")
                db.execute("CREATE UNIQUE INDEX IF NOT EXISTS live_orders_request_id_idx ON live_orders(request_id)")

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        return db

    def _truth(self, name: str) -> bool:
        return os.getenv(name, "false").strip().lower() in {"1", "true", "yes"}

    def _armed(self) -> bool:
        with self._connect() as db:
            row = db.execute("SELECT value FROM live_flags WHERE name='manual_armed'").fetchone()
            return bool(row and row["value"] == "1")

    def arm(self) -> dict[str, Any]:
        if not self._truth("ENABLE_LIVE_TRADING") or not self._truth("LIVE_MANUAL_ORDERS_ENABLED"):
            raise LiveOrderError("Manual Live orders are disabled by server configuration.")
        if self.control.stopped():
            raise LiveOrderError("Emergency stop is active. Release it explicitly first.")
        with self._lock, self._connect() as db:
            db.execute("UPDATE live_flags SET value='1' WHERE name='manual_armed'")
        return self.status()

    def disarm(self) -> dict[str, Any]:
        with self._lock, self._connect() as db:
            db.execute("UPDATE live_flags SET value='0' WHERE name='manual_armed'")
        return self.status()

    def emergency_stop(self) -> dict[str, Any]:
        # Latch the stop before attempting network cancellation so new orders
        # are blocked even if Binance is unavailable.
        self.disarm()
        self.control.set_stopped(True)
        with self._connect() as db:
            pending = [r["client_order_id"] for r in db.execute(
                "SELECT client_order_id FROM live_orders "
                "WHERE status IN ('intent','open','partially_filled','unknown')"
            )]
        canceled: list[str] = []
        unresolved: list[str] = []
        for client_id in pending:
            try:
                self.cancel(client_id)
                canceled.append(client_id)
            except (ccxt.BaseError, LiveOrderError, ExecutionDisabled, ValueError, sqlite3.Error):
                unresolved.append(client_id)
        snapshot = self.status()
        snapshot["emergency_cancel_attempted"] = len(pending)
        snapshot["emergency_cancel_resolved"] = canceled
        snapshot["emergency_cancel_unresolved"] = unresolved
        return snapshot

    def status(self) -> dict[str, Any]:
        with self._connect() as db:
            orders = [dict(r) for r in db.execute(
                "SELECT * FROM live_orders ORDER BY created_at DESC LIMIT 100"
            )]
            fills = [dict(r) for r in db.execute(
                "SELECT * FROM live_fills ORDER BY occurred_at DESC LIMIT 200"
            )]
            positions = [dict(r) for r in db.execute("SELECT * FROM live_positions")]
            balances = [dict(r) for r in db.execute("SELECT * FROM live_balances ORDER BY asset")]
            utc_day = datetime.now(UTC).date().isoformat()
            pnl_rows = db.execute(
                "SELECT realized_pnl_quote FROM live_fills WHERE substr(occurred_at,1,10)=?",
                (utc_day,),
            ).fetchall()
            pnl = sum((Decimal(r["realized_pnl_quote"]) for r in pnl_rows), Decimal(0))
        return {
            "mode": "binance_spot_live",
            "manual_only": True,
            "manual_armed": self._armed(),
            "emergency_stop": self.control.stopped(),
            "orders": orders,
            "fills": fills,
            "positions": positions,
            "balances": balances,
            "daily_realized_pnl_quote": str(pnl),
            "orders_enabled": self._armed() and not self.control.stopped(),
        }

    def _risk_gate(self, symbol: str, side: str, amount: Decimal, price: Decimal) -> None:
        if not self._truth("ENABLE_LIVE_TRADING") or not self._truth("LIVE_MANUAL_ORDERS_ENABLED"):
            raise LiveOrderError("Live order submission is disabled by server configuration.")
        if not self._armed() or self.control.stopped():
            raise LiveOrderError("Manual Live trading is not armed or emergency stop is active.")
        max_daily_loss = Decimal(os.getenv("MAX_DAILY_LOSS_QUOTE", "5"))
        max_position_quote = Decimal(os.getenv("MAX_POSITION_QUOTE_USDT", "25"))
        max_open_orders = int(os.getenv("MAX_OPEN_LIVE_ORDERS", "3"))
        if max_daily_loss <= 0 or max_position_quote <= 0 or max_open_orders < 1:
            raise LiveOrderError("Risk limits are invalid; order rejected.")
        quote = amount * price
        with self._connect() as db:
            open_rows = db.execute(
                "SELECT symbol,side,amount,price FROM live_orders "
                "WHERE status IN ('intent','open','partially_filled','unknown')"
            ).fetchall()
            open_count = len(open_rows)
            pending_buy_quote = sum(
                (Decimal(r["amount"]) * Decimal(r["price"] or "0")
                 for r in open_rows if r["side"] == "buy" and r["symbol"] == symbol),
                Decimal(0),
            )
            pending_sell_amount = sum(
                (Decimal(r["amount"]) for r in open_rows
                 if r["side"] == "sell" and r["symbol"] == symbol),
                Decimal(0),
            )
            day = datetime.now(UTC).date().isoformat()
            pnl_rows = db.execute(
                "SELECT realized_pnl_quote FROM live_fills WHERE substr(occurred_at,1,10)=?",
                (day,),
            ).fetchall()
            daily_pnl = sum((Decimal(r["realized_pnl_quote"]) for r in pnl_rows), Decimal(0))
        if side == "buy" and open_count >= max_open_orders:
            raise LiveOrderError("Maximum number of open Live orders reached.")
        if side == "buy" and daily_pnl <= -max_daily_loss:
            raise LiveOrderError("Daily realized-loss limit reached; new buy orders are blocked.")
        if side == "buy":
            with self._connect() as db:
                position = db.execute(
                    "SELECT amount FROM live_positions WHERE symbol=?", (symbol,)
                ).fetchone()
            current_amount = Decimal(position["amount"]) if position else Decimal(0)
            if (current_amount * price) + pending_buy_quote + quote > max_position_quote:
                raise LiveOrderError("Order would exceed MAX_POSITION_QUOTE_USDT including pending buys.")
        if side == "sell":
            with self._connect() as db:
                position = db.execute(
                    "SELECT amount FROM live_positions WHERE symbol=?", (symbol,)
                ).fetchone()
            current_amount = Decimal(position["amount"]) if position else Decimal(0)
            if pending_sell_amount + amount > current_amount:
                raise LiveOrderError("Sell quantity plus pending sells exceeds the reconciled Spot position.")
        if quote <= 0:
            raise LiveOrderError("Order notional must be positive.")


    def _sync_balances(self, adapter: BinanceSpotExecution) -> None:
        balances = adapter.fetch_balance()
        with self._connect() as db:
            for asset, values in balances.items():
                if not isinstance(values, dict):
                    continue
                if not any(values.get(key) is not None for key in ("free", "used", "total")):
                    continue
                db.execute(
                    """INSERT INTO live_balances(asset,free,used,total,observed_at)
                       VALUES(?,?,?,?,?) ON CONFLICT(asset) DO UPDATE SET
                       free=excluded.free,used=excluded.used,total=excluded.total,
                       observed_at=excluded.observed_at""",
                    (asset, str(values.get("free") or "0"), str(values.get("used") or "0"),
                     str(values.get("total") or "0"), datetime.now(UTC).isoformat()),
                )

    def submit(self, *, request_id: str, symbol: str, side: str, order_type: str,
               amount: str, price: str | None = None) -> dict[str, Any]:
        request_id = request_id.strip()
        if not request_id or len(request_id) > 128:
            raise LiveOrderError("A unique client request id is required.")
        with self._connect() as db:
            prior = db.execute("SELECT client_order_id FROM live_orders WHERE request_id=?", (request_id,)).fetchone()
        if prior:
            return self.status()
        if side not in {"buy", "sell"} or order_type not in {"limit", "market"}:
            raise LiveOrderError("Only manual Spot buy/sell limit or market orders are supported.")
        if order_type == "limit" and not price:
            raise LiveOrderError("A limit order requires a limit price.")
        adapter = BinanceSpotExecution()
        client_id = "isk" + hashlib.sha256(request_id.encode("utf-8")).hexdigest()[:30]
        try:
            if adapter.settings.testnet or not adapter.settings.live_enabled:
                raise ExecutionDisabled("The adapter is not configured for Spot Live.")
            normalized = symbol.strip().upper()
            if not normalized.endswith("/USDT"):
                raise LiveOrderError("This Live risk ledger currently accepts USDT-quoted Spot pairs only.")
            reference = Decimal(str(price)) if price else adapter.fetch_last_price(normalized)
            qty = Decimal(str(amount))
            self._risk_gate(normalized, side, qty, reference)
            # Validate against exchange precision and notional before writing intent.
            adapter._validate(normalized, side, qty, reference)
            if side == "buy":
                quote_asset = normalized.split("/", maxsplit=1)[1]
                balances = adapter.fetch_balance()
                free_quote = Decimal(str((balances.get(quote_asset) or {}).get("free") or "0"))
                if qty * reference > free_quote:
                    raise LiveOrderError("Buy notional exceeds the free quote-asset balance.")
            if side == "sell":
                base_asset = normalized.split("/", maxsplit=1)[0]
                with self._connect() as db:
                    position_row = db.execute(
                        "SELECT amount FROM live_positions WHERE symbol=?", (normalized,)
                    ).fetchone()
                tracked = Decimal(position_row["amount"]) if position_row else Decimal(0)
                balances = adapter.fetch_balance()
                free_base = Decimal(str((balances.get(base_asset) or {}).get("free") or "0"))
                if qty > tracked or qty > free_base:
                    raise LiveOrderError("Sell quantity exceeds the locally reconciled position or free Spot balance.")
            now = datetime.now(UTC).isoformat()
            with self._lock, self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    """INSERT INTO live_orders(client_order_id,request_id,symbol,side,order_type,
                       amount,price,status,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,'intent',?,?)""",
                    (client_id, request_id, normalized, side, order_type, str(qty),
                     str(reference) if order_type == "limit" else None, now, now),
                )
                db.commit()
            # Never retry this call automatically. If response is lost, reconciliation
            # queries Binance by origClientOrderId and resolves the durable intent.
            if order_type == "limit":
                order = adapter.create_limit_order(normalized, side, qty, reference, client_id)
            else:
                order = adapter.create_market_order(normalized, side, qty, client_id)
            self._save_order(client_id, order)
            self._sync_fills(adapter, client_id, normalized, order)
            self._sync_balances(adapter)
            return self.status()
        finally:
            adapter.close()

    def _save_order(self, client_id: str, order: dict[str, Any]) -> None:
        import json
        with self._lock, self._connect() as db:
            db.execute(
                """UPDATE live_orders SET exchange_order_id=?,filled=?,average=?,status=?,
                   raw_json=?,updated_at=? WHERE client_order_id=?""",
                (str(order.get("id") or "") or None, str(order.get("filled") or "0"),
                 str(order.get("average") or "") or None, str(order.get("status") or "unknown"),
                 json.dumps(order, default=str), datetime.now(UTC).isoformat(), client_id),
            )

    def _sync_fills(self, adapter: BinanceSpotExecution, client_id: str,
                    symbol: str, order: dict[str, Any]) -> None:
        exchange_id = str(order.get("id") or "")
        if not exchange_id:
            return
        fills = adapter.fetch_my_trades_for_order(symbol, exchange_id)
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for fill in fills:
                info = fill.get("info") if isinstance(fill.get("info"), dict) else {}
                fill_id = fill.get("id") or info.get("id") or info.get("tradeId")
                if fill_id is None:
                    continue
                amount = Decimal(str(fill.get("amount") or info.get("qty") or "0"))
                price = Decimal(str(fill.get("price") or info.get("price") or "0"))
                fee = fill.get("fee") if isinstance(fill.get("fee"), dict) else {}
                fee_cost = Decimal(str(fee.get("cost") or info.get("commission") or "0"))
                fee_currency = fee.get("currency") or info.get("commissionAsset")
                occurred = str(fill.get("datetime") or datetime.now(UTC).isoformat())
                key = client_id + ":" + str(fill_id)
                cur = db.execute(
                    """INSERT OR IGNORE INTO live_fills(fill_key,client_order_id,symbol,side,
                       amount,price,fee_cost,fee_currency,occurred_at)
                       VALUES(?,?,?,?,?,?,?,?,?)""",
                    (key, client_id, symbol, str(order.get("side") or ""),
                     str(amount), str(price), str(fee_cost), fee_currency, occurred),
                )
                if cur.rowcount != 1:
                    continue
                position = db.execute(
                    "SELECT amount,average_entry FROM live_positions WHERE symbol=?", (symbol,)
                ).fetchone()
                old_amount = Decimal(position["amount"]) if position else Decimal(0)
                old_avg = Decimal(position["average_entry"]) if position else Decimal(0)
                realized = Decimal(0)
                base_asset, quote_asset = symbol.split("/", maxsplit=1)
                if order.get("side") == "buy":
                    net_amount = amount - fee_cost if fee_currency == base_asset else amount
                    if net_amount <= 0:
                        raise LiveOrderError("Buy fill commission consumed the entire base quantity.")
                    new_amount = old_amount + net_amount
                    quote_cost = old_amount * old_avg + amount * price
                    if fee_currency == quote_asset:
                        quote_cost += fee_cost
                    avg = quote_cost / new_amount if new_amount else Decimal(0)
                    db.execute(
                        """INSERT INTO live_positions(symbol,amount,average_entry,updated_at)
                           VALUES(?,?,?,?) ON CONFLICT(symbol) DO UPDATE SET
                           amount=excluded.amount,average_entry=excluded.average_entry,
                           updated_at=excluded.updated_at""",
                        (symbol, str(new_amount), str(avg), datetime.now(UTC).isoformat()),
                    )
                elif order.get("side") == "sell":
                    base_fee = fee_cost if fee_currency == base_asset else Decimal(0)
                    quote_fee = fee_cost if fee_currency == quote_asset else Decimal(0)
                    debited_amount = amount + base_fee
                    if debited_amount > old_amount:
                        raise LiveOrderError("Exchange sell fill and base commission exceed locally journaled Spot position.")
                    realized = amount * (price - old_avg) - quote_fee - (base_fee * old_avg)
                    remaining = old_amount - debited_amount
                    if remaining == 0:
                        db.execute("DELETE FROM live_positions WHERE symbol=?", (symbol,))
                    else:
                        db.execute(
                            "UPDATE live_positions SET amount=?,updated_at=? WHERE symbol=?",
                            (str(remaining), datetime.now(UTC).isoformat(), symbol),
                        )
                db.execute(
                    "UPDATE live_fills SET realized_pnl_quote=? WHERE fill_key=?",
                    (str(realized), key),
                )
            db.commit()


    def cancel(self, client_order_id: str) -> dict[str, Any]:
        client_order_id = client_order_id.strip()
        if not client_order_id:
            raise LiveOrderError("Client order id is required.")
        with self._connect() as db:
            row = db.execute(
                "SELECT symbol,status FROM live_orders WHERE client_order_id=?",
                (client_order_id,),
            ).fetchone()
        if not row:
            raise LiveOrderError("Live order was not found in the durable journal.")
        adapter = BinanceSpotExecution()
        try:
            if adapter.settings.testnet or not adapter.settings.live_enabled:
                raise ExecutionDisabled("The adapter is not configured for Spot Live.")
            order = adapter.cancel_order_by_client_id(client_order_id, row["symbol"])
            self._save_order(client_order_id, order)
            self._sync_fills(adapter, client_order_id, row["symbol"], order)
            return self.status()
        finally:
            adapter.close()

    def reconcile(self) -> dict[str, Any]:
        adapter = BinanceSpotExecution()
        try:
            if adapter.settings.testnet or not adapter.settings.live_enabled:
                raise ExecutionDisabled("The adapter is not configured for Spot Live.")
            with self._connect() as db:
                pending = [dict(r) for r in db.execute(
                    "SELECT * FROM live_orders ORDER BY created_at DESC LIMIT 1000"
                )]
            for row in pending:
                try:
                    order = adapter.fetch_order_by_client_id(row["client_order_id"], row["symbol"])
                    self._save_order(row["client_order_id"], order)
                    self._sync_fills(adapter, row["client_order_id"], row["symbol"], order)
                except (ccxt.BaseError, LiveOrderError, ValueError, sqlite3.Error, KeyError):
                    # An uncertain order stays unresolved and is never resubmitted.
                    with self._connect() as db:
                        db.execute(
                            "UPDATE live_orders SET status='unknown',updated_at=? WHERE client_order_id=?",
                            (datetime.now(UTC).isoformat(), row["client_order_id"]),
                        )
            self._sync_balances(adapter)
            return self.status()
        finally:
            adapter.close()
