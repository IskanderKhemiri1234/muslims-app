"""Manually submitted Binance Spot Live orders with fail-closed controls."""
from __future__ import annotations

import hashlib
import os
import sqlite3
import threading

import ccxt
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

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
        self.disarm()
        self.control.set_stopped(True)
        return self.status()

    def status(self) -> dict[str, Any]:
        with self._connect() as db:
            orders = [dict(r) for r in db.execute(
                "SELECT * FROM live_orders ORDER BY created_at DESC LIMIT 100"
            )]
            fills = [dict(r) for r in db.execute(
                "SELECT * FROM live_fills ORDER BY occurred_at DESC LIMIT 200"
            )]
            positions = [dict(r) for r in db.execute("SELECT * FROM live_positions")]
            utc_day = datetime.now(UTC).date().isoformat()
            pnl = db.execute(
                "SELECT COALESCE(SUM(CAST(realized_pnl_quote AS REAL)),0) AS pnl "
                "FROM live_fills WHERE substr(occurred_at,1,10)=?",
                (utc_day,),
            ).fetchone()["pnl"]
        return {
            "mode": "binance_spot_live",
            "manual_only": True,
            "manual_armed": self._armed(),
            "emergency_stop": self.control.stopped(),
            "orders": orders,
            "fills": fills,
            "positions": positions,
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
            open_count = db.execute(
                "SELECT COUNT(*) AS n FROM live_orders WHERE status IN ('intent','open','partially_filled','unknown')"
            ).fetchone()["n"]
            day = datetime.now(UTC).date().isoformat()
            daily_pnl = Decimal(str(db.execute(
                "SELECT COALESCE(SUM(CAST(realized_pnl_quote AS REAL)),0) AS pnl "
                "FROM live_fills WHERE substr(occurred_at,1,10)=?", (day,)
            ).fetchone()["pnl"]))
        if open_count >= max_open_orders:
            raise LiveOrderError("Maximum number of open Live orders reached.")
        if side == "buy" and daily_pnl <= -max_daily_loss:
            raise LiveOrderError("Daily realized-loss limit reached; new buy orders are blocked.")
        if side == "buy":
            with self._connect() as db:
                position = db.execute(
                    "SELECT amount FROM live_positions WHERE symbol=?", (symbol,)
                ).fetchone()
            current_amount = Decimal(position["amount"]) if position else Decimal(0)
            if (current_amount * price) + quote > max_position_quote:
                raise LiveOrderError("Order would exceed MAX_POSITION_QUOTE_USDT.")
        if quote <= 0:
            raise LiveOrderError("Order notional must be positive.")

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
            reference = Decimal(str(price)) if price else adapter.fetch_last_price(normalized)
            qty = Decimal(str(amount))
            self._risk_gate(normalized, side, qty, reference)
            # Validate against exchange precision and notional before writing intent.
            adapter._validate(normalized, side, qty, reference)
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
                if order.get("side") == "buy":
                    new_amount = old_amount + amount
                    quote_cost = old_amount * old_avg + amount * price
                    if fee_currency == symbol.split("/")[1]:
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
                    if amount > old_amount:
                        raise LiveOrderError("Exchange sell fill exceeds locally journaled Spot position.")
                    realized = amount * (price - old_avg)
                    if fee_currency == symbol.split("/")[1]:
                        realized -= fee_cost
                    remaining = old_amount - amount
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

    def reconcile(self) -> dict[str, Any]:
        adapter = BinanceSpotExecution()
        try:
            if adapter.settings.testnet or not adapter.settings.live_enabled:
                raise ExecutionDisabled("The adapter is not configured for Spot Live.")
            with self._connect() as db:
                pending = [dict(r) for r in db.execute(
                    "SELECT * FROM live_orders WHERE status IN ('intent','open','partially_filled','unknown')"
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
            return self.status()
        finally:
            adapter.close()
