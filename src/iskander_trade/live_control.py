"""Fail-closed Live control plane. Read-only preflight; no order submission here."""
from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path
from typing import Any

import ccxt

from .live_execution import BinanceSpotExecution, ExecutionDisabled


class LiveControlError(RuntimeError):
    """Live control plane cannot safely proceed."""


class LiveControlService:
    """Persistent Live safety latch and read-only exchange preflight.

    The latch defaults to stopped. This service intentionally has no order
    submission method; order management is added only after reconciliation and
    risk controls are independently tested.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path or os.getenv("LIVE_CONTROL_DB", "data/live_control.sqlite3"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._connect() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS live_flags(
                    name TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL
                )"""
            )
            db.execute(
                """INSERT OR IGNORE INTO live_flags(name,value,updated_at)
                   VALUES('emergency_stop','1',CURRENT_TIMESTAMP)"""
            )

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        return db

    def stopped(self) -> bool:
        with self._connect() as db:
            row = db.execute(
                "SELECT value FROM live_flags WHERE name='emergency_stop'"
            ).fetchone()
            return row is None or row["value"] != "0"

    def set_stopped(self, stopped: bool) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                """INSERT INTO live_flags(name,value,updated_at)
                   VALUES('emergency_stop',?,CURRENT_TIMESTAMP)
                   ON CONFLICT(name) DO UPDATE SET
                   value=excluded.value,updated_at=CURRENT_TIMESTAMP""",
                ("1" if stopped else "0",),
            )

    def status(self) -> dict[str, Any]:
        testnet = os.getenv("BINANCE_TESTNET", "true").strip().lower() in {"1", "true", "yes"}
        enabled = os.getenv("ENABLE_LIVE_TRADING", "false").strip().lower() in {"1", "true", "yes"}
        has_key = bool(os.getenv("BINANCE_API_KEY", "").strip())
        has_secret = bool(os.getenv("BINANCE_API_SECRET", "").strip())
        symbols = sorted({
            item.strip().upper()
            for item in os.getenv("BINANCE_ALLOWED_SYMBOLS", "").split(",")
            if item.strip()
        })
        configured = not testnet and enabled and has_key and has_secret and bool(symbols)
        return {
            "mode": "binance_spot_live" if configured else "disabled",
            "configured": configured,
            "orders_enabled": False,
            "emergency_stop": self.stopped(),
            "allowed_symbols": symbols,
            "credentials_present": has_key and has_secret,
            "reason": None if configured else "Live server configuration is incomplete or disabled.",
        }

    def preflight(self) -> dict[str, Any]:
        status = self.status()
        if not status["configured"]:
            raise LiveControlError("Live preflight refused: server-side Live configuration is not enabled.")
        adapter = BinanceSpotExecution()
        try:
            if adapter.settings.testnet or not adapter.settings.live_enabled:
                raise LiveControlError("Exchange adapter is not in explicitly enabled Spot Live mode.")
            balance = adapter.fetch_balance()
            balances = {}
            for asset, values in balance.items():
                if isinstance(values, dict) and any(values.get(k) is not None for k in ("free", "used", "total")):
                    balances[asset] = {
                        "free": str(values.get("free") or "0"),
                        "used": str(values.get("used") or "0"),
                        "total": str(values.get("total") or "0"),
                    }
            prices = {symbol: str(adapter.fetch_last_price(symbol)) for symbol in sorted(adapter.settings.allowed_symbols)}
            return {
                "status": "read_only_preflight_passed",
                "mode": "binance_spot_live",
                "orders_submitted": False,
                "withdrawals_supported": False,
                "balances": balances,
                "prices": prices,
                "allowed_symbols": sorted(adapter.settings.allowed_symbols),
                "emergency_stop": self.stopped(),
                "orders_enabled": False,
            }
        except (ccxt.BaseError, ExecutionDisabled, ValueError, RuntimeError) as exc:
            raise LiveControlError("Binance Live read-only preflight failed.") from exc
        finally:
            adapter.close()
