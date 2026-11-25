"""Durable SQLite journal for guarded Binance Spot Testnet order workflows.

The journal is the source of truth for user trade settings, order intents, fills,
position state and emergency-stop state. An intent is committed before exchange
submission so a restart can reconcile by client order id instead of blindly
submitting a duplicate.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


class JournalError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _positive(value: Any, name: str, *, allow_zero: bool = False) -> str:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{name} must be a decimal number") from exc
    if not number.is_finite() or (number < 0 if allow_zero else number <= 0):
        raise ValueError(f"{name} is outside the permitted range")
    return format(number, "f")


class TradeJournal:
    """Thread-safe SQLite persistence with explicit transactions."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        with self._lock, self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS trade_configs (
                    trade_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL CHECK(side='buy'),
                    quantity TEXT NOT NULL,
                    entry_price TEXT NOT NULL,
                    stop_loss_price TEXT NOT NULL,
                    take_profit_price TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'configured',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS orders (
                    client_order_id TEXT PRIMARY KEY,
                    trade_id TEXT NOT NULL REFERENCES trade_configs(trade_id),
                    exchange_order_id TEXT,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    order_type TEXT NOT NULL,
                    amount TEXT NOT NULL,
                    price TEXT,
                    filled TEXT NOT NULL DEFAULT '0',
                    remaining TEXT,
                    average TEXT,
                    status TEXT NOT NULL,
                    raw_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS orders_trade_idx ON orders(trade_id, created_at);
                CREATE TABLE IF NOT EXISTS fills (
                    fill_key TEXT PRIMARY KEY,
                    trade_id TEXT NOT NULL REFERENCES trade_configs(trade_id),
                    client_order_id TEXT NOT NULL REFERENCES orders(client_order_id),
                    amount TEXT NOT NULL,
                    price TEXT NOT NULL,
                    fee_cost TEXT NOT NULL DEFAULT '0',
                    fee_currency TEXT,
                    occurred_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS positions (
                    trade_id TEXT PRIMARY KEY REFERENCES trade_configs(trade_id),
                    symbol TEXT NOT NULL,
                    amount TEXT NOT NULL,
                    average_entry TEXT NOT NULL,
                    status TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS system_flags (
                    flag TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audit_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_id TEXT,
                    event_type TEXT NOT NULL,
                    detail_json TEXT NOT NULL,
                    occurred_at TEXT NOT NULL
                );
            """)
            db.execute(
                "INSERT OR IGNORE INTO system_flags(flag,value,updated_at) VALUES('emergency_stop','1',?)",
                (_now(),),
            )

    def emergency_stopped(self) -> bool:
        with self._connect() as db:
            row = db.execute(
                "SELECT value FROM system_flags WHERE flag='emergency_stop'"
            ).fetchone()
            return not row or row["value"] != "0"

    def set_emergency_stop(self, stopped: bool, reason: str = "operator") -> None:
        value = "1" if stopped else "0"
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """INSERT INTO system_flags(flag,value,updated_at) VALUES('emergency_stop',?,?)
                   ON CONFLICT(flag) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at""",
                (value, _now()),
            )
            db.execute(
                "INSERT INTO audit_events(event_type,detail_json,occurred_at) VALUES(?,?,?)",
                ("emergency_stop_on" if stopped else "emergency_stop_off",
                 json.dumps({"reason": reason}), _now()),
            )
            db.commit()

    def create_trade(self, *, symbol: str, quantity: Any, entry_price: Any,
                     stop_loss_price: Any, take_profit_price: Any) -> dict[str, Any]:
        symbol = symbol.strip().upper()
        if not symbol or "/" not in symbol:
            raise ValueError("symbol must be a Binance Spot pair such as BTC/USDT")
        qty = _positive(quantity, "quantity")
        entry = _positive(entry_price, "entry_price")
        stop = _positive(stop_loss_price, "stop_loss_price")
        target = _positive(take_profit_price, "take_profit_price")
        if Decimal(stop) >= Decimal(entry) or Decimal(target) <= Decimal(entry):
            raise ValueError("For a long Spot trade, stop must be below entry and target above entry")
        trade_id = uuid.uuid4().hex
        now = _now()
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """INSERT INTO trade_configs
                   (trade_id,symbol,side,quantity,entry_price,stop_loss_price,
                    take_profit_price,enabled,status,created_at,updated_at)
                   VALUES(?,?,'buy',?,?,?,?,0,'configured',?,?)""",
                (trade_id, symbol, qty, entry, stop, target, now, now),
            )
            db.execute(
                "INSERT INTO audit_events(trade_id,event_type,detail_json,occurred_at) VALUES(?,?,?,?)",
                (trade_id, "trade_configured", json.dumps({
                    "symbol": symbol, "quantity": qty, "entry_price": entry,
                    "stop_loss_price": stop, "take_profit_price": target,
                }), now),
            )
            db.commit()
        return self.get_trade(trade_id)

    def get_trade(self, trade_id: str) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute("SELECT * FROM trade_configs WHERE trade_id=?", (trade_id,)).fetchone()
            if not row:
                raise KeyError("trade not found")
            return dict(row)

    def list_trades(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM trade_configs ORDER BY created_at DESC"
            ).fetchall()]

    def set_trade_enabled(self, trade_id: str, enabled: bool) -> None:
        now = _now()
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute(
                "UPDATE trade_configs SET enabled=?,status=?,updated_at=? WHERE trade_id=?",
                (int(enabled), "armed" if enabled else "paused", now, trade_id),
            )
            if cursor.rowcount != 1:
                db.rollback()
                raise KeyError("trade not found")
            db.execute(
                "INSERT INTO audit_events(trade_id,event_type,detail_json,occurred_at) VALUES(?,?,?,?)",
                (trade_id, "trade_enabled" if enabled else "trade_paused", "{}", now),
            )
            db.commit()

    def create_order_intent(self, *, trade_id: str, symbol: str, side: str,
                            amount: Any, price: Any | None, order_type: str = "limit") -> str:
        if side not in {"buy", "sell"} or order_type not in {"limit", "market"}:
            raise ValueError("unsupported order side or type")
        qty = _positive(amount, "amount")
        px = _positive(price, "price") if price is not None else None
        client_id = "isk" + uuid.uuid4().hex[:30]
        now = _now()
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            trade = db.execute(
                "SELECT trade_id FROM trade_configs WHERE trade_id=?", (trade_id,)
            ).fetchone()
            if not trade:
                db.rollback()
                raise KeyError("trade not found")
            db.execute(
                """INSERT INTO orders(client_order_id,trade_id,symbol,side,order_type,
                   amount,price,status,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,'intent',?,?)""",
                (client_id, trade_id, symbol, side, order_type, qty, px, now, now),
            )
            db.execute(
                "INSERT INTO audit_events(trade_id,event_type,detail_json,occurred_at) VALUES(?,?,?,?)",
                (trade_id, "order_intent_created",
                 json.dumps({"client_order_id": client_id, "side": side, "amount": qty}), now),
            )
            db.commit()
        return client_id

    def update_order(self, client_order_id: str, order: dict[str, Any]) -> None:
        now = _now()
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT trade_id FROM orders WHERE client_order_id=?", (client_order_id,)
            ).fetchone()
            if not row:
                db.rollback()
                raise KeyError("order intent not found")
            db.execute(
                """UPDATE orders SET exchange_order_id=?,filled=?,remaining=?,average=?,
                   status=?,raw_json=?,updated_at=? WHERE client_order_id=?""",
                (str(order.get("id") or "") or None, str(order.get("filled") or "0"),
                 str(order.get("remaining") or "0"), str(order.get("average") or ""),
                 str(order.get("status") or "unknown"),
                 json.dumps(order, default=str), now, client_order_id),
            )
            db.execute(
                "INSERT INTO audit_events(trade_id,event_type,detail_json,occurred_at) VALUES(?,?,?,?)",
                (row["trade_id"], "order_reconciled",
                 json.dumps({"client_order_id": client_order_id, "status": order.get("status"),
                             "filled": str(order.get("filled") or "0")}), now),
            )
            db.commit()

    def pending_orders(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM orders WHERE status IN ('intent','open','partially_filled')"
            ).fetchall()]

    def snapshot(self) -> dict[str, Any]:
        with self._connect() as db:
            return {
                "trades": [dict(r) for r in db.execute(
                    "SELECT * FROM trade_configs ORDER BY created_at DESC"
                )],
                "orders": [dict(r) for r in db.execute(
                    "SELECT * FROM orders ORDER BY created_at DESC LIMIT 200"
                )],
                "positions": [dict(r) for r in db.execute("SELECT * FROM positions")],
                "emergency_stop": self.emergency_stopped(),
            }
