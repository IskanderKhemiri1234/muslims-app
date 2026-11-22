"""Crash-resistant atomic JSON persistence for paper positions and trade history."""
from __future__ import annotations

import json
import os
import shutil
from datetime import date
from pathlib import Path
from typing import Any


def empty_state() -> dict[str, Any]:
    return {
        "day": date.today().isoformat(), "daily_pnl_usdt": 0.0,
        "total_realized_pnl_usdt": 0.0, "positions": {},
        "last_processed_candles": {}, "trade_history": [], "decision_history": [],
    }


def _read_state(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict) or not isinstance(data.get("positions"), dict):
        raise TypeError("Paper state file has an invalid structure.")
    if not isinstance(data.get("trade_history", []), list):
        raise TypeError("Paper trade history has an invalid structure.")
    for key, value in empty_state().items():
        data.setdefault(key, value)
    return data


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        backup = path.with_suffix(path.suffix + ".bak")
        if backup.exists():
            return _read_state(backup)
        return empty_state()
    try:
        return _read_state(path)
    except (OSError, ValueError, TypeError):
        backup = path.with_suffix(path.suffix + ".bak")
        if backup.exists():
            return _read_state(backup)
        raise


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    backup = path.with_suffix(path.suffix + ".bak")
    backup_temporary = backup.with_suffix(backup.suffix + ".tmp")

    if path.exists():
        shutil.copy2(path, backup_temporary)
        with backup_temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        backup_temporary.replace(backup)

    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, ensure_ascii=False)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)

    # Persist the directory entry where supported (Linux hosts).
    try:
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError:
        pass
