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
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .fill_accounting import AccountingError, replay_spot_fills


class JournalError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(UTC).isoformat()


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
                    updated_at TEXT NOT NULL,
                    fills_synced INTEGER NOT NULL DEFAULT 0
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
                CREATE TABLE IF NOT EXISTS trade_realizations (
                    realization_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_id TEXT NOT NULL REFERENCES trade_configs(trade_id),
                    client_order_id TEXT NOT NULL REFERENCES orders(client_order_id),
                    amount TEXT NOT NULL,
                    average_entry TEXT NOT NULL,
                    exit_price TEXT NOT NULL,
                    gross_pnl TEXT NOT NULL,
                    occurred_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS system_flags (
                    flag TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS account_balances (
                    asset TEXT PRIMARY KEY,
                    free TEXT NOT NULL,
                    used TEXT NOT NULL,
                    total TEXT NOT NULL,
                    observed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS cycle_leases (
                    lease_key TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    expires_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audit_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_id TEXT,
                    event_type TEXT NOT NULL,
                    detail_json TEXT NOT NULL,
                    occurred_at TEXT NOT NULL
                );
            """)
            order_columns = {
                row["name"] for row in db.execute("PRAGMA table_info(orders)").fetchall()
            }
            if "fills_synced" not in order_columns:
                db.execute(
                    "ALTER TABLE orders ADD COLUMN fills_synced INTEGER NOT NULL DEFAULT 0"
                )
            db.execute(
                "INSERT OR IGNORE INTO system_flags(flag,value,updated_at) VALUES('emergency_stop','1',?)",
                (_now(),),
            )

    def acquire_cycle_lease(
        self, owner_id: str, *, lease_key: str = "binance_testnet_monitor",
        ttl_seconds: int = 300,
    ) -> bool:
        if not owner_id or ttl_seconds < 30:
            raise ValueError("A lease owner and at least 30 seconds TTL are required.")
        now = datetime.now(UTC).timestamp()
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute(
                """INSERT INTO cycle_leases(lease_key,owner_id,expires_at)
                   VALUES(?,?,?)
                   ON CONFLICT(lease_key) DO UPDATE SET
                     owner_id=excluded.owner_id, expires_at=excluded.expires_at
                   WHERE cycle_leases.expires_at <= ? OR cycle_leases.owner_id = ?""",
                (lease_key, owner_id, now + ttl_seconds, now, owner_id),
            )
            acquired = cursor.rowcount == 1
            db.commit()
            return acquired

    def release_cycle_lease(
        self, owner_id: str, *, lease_key: str = "binance_testnet_monitor"
    ) -> None:
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "DELETE FROM cycle_leases WHERE lease_key=? AND owner_id=?",
                (lease_key, owner_id),
            )
            db.commit()

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
            previous = db.execute(
                "SELECT trade_id,side,filled,symbol,average FROM orders WHERE client_order_id=?",
                (client_order_id,),
            ).fetchone()
            old_filled = Decimal(previous["filled"] or "0")
            new_filled = Decimal(str(order.get("filled") or "0"))
            if new_filled < old_filled:
                db.rollback()
                raise JournalError("Exchange cumulative filled amount moved backwards.")
            delta = new_filled - old_filled
            average = Decimal(str(order.get("average") or "0"))
            previous_average = Decimal(str(previous["average"] or "0"))
            incremental_price = (
                (new_filled * average - old_filled * previous_average) / delta
                if delta > 0 and average > 0 else average
            )
            status = str(order.get("status") or "unknown")
            if status == "open" and new_filled > 0:
                status = "partially_filled"
            db.execute(
                """UPDATE orders SET exchange_order_id=?,filled=?,remaining=?,average=?,
                   status=?,raw_json=?,updated_at=? WHERE client_order_id=?""",
                (str(order.get("id") or "") or None, str(new_filled),
                 str(order.get("remaining") or "0"), str(average) if average else "",
                 status, json.dumps(order, default=str), now, client_order_id),
            )
            if delta > 0 and average > 0:
                position = db.execute(
                    "SELECT amount,average_entry,status FROM positions WHERE trade_id=?",
                    (previous["trade_id"],),
                ).fetchone()
                current_amount = Decimal(position["amount"]) if position else Decimal(0)
                if previous["side"] == "buy":
                    total = current_amount + delta
                    prior_cost = current_amount * Decimal(position["average_entry"]) if position else Decimal(0)
                    weighted = (prior_cost + delta * incremental_price) / total
                    db.execute(
                        """INSERT INTO positions(trade_id,symbol,amount,average_entry,status,updated_at)
                           VALUES(?,?,?,?,?,?) ON CONFLICT(trade_id) DO UPDATE SET
                           amount=excluded.amount,average_entry=excluded.average_entry,
                           status=excluded.status,updated_at=excluded.updated_at""",
                        (previous["trade_id"], previous["symbol"], str(total), str(weighted),
                         "open", now),
                    )
                else:
                    if not position or current_amount <= 0 or delta > current_amount:
                        db.rollback()
                        raise JournalError("Sell fill exceeds the journaled Spot position.")
                    entry = Decimal(position["average_entry"])
                    gross_pnl = delta * (incremental_price - entry)
                    db.execute(
                        """INSERT INTO trade_realizations
                           (trade_id,client_order_id,amount,average_entry,exit_price,gross_pnl,occurred_at)
                           VALUES(?,?,?,?,?,?,?)""",
                        (previous["trade_id"], client_order_id, str(delta), str(entry),
                         str(incremental_price), str(gross_pnl), now),
                    )
                    remaining = current_amount - delta
                    db.execute(
                        """UPDATE positions SET amount=?,status=?,updated_at=? WHERE trade_id=?""",
                        (str(remaining), "closed" if remaining == 0 else "open",
                         now, previous["trade_id"]),
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

    def record_fills(self, client_order_id: str, fills: list[dict[str, Any]]) -> int:
        """Persist exchange fills idempotently; position quantity remains driven by
        cumulative order status so replaying a trade-history page cannot double count.
        """
        inserted = 0
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            order = db.execute(
                "SELECT trade_id FROM orders WHERE client_order_id=?",
                (client_order_id,),
            ).fetchone()
            if not order:
                db.rollback()
                raise KeyError("order intent not found")
            for fill in fills:
                info = fill.get("info") if isinstance(fill.get("info"), dict) else {}
                fill_id = fill.get("id") or info.get("id") or info.get("tradeId")
                if fill_id is None:
                    continue
                amount = _positive(fill.get("amount") or info.get("qty") or "0", "fill.amount")
                price = _positive(fill.get("price") or info.get("price") or "0", "fill.price")
                fee = fill.get("fee") if isinstance(fill.get("fee"), dict) else {}
                fee_cost = fee.get("cost")
                fee_currency = fee.get("currency")
                if fee_cost is None:
                    commissions = info.get("commission")
                    fee_cost = commissions if commissions is not None else "0"
                    fee_currency = fee_currency or info.get("commissionAsset")
                fee_value = _positive(fee_cost, "fill.fee", allow_zero=True)
                occurred = fill.get("datetime") or _now()
                key = f"{client_order_id}:{fill_id}"
                cursor = db.execute(
                    """INSERT OR IGNORE INTO fills
                       (fill_key,trade_id,client_order_id,amount,price,fee_cost,fee_currency,occurred_at)
                       VALUES(?,?,?,?,?,?,?,?)""",
                    (key, order["trade_id"], client_order_id, amount, price,
                     fee_value, fee_currency, str(occurred)),
                )
                inserted += cursor.rowcount
            if inserted:
                db.execute(
                    "INSERT INTO audit_events(trade_id,event_type,detail_json,occurred_at) VALUES(?,?,?,?)",
                    (order["trade_id"], "exchange_fills_recorded",
                     json.dumps({"client_order_id": client_order_id, "new_fills": inserted}), _now()),
                )
            db.commit()
        return inserted

    def mark_fills_synced(self, client_order_id: str) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                "UPDATE orders SET fills_synced=1,updated_at=? WHERE client_order_id=?",
                (_now(), client_order_id),
            )

    def recent_orders(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._connect() as db:
            return [dict(row) for row in db.execute(
                """SELECT * FROM orders
                   WHERE status IN ('intent','open','partially_filled')
                      OR fills_synced=0
                   ORDER BY created_at DESC LIMIT ?""",
                (max(1, min(limit, 1000)),),
            ).fetchall()]

    def save_balance_snapshot(self, balances: dict[str, Any]) -> None:
        now = _now()
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for asset, values in balances.items():
                if not isinstance(values, dict):
                    continue
                free = str(values.get("free") or 0)
                used = str(values.get("used") or 0)
                total = str(values.get("total") or 0)
                db.execute(
                    """INSERT INTO account_balances(asset,free,used,total,observed_at)
                       VALUES(?,?,?,?,?) ON CONFLICT(asset) DO UPDATE SET
                       free=excluded.free,used=excluded.used,total=excluded.total,
                       observed_at=excluded.observed_at""",
                    (asset, free, used, total, now),
                )
            db.execute(
                "INSERT INTO audit_events(event_type,detail_json,occurred_at) VALUES(?,?,?)",
                ("account_balances_synced", json.dumps({"assets": len(balances)}), now),
            )
            db.commit()

    def snapshot(self) -> dict[str, Any]:
        with self._connect() as db:
            fee_rows = db.execute(
                "SELECT fee_currency,fee_cost FROM fills WHERE fee_currency IS NOT NULL"
            ).fetchall()
            fee_totals: dict[str, Decimal] = {}
            for fee_row in fee_rows:
                currency = str(fee_row["fee_currency"])
                fee_totals[currency] = fee_totals.get(currency, Decimal(0)) + Decimal(
                    fee_row["fee_cost"]
                )
            realized_rows = db.execute(
                "SELECT gross_pnl FROM trade_realizations"
            ).fetchall()
            gross_realized = sum(
                (Decimal(row["gross_pnl"]) for row in realized_rows), Decimal(0)
            )
            trades = [dict(r) for r in db.execute(
                "SELECT * FROM trade_configs ORDER BY created_at DESC"
            )]
            orders = [dict(r) for r in db.execute(
                "SELECT * FROM orders ORDER BY created_at DESC LIMIT 200"
            )]
            stored_positions = {
                row["trade_id"]: dict(row)
                for row in db.execute("SELECT * FROM positions")
            }
            joined_fills = [dict(row) for row in db.execute(
                """SELECT f.*,o.side,o.symbol FROM fills f
                   JOIN orders o ON o.client_order_id=f.client_order_id
                   ORDER BY f.occurred_at,f.fill_key"""
            )]
            filled_by_order: dict[str, Decimal] = {}
            for row in joined_fills:
                filled_by_order[row["client_order_id"]] = (
                    filled_by_order.get(row["client_order_id"], Decimal(0))
                    + Decimal(row["amount"])
                )
            accounting_issues: list[str] = []
            for order in db.execute(
                "SELECT client_order_id,filled FROM orders WHERE CAST(filled AS REAL)>0"
            ):
                expected = Decimal(order["filled"])
                actual = filled_by_order.get(order["client_order_id"], Decimal(0))
                if actual != expected:
                    accounting_issues.append(
                        f"Fill history mismatch for {order['client_order_id']}: "
                        f"order filled={expected}, recorded fills={actual}."
                    )

            fills_by_trade: dict[str, list[dict[str, object]]] = {}
            symbols_by_trade: dict[str, str] = {}
            for row in joined_fills:
                fills_by_trade.setdefault(row["trade_id"], []).append({
                    "id": row["fill_key"],
                    "side": row["side"],
                    "amount": row["amount"],
                    "price": row["price"],
                    "fee_cost": row["fee_cost"],
                    "fee_currency": row["fee_currency"] or "",
                    "timestamp": row["occurred_at"],
                })
                symbols_by_trade[row["trade_id"]] = row["symbol"]

            positions: list[dict[str, Any]] = []
            net_realized: list[dict[str, str]] = []
            for trade_id, fills in fills_by_trade.items():
                symbol = symbols_by_trade[trade_id]
                try:
                    ledger = replay_spot_fills(symbol, fills)
                except AccountingError as exc:
                    accounting_issues.append(f"Fill ledger error for {trade_id}: {exc}")
                    continue
                positions.append({
                    "trade_id": trade_id,
                    "symbol": symbol,
                    "amount": str(ledger.base_quantity),
                    "average_entry": str(ledger.average_entry),
                    "status": "open" if ledger.base_quantity > 0 else "closed",
                    "accounting_source": "exchange_fills",
                })
                net_realized.append({
                    "trade_id": trade_id,
                    "symbol": symbol,
                    "quote_asset": ledger.quote_asset,
                    "realized_quote_pnl": str(ledger.realized_quote_pnl),
                })
            # Legacy/test-only rows without trade-history records remain visible as
            # provisional positions, but the mismatch above blocks automated action.
            trades_with_fills = set(fills_by_trade)
            for trade_id, position in stored_positions.items():
                if trade_id not in trades_with_fills:
                    provisional = dict(position)
                    provisional["accounting_source"] = "cumulative_order_provisional"
                    positions.append(provisional)

            return {
                "balances": [dict(r) for r in db.execute(
                    "SELECT * FROM account_balances ORDER BY asset"
                )],
                "trades": trades,
                "orders": orders,
                "fills": [dict(r) for r in db.execute(
                    "SELECT * FROM fills ORDER BY occurred_at DESC LIMIT 500"
                )],
                "realizations": [dict(r) for r in db.execute(
                    "SELECT * FROM trade_realizations ORDER BY occurred_at DESC LIMIT 500"
                )],
                "fee_totals": [
                    {"currency": currency, "total_fee": str(total)}
                    for currency, total in sorted(fee_totals.items())
                ],
                "gross_realized_pnl": str(gross_realized),
                "net_realized_quote_pnl": net_realized,
                "accounting_issues": accounting_issues,
                "positions": positions,
                "emergency_stop": self.emergency_stopped(),
            }
