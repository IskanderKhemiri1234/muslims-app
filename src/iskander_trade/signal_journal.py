"""Durable idempotency journal for closed-candle strategy signals.

This module deliberately records decisions only. It does not submit exchange
orders or arm Testnet/Live execution. Callers must persist a signal before
starting any external order workflow and reconcile that workflow by its own
client order ID after ambiguous network outcomes.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


class SignalJournal:
    """SQLite-backed, restart-safe one-signal-per-market-candle journal."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._lock, self._connect() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS strategy_signals (
                    strategy_version TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    candle_timestamp INTEGER NOT NULL,
                    action TEXT NOT NULL CHECK(action IN ('buy', 'sell')),
                    close_price TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'detected',
                    detail_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(strategy_version, symbol, candle_timestamp)
                )
                """
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS strategy_signals_recent_idx "
                "ON strategy_signals(created_at DESC)"
            )

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        return db

    @staticmethod
    def _validate(signal: Any, strategy_version: str) -> tuple[str, str, int, str, str, str]:
        if not strategy_version or len(strategy_version) > 80:
            raise ValueError("strategy_version must contain 1 to 80 characters")
        symbol = str(getattr(signal, "symbol", "")).strip().upper()
        action = str(getattr(signal, "action", "")).strip().lower()
        reason = str(getattr(signal, "reason", "")).strip()
        try:
            timestamp = int(getattr(signal, "candle_timestamp"))
            close_price = float(getattr(signal, "close_price"))
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValueError("Signal must contain a valid timestamp and close price") from exc
        if not symbol or "/" not in symbol:
            raise ValueError("Signal symbol must be a normalized spot market such as BTC/USDT")
        if action not in {"buy", "sell"}:
            raise ValueError("Signal action must be buy or sell")
        if timestamp <= 0:
            raise ValueError("Signal candle timestamp must be positive")
        if not close_price > 0 or close_price == float("inf") or close_price != close_price:
            raise ValueError("Signal close price must be finite and positive")
        if not reason:
            raise ValueError("Signal reason is required")
        return strategy_version, symbol, timestamp, action, format(close_price, ".16g"), reason

    def claim(self, signal: Any, *, strategy_version: str = "ema20-50-v1") -> bool:
        """Atomically claim a signal key; False means it was already recorded.

        The unique key intentionally excludes action: a second, conflicting
        decision for the same strategy/market/candle must not create a second
        order opportunity.
        """
        values = self._validate(signal, strategy_version)
        now = _utc_now()
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute(
                """
                INSERT OR IGNORE INTO strategy_signals(
                    strategy_version, symbol, candle_timestamp, action,
                    close_price, reason, status, created_at, updated_at
                ) VALUES(?,?,?,?,?,?,'detected',?,?)
                """,
                (*values, now, now),
            )
            db.commit()
            return cursor.rowcount == 1

    def update_status(
        self,
        signal: Any,
        status: str,
        *,
        strategy_version: str = "ema20-50-v1",
        detail: dict[str, Any] | None = None,
    ) -> bool:
        """Update an existing claim without creating a new one."""
        allowed = {"detected", "rejected", "paper_recorded", "testnet_pending", "reconciled", "error"}
        if status not in allowed:
            raise ValueError(f"Unsupported signal status: {status}")
        version, symbol, timestamp, _action, _price, _reason = self._validate(
            signal, strategy_version
        )
        with self._lock, self._connect() as db:
            cursor = db.execute(
                """
                UPDATE strategy_signals
                   SET status=?, detail_json=?, updated_at=?
                 WHERE strategy_version=? AND symbol=? AND candle_timestamp=?
                """,
                (status, json.dumps(detail or {}, sort_keys=True), _utc_now(),
                 version, symbol, timestamp),
            )
            return cursor.rowcount == 1

    def get(
        self, symbol: str, candle_timestamp: int,
        *, strategy_version: str = "ema20-50-v1",
    ) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                """
                SELECT * FROM strategy_signals
                 WHERE strategy_version=? AND symbol=? AND candle_timestamp=?
                """,
                (strategy_version, symbol.strip().upper(), int(candle_timestamp)),
            ).fetchone()
        return dict(row) if row else None

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM strategy_signals ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]
